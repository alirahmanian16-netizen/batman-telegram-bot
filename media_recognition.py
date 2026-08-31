# -*- coding: utf-8 -*-
"""
media_recognition.py
================
سه امکان جدید برای بخش «🧩 امکانات جدید»:

    🎬 تشخیص فیلم/سریال — ریپلای رو عکس یا ویدیوی یه صحنه + «تشخیص فیلم».
       از یه مدل هوش مصنوعیِ چندوجهی (رایگان، از طریق OpenRouter) برای حدس زدن
       اسم اثر استفاده می‌کنه، بعد با TMDB چک می‌کنه تا سال/پوستر/خلاصه‌ی رسمی
       رو هم برگردونه.

    🎵 تشخیص آهنگ — ریپلای رو ویس/صدا/ویدیو + «تشخیص آهنگ».
       صدا رو (با ffmpeg اگه ویدیو باشه) استخراج می‌کنه و به AudD می‌فرسته.

    📝 خلاصه‌ی گروه — «خلاصه گروه» — آخرین پیام‌های متنیِ گروه (حداکثر ۵۰ تا،
       تو حافظه نگه داشته می‌شن، نه دیتابیس) رو خلاصه می‌کنه.

نیازمند سه Environment Variable رو Railway:
    OPENROUTER_API_KEY   (از قبل تو bot.py هست، دوباره همینجا هم خونده می‌شه)
    TMDB_API_KEY
    AUDD_API_TOKEN

اگه هرکدوم ست نباشن، همون بخش با یه پیام روشن غیرفعال می‌مونه (کرش نمی‌کنه).
"""

import os
from typing import Optional
import shutil
import logging
import tempfile
import subprocess
from collections import defaultdict, deque

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
AUDD_API_TOKEN = os.getenv("AUDD_API_TOKEN")

MOVIE_RE = filters.Regex(r"(?i)^\s*(تشخیص فیلم|این چه فیلمیه|چه فیلمیه|اسم فیلم چیه|فیلم\?|فیلم؟)\s*$") & filters.REPLY
SONG_RE = filters.Regex(r"(?i)^\s*(تشخیص آهنگ|این چه آهنگیه|اسم آهنگ چیه|آهنگه چیه|آهنگ\?|آهنگ؟)\s*$") & filters.REPLY
SUMMARY_RE = filters.Regex(r"(?i)^\s*(خلاصه گروه|خلاصه چت|خلاصه بحث)\s*$")

# چت خصوصی: وقتی عکس/ویدیو/صدا مستقیم (بدون ریپلای) برای ربات فرستاده بشه،
# فوراً دکمه‌ی تشخیص زیرش میاد — دیگه نیازی به تایپ کلمه نیست.
PRIVATE_MEDIA_FILTER = (
    filters.ChatType.PRIVATE
    & (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE)
    & ~filters.REPLY
)

# --- بافر حافظه‌ای برای خلاصه‌سازی گروه (بدون دیتابیس، فقط همین سشن) ---
_CHAT_LOG = defaultdict(lambda: deque(maxlen=60))


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# ---------------- تشخیص فیلم/سریال ----------------

