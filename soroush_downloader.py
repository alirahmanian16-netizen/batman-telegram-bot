# -*- coding: utf-8 -*-
"""
soroush_downloader.py
======================
اتصال اکانت Soroush Plus + دانلود استوری سروش — به‌عنوان یه افزونه‌ی کاملاً
جدا از bot.py و downloader.py (طبق قانون پروژه: هیچ Handler/دکمه/منوی قبلی
دست نمی‌خوره).

سه Callback Prefix اختصاصی این ماژول: "srs:menu", "srs:sendcode", "srs:test",
"srs:dl:menu" -- همه با پیشوند "srs:" که به‌صورت جدا تو bot.py به لیست
_FOREIGN_CALLBACK_PREFIXES اضافه شده تا button_handler عمومی (گروه ۰،
بدون pattern) قاپش نزنه؛ دقیقاً همون الگویی که برای reminders/mail/voice/...
تو خودِ پروژه استفاده شده.

register_soroush(app, deps) طبق همون الگوی register_xxx(app, deps) بقیه‌ی
ماژول‌های پروژه (مثل security_tools.py) عمل می‌کنه:
    deps = {"owner_id": OWNER_ID}
"""

import os
import shutil
import asyncio
import logging
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

import soroush_client as sc

log = logging.getLogger("batbot.soroush")

_DEPS = {}

# user_id -> asyncio.Future در انتظار کد ورودِ سروش‌پلاس (فقط تو حافظه، هرگز دیسک/DB)
_CODE_WAITERS: dict = {}

# user_id -> True یعنی «منتظر آیدی/یوزرنیم سروش برای دانلود استوری»
_STORY_WAIT: set = set()

STORY_TIMEOUT_SEC = 120
FETCH_TIMEOUT_SEC = 30

_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi")


def _safe_err_text(e: Exception, limit: int = 160) -> str:
    """متن کوتاه خطا برای نمایش به Owner -- شماره‌ی تلفن اگه بود پاک می‌شه."""
    txt = str(e) or "-"
    phone = getattr(sc, "SOROUSH_PHONE", "") or ""
    if phone:
        txt = txt.replace(phone, "***").replace(phone.lstrip("+"), "***")
    return txt[:limit]


def is_owner_id(user_id) -> bool:
    """برای ماژول‌های دیگه (مثلاً downloader.py) تا دکمه‌ی اتصال رو فقط به Owner نشون بدن."""
    owner_id = _DEPS.get("owner_id")
    return bool(owner_id and user_id == owner_id)


def _is_owner(update: Update) -> bool:
    owner_id = _DEPS.get("owner_id")
    return bool(update.effective_user and owner_id and update.effective_user.id == owner_id)


def _admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 تست اتصال سروش", callback_data="srs:test")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:downloader")],
    ])


def _send_code_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 ارسال کد ورود", callback_data="srs:sendcode")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:downloader")],
    ])


# =========================================================
#  بخش اول -- اتصال اکانت Soroush Plus (فقط Owner)
# =========================================================

async def srs_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("این بخش فقط برای مالک ربات در دسترسه.", show_alert=True)
        return
    await query.answer()

    if not sc.is_library_available():
        await query.edit_message_text(
            "⚠️ کتابخونه‌ی spluslib نصب نیست. اول اون رو به requirements.txt اضافه کن و ری‌دیپلوی کن.",
            reply_markup=_admin_menu_keyboard(),
        )
        return

    connected = await sc.is_connected()
    if connected:
        await query.edit_message_text("✅ اکانت سروش‌پلاس متصل است.", reply_markup=_admin_menu_keyboard())
    else:
        if not sc.SOROUSH_PHONE:
            await query.edit_message_text(
                "⚠️ متغیر محیطی SOROUSH_PHONE تو Railway تنظیم نشده. اول اون رو ست کن، بعد ری‌استارت کن.",
                reply_markup=_admin_menu_keyboard(),
            )
            return
        await query.edit_message_text(
            "🦇 اکانت سروش‌پلاس متصل نیست.\n\nبرای اتصال، دکمه‌ی زیر رو بزن.",
            reply_markup=_send_code_keyboard(),
        )


