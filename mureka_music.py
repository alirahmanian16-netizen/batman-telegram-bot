# -*- coding: utf-8 -*-
"""
mureka_music.py
================
🎵 «ساخت آهنگ با هوش مصنوعی» — تو بخش «🛠 ابزارها».

روند کار (state machine ساده، دقیقاً همون الگویی که برای پست‌ساز گاتهام
(post_saz.py → postsaz_intercept) استفاده شده تا هیچ تداخلی با handler
group های دیگه‌ی پروژه پیش نیاد): وضعیت هر کاربر تو context.user_data["mureka"]
نگه‌داری می‌شه و mureka_intercept(update, context) باید از داخل handle_message
(تو bot.py)، دقیقاً بعد از postsaz_intercept، صدا زده بشه.

    1) کاربر رو دکمه‌ی «🎵 ساخت آهنگ با هوش مصنوعی» (تو «🛠 ابزارها») می‌زنه.
    2) ربات متن آهنگ رو می‌خواد.
    3) بعد سبک آهنگ رو می‌پرسه (چندتا دکمه‌ی آماده + امکان نوشتن سبک دلخواه).
    4) عنوان رو می‌پرسه (اختیاری — «رد شو» یعنی از خط اول متن استخراج کن).
    5) درخواست به Mureka API (https://api.mureka.ai) فرستاده می‌شه، وضعیت
       ساخت پولینگ می‌شه، فایل نهایی دانلود و به‌صورت MP3 به کاربر ارسال می‌شه.

API Key: فقط و فقط از Environment Variable خونده می‌شه (os.getenv) — هیچ‌جای
این فایل هاردکد نشده.

مستندات رسمی Mureka (بررسی‌شده، https://platform.mureka.ai/docs/):
    POST /v1/song/generate   {"lyrics": "...", "model": "auto", "prompt": "..."}
        → {"id": "...", "status": "preparing", "model": "...", "trace_id": "..."}
    GET  /v1/song/query/{task_id}  → همون آبجکت با status به‌روزشده؛ وقتی
        status نهایی می‌شه (succeeded/completed) نتیجه (URL فایل صوتی) همراهشه.

⚠️ نکته‌ی مهم (شفاف اعلام می‌شه، حدس‌زده نشده): مستندات عمومی Mureka دقیق‌ترین
شکل «request» رو (پارامترهای lyrics/model/prompt و مسیر polling) روشن مشخص
کرده، ولی ساختار دقیق JSON پاسخ نهایی (اسم فیلد لینک فایل صوتی) تو صفحه‌ی
عمومی docs به‌صورت جدول ایستا در دسترس نبود (صفحه‌ش JS-rendered بود). برای
همین `_extract_audio_url` پایین به‌جای فرض کورکورانه‌ی یه اسم فیلد، چندتا اسم
رایج (mp3_url/audio_url/url/flac_url) رو هم تو یه آبجکت «choices» ممکن و هم
در سطح بالای پاسخ چک می‌کنه. اگه بعد از اولین اجرای واقعی روی Railway پاسخ
API یه شکل دیگه داشت، فقط کافیه یه لاگ از پاسخ خام (که پایین با
log.debug(f"Mureka raw response: {data}") قبل از parse ثبت می‌شه) رو بفرستی
تا این تابع رو دقیقاً منطبق کنیم.
"""

import os
import re
import time
import logging
import asyncio
import tempfile

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

log = logging.getLogger(__name__)

# ---------- تنظیمات (همه از Environment Variable، چیزی هاردکد نشده) ----------
MUREKA_API_KEY = os.getenv("MUREKA_API_KEY", "").strip()
MUREKA_BASE_URL = os.getenv("MUREKA_BASE_URL", "https://api.mureka.ai").rstrip("/")

MUREKA_SUBMIT_TIMEOUT_SECONDS = float(os.getenv("MUREKA_SUBMIT_TIMEOUT_SECONDS", "30"))
MUREKA_POLL_TIMEOUT_SECONDS = float(os.getenv("MUREKA_POLL_TIMEOUT_SECONDS", "360"))  # حداکثر ۶ دقیقه صبر
MUREKA_POLL_INTERVAL_SECONDS = float(os.getenv("MUREKA_POLL_INTERVAL_SECONDS", "6"))
MUREKA_MAX_RETRIES = int(os.getenv("MUREKA_MAX_RETRIES", "3"))
MAX_LYRICS_CHARS = 3000