async def _vision_guess(image_bytes: bytes) -> str:
    """از یه مدل رایگانِ چندوجهیِ OpenRouter می‌خواد حدس بزنه این صحنه از چه
    فیلم/سریالیه؛ فقط اسم رو برمی‌گردونه."""
    import base64
    b64 = base64.b64encode(image_bytes).decode()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "qwen/qwen2.5-vl-32b-instruct:free",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (
                            "این عکس از یه صحنه‌ی فیلم یا سریاله. فقط و فقط اسم دقیق "
                            "فیلم/سریال رو به انگلیسی بنویس (اگه مطمئن نیستی، محتمل‌ترین "
                            "گزینه رو بنویس). هیچ توضیح اضافه‌ای نده، فقط اسم اثر."
                        )},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }],
                "max_tokens": 40,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def _tmdb_lookup(title_guess: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        for endpoint in ("movie", "tv"):
            resp = await client.get(
                f"https://api.themoviedb.org/3/search/{endpoint}",
                params={"api_key": TMDB_API_KEY, "query": title_guess, "language": "fa-IR"},
            )
            if resp.status_code != 200:
                continue
            results = resp.json().get("results") or []
            if results:
                r = results[0]
                return {
                    "kind": "فیلم" if endpoint == "movie" else "سریال",
                    "title": r.get("title") or r.get("name"),
                    "year": (r.get("release_date") or r.get("first_air_date") or "----")[:4],
                    "overview": r.get("overview") or "",
                    "poster": (
                        f"https://image.tmdb.org/t/p/w500{r['poster_path']}"
                        if r.get("poster_path") else None
                    ),
                }
    return None


async def movie_recognize_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    replied = msg.reply_to_message
    photo_file = None
    if replied.photo:
        photo_file = await replied.photo[-1].get_file()
    elif replied.video:
        if not TMDB_API_KEY and not OPENROUTER_API_KEY:
            pass
        await msg.reply_text("🎬 دارم از ویدیو یه فریم می‌گیرم، صبر کن...")
        vid_file = await replied.video.get_file()
        with tempfile.TemporaryDirectory() as tmp:
            vid_path = os.path.join(tmp, "in.mp4")
            frame_path = os.path.join(tmp, "frame.jpg")
            await vid_file.download_to_drive(vid_path)
            if not _ffmpeg_available():
                await msg.reply_text("⚠️ ffmpeg رو سرور نصب نیست، نمی‌تونم از ویدیو فریم بگیرم.")
                return
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", vid_path, "-ss", "00:00:01", "-vframes", "1", frame_path],
                    check=True, capture_output=True, timeout=60,
                )
                with open(frame_path, "rb") as f:
                    image_bytes = f.read()
            except Exception as e:
                log.error(f"frame extract failed: {e}")
                await msg.reply_text("⚠️ نتونستم از ویدیو فریم بگیرم.")
                return
        await _run_movie_recognition(msg, image_bytes)
        return
    else:
        await msg.reply_text("🎬 رو یه عکس یا ویدیوی صحنه‌ی فیلم ریپلای کن و «تشخیص فیلم» بنویس.")
        return

    if photo_file:
        import io
        buf = io.BytesIO()
        await photo_file.download_to_memory(buf)
        await _run_movie_recognition(msg, buf.getvalue())


async def _run_movie_recognition(msg, image_bytes: bytes):
    if not OPENROUTER_API_KEY:
        await msg.reply_text("🎬 کلید OPENROUTER_API_KEY تنظیم نشده.")
        return
    await msg.reply_text("🎬 دارم صحنه رو تحلیل می‌کنم...")
    try:
        guess = await _vision_guess(image_bytes)
    except Exception as e:
        log.error(f"vision guess failed: {e}")
        await msg.reply_text("⚠️ نتونستم صحنه رو تحلیل کنم، بعداً دوباره امتحان کن.")
        return

    if not TMDB_API_KEY:
        await msg.reply_text(f"🎬 حدس من: *{guess}*\n(برای تایید رسمی با TMDB، کلید TMDB_API_KEY ست نشده)", parse_mode="Markdown")
        return

    try:
        info = await _tmdb_lookup(guess)
    except Exception as e:
        log.error(f"tmdb lookup failed: {e}")
        info = None

    if not info:
        await msg.reply_text(f"🎬 حدسم اینه: *{guess}* — ولی تو TMDB پیدا نشد، شاید عنوان دقیق نباشه.", parse_mode="Markdown")
        return

    text = (
        f"🎬 *{info['title']}* ({info['year']}) — {info['kind']}\n\n"
        f"{info['overview'][:400] if info['overview'] else 'خلاصه‌ای موجود نیست.'}"
    )
    if info["poster"]:
        await msg.reply_photo(info["poster"], caption=text, parse_mode="Markdown")
    else:
        await msg.reply_text(text, parse_mode="Markdown")


# ---------------- تشخیص آهنگ ----------------

async def _audd_recognize(audio_path: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                "https://api.audd.io/",
                data={"api_token": AUDD_API_TOKEN, "return": "spotify,apple_music"},
                files={"file": f},
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result")


async def song_recognize_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    replied = msg.reply_to_message

    if not AUDD_API_TOKEN:
        await msg.reply_text("🎵 کلید AUDD_API_TOKEN تنظیم نشده.")
        return

    tg_file = None
    is_video = False
    if replied.voice:
        tg_file = await replied.voice.get_file()
    elif replied.audio:
        tg_file = await replied.audio.get_file()
    elif replied.video:
        tg_file = await replied.video.get_file()
        is_video = True
    elif replied.video_note:
        tg_file = await replied.video_note.get_file()
        is_video = True
    else:
        await msg.reply_text("🎵 رو یه ویس، فایل صوتی یا ویدیو ریپلای کن و «تشخیص آهنگ» بنویس.")
        return

    await msg.reply_text("🎵 دارم آهنگ رو تشخیص می‌دم...")

    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "src")
        await tg_file.download_to_drive(src_path)
        audio_path = src_path

        if is_video:
            if not _ffmpeg_available():
                await msg.reply_text("⚠️ ffmpeg رو سرور نصب نیست، نمی‌تونم صدا رو از ویدیو جدا کنم.")
                return
            audio_path = os.path.join(tmp, "audio.mp3")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", src_path, "-vn", "-ar", "44100", "-ac", "2", audio_path],
                    check=True, capture_output=True, timeout=120,
                )
            except Exception as e:
                log.error(f"audio extract failed: {e}")
                await msg.reply_text("⚠️ نتونستم صدا رو از ویدیو جدا کنم.")
                return

        try:
            result = await _audd_recognize(audio_path)
        except Exception as e:
            log.error(f"audd failed: {e}")
            await msg.reply_text("⚠️ سرویس تشخیص آهنگ الان جواب نداد، بعداً دوباره امتحان کن.")
            return

    if not result:
        await msg.reply_text("🎵 نتونستم این آهنگ رو تشخیص بدم.")
        return

    title = result.get("title", "نامشخص")
    artist = result.get("artist", "نامشخص")
    lines = [f"🎵 *{title}*", f"🎤 {artist}"]
    spotify = result.get("spotify", {}) or {}
    apple = result.get("apple_music", {}) or {}
    if spotify.get("external_urls", {}).get("spotify"):
        lines.append(f"[Spotify]({spotify['external_urls']['spotify']})")
    if apple.get("url"):
        lines.append(f"[Apple Music]({apple['url']})")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


