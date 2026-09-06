# -*- coding: utf-8 -*-
"""
elevenlabs_service.py
================
🎙️ استودیو صدا — همه‌ی قابلیت‌های ElevenLabs زیر یه منو:

    🔊 متن به گفتار (Text to Speech)
    🎙️ گفتار به متن (Speech to Text)
    🗣️ تغییر صدا (Speech to Speech / Voice Changer)
    💥 افکت صوتی (Sound Effects)
    🧹 حذف نویز پس‌زمینه (Audio Isolation)

Environment Variable لازم: ELEVENLABS_API_KEY (فقط از Environment خونده
می‌شه، هیچ‌جا Hard-code نشده).

register_elevenlabs_service(app) — مستقل از بقیه‌ی ماژول‌ها.
"""

import os
import io
import time
import shutil
import logging
import tempfile

import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ApplicationHandlerStop,
)

log = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
XI_BASE = "https://api.elevenlabs.io/v1"

MAX_FILE_SIZE = 15 * 1024 * 1024  # ۱۵ مگابایت
MAX_TTS_CHARS = 2000
VOICES_TTL = 600  # ثانیه — کش لیست صداها

START_TRIGGER = filters.Regex(r"(?i)^\s*استودیو\s*صدا\s*$")
STUDIO_MEDIA_FILTER = filters.VOICE | filters.AUDIO

STUDIO_MENU_TEXT = (
    "🎙️ *استودیو صدای گاتهام* (ElevenLabs)\n\n"
    "🔊 متن به گفتار — از رو متن، صدا می‌سازم\n"
    "🎙️ گفتار به متن — از رو صدا، متنش رو در میارم\n"
    "🗣️ تغییر صدا — صدای فایلت رو با یه صدای دیگه عوض می‌کنم\n"
    "💥 افکت صوتی — توضیح بده، افکت می‌سازم\n"
    "🧹 حذف نویز پس‌زمینه — نویز رو از صدات پاک می‌کنم"
)

_voices_cache = {"data": None, "ts": 0}


# ------------------------------------------------------------------
# ⌨️ کیبوردها
# ------------------------------------------------------------------

def build_studio_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔊 متن به گفتار", callback_data="voice:tts:start")],
            [InlineKeyboardButton("🎙️ گفتار به متن", callback_data="voice:stt:start")],
            [InlineKeyboardButton("🗣️ تغییر صدا", callback_data="voice:s2s:start")],
            [InlineKeyboardButton("💥 افکت صوتی", callback_data="voice:sfx:start")],
            [InlineKeyboardButton("🧹 حذف نویز پس‌زمینه", callback_data="voice:isolate:start")],
        ]
    )


def _build_cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="voice:cancel")]])


def _build_voice_pick_keyboard(voices):
    rows = []
    for v in voices[:10]:
        gender = (v.get("labels") or {}).get("gender", "")
        emoji = "👨" if gender == "male" else ("👩" if gender == "female" else "🎙️")
        rows.append(
            [InlineKeyboardButton(f"{emoji} {v.get('name', '?')}", callback_data=f"voice:pick:{v['voice_id']}")]
        )
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="voice:cancel")])
    return InlineKeyboardMarkup(rows)


# ------------------------------------------------------------------
# 🌐 کلاینت ElevenLabs
# ------------------------------------------------------------------

def _friendly_api_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 401:
            return "⚠️ کلید ElevenLabs معتبر نیست."
        if code == 429:
            return "⚠️ سقف استفاده از ElevenLabs الان پره (Rate Limit)، یه‌کم بعد امتحان کن."
        if code == 413:
            return "⚠️ فایل خیلی بزرگه."
        if code >= 500:
            return "⚠️ سرویس ElevenLabs الان در دسترس نیست، بعداً امتحان کن."
        return "⚠️ درخواست رد شد (فرمت یا محتوای ورودی مشکل داشت)."
    if isinstance(e, httpx.TimeoutException):
        return "⚠️ سرویس دیر جواب داد، دوباره امتحان کن."
    return "⚠️ یه خطای غیرمنتظره پیش اومد، دوباره امتحان کن."


async def _xi_get_voices():
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{XI_BASE}/voices", headers={"xi-api-key": ELEVENLABS_API_KEY})
        r.raise_for_status()
        return r.json().get("voices", [])


async def _get_voices_cached():
    now = time.time()
    if _voices_cache["data"] and now - _voices_cache["ts"] < VOICES_TTL:
        return _voices_cache["data"]
    voices = await _xi_get_voices()
    _voices_cache["data"] = voices
    _voices_cache["ts"] = now
    return voices


async def _xi_tts(voice_id: str, text: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{XI_BASE}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
        )
        r.raise_for_status()
        return r.content


async def _xi_stt(file_path: str) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, "application/octet-stream")}
            data = {"model_id": "scribe_v1"}
            r = await client.post(
                f"{XI_BASE}/speech-to-text",
                headers={"xi-api-key": ELEVENLABS_API_KEY},
                files=files,
                data=data,
            )
        r.raise_for_status()
        return (r.json() or {}).get("text", "").strip()


