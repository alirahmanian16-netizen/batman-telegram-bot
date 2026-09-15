# -*- coding: utf-8 -*-
"""
api_monitor.py
================
🦇 GOTHAM API MONITOR — وضعیت API Key سرویس‌های خارجی پروژه، فقط برای Owner.

هدف: مالک ربات بتونه با یه دستور بفهمه هر API Key معتبره یا نه، سرویس در
دسترسه یا نه، و اگه خودِ API رسمی سرویس quota/credits/usage نشون می‌ده،
مقدار واقعیش رو ببینه — بدون هیچ عدد حدسی/ساختگی.

🔐 امنیت (مهم‌ترین بخش این فایل):
    - این ماژول فقط app.bot_data و یه owner_id از bot.py می‌گیره (همون
      OWNER_ID واقعی پروژه، از طریق register_api_monitor(app, deps) —
      دقیقاً هم‌الگو با admin_panel.py/config_manager.py). هیچ Owner ID
      جدید یا هاردکد نشده.
    - هم دستور /key_status و هم دکمه‌ی «🔄 بررسی مجدد» قبل از هر کاری
      چک می‌کنن که update.effective_user.id == owner_id باشه؛ در غیر این
      صورت فقط یه پیام/Alert کوتاه رد می‌شه و هیچ داده‌ای (نه وضعیت،
      نه حتی این‌که سرویسی وجود داره یا نه) به کاربر غیرمالک نمی‌ره.
    - هیچ API Key‌ای — کامل یا حتی Mask‌شده — تو هیچ پیام Telegram چاپ
      نمی‌شه. تابع _mask_key فقط برای لاگ داخلی (log.debug) وجود داره،
      نه برای نمایش به کاربر.
    - این فایل هیچ Key‌ای رو تغییر/حذف/تولید نمی‌کنه؛ فقط Read-Only از
      os.environ می‌خونه.

📋 سرویس‌هایی که واقعاً در کد پروژه استفاده می‌شن (قبل از اضافه کردن،
کل پروژه grep شد — هر کدوم رو دقیقاً کجا صدا می‌زنن):
    - OPENROUTER_API_KEY  → bot.py (call_ai) / media_recognition.py / tools_and_fun.py
    - TMDB_API_KEY        → media_recognition.py (تشخیص فیلم/سریال)
    - AUDD_API_TOKEN      → media_recognition.py (تشخیص آهنگ)
    - ELEVENLABS_API_KEY  → elevenlabs_service.py (استودیو صدا)
    - MUREKA_API_KEY      → mureka_music.py (register_mureka_music تو bot.py
                             واقعاً صدا زده می‌شه، یعنی هنوز فعاله)
    - TREBLO_API_KEY      → treblo_music.py *وجود داره و کدش واقعیه*، ولی
                             register_treblo/treblo_intercept هیچ‌جای
                             bot.py صدا زده نمی‌شه (import هم نشده) —
                             یعنی این قابلیت فعلاً برای کاربران غیرفعاله.
                             چون کلید/کد واقعیه (نه فرضی)، همچنان چک می‌شه
                             ولی این نکته صریح تو گزارش نوشته می‌شه.
    - (بدون کلید) TGJU    → dollar_price.py مستقیم از HTML صفحه‌ی
                             tgju.org/currency تغذیه می‌کنه، نه یه API
                             رسمی؛ DOLLAR_API_KEY تو Railway هست ولی طبق
                             خودِ dollar_price.py عمداً استفاده نمی‌شه
                             (چون معلوم نیست به کدوم سرویس وصله). برای
                             همین این ردیف «بدون کلید»ه، فقط Reachability.

حذف‌شده از لیست (چون واقعاً هیچ‌جای کد استفاده نمی‌شن — حدس زده نشده،
grep کامل پروژه انجام شد):
    - OPENAI_API_KEY      → فقط تو bot.py تعریف شده (os.getenv)، هیچ‌جای
                             دیگه‌ای صدا زده نمی‌شه.
    - TWELVE_LABS_API_KEY → اصلاً تو هیچ فایلی از پروژه وجود نداره.
این دوتا رو به‌جای حذفِ ساکت، به‌صورت یه یادداشت شفاف پایین گزارش نشون
می‌دیم تا مالک ربات بدونه چرا نیستن (نه اینکه فراموش شدن).

🧠 نحوه‌ی تشخیص وضعیت: هر check_* فقط یه درخواست سبک و Read-Only به
Endpoint رسمی همون سرویس می‌زنه (Timeout مشخص، بدون Retry شدید) و از
روی کد HTTP/بدنه‌ی پاسخ نتیجه می‌گیره. هیچ عددی حدس زده نمی‌شه: اگه خودِ
API فیلد quota/usage/limit نده، دقیقاً همین نوشته می‌شه که «توسط API
ارائه نمی‌شود» — نه یه مقدار ساختگی.
"""