CANCEL_WORDS = ("❌ انصراف", "انصراف", "لغو", "/cancel")

STYLE_PRESETS = {
    "pop": "pop, upbeat, catchy, modern production",
    "rap": "hip-hop, rap, strong beat, rhythmic vocal flow",
    "sad": "sad, emotional ballad, slow tempo, melancholic, minor key",
    "epic": "epic, cinematic, orchestral, powerful, heroic",
    "rock": "rock, electric guitar, energetic, powerful drums",
    "folk": "persian traditional folk, acoustic instruments, soulful vocal",
}
STYLE_LABELS = {
    "pop": "🎤 پاپ",
    "rap": "🔥 رپ",
    "sad": "😢 غمگین",
    "epic": "⚔️ حماسی",
    "rock": "🎸 راک",
    "folk": "🪕 سنتی",
}

LYRICS_PROMPT_TEXT = (
    "🦇 *ساخت آهنگ با هوش مصنوعی*\n\n"
    "متن آهنگ (اشعار) رو برام بفرست.\n"
    "می‌تونی ساختاردار بنویسی (مثلاً با خط‌هایی مثل «[Verse]» و «[Chorus]») یا "
    "فقط متن ساده — هر دو کار می‌کنه.\n\n"
    f"حداکثر {MAX_LYRICS_CHARS} کاراکتر. برای انصراف «{CANCEL_WORDS[0]}» رو بفرست."
)
STYLE_PROMPT_TEXT = (
    "🎨 حالا سبک آهنگ رو انتخاب کن، یا خودت یه سبک دلخواه بنویس "
    "(مثلاً «پاپ آروم با گیتار» یا «رپ فارسی تند»):"
)
TITLE_PROMPT_TEXT = (
    "📝 یه عنوان برای آهنگ می‌خوای بذاری؟ بنویسش، یا بزن «رد شو» تا خودم از "
    "خط اول متن آهنگ عنوان بسازم."
)


def _style_keyboard() -> InlineKeyboardMarkup:
    keys = list(STYLE_PRESETS.keys())
    rows = []
    for i in range(0, len(keys), 2):
        row = [InlineKeyboardButton(STYLE_LABELS[k], callback_data=f"mureka:style:{k}") for k in keys[i:i + 2]]
        rows.append(row)
    rows.append([InlineKeyboardButton(f"{CANCEL_WORDS[0]}", callback_data="mureka:cancel")])
    return InlineKeyboardMarkup(rows)


def _notitle_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ رد شو (استخراج خودکار)", callback_data="mureka:notitle")],
        [InlineKeyboardButton(f"{CANCEL_WORDS[0]}", callback_data="mureka:cancel")],
    ])


def is_mureka_configured() -> bool:
    return bool(MUREKA_API_KEY)


class MurekaAPIError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {MUREKA_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("error", {}).get("message") or data.get("message") or resp.text[:200])
    except Exception:
        return resp.text[:200] if resp.text else f"HTTP {resp.status_code}"


