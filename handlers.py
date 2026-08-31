# -*- coding: utf-8 -*-
"""
gotham_ai/handlers.py
========================
اتصال «✨ امکانات جدید گاتهام» به تلگرام: منو، callbackها، و پردازش
متن/عکس/فایل/صدا وقتی کاربر تو session هوش‌مصنوعی فعاله.

الگوی یکپارچگی: دقیقاً مثل post_saz.py — یه تابع intercept که از داخل
handle_message / handle_photo_sticker خودِ bot.py صدا زده می‌شه و اگه کاربر
تو حالت AI باشه، True برمی‌گردونه (یعنی «این پیام مال منه، کس دیگه‌ای روش کار
نکنه»). برای صدا/فایل که bot.py اصلاً هندلر عمومی نداره، این ماژول خودش
MessageHandler جدا (با گروه اختصاصی) ثبت می‌کنه.
"""

import os
import io
import time
import asyncio
import logging
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

from . import config
from . import store
from . import client
from . import tools as ai_tools

log = logging.getLogger(__name__)

# اکشن‌های «یک‌باره»ی در انتظار (ساخت تصویر/ویدیو/صدا) — نیازی به session کامل
# ندارن، فقط پیام بعدیِ همون کاربر رو منتظرن. حافظه‌ی موقت، نه دیتابیس (سبک و
# کوتاه‌مدت؛ با ری‌استارت ربات پاک می‌شه که مشکلی نیست چون خودِ عملیات هم
# یک‌باره‌ست).
_PENDING = {}  # (chat_id, user_id) -> {"action": str, "ts": float}
_PENDING_TTL = 300


def _set_pending(chat_id, user_id, action):
    _PENDING[(chat_id, user_id)] = {"action": action, "ts": time.time()}


def _pop_pending(chat_id, user_id):
    key = (chat_id, user_id)
    entry = _PENDING.get(key)
    if not entry:
        return None
    if time.time() - entry["ts"] > _PENDING_TTL:
        _PENDING.pop(key, None)
        return None
    _PENDING.pop(key, None)
    return entry["action"]


def _has_pending(chat_id, user_id):
    entry = _PENDING.get((chat_id, user_id))
    if not entry:
        return False
    if time.time() - entry["ts"] > _PENDING_TTL:
        _PENDING.pop((chat_id, user_id), None)
        return False
    return True


# ---------------- متن‌های ثابت ----------------

AI_MENU_TEXT = (
    "✨ *امکانات جدید گاتهام*\n\n"
    "اتصال کامل به FreeLLMAPI: چت هوشمند، تحلیل تصویر و فایل، ساخت تصویر و "
    "ویدیو، تبدیل صدا⇄متن، مدل‌های متعدد با Failover خودکار.\n\n"
    "یکی رو انتخاب کن:"
)


def build_ai_menu_keyboard(is_owner=False):
    rows = [
        [InlineKeyboardButton("🤖 AI Chat", callback_data="gai:chat:start"),
         InlineKeyboardButton("👁️ تحلیل تصویر", callback_data="gai:vision:prompt")],
        [InlineKeyboardButton("📄 تحلیل فایل", callback_data="gai:file:prompt"),
         InlineKeyboardButton("🖼️ ساخت تصویر", callback_data="gai:image:prompt")],
        [InlineKeyboardButton("🎬 ساخت ویدیو", callback_data="gai:video:prompt"),
         InlineKeyboardButton("🎤 صدا به متن", callback_data="gai:stt:info")],
        [InlineKeyboardButton("🔊 متن به صدا", callback_data="gai:tts:prompt"),
         InlineKeyboardButton("🤖 مدل‌های AI", callback_data="gai:models")],
        [InlineKeyboardButton("📊 وضعیت AI", callback_data="gai:status"),
         InlineKeyboardButton("🧠 مدیریت حافظه", callback_data="gai:mem")],
        [InlineKeyboardButton("⚙️ تنظیمات AI", callback_data="gai:settings")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ]
    return InlineKeyboardMarkup(rows)


def _back_kb(target="gai:menu"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=target)]])


