# -*- coding: utf-8 -*-
"""
tg_resilience.py
================
یه لایه‌ی محافظ نازک دور دو متد پرمصرف Bot (send_message و edit_message_text)
که رایج‌ترین خطاهای بی‌خطرِ Telegram API رو —که تو لاگ باگ‌ریپورتر مرتب
تکرار می‌شدن— قبل از رسیدن به global_error_handler خنثی می‌کنه:

    - BadRequest("Message text is empty"): بعضی هندلرها تو حالت‌های لبه‌ای
      (لیست خالی، حالت خاص یه بازی و...) یه رشته‌ی خالی می‌سازن. به‌جای اینکه
      کل Update با Exception بترکه، یه متن جایگزین کوتاه فرستاده می‌شه.
    - BadRequest("Message is not modified"): edit با همون متن/دکمه‌ی قبلی
      (مثلاً دابل‌کلیک رو یه دکمه). کاملاً بی‌خطره، فقط باید بی‌صدا رد بشه.
    - BadRequest("Can't parse entities..."): متن دینامیک (تایتل ویدیو، خروجی
      AI، ورودی خام کاربر) یه کاراکتر Markdown نصفه‌ونیمه داره. یه بار Retry
      با متن خام (بدون parse_mode) به‌جای شکست کامل.
    - RetryAfter (Flood control): طبق زمانی که تلگرام گفته صبر می‌کنه و یه بار
      Retry می‌کنه، به‌جای اینکه پیام کامل گم بشه.
    - TimedOut: یه بار Retry ساده.

⚠️ این پچ فقط رو *نمونه‌ی* app.bot اعمال می‌شه (نه رو کلاس Bot)، و فقط رو
مسیرهای خطا اثر می‌ذاره — هیچ رفتار موفقیت‌آمیزِ فعلیِ هیچ ماژولی
(bot.py, downloader.py, card_room.py, ...) تغییر نمی‌کنه. مستقل و
self-contained است؛ اگه حذفش کنی همه‌چیز دقیقاً به رفتار قبلی برمی‌گرده.
"""
import asyncio
import logging

from telegram.error import BadRequest, RetryAfter, TimedOut

log = logging.getLogger(__name__)

_EMPTY_TEXT_FALLBACK = "🦇 …"
_MAX_RETRY_AFTER_WAIT = 30


def _has(msg: str, needle: str) -> bool:
    return needle.lower() in (msg or "").lower()


def _get_text(args, kwargs, pos: int):
    """text گاهی کلیدواژه‌ست، گاهی پوزیشنال (مثل send_message(chat_id, text))."""
    if "text" in kwargs:
        return kwargs["text"]
    if len(args) > pos:
        return args[pos]
    return None


def _set_text(args, kwargs, pos: int, value: str):
    if "text" in kwargs:
        kwargs["text"] = value
        return args, kwargs
    if len(args) > pos:
        args = list(args)
        args[pos] = value
        return tuple(args), kwargs
    kwargs["text"] = value
    return args, kwargs