async def _submit_song(lyrics: str, style_prompt: str) -> dict:
    """POST /v1/song/generate — با Retry محدود (نه بی‌نهایت) رو خطاهای موقتی
    شبکه/سرور. خطاهای غیرموقتی (401/400 و ...) بلافاصله بدون Retry بالا می‌رن."""
    payload = {"lyrics": lyrics, "model": "auto"}
    if style_prompt:
        payload["prompt"] = style_prompt

    last_err = None
    for attempt in range(1, MUREKA_MAX_RETRIES + 1):
        try:
            timeout = httpx.Timeout(MUREKA_SUBMIT_TIMEOUT_SECONDS, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{MUREKA_BASE_URL}/v1/song/generate", headers=_headers(), json=payload)
            if resp.status_code >= 500:
                raise MurekaAPIError(f"خطای سرور Mureka ({resp.status_code})", retryable=True)
            if resp.status_code >= 400:
                raise MurekaAPIError(_extract_error(resp), retryable=False)
            data = resp.json()
            log.info(f"🎵 Mureka submit ok — task id={data.get('id')} status={data.get('status')}")
            return data
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = MurekaAPIError(f"خطای شبکه هنگام ارسال درخواست به Mureka: {e}", retryable=True)
        except MurekaAPIError as e:
            last_err = e
            if not e.retryable:
                raise
        if attempt < MUREKA_MAX_RETRIES:
            await asyncio.sleep(2 * attempt)
    raise last_err or MurekaAPIError("ارسال درخواست به Mureka بعد از چند تلاش شکست خورد.", retryable=False)


async def _poll_song(task_id: str, on_status=None) -> dict:
    """GET /v1/song/query/{task_id} — تا وقتی status نهایی (succeeded/failed/...)
    بشه یا زمان MUREKA_POLL_TIMEOUT_SECONDS تموم بشه، Poll می‌کنه. خطاهای موقتی
    شبکه تو یه تلاش Poll باعث لغو کل عملیات نمی‌شن — فقط همون دور رو رد می‌کنه و
    دور بعدی امتحان می‌کنه."""
    deadline = time.monotonic() + MUREKA_POLL_TIMEOUT_SECONDS
    interval = MUREKA_POLL_INTERVAL_SECONDS
    last_status = None

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            timeout = httpx.Timeout(20.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{MUREKA_BASE_URL}/v1/song/query/{task_id}", headers=_headers())
            if resp.status_code >= 500:
                interval = min(interval * 1.3, 20.0)
                continue
            if resp.status_code >= 400:
                raise MurekaAPIError(_extract_error(resp), retryable=False)
            data = resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.info(f"🎵 Mureka poll: خطای شبکه‌ی موقتی، دور بعد دوباره امتحان می‌شه: {e}")
            interval = min(interval * 1.3, 20.0)
            continue

        log.debug(f"Mureka raw response: {data}")
        status = str(data.get("status") or "").strip().lower()
        if status != last_status and on_status:
            try:
                await on_status(status)
            except Exception:
                pass
        last_status = status

        if status in ("succeeded", "success", "completed", "finished", "done"):
            return data
        if status in ("failed", "error", "timeouted", "cancelled", "canceled"):
            raise MurekaAPIError(f"ساخت آهنگ توسط Mureka ناموفق بود (status: {status}).", retryable=False)
        interval = min(interval * 1.15, 20.0)

    raise MurekaAPIError("زمان انتظار برای آماده‌شدن آهنگ تمام شد؛ سرور Mureka خیلی کند جواب داد.", retryable=False)


def _extract_audio_result(data: dict):
    """(url, duration_ms, api_title) رو از پاسخ Mureka درمیاره — چندتا شکل ممکن
    پاسخ رو پوشش می‌ده (توضیح بالای فایل رو ببین)."""
    candidates = data.get("choices") or data.get("songs") or data.get("data") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    if isinstance(candidates, list) and candidates:
        first = candidates[0] or {}
        for key in ("mp3_url", "audio_url", "url", "flac_url", "wav_url"):
            if first.get(key):
                return first[key], first.get("duration_milliseconds") or first.get("duration"), first.get("title")
    for key in ("mp3_url", "audio_url", "url"):
        if data.get(key):
            return data[key], data.get("duration_milliseconds") or data.get("duration"), data.get("title")
    return None, None, None


async def _download_audio(url: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="mureka_")
    os.close(fd)
    last_err = None
    for attempt in range(1, MUREKA_MAX_RETRIES + 1):
        try:
            timeout = httpx.Timeout(120.0, connect=15.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as f:
                        async for chunk in resp.aiter_bytes(65536):
                            f.write(chunk)
            return path
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
            last_err = e
            if attempt < MUREKA_MAX_RETRIES:
                await asyncio.sleep(2 * attempt)
    try:
        os.remove(path)
    except OSError:
        pass
    raise MurekaAPIError(f"دانلود فایل صوتی از Mureka شکست خورد: {last_err}", retryable=False)


def _extract_title_from_lyrics(lyrics: str) -> str:
    for line in lyrics.splitlines():
        line = line.strip()
        if not line or re.match(r"^\[.*\]$", line):
            continue
        return line[:60]
    return "بدون عنوان"


def _format_duration(ms) -> str:
    try:
        total_seconds = int(round(float(ms) / 1000.0))
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"
    except (TypeError, ValueError):
        return "--:--"


def _created_at_str() -> str:
    from datetime import datetime
    dt = datetime.now()
    try:
        import jdatetime
        jnow = jdatetime.datetime.fromgregorian(datetime=dt)
        return jnow.strftime("%Y/%m/%d")
    except Exception:
        return dt.strftime("%Y-%m-%d")


_STATUS_LABELS_FA = {
    "preparing": "🛠 در حال آماده‌سازی",
    "queued": "⏳ تو صف پردازش",
    "running": "🎼 در حال ساخت ملودی و صدا",
    "streaming": "🎧 در حال نهایی‌سازی صدا",
}


async def _run_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, session: dict):
    chat_id = update.effective_chat.id
    user = update.effective_user
    msg = update.effective_message

    lyrics = session.get("lyrics") or ""
    style_prompt = session.get("style_prompt") or ""
    title = session.get("title") or _extract_title_from_lyrics(lyrics)

    status_msg = await context.bot.send_message(
        chat_id=chat_id, text="🦇 در حال ارسال درخواست به آزمایشگاه موسیقی گاتهام..."
    )

    async def on_status(status: str):
        label = _STATUS_LABELS_FA.get(status, f"در حال پردازش ({status})")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id, text=f"🦇 {label}..."
            )
        except Exception:
            pass

    audio_path = None
    try:
        submitted = await _submit_song(lyrics, style_prompt)
        task_id = str(submitted.get("id") or "")
        if not task_id:
            raise MurekaAPIError("پاسخ Mureka فاقد شناسه‌ی تسک (id) بود.", retryable=False)

        result = await _poll_song(task_id, on_status=on_status)
        audio_url, duration_ms, api_title = _extract_audio_result(result)
        if not audio_url:
            log.error(f"Mureka: نتونستم لینک فایل صوتی رو تو پاسخ پیدا کنم — raw: {result}")
            raise MurekaAPIError(
                "آهنگ ساخته شد ولی ربات نتونست لینک فایل نهایی رو تو پاسخ Mureka پیدا کنه "
                "(ساختار پاسخ API عوض شده). این مورد لاگ شد.",
                retryable=False,
            )
        if api_title:
            title = api_title

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id, text="📥 در حال دانلود فایل صوتی..."
            )
        except Exception:
            pass

        audio_path = await _download_audio(audio_url)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id, text="📤 در حال ارسال آهنگ..."
            )
        except Exception:
            pass

        with open(audio_path, "rb") as audio_file:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=title,
                performer="🦇 Gotham Music Engine",
                filename=f"{title}.mp3",
            )

        username = f"@{user.username}" if user.username else (user.first_name or "کاربر")
        gotham_card = (
            "🦇 GOTHAM MUSIC\n"
            "━━━━━━━━━━━━━━\n"
            f"🎵 عنوان: {title}\n"
            f"👤 By: {username}\n"
            f"⏱ Duration: {_format_duration(duration_ms)}\n"
            f"📅 Created: {_created_at_str()}\n\n"
            "▶️ Plays: 0\n"
            "❤️ Likes: 0\n\n"
            "📝 Lyrics: Ready\n"
            "━━━━━━━━━━━━━━\n"
            "🦇 Gotham Music Engine"
        )
        await context.bot.send_message(chat_id=chat_id, text=gotham_card)

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id, text="✅ آهنگ آماده و ارسال شد."
            )
        except Exception:
            pass

    except MurekaAPIError as e:
        log.warning(f"🎵 Mureka: خطای کنترل‌شده — {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id,
                text=f"🦇 گاتهام نتونست آهنگ رو بسازه.\n\nدلیل: {e}\n\nمی‌تونی دوباره امتحان کنی.",
            )
        except Exception:
            await msg.reply_text(f"🦇 گاتهام نتونست آهنگ رو بسازه.\n\nدلیل: {e}")
    except Exception as e:
        # هیچ خطای پیش‌بینی‌نشده‌ای نباید این فلو رو کرش بده یا بدون پیام بمونه.
        log.exception("🎵 Mureka: خطای پیش‌بینی‌نشده در ساخت آهنگ", exc_info=e)
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id,
                text="🦇 یه مشکل غیرمنتظره پیش اومد و ساخت آهنگ ناتموم موند. لطفاً دوباره امتحان کن.",
            )
        except Exception:
            pass
    finally:
        if audio_path:
            try:
                os.remove(audio_path)
            except OSError:
                pass


