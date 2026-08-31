# -*- coding: utf-8 -*-
"""
voice_to_text.py
================
🎙 تبدیل صدا به متن — روی یه پیام صوتی/ویس ریپلای کن و بنویس «متن کن»، «رونویسی»
یا «تبدیل به متن».

از سرویس رایگان تشخیص گفتار گوگل (از طریق کتابخانه‌ی SpeechRecognition) استفاده
می‌کنه، بدون نیاز به کلید API. اول فارسی رو امتحان می‌کنه، اگه جواب قانع‌کننده
نگرفت انگلیسی رو هم امتحان می‌کنه.

پیش‌نیاز: ffmpeg باید رو هاست نصب باشه (برای تبدیل ogg ویس تلگرام به wav) —
دقیقاً همون محدودیتی که compress_tools.py هم داره؛ اگه نصب نباشه به‌جای کرش
کردن پیام روشنی می‌ده.
"""

import os
import shutil
import logging
import tempfile
import subprocess

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

TRIGGER_RE = filters.Regex(r"(?i)^\s*(متن کن|رونویسی|تبدیل به متن|رونویسی کن)\s*$") & filters.REPLY

try:
    import speech_recognition as sr
    HAS_SR = True
except ImportError:
    HAS_SR = False


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _ogg_to_wav(in_path: str, out_path: str):
    subprocess.run(
        ["ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1", out_path],
        check=True, capture_output=True, timeout=120,
    )


def _transcribe(wav_path: str):
    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
        audio = recognizer.record(source)
    # اول فارسی، اگه نشد انگلیسی
    for lang in ("fa-IR", "en-US"):
        try:
            return recognizer.recognize_google(audio, language=lang), lang
        except sr.UnknownValueError:
            continue
        except sr.RequestError as e:
            raise
    return None, None


async def voice_to_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.reply_to_message
    if target is None:
        return
    voice = target.voice or target.audio
    if voice is None:
        await update.message.reply_text("⚠️ روی یه پیام صوتی/ویس ریپلای کن.")
        return

    if not _ffmpeg_available():
        await update.message.reply_text(
            "⚠️ تبدیل صدا به متن نیاز به ffmpeg داره که رو این هاست نصب نیست."
        )
        return
    if not HAS_SR:
        await update.message.reply_text(
            "⚠️ کتابخانه‌ی SpeechRecognition نصب نیست (تو requirements اضافه شده، فقط دیپلوی دوباره لازمه)."
        )
        return

    status = await update.message.reply_text("⏳ در حال رونویسی...")
    tg_file = await voice.get_file()

    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "voice.ogg")
        wav_path = os.path.join(tmpdir, "voice.wav")
        await tg_file.download_to_drive(in_path)
        try:
            _ogg_to_wav(in_path, wav_path)
        except Exception as e:
            log.warning(f"ogg->wav failed: {e}")
            await status.edit_text("❌ تبدیل فرمت صدا شکست خورد.")
            return

        try:
            import asyncio
            text, lang = await asyncio.to_thread(_transcribe, wav_path)
        except Exception as e:
            log.warning(f"transcribe failed: {e}")
            await status.edit_text("⚠️ سرویس رونویسی الان جواب نداد، یه‌کم بعد دوباره امتحان کن.")
            return

        if not text:
            await status.edit_text("🤷 نتونستم چیزی از این ویس تشخیص بدم (شاید کیفیت صدا پایینه).")
            return

        lang_label = "فارسی" if lang == "fa-IR" else "انگلیسی"
        await status.edit_text(f"🎙 رونویسی ({lang_label}):\n{text}")


def register_voice_to_text(app):
    app.add_handler(MessageHandler(TRIGGER_RE, voice_to_text_handler), group=23)
