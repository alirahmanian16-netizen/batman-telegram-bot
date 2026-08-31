# -*- coding: utf-8 -*-
"""
group_admin_extra.py
================
دو ابزار مدیریتی جدید برای «مدیریت گروه»:

    🔒 قفل گروه / باز کردن گروه — با یه کلمه، فرستادن پیام رو برای همه‌ی
       غیرادمین‌ها می‌بنده/باز می‌کنه (برای وقتی گروه شلوغ/بحرانیه).
    🧹 پاکسازی سریع — روی قدیمی‌ترین پیامی که می‌خوای حذفش کنی ریپلای کن و
       بنویس «پاکسازی» تا همه‌ی پیام‌های از اونجا تا الان (حداکثر ۱۰۰ تا)
       پاک بشن. حداقل برای OWNER_ID همیشه فعاله؛ بقیه‌ی ادمین‌ها هم فقط اگه
       تو همون گروه واقعاً مجوز حذف پیام (can_delete_messages) داشته باشن.
"""

import asyncio
import logging

from telegram import Update, ChatPermissions
from telegram.constants import ChatType
from telegram.error import RetryAfter
from telegram.ext import ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

LOCK_RE = filters.Regex(r"(?i)^\s*قفل گروه\s*$")
UNLOCK_RE = filters.Regex(r"(?i)^\s*باز کردن گروه\s*$")
PURGE_RE = filters.Regex(r"(?i)^\s*پاکسازی\s*$") & filters.REPLY

MAX_PURGE = 100
PURGE_CONCURRENCY = 10  # چند حذف هم‌زمان — سریع‌تر از یکی‌یکی، ولی محافظه‌کارانه برای جلوگیری از RetryAfter


async def _can_purge(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id) -> bool:
    """Permission Check واقعی قبل از اجرای پاکسازی:
    - OWNER_ID همیشه مجازه (حداقل تضمین‌شده طبق قانون پروژه).
    - بقیه‌ی کاربرها فقط اگه creator باشن، یا administrator با can_delete_messages=True."""
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    if user.id == owner_id:
        return True
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except Exception:
        return False
    if member.status == "creator":
        return True
    if member.status == "administrator":
        return bool(getattr(member, "can_delete_messages", False))
    return False


async def _delete_one(bot, chat_id, message_id, sem: asyncio.Semaphore) -> bool:
    """حذف یه پیام — خطا یا عدم‌مجوز تلگرام روی این پیام باعث توقف بقیه‌ی
    عملیات نمی‌شه، فقط همین یکی ناموفق حساب می‌شه."""
    async with sem:
        try:
            await bot.delete_message(chat_id, message_id)
            return True
        except RetryAfter as e:
            # Flood control گفته صبر کن — یه‌بار صبر می‌کنیم و دوباره امتحان می‌کنیم،
            # نه اینکه با ارسال پشت‌سرهم خودمون باعث Flood بیشتر بشیم.
            try:
                await asyncio.sleep(e.retry_after)
                await bot.delete_message(chat_id, message_id)
                return True
            except Exception:
                return False
        except Exception:
            return False


def register_group_admin_extra(app, deps):
    """deps = {"is_group_admin": async(update, context) -> bool, "owner_id": int}"""
    owner_id = deps.get("owner_id")

    async def lock_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return
        if not await deps["is_group_admin"](update, context):
            await update.message.reply_text("⛔️ فقط ادمین‌ها می‌تونن گروه رو قفل کنن.")
            return
        try:
            await context.bot.set_chat_permissions(
                update.effective_chat.id, ChatPermissions(can_send_messages=False)
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ نشد: {e}")
            return
        await update.message.reply_text("🔒 گروه قفل شد؛ فقط ادمین‌ها می‌تونن پیام بدن.")

    async def unlock_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return
        if not await deps["is_group_admin"](update, context):
            await update.message.reply_text("⛔️ فقط ادمین‌ها می‌تونن گروه رو باز کنن.")
            return
        try:
            await context.bot.set_chat_permissions(
                update.effective_chat.id,
                ChatPermissions(
                    can_send_messages=True, can_send_audios=True, can_send_documents=True,
                    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ نشد: {e}")
            return
        await update.message.reply_text("🔓 گروه باز شد؛ همه می‌تونن دوباره پیام بدن.")

    async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return
        # Permission Check قبل از هرگونه اجرا — فقط مخفی کردن دکمه/محدود کردن
        # دستور کافی نیست، اینجا واقعاً با دیتای زنده‌ی تلگرام چک می‌کنیم.
        if not await _can_purge(update, context, owner_id):
            await update.message.reply_text(
                "⛔️ فقط سازنده‌ی ربات یا ادمین‌هایی که تو همین گروه مجوز حذف پیام دارن می‌تونن پاکسازی کنن."
            )
            return
        chat_id = update.effective_chat.id  # فقط همین چت تحت تاثیر قرار می‌گیره
        start_id = update.message.reply_to_message.message_id
        end_id = update.message.message_id
        if end_id - start_id > MAX_PURGE:
            start_id = end_id - MAX_PURGE
        message_ids = list(range(start_id, end_id + 1))

        # حذف هم‌زمان (با سقف Concurrency) برای سرعت بیشتر، بدون اینکه خطای
        # یه پیام باعث توقف یا Crash کل عملیات بشه.
        sem = asyncio.Semaphore(PURGE_CONCURRENCY)
        results = await asyncio.gather(
            *[_delete_one(context.bot, chat_id, mid, sem) for mid in message_ids],
            return_exceptions=True,
        )
        deleted = sum(1 for r in results if r is True)

        # پیام نتیجه — دقیقاً یکی، و برخلاف قبل، دیگه حذف نمی‌شه.
        try:
            await context.bot.send_message(
                chat_id, f"🧹 گاتهام از مجرمان پاکسازی شد.\n🗑 {deleted} پیام حذف شد."
            )
        except Exception:
            pass

    app.add_handler(MessageHandler(LOCK_RE, lock_group), group=24)
    app.add_handler(MessageHandler(UNLOCK_RE, unlock_group), group=24)
    app.add_handler(MessageHandler(PURGE_RE, purge), group=24)