async def srs_sendcode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("این بخش فقط برای مالک ربات در دسترسه.", show_alert=True)
        return
    await query.answer()
    user_id = query.from_user.id

    if not sc.SOROUSH_PHONE:
        await query.edit_message_text(
            "⚠️ متغیر محیطی SOROUSH_PHONE تو Railway تنظیم نشده.", reply_markup=_admin_menu_keyboard()
        )
        return

    if user_id in _CODE_WAITERS:
        await query.edit_message_text("⏳ یه درخواست کد قبلاً در حال انجامه؛ کد رو همینجا بفرست.")
        return

    await query.edit_message_text("📤 در حال ارسال درخواست ورود به Soroush Plus...")

    loop = asyncio.get_event_loop()

    async def _code_callback():
        fut = loop.create_future()
        _CODE_WAITERS[user_id] = fut
        try:
            code = await asyncio.wait_for(fut, timeout=300)
        finally:
            _CODE_WAITERS.pop(user_id, None)
        return code

    async def _run_login():
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🔑 کد ورودی که برای شماره‌ی سروش‌پلاس ارسال شد رو همینجا بفرست:",
            )
            me = await sc.start_login(code_callback=_code_callback)
            name = me.get("first_name") if isinstance(me, dict) else ""
            name = name or ""
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ اکانت سروش‌پلاس با موفقیت متصل شد.{(' (' + name + ')') if name else ''}",
                reply_markup=_admin_menu_keyboard(),
            )
        except sc.SoroushNotConfigured as e:
            await context.bot.send_message(chat_id=user_id, text=f"⚠️ {e}")
        except asyncio.TimeoutError:
            await context.bot.send_message(chat_id=user_id, text="⚠️ زمان وارد کردن کد تموم شد. دوباره تلاش کن.")
        except Exception as e:
            # 🔐 traceback کامل تو لاگ Railway ثبت می‌شه (traceback شامل مقدار
            # شماره/کد نیست). تو پیام کاربر هم نوع خطا + متن کوتاهِ خطا (مثلاً
            # اسم attribute) نشون داده می‌شه، با پاک‌سازی شماره.
            log.error("⚠️ خطای لاگین سروش‌پلاس (traceback کامل پایین)", exc_info=True)
            detail = _safe_err_text(e)
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ اتصال ناموفق بود ({type(e).__name__}: {detail}). دوباره تلاش کن.",
            )

    context.application.create_task(_run_login())


