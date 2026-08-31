# -*- coding: utf-8 -*-
"""
compress_tools.py
================
تبدیل و فشرده‌سازی فایل.

طرز کار: روی یه عکس/ویدیو/فایل صوتی ریپلای کن و بنویس «فشرده» یا «فشرده کن»
تا ربات یه نسخه‌ی سبک‌تر ازش بسازه و بفرسته.

- عکس: با Pillow تغییر اندازه (حداکثر ۱۲۸۰px) و کیفیت JPEG ۷۰٪.
- ویدیو: با ffmpeg (اگه رو سرور نصب باشه) کیفیت crf=28 و صدای ۹۶kbps.
- صدا: با ffmpeg به mp3 با بیت‌ریت ۶۴kbps.

اگه ffmpeg رو سرور نصب نباشه، ربات به‌جای کرش کردن پیام واضح می‌ده که این
قابلیت نیاز به نصب ffmpeg داره (یه محدودیت هاست، نه باگ کد).
"""

import os
import shutil
import logging
import tempfile
import subprocess

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

TRIGGER_RE = filters.Regex(r"(?i)^\s*(فشرده|فشرده کن|فشرده‌سازی|compress)\s*$") & filters.REPLY

try:
    from PIL import Image
except ImportError:
    Image = None


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _compress_image(in_path: str, out_path: str):
    with Image.open(in_path) as im:
        im = im.convert("RGB")
        im.thumbnail((1280, 1280))
        im.save(out_path, "JPEG", quality=70, optimize=True)


def _compress_video(in_path: str, out_path: str):
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", in_path,
            "-vcodec", "libx264", "-crf", "28", "-preset", "fast",
            "-acodec", "aac", "-b:a", "96k",
            out_path,
        ],
        check=True, capture_output=True, timeout=300,
    )


def _compress_audio(in_path: str, out_path: str):
    subprocess.run(
        ["ffmpeg", "-y", "-i", in_path, "-b:a", "64k", out_path],
        check=True, capture_output=True, timeout=300,
    )


async def compress_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.reply_to_message
    if target is None:
        return
    status = await update.message.reply_text("⏳ در حال فشرده‌سازی...")

    tg_file = None
    kind = None
    if target.photo:
        tg_file = await target.photo[-1].get_file()
        kind = "photo"
    elif target.video:
        tg_file = await target.video.get_file()
        kind = "video"
    elif target.audio:
        tg_file = await target.audio.get_file()
        kind = "audio"
    elif target.voice:
        tg_file = await target.voice.get_file()
        kind = "audio"
    elif target.document:
        mime = (target.document.mime_type or "")
        if mime.startswith("image/"):
            kind = "photo"
        elif mime.startswith("video/"):
            kind = "video"
        elif mime.startswith("audio/"):
            kind = "audio"
        if kind:
            tg_file = await target.document.get_file()

    if not tg_file or not kind:
        await status.edit_text("⚠️ این پیام عکس/ویدیو/صوت نیست؛ روی یکی از این‌ها ریپلای کن.")
        return

    if kind in ("video", "audio") and not _ffmpeg_available():
        await status.edit_text(
            "⚠️ فشرده‌سازی ویدیو/صدا نیاز به ffmpeg داره که رو این هاست نصب نیست. "
            "برای عکس‌ها مشکلی نیست، همین الان امتحان کن."
        )
        return

    if kind == "photo" and Image is None:
        await status.edit_text("⚠️ کتابخانه‌ی Pillow نصب نیست (تو requirements اضافه شده، فقط دیپلوی دوباره لازمه).")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "input")
        await tg_file.download_to_drive(in_path)
        try:
            if kind == "photo":
                out_path = os.path.join(tmpdir, "out.jpg")
                _compress_image(in_path, out_path)
            elif kind == "video":
                out_path = os.path.join(tmpdir, "out.mp4")
                _compress_video(in_path, out_path)
            else:
                out_path = os.path.join(tmpdir, "out.mp3")
                _compress_audio(in_path, out_path)
        except Exception as e:
            log.warning(f"compress failed: {e}")
            await status.edit_text("❌ فشرده‌سازی شکست خورد. فایل شاید خیلی بزرگ یا خراب باشه.")
            return

        before = os.path.getsize(in_path)
        after = os.path.getsize(out_path)
        caption = f"📦 حجم قبل: {before/1024/1024:.1f}MB → بعد: {after/1024/1024:.1f}MB"

        try:
            with open(out_path, "rb") as f:
                if kind == "photo":
                    await update.message.reply_photo(f, caption=caption)
                elif kind == "video":
                    await update.message.reply_video(f, caption=caption, supports_streaming=True)
                else:
                    await update.message.reply_audio(f, caption=caption)
        except Exception as e:
            log.warning(f"send compressed failed: {e}")
            await status.edit_text(f"❌ فایل فشرده شد ولی ارسالش شکست خورد: {str(e)[:150]}")
            return

    try:
        await status.delete()
    except Exception:
        pass


def register_compress(app):
    app.add_handler(MessageHandler(TRIGGER_RE, compress_handler), group=8)