# =========================================================
#  نقطه‌ی ورود مشترک — از handle_message تو bot.py صدا زده می‌شه
# =========================================================


async def mureka_intercept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر تو یه سشن «ساخت آهنگ» فعال باشه، پیام رو خودمون مصرف می‌کنیم
    و True برمی‌گردونیم (هندلر صدازننده باید فوراً return کنه). اگه سشنی فعال
    نباشه False برمی‌گردونیم — صفر تداخل با رفتار قبلی handle_message."""
    session = context.user_data.get("mureka")
    if not session or not session.get("awaiting"):
        return False

    msg = update.effective_message
    if msg is None or not (msg.text or "").strip():
        return False

    text = msg.text.strip()
    if text in CANCEL_WORDS:
        context.user_data.pop("mureka", None)
        await msg.reply_text("🦇 عملیات ساخت آهنگ لغو شد.")
        return True

    awaiting = session.get("awaiting")
    if awaiting == "lyrics":
        if len(text) > MAX_LYRICS_CHARS:
            await msg.reply_text(f"⚠️ متن آهنگ خیلی طولانیه (حداکثر {MAX_LYRICS_CHARS} کاراکتر). یه نسخه‌ی کوتاه‌تر بفرست.")
            return True
        session["lyrics"] = text
        session["awaiting"] = "style"
        await msg.reply_text(STYLE_PROMPT_TEXT, reply_markup=_style_keyboard())
        return True

    if awaiting == "style":
        session["style_prompt"] = text
        session["awaiting"] = "title"
        await msg.reply_text(TITLE_PROMPT_TEXT, reply_markup=_notitle_keyboard())
        return True

    if awaiting == "title":
        session["title"] = text[:60]
        session.pop("awaiting", None)
        asyncio.create_task(_run_generation(update, context, dict(session)))
        context.user_data.pop("mureka", None)
        return True

    return False


async def mureka_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if data == "mureka:start":
        await query.answer()
        if not is_mureka_configured():
            await query.answer(
                "🦇 این قابلیت هنوز روی سرور فعال نشده (MUREKA_API_KEY تنظیم نیست).",
                show_alert=True,
            )
            return
        context.user_data["mureka"] = {"awaiting": "lyrics"}
        await query.message.reply_text(LYRICS_PROMPT_TEXT, parse_mode="Markdown")
        return

    session = context.user_data.get("mureka")
    if not session:
        await query.answer()
        return

    if data == "mureka:cancel":
        await query.answer()
        context.user_data.pop("mureka", None)
        await query.message.reply_text("🦇 عملیات ساخت آهنگ لغو شد.")
        return

    if data.startswith("mureka:style:") and session.get("awaiting") == "style":
        await query.answer()
        key = data.split(":", 2)[2]
        session["style_prompt"] = STYLE_PRESETS.get(key, key)
        session["awaiting"] = "title"
        await query.message.reply_text(TITLE_PROMPT_TEXT, reply_markup=_notitle_keyboard())
        return

    if data == "mureka:notitle" and session.get("awaiting") == "title":
        await query.answer()
        session["title"] = None
        session.pop("awaiting", None)
        asyncio.create_task(_run_generation(update, context, dict(session)))
        context.user_data.pop("mureka", None)
        return

    await query.answer()


def register_mureka_music(app):
    # group=29 عمداً انتخاب شده — تو کل پروژه هیچ ماژول دیگه‌ای از این شماره
    # گروه استفاده نمی‌کنه (گروه‌های استفاده‌شده: -1,0,1,2,3,4,5,6,8,11,12,13,
    # 14,20,21,22,23,24,25,26,27,28,30)، پس صفر ریسک برخورد با هندلر دیگه‌ای.
    app.add_handler(CallbackQueryHandler(mureka_callback, pattern=r"^mureka:"), group=29)
    if not is_mureka_configured():
        log.warning("🎵 MUREKA_API_KEY تنظیم نشده — دکمه‌ی «ساخت آهنگ» نمایش داده می‌شه ولی موقع استفاده پیام خطای روشن می‌ده.")
    else:
        log.info("🎵 Mureka Music Generator فعال شد.")