def patch_bot_resilience(app):
    """رو app.bot صدا زده می‌شه، بعد از ساخته‌شدن Application."""
    bot = app.bot
    orig_send_message = bot.send_message
    orig_edit_message_text = bot.edit_message_text

    async def safe_send_message(*args, **kwargs):
        # send_message(self, chat_id, text, ...) → text پوزیشنال، اندیس 1
        if not (_get_text(args, kwargs, 1) or "").strip():
            args, kwargs = _set_text(args, kwargs, 1, _EMPTY_TEXT_FALLBACK)
        try:
            return await orig_send_message(*args, **kwargs)
        except BadRequest as e:
            if _has(str(e), "can't parse entities") and kwargs.get("parse_mode"):
                kwargs["parse_mode"] = None
                try:
                    return await orig_send_message(*args, **kwargs)
                except Exception:
                    log.info(f"send_message: retry بدون parse_mode هم شکست خورد: {e}")
                    return None
            raise
        except RetryAfter as e:
            wait = min(e.retry_after, _MAX_RETRY_AFTER_WAIT) + 1
            log.info(f"send_message: Flood control — {wait}s صبر می‌کنیم")
            await asyncio.sleep(wait)
            try:
                return await orig_send_message(*args, **kwargs)
            except Exception as e2:
                log.info(f"send_message: retry بعد از Flood control هم شکست خورد: {e2}")
                return None
        except TimedOut:
            try:
                return await orig_send_message(*args, **kwargs)
            except Exception as e2:
                log.info(f"send_message: retry بعد از TimedOut هم شکست خورد: {e2}")
                return None

    async def safe_edit_message_text(*args, **kwargs):
        # edit_message_text(self, text, chat_id=None, ...) → text پوزیشنال، اندیس 0
        if not (_get_text(args, kwargs, 0) or "").strip():
            args, kwargs = _set_text(args, kwargs, 0, _EMPTY_TEXT_FALLBACK)
        try:
            return await orig_edit_message_text(*args, **kwargs)
        except BadRequest as e:
            msg = str(e)
            if _has(msg, "message is not modified"):
                return None
            if _has(msg, "can't parse entities") and kwargs.get("parse_mode"):
                kwargs["parse_mode"] = None
                try:
                    return await orig_edit_message_text(*args, **kwargs)
                except Exception:
                    log.info(f"edit_message_text: retry بدون parse_mode هم شکست خورد: {e}")
                    return None
            raise
        except RetryAfter as e:
            wait = min(e.retry_after, _MAX_RETRY_AFTER_WAIT) + 1
            log.info(f"edit_message_text: Flood control — {wait}s صبر می‌کنیم")
            await asyncio.sleep(wait)
            try:
                return await orig_edit_message_text(*args, **kwargs)
            except Exception as e2:
                log.info(f"edit_message_text: retry بعد از Flood control هم شکست خورد: {e2}")
                return None
        except TimedOut:
            try:
                return await orig_edit_message_text(*args, **kwargs)
            except Exception as e2:
                log.info(f"edit_message_text: retry بعد از TimedOut هم شکست خورد: {e2}")
                return None

    bot.send_message = safe_send_message
    bot.edit_message_text = safe_edit_message_text

    # 🐛 رفع باگ «Can't parse entities» رو کپشن‌ها هم: جاهایی مثل تشخیص فیلم
    # (media_recognition.py) خلاصه‌ی TMDB رو مستقیم تو یه caption با
    # parse_mode=Markdown می‌ذارن؛ اون متن از بیرون میاد و می‌تونه کاراکتر
    # Markdown نصفه‌ونیمه داشته باشه. همون Retry-بدون-parse_mode رو اینجا هم
    # (فقط رو caption، نه کل پیام) اضافه می‌کنیم — بدون Retry برای Timeout/
    # Flood چون آپلود فایل حجیمه و Retry خودکارش هزینه‌بره.
    def _wrap_caption_method(method_name):
        orig = getattr(bot, method_name)

        async def safe_method(*args, **kwargs):
            try:
                return await orig(*args, **kwargs)
            except BadRequest as e:
                if _has(str(e), "can't parse entities") and kwargs.get("parse_mode") and kwargs.get("caption"):
                    kwargs["parse_mode"] = None
                    # اگه پارامتر فایل یه file-like باز باشه (نه bytes/URL)،
                    # قبل از Retry برش می‌گردونیم اول فایل — وگرنه ممکنه
                    # تلاش اول تا حدی مصرفش کرده باشه.
                    for v in list(args) + list(kwargs.values()):
                        seek = getattr(v, "seek", None)
                        if callable(seek):
                            try:
                                seek(0)
                            except Exception:
                                pass
                    try:
                        return await orig(*args, **kwargs)
                    except Exception:
                        log.info(f"{method_name}: retry بدون parse_mode هم شکست خورد: {e}")
                        raise e
                raise

        setattr(bot, method_name, safe_method)

    for _m in ("send_photo", "send_video", "send_audio", "send_document"):
        _wrap_caption_method(_m)

    log.info("🛡️ tg_resilience: send_message/edit_message_text/send_photo/send_video/send_audio/send_document patch شدن (empty-text / not-modified / parse-entities / flood / timeout)")