import os
import time
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

log = logging.getLogger(__name__)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

# ---------- کلیدها (فقط از Environment Variable، هیچی هاردکد نشده) ----------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
AUDD_API_TOKEN = os.getenv("AUDD_API_TOKEN", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
MUREKA_API_KEY = os.getenv("MUREKA_API_KEY", "").strip()
TREBLO_API_KEY = os.getenv("TREBLO_API_KEY", "").strip()

REQUEST_TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=8.0)
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GothamAPIMonitor/1.0)"}

# ---------- وضعیت‌های ممکن ----------
ST_OK = "🟢 فعال / معتبر"
ST_NO_QUOTA = "🟡 فعال ولی Quota در دسترس نیست"
ST_NEAR_LIMIT = "🟠 نزدیک به محدودیت"
ST_INVALID = "🔴 نامعتبر / Unauthorized"
ST_UNREACHABLE = "⚫ سرویس در دسترس نیست"
ST_NOT_SET = "⚪ API Key تنظیم نشده"

_DEPS_KEY = "api_monitor_deps"
_REFRESH_COOLDOWN_SECONDS = 5  # جلوگیری از فلود دکمه‌ی «بررسی مجدد»
_last_refresh_ts: dict = {}


def _mask_key(key: str) -> str:
    """فقط برای Debug داخلی (log.debug) — هرگز به Telegram فرستاده نمی‌شه."""
    key = (key or "").strip()
    if not key:
        return "(خالی)"
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}...{key[-4:]}"


def _result(name, emoji, status, lines=None, error=None):
    return {"name": name, "emoji": emoji, "status": status, "lines": lines or [], "error": error}


def _network_error_result(name, emoji, exc):
    kind = type(exc).__name__
    log.info(f"api_monitor: {name} در دسترس نبود ({kind})")
    return _result(name, emoji, ST_UNREACHABLE, error=kind)


def _invalid_result(name, emoji, key: str):
    # فقط نسخه‌ی Mask شده تو لاگ داخلی می‌ره — هیچ‌وقت کامل، هیچ‌وقت به Telegram.
    log.warning(f"api_monitor: کلید {name} نامعتبر/Unauthorized است (Key: {_mask_key(key)})")
    return _result(name, emoji, ST_INVALID)


# ============================== OpenRouter ==============================
async def check_openrouter(client: httpx.AsyncClient) -> dict:
    name, emoji = "OpenRouter", "🤖"
    if not OPENROUTER_API_KEY:
        return _result(name, emoji, ST_NOT_SET)
    try:
        r = await client.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code in (401, 403):
        return _invalid_result(name, emoji, OPENROUTER_API_KEY)
    if r.status_code == 429:
        return _result(name, emoji, ST_NO_QUOTA, error="429 هنگام بررسی (Rate limited)")
    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    if r.status_code != 200:
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")

    try:
        data = r.json().get("data", {})
    except Exception:
        return _result(name, emoji, ST_NO_QUOTA, error="پاسخ غیرمنتظره از OpenRouter")

    usage = data.get("usage")
    limit = data.get("limit")
    if limit is None:
        lines = ["Credits: بدون محدودیت مشخص (Pay-as-you-go)"]
        if usage is not None:
            lines.append(f"Usage: ${usage:.4f}")
        return _result(name, emoji, ST_OK, lines=lines)

    remaining = max(limit - (usage or 0), 0)
    lines = [
        f"Credits باقی‌مانده: ${remaining:.4f}",
        f"Usage: ${(usage or 0):.4f}",
        f"Limit: ${limit:.4f}",
    ]
    status = ST_OK
    if limit > 0:
        pct_remaining = remaining / limit * 100
        if pct_remaining <= 10:
            status = ST_NEAR_LIMIT
            lines.append(f"⚠️ فقط {pct_remaining:.1f}٪ اعتبار باقی مونده (طبق خودِ API)")
    return _result(name, emoji, status, lines=lines)