async def _xi_speech_to_speech(voice_id: str, file_path: str) -> bytes:
    async with httpx.AsyncClient(timeout=90) as client:
        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f, "application/octet-stream")}
            data = {"model_id": "eleven_multilingual_sts_v2"}
            r = await client.post(
                f"{XI_BASE}/speech-to-speech/{voice_id}",
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Accept": "audio/mpeg"},
                files=files,
                data=data,
            )
        r.raise_for_status()
        return r.content


async def _xi_sound_effect(prompt: str, duration: float = 5.0) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{XI_BASE}/sound-generation",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={"text": prompt, "duration_seconds": duration},
        )
        r.raise_for_status()
        return r.content


async def _xi_audio_isolation(file_path: str) -> bytes:
    async with httpx.AsyncClient(timeout=90) as client:
        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f, "application/octet-stream")}
            r = await client.post(
                f"{XI_BASE}/audio-isolation",
                headers={"xi-api-key": ELEVENLABS_API_KEY, "Accept": "audio/mpeg"},
                files=files,
            )
        r.raise_for_status()
        return r.content


# ------------------------------------------------------------------
# 🆕 هندلرها
# ------------------------------------------------------------------

async def studio_start_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        STUDIO_MENU_TEXT, reply_markup=build_studio_menu_keyboard(), parse_mode="Markdown"
    )