# ---------------- خلاصه‌ی گروه ----------------

async def collect_message_for_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر منفعل — فقط پیام‌های متنیِ گروه رو تو حافظه نگه می‌داره، هیچ ریپلای‌ای نمی‌ده."""
    msg = update.effective_message
    if not msg or not msg.text or update.effective_chat.type not in ("group", "supergroup"):
        return
    name = update.effective_user.first_name if update.effective_user else "کاربر"
    _CHAT_LOG[update.effective_chat.id].append(f"{name}: {msg.text}")


async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OPENROUTER_API_KEY:
        await update.effective_message.reply_text("📝 کلید OPENROUTER_API_KEY تنظیم نشده.")
        return
    chat_id = update.effective_chat.id
    log_lines = list(_CHAT_LOG[chat_id])
    if len(log_lines) < 5:
        await update.effective_message.reply_text("📝 هنوز پیام کافی برای خلاصه کردن تو حافظه‌م نیست.")
        return

    convo = "\n".join(log_lines[-60:])
    await update.effective_message.reply_text("📝 دارم بحث اخیر رو می‌خونم...")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [
                        {"role": "system", "content": (
                            "تو دستیار خلاصه‌سازی گفتگوی گروه تلگرام هستی. متن زیر آخرین "
                            "پیام‌های یه گروهه. یه خلاصه‌ی کوتاه فارسی (حداکثر ۶-۷ خط) از "
                            "موضوعات اصلی بحث بنویس، بدون قضاوت یا نظر شخصی."
                        )},
                        {"role": "user", "content": convo},
                    ],
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            summary = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error(f"summary failed: {e}")
        await update.effective_message.reply_text("⚠️ نتونستم خلاصه بسازم، بعداً دوباره امتحان کن.")
        return

    await update.effective_message.reply_text(f"📝 *خلاصه‌ی بحث اخیر گروه:*\n\n{summary}", parse_mode="Markdown")


async def private_media_offer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تو چت خصوصی، رو هر عکس/ویدیو/صدایی که مستقیم فرستاده بشه، دکمه‌ی تشخیص
    زیرش میاد — کاربر لازم نیست هیچی تایپ کنه.

    اگه کاربر تو سشن «🎬 پست‌ساز گاتهام» فعاله، این پیشنهاد نباید هم‌زمان
    نمایش داده بشه (پست‌ساز خودش داره همین فایل رو تو یه هندلر جدا پردازش
    می‌کنه؛ اینجا فقط چک سبک/بدون‌عارضه، نه صدا زدن مجدد پردازش)."""
    from post_saz import is_postsaz_active
    if is_postsaz_active(context):
        return
    msg = update.effective_message
    rows = []
    if msg.photo or msg.video:
        rows.append([InlineKeyboardButton("🎬 تشخیص فیلم/سریال", callback_data="mr:movie")])
    if msg.voice or msg.audio or msg.video or msg.video_note:
        rows.append([InlineKeyboardButton("🎵 تشخیص آهنگ", callback_data="mr:song")])
    if not rows:
        return
    await msg.reply_text("چیکار کنم باهاش؟ 👇", reply_markup=InlineKeyboardMarkup(rows))