# ================================= TMDB =================================
async def check_tmdb(client: httpx.AsyncClient) -> dict:
    name, emoji = "TMDB", "🎬"
    if not TMDB_API_KEY:
        return _result(name, emoji, ST_NOT_SET)
    try:
        r = await client.get(
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": TMDB_API_KEY},
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code == 200:
        return _result(
            name, emoji, ST_NO_QUOTA,
            lines=["Quota: توسط API ارائه نمی‌شود (TMDB سهمیه‌ی مشخصی publish نمی‌کنه)"],
        )
    if r.status_code in (401, 403):
        return _invalid_result(name, emoji, TMDB_API_KEY)
    if r.status_code == 429:
        return _result(name, emoji, ST_NO_QUOTA, error="429 هنگام بررسی (Rate limited)")
    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")


# ================================= AudD ==================================
async def check_audd(client: httpx.AsyncClient) -> dict:
    name, emoji = "AudD", "🎵"
    if not AUDD_API_TOKEN:
        return _result(name, emoji, ST_NOT_SET)
    try:
        # بدون url/file می‌فرستیم (عمداً) — فقط برای اعتبارسنجی token، بدون
        # مصرف واقعی از quota تشخیص آهنگ. طبق مستندات AudD، بدنه‌ی خطا
        # مشخص می‌کنه مشکل از api_token هست یا از نبود url/file.
        r = await client.post("https://api.audd.io/", data={"api_token": AUDD_API_TOKEN})
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    if r.status_code == 429:
        return _result(name, emoji, ST_NO_QUOTA, error="429 هنگام بررسی (Rate limited)")
    if r.status_code != 200:
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception:
        return _result(name, emoji, ST_NO_QUOTA, error="پاسخ غیرمنتظره از AudD")

    if data.get("status") == "success":
        # عملاً نباید برسه چون url/file نفرستادیم، ولی اگه رسید یعنی توکن سالمه.
        return _result(name, emoji, ST_OK, lines=["Quota: توسط API ارائه نمی‌شود"])

    err_msg = str((data.get("error") or {}).get("error_message") or "").lower()
    if "api_token" in err_msg or "token" in err_msg:
        return _invalid_result(name, emoji, AUDD_API_TOKEN)
    if "url" in err_msg or "file" in err_msg:
        # یعنی token قبول شد و فقط پارامتر تشخیص (url/file) نبود — دقیقاً انتظارمون.
        return _result(name, emoji, ST_NO_QUOTA, lines=["Quota: توسط API ارائه نمی‌شود"])
    return _result(name, emoji, ST_NO_QUOTA, error="پاسخ نامشخص از AudD (نه تایید نه رد صریح)")


# ============================== ElevenLabs ===============================
async def check_elevenlabs(client: httpx.AsyncClient) -> dict:
    name, emoji = "ElevenLabs", "🎙"
    if not ELEVENLABS_API_KEY:
        return _result(name, emoji, ST_NOT_SET)
    try:
        r = await client.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code in (401, 403):
        return _invalid_result(name, emoji, ELEVENLABS_API_KEY)
    if r.status_code == 429:
        return _result(name, emoji, ST_NO_QUOTA, error="429 هنگام بررسی (Rate limited)")
    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    if r.status_code != 200:
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception:
        return _result(name, emoji, ST_NO_QUOTA, error="پاسخ غیرمنتظره از ElevenLabs")

    used = data.get("character_count")
    limit = data.get("character_limit")
    if used is None or limit is None:
        return _result(name, emoji, ST_NO_QUOTA, lines=["Quota: توسط API ارائه نمی‌شود"])

    remaining = max(limit - used, 0)
    lines = [f"Character Quota: {remaining:,}/{limit:,} باقی‌مانده", f"Usage: {used:,}"]
    reset_ts = data.get("next_character_count_reset_unix")
    if reset_ts:
        try:
            reset_dt = datetime.fromtimestamp(reset_ts, TEHRAN_TZ).strftime("%Y-%m-%d")
            lines.append(f"Reset: {reset_dt}")
        except Exception:
            pass
    status = ST_OK
    if limit > 0:
        pct_remaining = remaining / limit * 100
        if pct_remaining <= 10:
            status = ST_NEAR_LIMIT
            lines.append(f"⚠️ فقط {pct_remaining:.1f}٪ کاراکتر باقی مونده (طبق خودِ API)")
    return _result(name, emoji, status, lines=lines)


# ================================ Treblo =================================
async def check_treblo(client: httpx.AsyncClient) -> dict:
    name, emoji = "Treblo", "🎼"
    if not TREBLO_API_KEY:
        return _result(name, emoji, ST_NOT_SET)
    try:
        # مستندات رسمی Treblo endpoint جدایی برای اعتبار/سهمیه نداره؛ فقط
        # یه GET سبک به status یه task فرضی می‌زنیم تا اعتبار Key رو (از
        # روی 401/403 در مقابل هر پاسخ دیگه) بسنجیم — بدون مصرف quota ساخت آهنگ.
        r = await client.get(
            "https://api.treblo.com/v1/generations/status/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {TREBLO_API_KEY}"},
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code in (401, 403):
        return _invalid_result(name, emoji, TREBLO_API_KEY)
    if r.status_code == 429:
        return _result(name, emoji, ST_NO_QUOTA, error="429 هنگام بررسی (Rate limited)")
    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    # هر چیزی جز 401/403/429/5xx یعنی Key قبول شده (حتی 404 چون task فرضیه).
    return _result(
        name, emoji, ST_NO_QUOTA,
        lines=["Quota: توسط API ارائه نمی‌شود (Treblo مستندات رسمی برای اعتبار/سهمیه نداره)"],
    )


# ================================ Mureka ==================================
async def check_mureka(client: httpx.AsyncClient) -> dict:
    name, emoji = "Mureka", "🎶"
    if not MUREKA_API_KEY:
        return _result(name, emoji, ST_NOT_SET)
    base_url = os.getenv("MUREKA_BASE_URL", "https://api.mureka.ai").rstrip("/")
    try:
        r = await client.get(
            f"{base_url}/v1/song/query/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {MUREKA_API_KEY}"},
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code in (401, 403):
        return _invalid_result(name, emoji, MUREKA_API_KEY)
    if r.status_code == 429:
        return _result(name, emoji, ST_NO_QUOTA, error="429 هنگام بررسی (Rate limited)")
    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    return _result(
        name, emoji, ST_NO_QUOTA,
        lines=["Quota: توسط API ارائه نمی‌شود (مستندات عمومی Mureka endpoint اعتبار نداره)"],
    )


# ============================ Dollar / TGJU ==============================
async def check_dollar_tgju(client: httpx.AsyncClient) -> dict:
    """بدون API Key — dollar_price.py مستقیم صفحه‌ی TGJU رو اسکرپ می‌کنه،
    نه یه API رسمی؛ برای همین اینجا فقط Reachability چک می‌شه، نه Quota."""
    name, emoji = "Dollar Price (TGJU)", "💵"
    try:
        from dollar_price import TGJU_CURRENCY_URL
    except Exception:
        TGJU_CURRENCY_URL = "https://www.tgju.org/currency"
    try:
        r = await client.get(TGJU_CURRENCY_URL)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as e:
        return _network_error_result(name, emoji, e)
    except Exception as e:
        return _network_error_result(name, emoji, e)

    if r.status_code == 200:
        return _result(name, emoji, ST_OK, lines=["بدون نیاز به API Key (اسکرپ HTML)"])
    if r.status_code in (500, 502, 503):
        return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")
    return _result(name, emoji, ST_UNREACHABLE, error=f"HTTP {r.status_code}")


# ============================================================================

async def check_all_api_keys() -> list:
    """همه‌ی سرویس‌ها رو موازی چک می‌کنه؛ خطای یکی روی بقیه اثر نمی‌ذاره
    چون هر check_* خودش کامل try/except شده. با یه httpx.AsyncClient
    مشترک (connection pool محدود) اجرا می‌شه تا به هیچ سروری فشار زیادی
    نیاد و Telegram Bot API هم اصلاً درگیر این بخش نیست (Flood نمی‌کنه)."""
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=_HTTP_HEADERS, limits=limits) as client:
        checks = [
            check_openrouter(client),
            check_tmdb(client),
            check_audd(client),
            check_elevenlabs(client),
            check_treblo(client),
            check_mureka(client),
            check_dollar_tgju(client),
        ]
        results = await asyncio.gather(*checks, return_exceptions=True)

    final = []
    labels = ["OpenRouter", "TMDB", "AudD", "ElevenLabs", "Treblo", "Mureka", "Dollar Price (TGJU)"]
    emojis = ["🤖", "🎬", "🎵", "🎙", "🎼", "🎶", "💵"]
    for label, emoji, res in zip(labels, emojis, results):
        if isinstance(res, Exception):
            log.exception(f"api_monitor: خطای پیش‌بینی‌نشده حین چک {label}", exc_info=res)
            final.append(_result(label, emoji, ST_UNREACHABLE, error=type(res).__name__))
        else:
            final.append(res)
    return final