async def srs_test_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("این بخش فقط برای مالک ربات در دسترسه.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text("⏳ در حال تست اتصال...")
    connected = await sc.is_connected()
    text = "🟢 اتصال سروش فعال است" if connected else "🔴 اتصال سروش برقرار نیست"
    await query.edit_message_text(text, reply_markup=_admin_menu_keyboard())


# =========================================================
#  بخش دوم -- دانلود استوری سروش (داخل دانلودر عمومی)
# =========================================================

async def srs_story_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _STORY_WAIT.add(query.from_user.id)
    await query.edit_message_text("📱 آیدی یا نام کاربری سروش‌پلاس را ارسال کنید:")


def _normalize_target(raw: str):
    raw = raw.strip().lstrip("@")
    if raw.isdigit():
        return int(raw)
    return raw


async def _download_and_send_stories(update: Update, context: ContextTypes.DEFAULT_TYPE, target):
    message = update.effective_message
    chat_id = update.effective_chat.id

    if not sc.is_library_available():
        await message.reply_text("⚠️ کتابخونه‌ی spluslib نصب نیست.")
        return
    if not sc.session_file_exists():
        await message.reply_text("❌ اکانت سروش‌پلاس متصل نیست.")
        return

    errors = sc.splus_errors

    async def _fetch(client):
        return await client.has_story(target)

    try:
        info = await asyncio.wait_for(sc.with_client(_fetch), timeout=FETCH_TIMEOUT_SEC)
    except sc.SoroushNotConfigured:
        await message.reply_text("❌ اکانت سروش‌پلاس متصل نیست.")
        return
    except asyncio.TimeoutError:
        await message.reply_text("⚠️ هنگام دریافت استوری خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        return
    except Exception as e:
        name = type(e).__name__
        if errors and isinstance(e, getattr(errors, "UserNotFoundError", ())):
            await message.reply_text("❌ کاربر سروش‌پلاس پیدا نشد.")
        elif errors and isinstance(e, getattr(errors, "UsernameNotFoundError", ())):
            await message.reply_text("❌ کاربر سروش‌پلاس پیدا نشد.")
        else:
            log.warning(f"⚠️ خطای has_story سروش: {name}", exc_info=True)
            await message.reply_text("⚠️ هنگام دریافت استوری خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        return

    if not info or not info.get("has_story"):
        await message.reply_text("❌ این کاربر استوری فعالی ندارد.")
        return

    stories = info.get("stories") or []
    if not stories:
        await message.reply_text("❌ این کاربر استوری فعالی ندارد.")
        return

    for story in stories:
        story_id = story.get("id")
        tmp_dir = tempfile.mkdtemp(prefix="srs_")
        try:
            dest = os.path.join(tmp_dir, f"story_{story_id}")

            async def _dl(client, _dest=dest, _sid=story_id):
                return await client.download_story(target, _sid, file_path=_dest)

            path = await asyncio.wait_for(sc.with_client(_dl), timeout=STORY_TIMEOUT_SEC)
            if not path or not os.path.exists(path):
                await message.reply_text("⚠️ هنگام دریافت استوری خطایی رخ داد. لطفاً دوباره تلاش کنید.")
                continue

            ext = os.path.splitext(path)[1].lower()
            with open(path, "rb") as f:
                if ext in _VIDEO_EXTS:
                    await context.bot.send_video(chat_id=chat_id, video=f)
                else:
                    await context.bot.send_photo(chat_id=chat_id, photo=f)
        except asyncio.TimeoutError:
            await message.reply_text("⚠️ هنگام دریافت استوری خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        except Exception as e:
            name = type(e).__name__
            if errors and isinstance(e, (
                getattr(errors, "UserPrivacyError", ()),
                getattr(errors, "NoPermissionError", ()),
            )):
                await message.reply_text("❌ این استوری برای اکانت متصل قابل‌دسترسی نیست.")
            else:
                log.warning(f"⚠️ خطای download_story سروش: {name}", exc_info=True)
                await message.reply_text("⚠️ هنگام دریافت استوری خطایی رخ داد. لطفاً دوباره تلاش کنید.")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# =========================================================
#  یک هندلر متنی مشترک -- فقط وقتی کاربر تو یکی از دو حالتِ بالاست فعال می‌شه
#  (کد ورود / آیدی سروش)؛ در غیر این صورت هیچ کاری نمی‌کنه و به بقیه‌ی
#  هندلرهای پروژه (تو گروه‌های دیگه) دست نمی‌زنه.
# =========================================================

async def soroush_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return
    user_id = update.effective_user.id
    text = message.text.strip()

    if user_id in _CODE_WAITERS:
        fut = _CODE_WAITERS.get(user_id)
        if fut and not fut.done():
            fut.set_result(text)
        return

    if user_id in _STORY_WAIT:
        _STORY_WAIT.discard(user_id)
        target = _normalize_target(text)
        await _download_and_send_stories(update, context, target)
        return


def register_soroush(app, deps: dict):
    _DEPS.update(deps or {})

    app.add_handler(CallbackQueryHandler(srs_menu_callback, pattern=r"^srs:menu$"), group=6)
    app.add_handler(CallbackQueryHandler(srs_sendcode_callback, pattern=r"^srs:sendcode$"), group=6)
    app.add_handler(CallbackQueryHandler(srs_test_callback, pattern=r"^srs:test$"), group=6)
    app.add_handler(CallbackQueryHandler(srs_story_menu_callback, pattern=r"^srs:dl:menu$"), group=6)

    # گروه اختصاصی و جدید (۹) تا با هندلرهای متنیِ گروه ۶ (مثل لینک‌های
    # دانلودر) تداخل نکنه -- هر دو مستقل اجرا می‌شن.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, soroush_text_router),
        group=9,
    )
