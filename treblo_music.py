# -*- coding: utf-8 -*-
"""
treblo_music.py
================
🎵 «ساخت آهنگ با هوش مصنوعی» — تو بخش «🛠 ابزارها».

روند کار (state machine ساده، دقیقاً همون الگویی که برای پست‌ساز گاتهام
(post_saz.py → postsaz_intercept) استفاده شده تا هیچ تداخلی با handlerهای
دیگه‌ی پروژه پیش نیاد): وضعیت هر کاربر تو context.user_data["treblo"]
نگه‌داری می‌شه و treblo_intercept(update, context) باید از داخل handle_message
(تو bot.py)، دقیقاً بعد از postsaz_intercept، صدا زده بشه.

    1) کاربر دکمه‌ی «🎵 ساخت آهنگ با هوش مصنوعی» (تو «🛠 ابزارها») رو می‌زنه.
    2) ربات متن آهنگ (Lyrics) رو می‌خواد.
    3) بعد سبک آهنگ (Style) رو می‌پرسه (چندتا دکمه‌ی آماده + امکان نوشتن سبک دلخواه).
    4) عنوان رو می‌پرسه (اختیاری — «رد شو» یعنی از خط اول Lyrics استخراج کن).
    5) درخواست ساخت به Treblo API فرستاده می‌شه، وضعیت ساخت پولینگ می‌شه، فایل
       نهایی (MP3) دانلود و به کاربر ارسال می‌شه.

API Key: فقط و فقط از Environment Variable خونده می‌شه:
    TREBLO_API_KEY = os.getenv("TREBLO_API_KEY")
هیچ‌جای این فایل هاردکد نشده و در هیچ لاگ/پیام تلگرام/traceback چاپ نمی‌شه.

مستندات رسمی Treblo (بررسی‌شده، https://treblo.com/developers/docs —
Treblo با همین دامنه‌ی api.treblo.com/v1 قبلاً Sonauto نام داشت):

    Base URL: https://api.treblo.com/v1
    Auth:     Authorization: Bearer <TREBLO_API_KEY>

    POST /generations/v3
        Body: {"lyrics": "...", "prompt": "...", "output_format": "mp3"}
        → {"task_id": "..."}
        (طبق مستندات: باید حداقل یکی از tags/lyrics/prompt رو بفرستی؛ وقتی
        فقط lyrics می‌فرستی، باید prompt رو هم بفرستی — حتی رشته‌ی خالی —
        چون «فقط lyrics بدون prompt» رسماً پشتیبانی نمی‌شه.)

    GET /generations/status/{task_id}
        → یه رشته‌ی JSON ساده مثل "GENERATING" یا "SUCCESS" یا "FAILURE"
        (نه یه آبجکت — دقیقاً طبق نمونه‌ی مستندات).

    GET /generations/{task_id}
        → آبجکت کامل شامل «song_paths» (آرایه‌ای از URL فایل‌های صوتی؛ چون
        output_format=mp3 فرستادیم، این فایل‌ها MP3 هستن) و «model_version»
        و بقیه‌ی پارامترهای استفاده‌شده. این پاسخ فیلد title یا duration نداره
        (طبق مستندات رسمی) — برای همین این پروژه اونا رو حدس نمی‌زنه؛ عنوان از
        ورودی کاربر/اولین خط Lyrics ساخته می‌شه و در کارت خروجی «Duration»
        نمایش داده نمی‌شه (به‌جاش model_version نمایش داده می‌شه، چون واقعاً
        تو پاسخ هست).

⚠️ نکته‌ی مهم (شفاف اعلام می‌شه، حدس‌زده نشده): این پیاده‌سازی فقط از
endpointها/پارامترهایی استفاده می‌کنه که توی مستندات رسمی بالا صراحتاً اومدن؛
مدل v3 (endpoint فعلی و توصیه‌شده‌ی مستندات برای ساخت آهنگ کامل) انتخاب شده،
نه v2 که در مستندات به‌عنوان deprecated علامت‌گذاری شده.
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

# ---------- تنظیمات (کلید فقط از Environment Variable، چیزی هاردکد نشده) ----------
TREBLO_API_KEY = os.getenv("TREBLO_API_KEY", "").strip()

# طبق مستندات رسمی Treblo، آدرس پایه‌ی API همیشه همینه (نسخه‌ی مدل تو خودِ
# مسیر endpoint مشخص می‌شه، نه تو Base URL) — برای همین این مقدار ثابته و از
# Environment Variable خونده نمی‌شه.
TREBLO_BASE_URL = "https://api.treblo.com/v1"

TREBLO_SUBMIT_TIMEOUT_SECONDS = float(os.getenv("TREBLO_SUBMIT_TIMEOUT_SECONDS", "30"))
TREBLO_POLL_TIMEOUT_SECONDS = float(os.getenv("TREBLO_POLL_TIMEOUT_SECONDS", "420"))  # v3 نسبت به v2 کندتره
TREBLO_POLL_INTERVAL_SECONDS = float(os.getenv("TREBLO_POLL_INTERVAL_SECONDS", "5"))
TREBLO_MAX_RETRIES = int(os.getenv("TREBLO_MAX_RETRIES", "3"))
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
        row = [InlineKeyboardButton(STYLE_LABELS[k], callback_data=f"treblo:style:{k}") for k in keys[i:i + 2]]
        rows.append(row)
    rows.append([InlineKeyboardButton(f"{CANCEL_WORDS[0]}", callback_data="treblo:cancel")])
    return InlineKeyboardMarkup(rows)


def _notitle_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ رد شو (استخراج خودکار)", callback_data="treblo:notitle")],
        [InlineKeyboardButton(f"{CANCEL_WORDS[0]}", callback_data="treblo:cancel")],
    ])


def is_treblo_configured() -> bool:
    return bool(TREBLO_API_KEY)


class TrebloAPIError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {TREBLO_API_KEY}",
        "Content-Type": "application/json",
    }


def _extract_error(resp: httpx.Response) -> str:
    """پیام خطا رو از پاسخ استخراج می‌کنه — بدون هیچ فرضی راجع به کلید API،
    چون مستندات رسمی شکل دقیق JSON خطا رو مشخص نکرده؛ چندتا کلید رایج (detail/
    message/error) رو امتحان می‌کنه و در آخر به متن خام برمی‌گرده."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("detail") or data.get("message") or data.get("error")
            if isinstance(err, dict):
                err = err.get("message")
            if err:
                return str(err)
        return resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
    except Exception:
        return resp.text[:200] if resp.text else f"HTTP {resp.status_code}"