def _build_report_text(results: list) -> str:
    now = datetime.now(TEHRAN_TZ).strftime("%H:%M:%S")
    lines = ["🦇 *GOTHAM API MONITOR*", "", "━━━━━━━━━━━━━━"]
    for item in results:
        lines.append(f"\n{item['emoji']} {item['name']}")
        lines.append(item["status"])
        for l in item["lines"]:
            lines.append(f"    {l}")
        if item.get("error"):
            lines.append(f"    ⚠️ {item['error']}")
    lines.append("\n━━━━━━━━━━━━━━")

    notes = [
        "ℹ️ *یادداشت*",
        "• `OPENAI_API_KEY` در Environment تعریف شده ولی هیچ‌جای کد پروژه صدا زده نمی‌شه — در این پنل چک نشد.",
        "• `TWELVE_LABS_API_KEY` هیچ‌جای کد پروژه استفاده نمی‌شه — در این پنل چک نشد.",
        "• ماژول Treblo (`treblo_music.py`) به `bot.py` وصل (register) نشده — یعنی قابلیت «ساخت آهنگ Treblo» فعلاً برای کاربران غیرفعاله، صرف‌نظر از وضعیت Key بالا.",
    ]
    lines.append("")
    lines.extend(notes)
    lines.append(f"\n🕐 آخرین بررسی: {now}")
    return "\n".join(lines)


