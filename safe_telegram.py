# -*- coding: utf-8 -*-
"""
safe_telegram.py
================
هستهٔ مرکزیِ «ارسال/ویرایشِ امنِ پیام تلگرام» — برای رفع این باگ‌های واقعیِ
لاگ‌شده، بدون نیاز به تغییر تک‌تکِ صداهای send_message/edit_message_text تو
کل پروژه:

    BadRequest: Message text is empty
    BadRequest: Message is not modified
    BadRequest: Can't parse entities: can't find end of the entity ...
    RetryAfter: Flood control exceeded. Retry in N seconds
    TimedOut

install_safe_telegram_patches() یه‌بار (تو main()، قبل از ساختن Application)
صدا زده می‌شه و متدهای زیر رو پچ می‌کنه:

    telegram.Bot.send_message
    telegram.Bot.edit_message_text
    telegram.Bot.edit_message_caption
    telegram.Message.edit_text
    telegram.Message.edit_caption

بعد از پچ، همهٔ کدِ موجودِ پروژه (بدون تغییر) خودکار در برابر این خطاها
مقاومه. این helper هیچ قابلیتی رو حذف/تغییر نمی‌ده — فقط قبل از رفتنِ
درخواست به تلگرام validate می‌کنه، و بعد از خطای موقتی (Flood/Timeout) با
backoff محدود (نه بی‌نهایت) دوباره امتحان می‌کنه.
"""

import asyncio
import logging

from telegram import Bot, Message
from telegram.error import BadRequest, RetryAfter, TimedOut

log = logging.getLogger(__name__)

_PATCHED = False
_MAX_RETRIES = 2


def _is_empty_text(text) -> bool:
    return text is None or (isinstance(text, str) and text.strip() == "")


async def _call_with_resilience(func, *args, **kwargs):
    """یه فراخوانیِ تلگرام رو با retry کنترل‌شده (نه loop بی‌نهایت) اجرا
    می‌کنه، «Message is not modified» رو بی‌صدا می‌بلعه، و اگه parse_mode
    باعثِ خطای entity parsing بشه یه‌بار بدون parse_mode fallback می‌کنه."""
    attempt = 0
    tried_plain_fallback = False
    while True:
        try:
            return await func(*args, **kwargs)
        except RetryAfter as e:
            attempt += 1
            wait = float(getattr(e, "retry_after", 5) or 5)
            if attempt > _MAX_RETRIES:
                log.warning(f"RetryAfter بعد از {attempt} تلاش رها شد (wait={wait}s)")
                return None
            log.info(f"⏳ Flood control — {wait} ثانیه صبر (تلاش {attempt}/{_MAX_RETRIES})")
            await asyncio.sleep(wait + 0.5)
        except TimedOut:
            attempt += 1
            if attempt > _MAX_RETRIES:
                log.warning(f"TimedOut بعد از {attempt} تلاش رها شد")
                return None
            await asyncio.sleep(1.5 * attempt)
        except BadRequest as e:
            msg = str(e).lower()
            if "message is not modified" in msg:
                return None
            if "message to edit not found" in msg or "message can't be edited" in msg or "message to delete not found" in msg:
                return None
            if (
                "can't parse entities" in msg
                and not tried_plain_fallback
                and kwargs.get("parse_mode") is not None
            ):
                tried_plain_fallback = True
                log.info("⚠️ خطای parse entities — fallback به متن ساده (بدون parse_mode)")
                kwargs["parse_mode"] = None
                continue
            raise


def install_safe_telegram_patches():
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    orig_bot_send_message = Bot.send_message
    orig_bot_edit_text = Bot.edit_message_text
    orig_bot_edit_caption = Bot.edit_message_caption
    orig_msg_edit_text = Message.edit_text
    orig_msg_edit_caption = Message.edit_caption

    async def patched_bot_send_message(self, chat_id, text, *args, **kwargs):
        if _is_empty_text(text):
            log.warning(f"send_message با متن خالی به chat_id={chat_id} — نادیده گرفته شد")
            return None
        return await _call_with_resilience(orig_bot_send_message, self, chat_id, text, *args, **kwargs)

    async def patched_bot_edit_message_text(self, text, *args, **kwargs):
        if _is_empty_text(text):
            log.warning("edit_message_text با متن خالی — نادیده گرفته شد")
            return None
        return await _call_with_resilience(orig_bot_edit_text, self, text, *args, **kwargs)

    async def patched_bot_edit_message_caption(self, *args, **kwargs):
        # caption می‌تونه عمداً خالی/None باشه (پاک کردن کپشن)؛ اینجا فقط
        # مقاوم‌سازی در برابر flood/timeout/not-modified لازمه، نه empty-guard.
        return await _call_with_resilience(orig_bot_edit_caption, self, *args, **kwargs)

    async def patched_msg_edit_text(self, text, *args, **kwargs):
        if _is_empty_text(text):
            log.warning("message.edit_text با متن خالی — نادیده گرفته شد")
            return None
        return await _call_with_resilience(orig_msg_edit_text, self, text, *args, **kwargs)

    async def patched_msg_edit_caption(self, *args, **kwargs):
        return await _call_with_resilience(orig_msg_edit_caption, self, *args, **kwargs)

    Bot.send_message = patched_bot_send_message
    Bot.edit_message_text = patched_bot_edit_message_text
    Bot.edit_message_caption = patched_bot_edit_message_caption
    Message.edit_text = patched_msg_edit_text
    Message.edit_caption = patched_msg_edit_caption

    log.info("✅ safe_telegram: پچ‌های ارسال/ویرایش امن پیام (empty-text/not-modified/parse/flood/timeout) نصب شد")