def _mem_kb():
    rows = [
        [InlineKeyboardButton("🆕 چت جدید", callback_data="gai:mem:new"),
         InlineKeyboardButton("🧹 پاک‌کردن Context", callback_data="gai:mem:clear")],
        [InlineKeyboardButton("♻️ ریست کامل AI", callback_data="gai:mem:reset"),
         InlineKeyboardButton("⏹ پایان چت", callback_data="gai:chat:end")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="gai:menu")],
    ]
    return InlineKeyboardMarkup(rows)


def _settings_kb():
    rows = [
        [InlineKeyboardButton("🧭 Auto", callback_data="gai:model:auto"),
         InlineKeyboardButton("⚡ Auto Fast", callback_data="gai:model:auto-fast")],
        [InlineKeyboardButton("🧠 Auto Smart", callback_data="gai:model:auto-smart"),
         InlineKeyboardButton("💻 مخصوص Coding", callback_data="gai:model:coding")],
        [InlineKeyboardButton("👁️ مخصوص Vision", callback_data="gai:model:vision"),
         InlineKeyboardButton("🧩 مخصوص Reasoning", callback_data="gai:model:reasoning")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="gai:menu")],
    ]
    return InlineKeyboardMarkup(rows)


_MODEL_ROLE_TO_ENV = {
    "auto": config.FREELLMAPI_DEFAULT_MODEL,
    "auto-fast": config.FREELLMAPI_FAST_MODEL,
    "auto-smart": config.FREELLMAPI_SMART_MODEL,
    "coding": config.FREELLMAPI_CODING_MODEL,
    "vision": config.FREELLMAPI_VISION_MODEL,
    "reasoning": config.FREELLMAPI_REASONING_MODEL,
}


# ---------------- ارسال پاسخ با رعایت محدودیت تلگرام ----------------

async def _send_long_text(update: Update, text: str, header=""):
    text = text or "🦇 (پاسخ خالی بود)"
    full = f"{header}\n\n{text}" if header else text
    if len(full) > 12000:
        # به‌جای اسپم چند پیام، فایل TXT بفرست
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as f:
                f.write(full)
                tmp_path = f.name
            with open(tmp_path, "rb") as fh:
                await update.effective_message.reply_document(
                    fh, filename="gotham_ai_response.txt",
                    caption="🦇 پاسخ طولانی بود، به‌صورت فایل فرستادم."
                )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        return
    for i in range(0, len(full), config.TELEGRAM_TEXT_LIMIT):
        chunk = full[i:i + config.TELEGRAM_TEXT_LIMIT]
        await update.effective_message.reply_text(chunk)


def _model_for_session(session_model: str):
    role = session_model or "auto"
    resolved = _MODEL_ROLE_TO_ENV.get(role, role)
    chain = [resolved] + [m for m in config.CHAT_FAILOVER_CHAIN if m != resolved]
    return chain


# ---------------- AI Chat (متن) ----------------

GOTHAM_AI_SYSTEM_PROMPT = (
    "You are Gotham AI, a helpful multilingual assistant embedded in a Batman-themed "
    "Telegram bot. Reply in the same language the user writes in (Persian or English). "
    "Be concise, clear, and helpful."
)


async def _run_chat_turn(update: Update, chat_id, user_id, user_text: str):
    session = await store.get_session(chat_id, user_id)
    chain = _model_for_session(session["model"])

    cached = await store.cache_get(user_id, chain[0], user_text)
    if cached:
        await _send_long_text(update, cached)
        return

    messages = [{"role": "system", "content": GOTHAM_AI_SYSTEM_PROMPT}]
    messages.extend(session["history"])
    messages.append({"role": "user", "content": user_text})

    thinking_msg = None
    try:
        thinking_msg = await update.effective_message.reply_text("🦇 در حال فکر کردن...")
    except Exception:
        pass

    result, used_model, err = await client.chat_completion(messages, model_chain=chain)

    if thinking_msg:
        try:
            await thinking_msg.delete()
        except Exception:
            pass

    if err and not result:
        await update.effective_message.reply_text(err)
        return

    reply_text = result["content"] or "🦇 (پاسخ خالی بود)"
    await store.append_turn(chat_id, user_id, user_text, reply_text)
    await store.cache_set(user_id, chain[0], user_text, reply_text)
    await _send_long_text(update, reply_text)