async def _submit_song(lyrics: str, style_prompt: str) -> dict:
    """POST /generations/v3 — با Retry محدود (نه بی‌نهایت) رو خطاهای موقتی
    شبکه/سرور. خطاهای غیرموقتی (401/400 و ...) بلافاصله بدون Retry بالا می‌رن.
    طبق مستندات، وقتی فقط lyrics می‌فرستیم باید prompt رو هم بفرستیم (حتی
    رشته‌ی خالی)، چون «فقط lyrics بدون prompt» پشتیبانی نمی‌شه."""
    payload = {
        "lyrics": lyrics,
        "prompt": style_prompt or "",
        "output_format": "mp3",
    }

    last_err = None
    for attempt in range(1, TREBLO_MAX_RETRIES + 1):
        try:
            timeout = httpx.Timeout(TREBLO_SUBMIT_TIMEOUT_SECONDS, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{TREBLO_BASE_URL}/generations/v3", headers=_headers(), json=payload)
            if resp.status_code >= 500:
                raise TrebloAPIError(f"خطای سرور Treblo ({resp.status_code})", retryable=True)
            if resp.status_code >= 400:
                raise TrebloAPIError(_extract_error(resp), retryable=False)
            data = resp.json()
            log.info(f"🎵 Treblo submit ok — task_id={data.get('task_id')}")
            return data
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = TrebloAPIError(f"خطای شبکه هنگام ارسال درخواست به Treblo: {e}", retryable=True)
        except TrebloAPIError as e:
            last_err = e
            if not e.retryable:
                raise
        if attempt < TREBLO_MAX_RETRIES:
            await asyncio.sleep(2 * attempt)
    raise last_err or TrebloAPIError("ارسال درخواست به Treblo بعد از چند تلاش شکست خورد.", retryable=False)


async def _poll_status(task_id: str, on_status=None) -> str:
    """GET /generations/status/{task_id} — طبق مستندات، پاسخ یه رشته‌ی JSON
    ساده‌ست (مثل "GENERATING" یا "SUCCESS")، نه یه آبجکت. تا وقتی status نهایی
    (SUCCESS/FAILURE) بشه یا زمان TREBLO_POLL_TIMEOUT_SECONDS تموم بشه Poll
    می‌کنه. خطاهای موقتی شبکه تو یه دور Poll باعث لغو کل عملیات نمی‌شن — فقط
    همون دور رو رد می‌کنه و دور بعدی امتحان می‌کنه."""
    deadline = time.monotonic() + TREBLO_POLL_TIMEOUT_SECONDS
    interval = TREBLO_POLL_INTERVAL_SECONDS
    last_status = None

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            timeout = httpx.Timeout(20.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{TREBLO_BASE_URL}/generations/status/{task_id}", headers=_headers())
            if resp.status_code >= 500:
                interval = min(interval * 1.3, 20.0)
                continue
            if resp.status_code >= 400:
                raise TrebloAPIError(_extract_error(resp), retryable=False)
            data = resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.info(f"🎵 Treblo poll: خطای شبکه‌ی موقتی، دور بعد دوباره امتحان می‌شه: {e}")
            interval = min(interval * 1.3, 20.0)
            continue

        status = str(data).strip().upper() if isinstance(data, str) else str((data or {}).get("status") or "").strip().upper()

        if status != last_status and on_status:
            try:
                await on_status(status)
            except Exception:
                pass
        last_status = status

        if status == "SUCCESS":
            return status
        if status == "FAILURE":
            raise TrebloAPIError("ساخت آهنگ توسط Treblo ناموفق بود (status: FAILURE).", retryable=False)
        interval = min(interval * 1.15, 20.0)

    raise TrebloAPIError("زمان انتظار برای آماده‌شدن آهنگ تمام شد؛ سرور Treblo خیلی کند جواب داد.", retryable=False)


async def _fetch_result(task_id: str) -> dict:
    """GET /generations/{task_id} — نتیجه‌ی نهایی (song_paths و بقیه‌ی
    اطلاعات) رو بعد از رسیدن status به SUCCESS برمی‌گردونه."""
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{TREBLO_BASE_URL}/generations/{task_id}", headers=_headers())
    if resp.status_code >= 400:
        raise TrebloAPIError(_extract_error(resp), retryable=False)
    return resp.json()


async def _download_audio(url: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="treblo_")
    os.close(fd)
    last_err = None
    for attempt in range(1, TREBLO_MAX_RETRIES + 1):
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
            if attempt < TREBLO_MAX_RETRIES:
                await asyncio.sleep(2 * attempt)
    try:
        os.remove(path)
    except OSError:
        pass
    raise TrebloAPIError(f"دانلود فایل صوتی از Treblo شکست خورد: {last_err}", retryable=False)


def _extract_title_from_lyrics(lyrics: str) -> str:
    for line in lyrics.splitlines():
        line = line.strip()
        if not line or re.match(r"^\[.*\]$", line):
            continue
        return line[:60]
    return "بدون عنوان"


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
    "RECEIVED": "🛠 در حال دریافت درخواست",
    "PROMPT": "✍️ در حال تحلیل سبک و متن",
    "TASK_SENT": "⏳ تو صف پردازش",
    "GENERATE_TASK_STARTED": "🎼 در حال شروع ساخت",
    "BEGINNING_GENERATION": "🎼 در حال شروع ساخت ملودی",
    "GENERATING": "🎧 در حال ساخت ملودی و صدا",
    "GENERATING_STREAMING_READY": "🎧 در حال ساخت ملودی و صدا",
    "DECOMPRESSING": "🔧 در حال پردازش نهایی صدا",
    "SAVING": "📦 در حال ذخیره‌سازی فایل",
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
        task_id = str(submitted.get("task_id") or "")
        if not task_id:
            raise TrebloAPIError("پاسخ Treblo فاقد شناسه‌ی تسک (task_id) بود.", retryable=False)

        await _poll_status(task_id, on_status=on_status)
        result = await _fetch_result(task_id)

        song_paths = result.get("song_paths") or []
        audio_url = song_paths[0] if song_paths else None
        if not audio_url:
            log.error(f"Treblo: نتونستم لینک فایل صوتی رو تو پاسخ پیدا کنم — raw: {result}")
            raise TrebloAPIError(
                "آهنگ ساخته شد ولی ربات نتونست لینک فایل نهایی رو تو پاسخ Treblo پیدا کنه.",
                retryable=False,
            )
        model_version = result.get("model_version") or "Treblo"

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
            f"🤖 Engine: Treblo ({model_version})\n"
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

    except TrebloAPIError as e:
        log.warning(f"🎵 Treblo: خطای کنترل‌شده — {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=status_msg.message_id,
                text=f"🦇 گاتهام نتونست آهنگ رو بسازه.\n\nدلیل: {e}\n\nمی‌تونی دوباره امتحان کنی.",
            )
        except Exception:
            await msg.reply_text(f"🦇 گاتهام نتونست آهنگ رو بسازه.\n\nدلیل: {e}")
    except Exception as e:
        # هیچ خطای پیش‌بینی‌نشده‌ای نباید این فلو رو کرش بده یا بدون پیام بمونه.
        log.exception("🎵 Treblo: خطای پیش‌بینی‌نشده در ساخت آهنگ", exc_info=e)
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


async def treblo_intercept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر تو یه سشن «ساخت آهنگ» فعال باشه، پیام رو خودمون مصرف می‌کنیم
    و True برمی‌گردونیم (هندلر صدازننده باید فوراً return کنه). اگه سشنی فعال
    نباشه False برمی‌گردونیم — صفر تداخل با رفتار قبلی handle_message."""
    session = context.user_data.get("treblo")
    if not session or not session.get("awaiting"):
        return False

    msg = update.effective_message
    if msg is None or not (msg.text or "").strip():
        return False

    text = msg.text.strip()
    if text in CANCEL_WORDS:
        context.user_data.pop("treblo", None)
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
        context.user_data.pop("treblo", None)
        return True

    return False


async def treblo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if data == "treblo:start":
        await query.answer()
        if not is_treblo_configured():
            await query.answer(
                "🦇 این قابلیت هنوز روی سرور فعال نشده (TREBLO_API_KEY تنظیم نیست).",
                show_alert=True,
            )
            return
        context.user_data["treblo"] = {"awaiting": "lyrics"}
        await query.message.reply_text(LYRICS_PROMPT_TEXT, parse_mode="Markdown")
        return

    session = context.user_data.get("treblo")
    if not session:
        await query.answer()
        return

    if data == "treblo:cancel":
        await query.answer()
        context.user_data.pop("treblo", None)
        await query.message.reply_text("🦇 عملیات ساخت آهنگ لغو شد.")
        return

    if data.startswith("treblo:style:") and session.get("awaiting") == "style":
        await query.answer()
        key = data.split(":", 2)[2]
        session["style_prompt"] = STYLE_PRESETS.get(key, key)
        session["awaiting"] = "title"
        await query.message.reply_text(TITLE_PROMPT_TEXT, reply_markup=_notitle_keyboard())
        return

    if data == "treblo:notitle" and session.get("awaiting") == "title":
        await query.answer()
        session["title"] = None
        session.pop("awaiting", None)
        asyncio.create_task(_run_generation(update, context, dict(session)))
        context.user_data.pop("treblo", None)
        return

    await query.answer()


def register_treblo_music(app):
    # group=29 عمداً انتخاب شده — تو کل پروژه هیچ ماژول دیگه‌ای از این شماره
    # گروه استفاده نمی‌کنه (گروه‌های استفاده‌شده: -1,0,1,2,3,4,5,6,8,11,12,13,
    # 14,20,21,22,23,24,25,26,27,28,30)، پس صفر ریسک برخورد با هندلر دیگه‌ای.
    app.add_handler(CallbackQueryHandler(treblo_callback, pattern=r"^treblo:"), group=29)
    if not is_treblo_configured():
        log.warning("🎵 TREBLO_API_KEY تنظیم نشده — دکمه‌ی «ساخت آهنگ» نمایش داده می‌شه ولی موقع استفاده پیام خطای روشن می‌ده.")
    else:
        log.info("🎵 Treblo Music Generator فعال شد.")