async def studio_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    if data == "voice:menu":
        context.user_data.pop("voice_studio", None)
        await query.edit_message_text(
            STUDIO_MENU_TEXT, reply_markup=build_studio_menu_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "voice:cancel":
        context.user_data.pop("voice_studio", None)
        await query.edit_message_text(
            STUDIO_MENU_TEXT, reply_markup=build_studio_menu_keyboard(), parse_mode="Markdown"
        )
        return

    if not ELEVENLABS_API_KEY:
        await query.edit_message_text(
            "⚠️ کلید ELEVENLABS_API_KEY تنظیم نشده.", reply_markup=build_studio_menu_keyboard()
        )
        return

    if data in ("voice:tts:start", "voice:s2s:start"):
        mode = "tts" if data == "voice:tts:start" else "s2s"
        await query.edit_message_text("⏳ در حال گرفتن لیست صداها...")
        try:
            voices = await _get_voices_cached()
        except Exception as e:
            log.error(f"voice studio get_voices failed: {e}")
            await query.edit_message_text(
                _friendly_api_error(e), reply_markup=build_studio_menu_keyboard()
            )
            return
        if not voices:
            await query.edit_message_text(
                "⚠️ هیچ صدایی رو حساب ElevenLabs پیدا نشد.", reply_markup=build_studio_menu_keyboard()
            )
            return
        context.user_data["voice_studio"] = {"mode": mode, "step": "voice"}
        prompt = "🔊 یه صدا انتخاب کن:" if mode == "tts" else "🗣️ صدای مقصد رو انتخاب کن:"
        await query.edit_message_text(prompt, reply_markup=_build_voice_pick_keyboard(voices))
        return

    if data == "voice:stt:start":
        context.user_data["voice_studio"] = {"mode": "stt", "step": "file"}
        await query.edit_message_text(
            "🎙 یه پیام صوتی یا فایل صوتی بفرست تا متنش رو در بیارم.",
            reply_markup=_build_cancel_kb(),
        )
        return

    if data == "voice:sfx:start":
        context.user_data["voice_studio"] = {"mode": "sfx", "step": "text"}
        await query.edit_message_text(
            "💥 توضیح بده چه افکتی می‌خوای (مثلاً: صدای رعد و برق، شکستن شیشه، پارس سگ):",
            reply_markup=_build_cancel_kb(),
        )
        return

    if data == "voice:isolate:start":
        context.user_data["voice_studio"] = {"mode": "isolate", "step": "file"}
        await query.edit_message_text(
            "🧹 فایل صوتی/ویسی که می‌خوای نویز پس‌زمینه‌اش پاک بشه رو بفرست.",
            reply_markup=_build_cancel_kb(),
        )
        return

    if data.startswith("voice:pick:"):
        flow = context.user_data.get("voice_studio")
        if not flow or flow.get("step") != "voice":
            await query.answer("⚠️ اول از منوی استودیو صدا شروع کن.", show_alert=True)
            return
        voice_id = data.split(":", 2)[2]
        flow["voice_id"] = voice_id
        if flow["mode"] == "tts":
            flow["step"] = "text"
            context.user_data["voice_studio"] = flow
            await query.edit_message_text(
                f"✏️ متنی که می‌خوای به صدا تبدیل بشه رو بفرست (حداکثر {MAX_TTS_CHARS} حرف):",
                reply_markup=_build_cancel_kb(),
            )
        else:  # s2s
            flow["step"] = "file"
            context.user_data["voice_studio"] = flow
            await query.edit_message_text(
                "🎙 حالا فایل صوتی/ویسِ منبع رو بفرست:", reply_markup=_build_cancel_kb()
            )
        return


async def studio_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گرفتن ورودی متنیِ مراحل «متن به گفتار» و «افکت صوتی». اگه کاربر تو این
    فلو نباشه (یا منتظر فایل باشه، نه متن)، بدون کاری برمی‌گرده."""
    flow = context.user_data.get("voice_studio")
    if not flow or flow.get("step") != "text":
        return

    msg = update.effective_message
    text = (msg.text or "").strip()
    if not text:
        await msg.reply_text("✏️ یه متن بفرست.", reply_markup=_build_cancel_kb())
        raise ApplicationHandlerStop

    if not ELEVENLABS_API_KEY:
        await msg.reply_text("⚠️ کلید ELEVENLABS_API_KEY تنظیم نشده.")
        context.user_data.pop("voice_studio", None)
        raise ApplicationHandlerStop

    if len(text) > MAX_TTS_CHARS:
        text = text[:MAX_TTS_CHARS]

    mode = flow["mode"]
    status = await msg.reply_text("⏳ در حال پردازش... چند لحظه صبر کن 🦇")
    try:
        if mode == "tts":
            audio = await _xi_tts(flow["voice_id"], text)
            await msg.reply_voice(io.BytesIO(audio), caption="🔊 آماده‌ست!")
        elif mode == "sfx":
            audio = await _xi_sound_effect(text)
            await msg.reply_audio(io.BytesIO(audio), caption="💥 افکت آماده‌ست!")
        try:
            await status.delete()
        except Exception:
            pass
    except Exception as e:
        log.error(f"voice studio ({mode}) failed: {e}")
        try:
            await status.edit_text(_friendly_api_error(e))
        except Exception:
            await msg.reply_text(_friendly_api_error(e))
    finally:
        context.user_data.pop("voice_studio", None)
    raise ApplicationHandlerStop


async def studio_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گرفتن ورودی صوتیِ مراحل «گفتار به متن»، «تغییر صدا» و «حذف نویز».
    اگه کاربر تو این فلو نباشه، بدون هیچ کاری برمی‌گرده تا هندلرهای دیگه‌ی
    صدا (تشخیص آهنگ، تبدیل صدا به متن رایگان و ...) عادی کار کنن."""
    flow = context.user_data.get("voice_studio")
    if not flow or flow.get("step") != "file":
        return

    msg = update.effective_message
    media = msg.voice or msg.audio
    if media is None:
        return

    if media.file_size and media.file_size > MAX_FILE_SIZE:
        await msg.reply_text("⚠️ فایل خیلی بزرگه (حداکثر ۱۵ مگابایت).", reply_markup=_build_cancel_kb())
        raise ApplicationHandlerStop

    if not ELEVENLABS_API_KEY:
        await msg.reply_text("⚠️ کلید ELEVENLABS_API_KEY تنظیم نشده.")
        context.user_data.pop("voice_studio", None)
        raise ApplicationHandlerStop

    mode = flow["mode"]
    status = await msg.reply_text("⏳ در حال پردازش... ممکنه چند لحظه طول بکشه 🦇")

    tmpdir = tempfile.mkdtemp(prefix="voicestudio_")
    try:
        tg_file = await context.bot.get_file(media.file_id)
        in_path = os.path.join(tmpdir, "input.ogg")
        await tg_file.download_to_drive(in_path)

        if mode == "stt":
            text = await _xi_stt(in_path)
            if not text:
                await status.edit_text("❌ چیزی از این فایل تشخیص داده نشد.")
            else:
                await status.delete()
                await msg.reply_text(f"📝 متن تشخیص داده‌شده:\n\n{text}")
        elif mode == "s2s":
            audio = await _xi_speech_to_speech(flow["voice_id"], in_path)
            await status.delete()
            await msg.reply_voice(io.BytesIO(audio), caption="🗣️ صدای جدید آماده‌ست!")
        elif mode == "isolate":
            audio = await _xi_audio_isolation(in_path)
            await status.delete()
            await msg.reply_audio(io.BytesIO(audio), caption="🧹 نویز پس‌زمینه پاک شد!")
    except Exception as e:
        log.error(f"voice studio media ({mode}) failed: {e}")
        try:
            await status.edit_text(_friendly_api_error(e))
        except Exception:
            await msg.reply_text(_friendly_api_error(e))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        context.user_data.pop("voice_studio", None)
    raise ApplicationHandlerStop


def register_elevenlabs_service(app):
    app.add_handler(MessageHandler(START_TRIGGER, studio_start_text_handler), group=20)
    app.add_handler(CallbackQueryHandler(studio_button_callback, pattern=r"^voice:"), group=28)
    # کچرِ فایل صوتی — تو یه گروه زودهنگام (۱۵) با ApplicationHandlerStop، تا
    # وقتی فلو فعاله با تشخیص‌آهنگ/سایر قابلیت‌های صوتی تداخل نکنه؛ وقتی فلو
    # فعال نیست، بدون کاری رد می‌شه و همه‌چیز عادی کار می‌کنه.
    app.add_handler(MessageHandler(STUDIO_MEDIA_FILTER, studio_media_handler), group=15)
    # کچرِ ورودی متنی — تو گروه ۳۱ (بعد از فلوی یادآوری تو گروه ۳۰)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, studio_text_handler), group=31)