async def private_media_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    target_msg = query.message.reply_to_message
    if not target_msg:
        await query.edit_message_text("⚠️ پیام اصلی پیدا نشد، دوباره عکس/صدا رو بفرست.")
        return

    action = query.data.split(":", 1)[1]
    await query.edit_message_text("در حال پردازش... ⏳")

    if action == "movie":
        if target_msg.photo:
            import io
            photo_file = await target_msg.photo[-1].get_file()
            buf = io.BytesIO()
            await photo_file.download_to_memory(buf)
            await _run_movie_recognition(query.message, buf.getvalue())
        elif target_msg.video:
            await _run_movie_recognition_from_video(query.message, target_msg)
        else:
            await query.message.reply_text("⚠️ این پیام عکس یا ویدیو نیست.")
    elif action == "song":
        await _run_song_recognition(query.message, target_msg)


async def _run_movie_recognition_from_video(msg, video_msg):
    await msg.reply_text("🎬 دارم از ویدیو یه فریم می‌گیرم...")
    if not _ffmpeg_available():
        await msg.reply_text("⚠️ ffmpeg رو سرور نصب نیست، نمی‌تونم از ویدیو فریم بگیرم.")
        return
    vid_file = await video_msg.video.get_file()
    with tempfile.TemporaryDirectory() as tmp:
        vid_path = os.path.join(tmp, "in.mp4")
        frame_path = os.path.join(tmp, "frame.jpg")
        await vid_file.download_to_drive(vid_path)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", vid_path, "-ss", "00:00:01", "-vframes", "1", frame_path],
                check=True, capture_output=True, timeout=60,
            )
            with open(frame_path, "rb") as f:
                image_bytes = f.read()
        except Exception as e:
            log.error(f"frame extract failed: {e}")
            await msg.reply_text("⚠️ نتونستم از ویدیو فریم بگیرم.")
            return
    await _run_movie_recognition(msg, image_bytes)


async def _run_song_recognition(msg, media_msg):
    if not AUDD_API_TOKEN:
        await msg.reply_text("🎵 کلید AUDD_API_TOKEN تنظیم نشده.")
        return

    tg_file = None
    is_video = False
    if media_msg.voice:
        tg_file = await media_msg.voice.get_file()
    elif media_msg.audio:
        tg_file = await media_msg.audio.get_file()
    elif media_msg.video:
        tg_file = await media_msg.video.get_file()
        is_video = True
    elif media_msg.video_note:
        tg_file = await media_msg.video_note.get_file()
        is_video = True
    else:
        await msg.reply_text("⚠️ این پیام صدا/ویدیو نیست.")
        return

    await msg.reply_text("🎵 دارم آهنگ رو تشخیص می‌دم...")
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "src")
        await tg_file.download_to_drive(src_path)
        audio_path = src_path
        if is_video:
            if not _ffmpeg_available():
                await msg.reply_text("⚠️ ffmpeg رو سرور نصب نیست، نمی‌تونم صدا رو از ویدیو جدا کنم.")
                return
            audio_path = os.path.join(tmp, "audio.mp3")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", src_path, "-vn", "-ar", "44100", "-ac", "2", audio_path],
                    check=True, capture_output=True, timeout=120,
                )
            except Exception as e:
                log.error(f"audio extract failed: {e}")
                await msg.reply_text("⚠️ نتونستم صدا رو از ویدیو جدا کنم.")
                return
        try:
            result = await _audd_recognize(audio_path)
        except Exception as e:
            log.error(f"audd failed: {e}")
            await msg.reply_text("⚠️ سرویس تشخیص آهنگ الان جواب نداد.")
            return

    if not result:
        await msg.reply_text("🎵 نتونستم این آهنگ رو تشخیص بدم.")
        return

    title = result.get("title", "نامشخص")
    artist = result.get("artist", "نامشخص")
    lines = [f"🎵 *{title}*", f"🎤 {artist}"]
    spotify = result.get("spotify", {}) or {}
    apple = result.get("apple_music", {}) or {}
    if spotify.get("external_urls", {}).get("spotify"):
        lines.append(f"[Spotify]({spotify['external_urls']['spotify']})")
    if apple.get("url"):
        lines.append(f"[Apple Music]({apple['url']})")
    await msg.reply_text("\n".join(lines), parse_mode="Markdown", disable_web_page_preview=True)


def register_media_recognition(app):
    app.add_handler(MessageHandler(MOVIE_RE, movie_recognize_handler), group=28)
    app.add_handler(MessageHandler(SONG_RE, song_recognize_handler), group=28)
    app.add_handler(MessageHandler(SUMMARY_RE, summary_handler), group=28)
    app.add_handler(MessageHandler(PRIVATE_MEDIA_FILTER, private_media_offer_handler), group=28)
    app.add_handler(CallbackQueryHandler(private_media_button_callback, pattern=r"^mr:"), group=28)
    # هندلر منفعلِ جمع‌آوری پیام برای خلاصه‌سازی، تو گروه جدا (۲۹) تا رو بقیه تاثیر نذاره
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, collect_message_for_summary), group=29
    )