async def gotham_ai_intercept_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه True برگردونه، یعنی این پیام مصرف شد و نباید هیچ هندلر دیگه‌ای
    (شخصیت/بازی) روش واکنش نشون بده."""
    msg = update.effective_message
    if not msg or not msg.text:
        return False
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = msg.text.strip()

    pending = _pop_pending(chat_id, user_id)
    if pending == "image":
        await _handle_image_generate(update, text)
        return True
    if pending == "video":
        await _handle_video_generate(update, text)
        return True
    if pending == "tts":
        await _handle_tts(update, text)
        return True

    if text in ("پایان چت", "خروج از هوش مصنوعی", "/endchat"):
        if await store.is_session_active(chat_id, user_id):
            await store.end_session(chat_id, user_id)
            await msg.reply_text("⏹ چت هوش مصنوعی تموم شد.")
            return True
        return False

    if not await store.is_session_active(chat_id, user_id):
        return False

    if not config.is_configured():
        await msg.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return True

    tool_result = ai_tools.try_local_tool(text)
    if tool_result is not None:
        await msg.reply_text(tool_result)
        return True

    await _run_chat_turn(update, chat_id, user_id, text)
    return True


# ---------------- Vision ----------------

async def gotham_ai_intercept_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.effective_message
    if not msg or not msg.photo:
        return False
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # فقط وقتی کاربر واقعاً تو session AI باشه پردازش می‌کنیم (تا با
    # واکنش‌های استیکر/عکسِ شخصیت‌های ربات قاطی نشه)
    if not await store.is_session_active(chat_id, user_id):
        return False

    if not config.is_configured():
        await msg.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return True

    prompt = (msg.caption or "این عکس رو توضیح بده و اگه متنی توش هست بخون.").strip()

    try:
        photo = msg.photo[-1]
        tg_file = await photo.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        image_bytes = buf.getvalue()
    except Exception as e:
        log.warning(f"gotham_ai vision download failed: {e}")
        await msg.reply_text("🦇 دانلود عکس از تلگرام شکست خورد.")
        return True

    thinking_msg = None
    try:
        thinking_msg = await msg.reply_text("👁️ در حال تحلیل تصویر...")
    except Exception:
        pass

    reply_text, err = await client.vision_completion(image_bytes, "image/jpeg", prompt)

    if thinking_msg:
        try:
            await thinking_msg.delete()
        except Exception:
            pass

    if err:
        await msg.reply_text(err)
        return True

    await store.append_turn(chat_id, user_id, f"[عکس] {prompt}", reply_text)
    await _send_long_text(update, reply_text)
    return True


# ---------------- تحلیل فایل ----------------

_TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".yml", ".yaml", ".py", ".js", ".html", ".xml")


async def gotham_ai_intercept_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.effective_message
    if not msg or not msg.document:
        return False
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not await store.is_session_active(chat_id, user_id):
        return False

    if not config.is_configured():
        await msg.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return True

    doc = msg.document
    name = doc.file_name or "file.txt"
    if not name.lower().endswith(_TEXT_EXTS):
        await msg.reply_text(
            "📄 فقط فایل‌های متنی (TXT/MD/CSV/JSON/LOG/...) رو می‌تونم بخونم."
        )
        return True
    if doc.file_size and doc.file_size > 3 * 1024 * 1024:
        await msg.reply_text("📄 فایل خیلی بزرگه (بیشتر از ۳ مگابایت).")
        return True

    try:
        tg_file = await doc.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        raw = buf.getvalue().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning(f"gotham_ai file download failed: {e}")
        await msg.reply_text("🦇 دانلود فایل شکست خورد.")
        return True

    raw = raw[:config.MAX_FILE_CHARS]
    instruction = (msg.caption or "خلاصه کن و نکات مهمش رو استخراج کن.").strip()
    prompt = f"محتوای فایل «{name}»:\n\n{raw}\n\n---\nدرخواست کاربر: {instruction}"

    thinking_msg = None
    try:
        thinking_msg = await msg.reply_text("📄 در حال تحلیل فایل...")
    except Exception:
        pass

    session = await store.get_session(chat_id, user_id)
    chain = _model_for_session(session["model"])
    result, used_model, err = await client.chat_completion(
        [{"role": "system", "content": GOTHAM_AI_SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        model_chain=chain,
    )

    if thinking_msg:
        try:
            await thinking_msg.delete()
        except Exception:
            pass

    if err and not result:
        await msg.reply_text(err)
        return True

    await _send_long_text(update, result["content"])
    return True


# ---------------- صدا به متن ----------------

async def gotham_ai_intercept_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.effective_message
    if not msg or not (msg.voice or msg.audio):
        return False
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if not await store.is_session_active(chat_id, user_id):
        return False

    if not config.is_configured():
        await msg.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return True

    voice = msg.voice or msg.audio
    if voice.file_size and voice.file_size > 20 * 1024 * 1024:
        await msg.reply_text("🎤 فایل صوتی خیلی بزرگه.")
        return True

    try:
        tg_file = await voice.get_file()
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        audio_bytes = buf.getvalue()
    except Exception as e:
        log.warning(f"gotham_ai voice download failed: {e}")
        await msg.reply_text("🦇 دانلود فایل صوتی شکست خورد.")
        return True

    thinking_msg = None
    try:
        thinking_msg = await msg.reply_text("🎤 در حال تبدیل صدا به متن...")
    except Exception:
        pass

    text, err = await client.audio_transcription(audio_bytes, "voice.ogg")

    if thinking_msg:
        try:
            await thinking_msg.delete()
        except Exception:
            pass

    if err:
        await msg.reply_text(err)
        return True

    await _send_long_text(update, text or "(چیزی تشخیص داده نشد)", header="🎤 *متن استخراج‌شده:*")
    return True


# ---------------- ساخت تصویر/ویدیو/صدا (اکشن یک‌باره) ----------------

async def _handle_image_generate(update: Update, prompt: str):
    if not config.is_configured():
        await update.effective_message.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return
    thinking_msg = await update.effective_message.reply_text("🖼️ در حال ساخت تصویر...")
    result, err = await client.image_generate(prompt)
    try:
        await thinking_msg.delete()
    except Exception:
        pass
    if err:
        await update.effective_message.reply_text(err)
        return
    try:
        if isinstance(result, str) and result.startswith("http"):
            await update.effective_message.reply_photo(result, caption=f"🖼️ {prompt[:900]}")
        else:
            import base64
            img_bytes = base64.b64decode(result)
            await update.effective_message.reply_photo(io.BytesIO(img_bytes), caption=f"🖼️ {prompt[:900]}")
    except Exception as e:
        log.warning(f"gotham_ai image send failed: {e}")
        await update.effective_message.reply_text("🦇 تصویر ساخته شد ولی ارسالش به تلگرام شکست خورد.")


async def _handle_video_generate(update: Update, prompt: str):
    if not config.is_configured():
        await update.effective_message.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return
    thinking_msg = await update.effective_message.reply_text(
        "🎬 در حال ساخت ویدیو... (ممکنه چند دقیقه طول بکشه)"
    )
    result, err = await client.video_generate(prompt)
    try:
        await thinking_msg.delete()
    except Exception:
        pass
    if err:
        await update.effective_message.reply_text(err)
        return
    tmp_path = None
    try:
        if isinstance(result, str) and result.startswith("http"):
            await update.effective_message.reply_video(result, caption=f"🎬 {prompt[:900]}")
        else:
            import base64
            video_bytes = base64.b64decode(result)
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(video_bytes)
                tmp_path = f.name
            with open(tmp_path, "rb") as fh:
                await update.effective_message.reply_video(fh, caption=f"🎬 {prompt[:900]}")
    except Exception as e:
        log.warning(f"gotham_ai video send failed: {e}")
        await update.effective_message.reply_text("🦇 ویدیو ساخته شد ولی ارسالش به تلگرام شکست خورد.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def _handle_tts(update: Update, text: str):
    if not config.is_configured():
        await update.effective_message.reply_text(config.missing_config_message(), parse_mode="Markdown")
        return
    thinking_msg = await update.effective_message.reply_text("🔊 در حال ساخت صدا...")
    audio_bytes, err = await client.audio_speech(text)
    try:
        await thinking_msg.delete()
    except Exception:
        pass
    if err:
        await update.effective_message.reply_text(err)
        return
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        with open(tmp_path, "rb") as fh:
            await update.effective_message.reply_voice(fh)
    except Exception as e:
        log.warning(f"gotham_ai tts send failed: {e}")
        await update.effective_message.reply_text("🦇 صدا ساخته شد ولی ارسالش شکست خورد.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------- Callback اصلی (gai:...) ----------------

async def gai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("gai:") and data != "panel:gotham_ai":
        return
    await query.answer()

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if data in ("panel:gotham_ai", "gai:menu"):
        if not config.is_configured():
            await query.edit_message_text(
                config.missing_config_message(), reply_markup=_back_kb("panel:main"),
                parse_mode="Markdown",
            )
            return
        await query.edit_message_text(AI_MENU_TEXT, reply_markup=build_ai_menu_keyboard(),
                                       parse_mode="Markdown")
        return

    if data == "gai:chat:start":
        if not config.is_configured():
            await query.edit_message_text(config.missing_config_message(),
                                           reply_markup=_back_kb(), parse_mode="Markdown")
            return
        session = await store.get_session(chat_id, user_id)
        await store.start_session(chat_id, user_id, model=session.get("model") or "auto")
        await query.edit_message_text(
            "🤖 *AI Chat فعال شد!*\n\nهمینجا (یا تو پیوی ربات) هرچی بنویسی جواب می‌گیری، "
            "context مکالمه هم حفظ می‌شه. برای تموم‌کردن بنویس «پایان چت» یا از منوی "
            "🧠 مدیریت حافظه استفاده کن.",
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:chat:end":
        await store.end_session(chat_id, user_id)
        await query.edit_message_text("⏹ چت هوش مصنوعی تموم شد.", reply_markup=_back_kb())
        return

    if data == "gai:mem":
        await query.edit_message_text(
            "🧠 *مدیریت حافظه‌ی AI*\n\nهر کاربر context جدای خودشو داره.",
            reply_markup=_mem_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:mem:new":
        await store.start_session(chat_id, user_id)
        await query.edit_message_text("🆕 یه چت کاملاً تازه شروع شد.", reply_markup=_mem_kb())
        return

    if data == "gai:mem:clear":
        await store.clear_context(chat_id, user_id)
        await query.edit_message_text("🧹 context مکالمه پاک شد (session فعال می‌مونه).",
                                       reply_markup=_mem_kb())
        return

    if data == "gai:mem:reset":
        await store.end_session(chat_id, user_id)
        await store.clear_context(chat_id, user_id)
        await query.edit_message_text("♻️ AI کاملاً ریست شد.", reply_markup=_mem_kb())
        return

    if data == "gai:settings":
        await query.edit_message_text(
            "⚙️ *تنظیمات AI*\n\nمدل/نقشی که می‌خوای پیش‌فرض چت باشه رو انتخاب کن:",
            reply_markup=_settings_kb(), parse_mode="Markdown",
        )
        return

    if data.startswith("gai:model:"):
        role = data.split(":", 2)[2]
        await store.set_model(chat_id, user_id, role)
        label = config.MODEL_LABELS.get(role, role)
        await query.edit_message_text(f"✅ مدل پیش‌فرض چت روی «{label}» تنظیم شد.",
                                       reply_markup=_settings_kb())
        return

    if data == "gai:vision:prompt":
        session_active = await store.is_session_active(chat_id, user_id)
        extra = "" if session_active else "\n\n(اول باید 🤖 AI Chat رو فعال کنی.)"
        await query.edit_message_text(
            "👁️ *تحلیل تصویر*\n\nیه عکس بفرست (با یا بدون کپشن). اگه کپشن ندی، "
            "خودم توضیحش می‌دم و متن داخلش رو هم می‌خونم." + extra,
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:file:prompt":
        session_active = await store.is_session_active(chat_id, user_id)
        extra = "" if session_active else "\n\n(اول باید 🤖 AI Chat رو فعال کنی.)"
        await query.edit_message_text(
            "📄 *تحلیل فایل*\n\nیه فایل متنی (TXT/MD/CSV/JSON/LOG) بفرست. تو کپشن "
            "بنویس چیکار کنم (مثلاً «ترجمه کن» یا «نکات مهم رو بگو»)." + extra,
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:image:prompt":
        _set_pending(chat_id, user_id, "image")
        await query.edit_message_text(
            "🖼️ *ساخت تصویر*\n\nپیام بعدیت رو به‌عنوان prompt تصویر در نظر می‌گیرم. "
            "همین‌جا بنویس چی می‌خوای بسازم.",
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:video:prompt":
        _set_pending(chat_id, user_id, "video")
        await query.edit_message_text(
            "🎬 *ساخت ویدیو*\n\nپیام بعدیت رو به‌عنوان prompt ویدیو در نظر می‌گیرم. "
            "اگه provider ویدیوی فعالی روی instance فعلی نباشه، بهت اطلاع می‌دم.",
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:tts:prompt":
        _set_pending(chat_id, user_id, "tts")
        await query.edit_message_text(
            "🔊 *متن به صدا*\n\nمتنی که می‌خوای تبدیل به صدا بشه رو بفرست.",
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:stt:info":
        await query.edit_message_text(
            "🎤 *صدا به متن*\n\nاگه 🤖 AI Chat فعال باشه، کافیه یه ویس/فایل صوتی بفرستی؛ "
            "خودم متنشو برات می‌فرستم.",
            reply_markup=_back_kb(), parse_mode="Markdown",
        )
        return

    if data == "gai:models":
        await query.edit_message_text("🤖 در حال گرفتن لیست مدل‌ها...", reply_markup=_back_kb())
        if not config.is_configured():
            await query.edit_message_text(config.missing_config_message(),
                                           reply_markup=_back_kb(), parse_mode="Markdown")
            return
        models, err = await client.list_models()
        if err:
            await query.edit_message_text(err, reply_markup=_back_kb())
            return
        if not models:
            await query.edit_message_text("🤖 هیچ مدلی از instance فعلی برنگشت.",
                                           reply_markup=_back_kb())
            return
        lines = ["🤖 *مدل‌های موجود* (از /v1/models):\n"]
        for m in models[:40]:
            mid = m.get("id", "?")
            owner = m.get("owned_by", "")
            lines.append(f"• `{mid}`" + (f" — {owner}" if owner else ""))
        if len(models) > 40:
            lines.append(f"\n… و {len(models) - 40} مدل دیگه.")
        await _edit_or_send(query, "\n".join(lines), _back_kb())
        return

    if data == "gai:status":
        stats = await store.get_stats()
        active_sessions = await store.count_active_sessions()
        total = stats.get("total_requests", 0)
        success = stats.get("success", 0)
        errors = stats.get("errors", 0)
        fallbacks = stats.get("fallbacks", 0)
        lat_sum = stats.get("latency_sum", 0)
        lat_count = stats.get("latency_count", 0)
        avg_latency = f"{lat_sum / lat_count:.0f} ms" if lat_count else "—"
        configured = "✅ متصل" if config.is_configured() else "❌ تنظیم نشده"
        text = (
            "📊 *وضعیت هوش مصنوعی*\n\n"
            f"وضعیت API: {configured}\n"
            f"Base URL: `{'تنظیم شده' if config.FREELLMAPI_BASE_URL else '—'}`\n"
            f"مدل پیش‌فرض: `{config.FREELLMAPI_DEFAULT_MODEL}`\n"
            f"Session‌های فعال: {active_sessions}\n"
            f"تعداد درخواست‌ها: {total}\n"
            f"موفق: {success} | خطا: {errors} | فیل‌اوور: {fallbacks}\n"
            f"میانگین latency: {avg_latency}\n"
        )
        await query.edit_message_text(text, reply_markup=_back_kb(), parse_mode="Markdown")
        return


async def _edit_or_send(query, text, kb):
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        try:
            await query.edit_message_text(text.replace("*", "").replace("`", ""), reply_markup=kb)
        except Exception:
            await query.message.reply_text(text.replace("*", "").replace("`", ""), reply_markup=kb)


# ---------------- ثبت هندلرها ----------------

def register_gotham_ai(app, deps=None):
    deps = deps or {}
    store.init(deps.get("db_path"))

    app.add_handler(CallbackQueryHandler(gai_callback, pattern=r"^(gai:|panel:gotham_ai$)"), group=24)

    # صدا/فایل: bot.py گروه ۰ برای این نوع محتوا هندلر عمومی نداره، پس مستقل
    # ثبت می‌کنیم (فقط وقتی session فعاله واکنش نشون می‌دیم، وگرنه بی‌صدا رد می‌شه)
    app.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO, gotham_ai_intercept_voice), group=24
    )
    app.add_handler(
        MessageHandler(filters.Document.ALL, gotham_ai_intercept_document), group=24
    )

    log.info("✨ گاتهام AI (FreeLLMAPI) ثبت شد.")