def _refresh_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 بررسی مجدد", callback_data="apimon:refresh")]])


def _is_owner(update: Update, owner_id: int) -> bool:
    user = update.effective_user
    return bool(user and owner_id and user.id == owner_id)


async def key_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deps = context.bot_data.get(_DEPS_KEY)
    owner_id = deps.get("owner_id") if deps else None
    if not _is_owner(update, owner_id):
        # عمداً هیچ اطلاعاتی درباره‌ی این‌که این دستور اصلاً چیه فاش نمی‌شه.
        await update.effective_message.reply_text("⛔️ این دستور فقط برای مالک ربات فعاله.")
        return

    wait_msg = await update.effective_message.reply_text("🦇 در حال بررسی وضعیت سرویس‌ها...")
    try:
        results = await check_all_api_keys()
        text = _build_report_text(results)
        await wait_msg.edit_text(text, parse_mode="Markdown", reply_markup=_refresh_keyboard())
    except Exception as e:
        log.exception("api_monitor: key_status_cmd شکست خورد", exc_info=e)
        try:
            await wait_msg.edit_text("⚠️ بررسی وضعیت سرویس‌ها با خطا مواجه شد. دوباره امتحان کن.")
        except Exception:
            pass


async def key_status_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    deps = context.bot_data.get(_DEPS_KEY)
    owner_id = deps.get("owner_id") if deps else None
    if not _is_owner(update, owner_id):
        await query.answer("⛔️ این دکمه فقط برای مالک ربات فعاله.", show_alert=True)
        return

    uid = update.effective_user.id
    now = time.monotonic()
    last = _last_refresh_ts.get(uid, 0.0)
    if now - last < _REFRESH_COOLDOWN_SECONDS:
        await query.answer("⏳ کمی صبر کن، همین الان یه‌بار بررسی شد.", show_alert=False)
        return
    _last_refresh_ts[uid] = now

    await query.answer("🔄 در حال بررسی مجدد...")
    try:
        results = await check_all_api_keys()
        text = _build_report_text(results)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_refresh_keyboard())
    except Exception as e:
        log.exception("api_monitor: refresh callback شکست خورد", exc_info=e)
        try:
            await query.edit_message_text("⚠️ بررسی وضعیت سرویس‌ها با خطا مواجه شد. دوباره امتحان کن.")
        except Exception:
            pass


def register_api_monitor(app, deps: dict):
    """deps = {"owner_id": OWNER_ID} — دقیقاً همون OWNER_ID واقعی bot.py،
    نه یه مقدار جدید. هم‌الگو با register_admin_panel/register_gotham_config."""
    app.bot_data[_DEPS_KEY] = deps
    app.add_handler(CommandHandler("key_status", key_status_cmd))
    app.add_handler(CallbackQueryHandler(key_status_refresh_callback, pattern=r"^apimon:refresh$"))
    log.info("🦇 api_monitor: دستور /key_status (فقط Owner) ثبت شد.")
