# -*- coding: utf-8 -*-
"""
dollar_price.py
================
🦇💵 سیستم قیمت دلار گاتهام

ماژول مستقل (دقیقاً هم‌الگو با فایل‌های دیگه‌ی پروژه مثل reminders.py،
security_tools.py و...) که فقط یک register_dollar_price(app) بیرون می‌ده و
هیچ Handler/منو/Database فعلی رو دست نمی‌زنه.

━━━━━━━━━━━━━━━━━━━━ منبع قیمت ━━━━━━━━━━━━━━━━━━━━
TGJU هیچ API عمومی/رسمی‌ای نداره (این نکته رو قبل از پیاده‌سازی بررسی شد؛
tgju.org/api فقط یه صفحه‌ی تبلیغاتی/تماسه، نه یه REST API واقعی). بنابراین
طبق دستور صریح کار، مستقیماً از ساختار HTML صفحه‌ی «نرخ ارز آزاد» تغذیه
می‌کنیم؛ نه از صفحه‌ی پروفایل تک‌ارزی (که ویجت‌ها و اسکریپت‌های زیادی داره)
بلکه از جدول سبک‌تر و پایدارتر:

    https://www.tgju.org/currency

هر ردیف این جدول یه <tr data-market-nameslug="..." data-price="..."> داره؛
یعنی به‌جای پارس کردن کلاس‌های CSS (که با هر بازطراحی جزئی سایت می‌شکنه)،
از خودِ Attribute های داده‌ای صفحه استفاده می‌کنیم — طبق مستندات باز
پروژه‌های اسکرپر معروف tgju (که همین ساختار رو تأیید کردن)، این Attribute ها
پایدارترین بخش صفحه هستن. ردیف هدف: data-market-nameslug="price_dollar_rl"
(دقیقاً همون کلید URL که تو دستور کار خواسته شده: profile/price_dollar_rl).

⚠️ DOLLAR_API_KEY: این Environment Variable از قبل تو Railway هست، ولی چون
مشخص نیست دقیقاً به کدوم سرویس متصله و TGJU (منبع خواسته‌شده برای «دلار
آزاد») خودش API رسمی نداره، طبق قانون کار («فقط به خاطر وجود این
Environment Variable فرض نکن API دقیقه») ازش به‌عنوان منبع اصلی استفاده
نشده. اگه بعداً مطمئن شدی این کلید به یه سرویس well-known و معتبر برای
دلار آزاد وصله، کافیه تابع fetch_dollar_raw رو با endpoint اون سرویس عوض
کنی؛ بقیه‌ی pipeline (تبدیل ریال/تومن، مقایسه، فرمت پیام، دکمه‌ی
بروزرسانی و...) دست‌نخورده می‌مونه.
"""

import re
import time
import random
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

from custom_emojis import ce, CUSTOM_EMOJIS, esc

try:
    import jdatetime
except ImportError:  # نباید پیش بیاد، jdatetime تو requirements.txt هست
    jdatetime = None

log = logging.getLogger(__name__)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

TGJU_CURRENCY_URL = "https://www.tgju.org/currency"
TGJU_ROW_SLUG = "price_dollar_rl"
HTTP_TIMEOUT = 10.0
MAX_FETCH_ATTEMPTS = 2  # یه تلاش اصلی + یه ریترای محدود، فقط برای خطاهای شبکه‌ای

# کش خیلی کوتاه‌مدت (In-memory، نه دیتابیس) — فقط برای اینکه چند کاربر
# هم‌زمان باعث چند Request تکراری به TGJU تو چند ثانیه نشن. با ری‌استارت
# ربات خودش خالی می‌شه که مشکلی نیست چون همیشه دوباره از منبع تازه می‌گیریم.
_CACHE_TTL_SECONDS = 20
_cache_lock = asyncio.Lock()
_cache_data = None
_cache_ts = 0.0

# فلود-کنترل ساده‌ی سمت ربات (جدا از RetryAfter تلگرام) تا اسپم کردن «دلار»
# یا دکمه‌ی بروزرسانی باعث درخواست‌های بی‌رویه به TGJU نشه.
_REFRESH_COOLDOWN_SECONDS = 5
_last_request_by_chat = {}

# آستانه‌های شدت واکنش بتمن (قابل تنظیم)
FLAT_CHANGE_PERCENT = 0.05     # زیر این درصد = «بدون تغییر»
SEVERE_CHANGE_PERCENT = 1.5    # بالای این درصد = واکنش شدید

# بازه‌ی منطقی قیمت دلار به تومان، فقط برای جلوگیری از نمایش داده‌ی
# مخدوش/ناقص در صورت تغییر ساختار صفحه (Sanity Check، نه قیمت ساختگی).
TOMAN_SANITY_MIN = 50_000
TOMAN_SANITY_MAX = 5_000_000

DOLLAR_TRIGGER_RE = re.compile(r"^\s*(قیمت\s+)?دلار\s*[؟?]?\s*$")


class DollarFetchError(Exception):
    """خطای عمومی گرفتن/پارس قیمت — پیام کاربر همیشه یکی از پیام‌های ثابت پایینه."""


# ---------------- دریافت و پارس خام از TGJU ----------------

async def _fetch_dollar_raw():
    """یه GET به جدول ارز TGJU می‌زنه و ردیف دلار آزاد رو برمی‌گردونه (خام، به ریال).

    خروجی: dict با کلیدهای price_rial, change_rial (می‌تونه None), change_percent
    (می‌تونه None), direction ("high"/"low"/None), low_rial (اختیاری), high_rial
    (اختیاری). در صورت شکست، DollarFetchError می‌ندازه.
    """
    last_exc = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={
                "User-Agent": "Mozilla/5.0 (compatible; GothamDollarBot/1.0)"
            }) as client:
                resp = await client.get(TGJU_CURRENCY_URL)
                resp.raise_for_status()
            return _parse_dollar_row(resp.text)
        except DollarFetchError:
            raise  # خطای پارس، ریترای فایده‌ای نداره
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError, httpx.HTTPError) as e:
            last_exc = e
            log.warning(f"[dollar_price] تلاش {attempt}/{MAX_FETCH_ATTEMPTS} برای گرفتن قیمت شکست خورد: {e}")
            if attempt < MAX_FETCH_ATTEMPTS:
                await asyncio.sleep(1.0)
    raise DollarFetchError(f"fetch failed after {MAX_FETCH_ATTEMPTS} attempts: {last_exc}")


def _to_number(text: str):
    if not text:
        return None
    cleaned = text.strip().replace(",", "")
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _parse_dollar_row(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find(attrs={"data-market-nameslug": TGJU_ROW_SLUG})
    if row is None:
        raise DollarFetchError("ردیف دلار آزاد تو جدول TGJU پیدا نشد (احتمالاً ساختار صفحه عوض شده)")

    price_rial = _to_number(row.get("data-price"))
    if price_rial is None:
        # Fallback: شاید data-price نبود، سلول اول جدول رو امتحان کن
        cells = row.find_all("td")
        if cells:
            price_rial = _to_number(cells[0].get_text())
    if price_rial is None:
        raise DollarFetchError("مقدار قیمت دلار تو ردیف TGJU پیدا/پارس نشد")

    change_percent = None
    change_rial = None
    direction = None
    change_span = row.find("span", class_=re.compile(r"^(high|low)$"))
    if change_span is not None:
        direction = "high" if "high" in (change_span.get("class") or []) else "low"
        m = re.search(r"\(([\d.]+)\s*%\)\s*([\d,]+)", change_span.get_text())
        if m:
            change_percent = _to_number(m.group(1))
            change_rial = _to_number(m.group(2))

    low_rial = None
    high_rial = None
    cells = row.find_all("td")
    # ستون‌های جدول (طبق ساختار فعلی TGJU): قیمت، تغییر، کمترین، بیشترین، زمان.
    # اگه این ترتیب عوض بشه، این بخش فقط سقف/کف رو نادیده می‌گیره (نه کل قابلیت رو).
    try:
        if len(cells) >= 4:
            low_rial = _to_number(cells[2].get_text())
            high_rial = _to_number(cells[3].get_text())
    except Exception as e:
        log.info(f"[dollar_price] پارس سقف/کف روز شکست خورد (نادیده گرفته شد): {e}")

    return {
        "price_rial": price_rial,
        "change_rial": change_rial,
        "change_percent": change_percent,
        "direction": direction,  # "high" = افزایش, "low" = کاهش, None = نامشخص
        "low_rial": low_rial,
        "high_rial": high_rial,
    }


# ---------------- تبدیل ریال↔تومان + Sanity Check ----------------

def _rial_to_toman(value):
    """TGJU قیمت دلار آزاد رو همیشه به ریال می‌ده (رقم میلیونی)، پس همیشه ÷۱۰.

    برای جلوگیری از خطای ده‌برابری در صورت هر خرابی/تغییر پیش‌بینی‌نشده تو
    منبع، نتیجه رو با یه بازه‌ی منطقی چک می‌کنیم؛ اگه خارج از بازه بود یعنی
    داده قابل‌اعتماد نیست و نباید نمایش داده بشه (نه اینکه دوباره حدس زده بشه).
    """
    if value is None:
        return None
    return value / 10.0


def _sanity_ok(toman_value) -> bool:
    if toman_value is None:
        return False
    return TOMAN_SANITY_MIN <= toman_value <= TOMAN_SANITY_MAX


# ---------------- پردازش نهایی + کش کوتاه‌مدت ----------------

async def get_dollar_data(force: bool = False) -> dict:
    """داده‌ی پردازش‌شده (به تومان) رو برمی‌گردونه؛ در صورت شکست DollarFetchError می‌ندازه."""
    global _cache_data, _cache_ts
    async with _cache_lock:
        now = time.time()
        if not force and _cache_data is not None and (now - _cache_ts) < _CACHE_TTL_SECONDS:
            return _cache_data

        raw = await _fetch_dollar_raw()
        price_toman = _rial_to_toman(raw["price_rial"])
        if not _sanity_ok(price_toman):
            raise DollarFetchError(f"قیمت خارج از بازه‌ی منطقی بود: {price_toman}")

        change_toman = _rial_to_toman(raw["change_rial"]) if raw["change_rial"] is not None else None
        low_toman = _rial_to_toman(raw["low_rial"]) if raw["low_rial"] is not None else None
        high_toman = _rial_to_toman(raw["high_rial"]) if raw["high_rial"] is not None else None

        yesterday_toman = None
        if change_toman is not None and raw["direction"] in ("high", "low"):
            if raw["direction"] == "high":
                yesterday_toman = price_toman - change_toman
            else:
                yesterday_toman = price_toman + change_toman

        data = {
            "price_toman": price_toman,
            "change_toman": change_toman,
            "change_percent": raw["change_percent"],
            "direction": raw["direction"],
            "yesterday_toman": yesterday_toman,
            "low_toman": low_toman if _sanity_ok(low_toman) else None,
            "high_toman": high_toman if _sanity_ok(high_toman) else None,
            "fetched_at": datetime.now(TEHRAN_TZ),
        }
        _cache_data = data
        _cache_ts = now
        return data


# ---------------- شخصیت گاتهام/بتمن ----------------

_LINES_SEVERE_UP = [
    "🚨 هشدار گاتهام!\nدلار داره پرواز می‌کنه 💀📈\nجیب جوون ایرانی هم باهاش پرواز کرد!",
    "🚨 آژیر گاتهام به صدا دراومد!\nدلار امروز رحم نکرد 💀📈",
]
_LINES_MILD_UP = [
    "دلار دوباره رفت بالا...\nبتمن امروز حال خوبی نداره 😐🦇",
    "بازار امروز رو به بالاست... بتمن سکوت کرده 😑🦇",
]
_LINES_SEVERE_DOWN = [
    "🎉 گاتهام امروز یه لبخند زد! 😎🦇\nدلار حسابی عقب نشست.",
    "🎉 خبر خوش تو گاتهام می‌پیچه!\nدلار امروز فرار کرد 🔥📉",
]
_LINES_MILD_DOWN = [
    "بالاخره یه خبر خوب! 😎🟢\nدلار یه پله اومد پایین.",
    "امروز بازار یه‌کم آروم‌تره... بتمن نفس راحت کشید 😎🟢",
]
_LINES_FLAT = [
    "بازار فعلاً آرومه... 🦇",
    "دلار امروز جاش رو حفظ کرد. گاتهام هم آرومه 🦇➖",
]


def _gotham_flavor(change_percent, direction) -> str:
    """فقط متن طنز/شخصیت گاتهام رو برمی‌گردونه، بر اساس شدت و جهت تغییر."""
    if change_percent is None or direction is None:
        return random.choice(_LINES_FLAT)

    abs_pct = abs(change_percent)
    if abs_pct < FLAT_CHANGE_PERCENT:
        return random.choice(_LINES_FLAT)

    if direction == "high":
        return random.choice(_LINES_SEVERE_UP if abs_pct >= SEVERE_CHANGE_PERCENT else _LINES_MILD_UP)
    return random.choice(_LINES_SEVERE_DOWN if abs_pct >= SEVERE_CHANGE_PERCENT else _LINES_MILD_DOWN)


# ---------------- فرمت پیام نهایی ----------------

def _format_jalali_now(dt: datetime) -> str:
    if jdatetime is None:
        return dt.strftime("%Y-%m-%d | %H:%M:%S") + " (میلادی، jdatetime نصب نیست)"
    jnow = jdatetime.datetime.fromgregorian(datetime=dt)
    return jnow.strftime("%Y/%m/%d | %H:%M:%S")


def dollar_direction_emoji(direction, change_percent=None, fallback_flat="➖") -> str:
    """تگ Custom Emoji مناسبِ جهت تغییر دلار رو برمی‌گردونه (صعودی/نزولی/خنثی).
    parse_mode="HTML" لازمه تا نمایش داده بشه."""
    if change_percent is not None and abs(change_percent) < FLAT_CHANGE_PERCENT:
        return fallback_flat
    if direction == "high":
        return ce(CUSTOM_EMOJIS["dollar_up"][0], "📈")
    if direction == "low":
        return ce(CUSTOM_EMOJIS["dollar_down"][0], "📉")
    return fallback_flat


def build_dollar_message(data: dict) -> str:
    price = data["price_toman"]
    change_toman = data["change_toman"]
    change_percent = data["change_percent"]
    direction = data["direction"]
    yesterday = data["yesterday_toman"]
    low = data["low_toman"]
    high = data["high_toman"]

    dollar_emoji = ce(CUSTOM_EMOJIS["dollar"][0], "💵")

    change_line = None
    if change_toman is not None and change_percent is not None and direction is not None:
        abs_pct = abs(change_percent)
        if abs_pct < FLAT_CHANGE_PERCENT:
            change_line = "⚪ بدون تغییر\n➖ 0.00٪"
        elif direction == "high":
            up = ce(CUSTOM_EMOJIS["dollar_up"][0], "📈")
            change_line = f"🔴 +{change_toman:,.0f} تومان\n{up} +{change_percent:.2f}٪ نسبت به دیروز"
        else:
            down = ce(CUSTOM_EMOJIS["dollar_down"][0], "📉")
            change_line = f"🟢 -{change_toman:,.0f} تومان\n{down} -{change_percent:.2f}٪ نسبت به دیروز"

    flavor = _gotham_flavor(change_percent, direction)

    lines = [
        "🦇 GOTHAM DOLLAR",
        "",
        f"{dollar_emoji} 1 دلار آمریکا",
        f"💸 {price:,.0f} تومان",
        "",
    ]
    if change_line:
        lines.append(change_line)
        lines.append("")
    if yesterday is not None:
        lines.append(f"🪙 دیروز: {yesterday:,.0f} تومان")
        lines.append("")
    if low is not None and high is not None:
        lines.append(f"📈 سقف امروز: {high:,.0f} تومان")
        lines.append(f"📉 کف امروز: {low:,.0f} تومان")
        lines.append("")

    lines.append(f"🕐 {esc(_format_jalali_now(data['fetched_at']))} (زمان دریافت)")
    lines.append("")
    lines.append("🦇 وضعیت گاتهام:")
    lines.append(esc(flavor))
    lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    lines.append("🇮🇷 بازار آزاد ایران")
    lines.append("📡 منبع: TGJU")
    lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines)


def _dollar_error_message() -> str:
    warn = ce(CUSTOM_EMOJIS["warning"][0], "⚠️")
    return (
        f"{warn} گاتهام فعلاً به بازار دلار دسترسی ندارد.\n\n"
        "🔄 چند لحظه بعد دوباره امتحان کن."
    )


DOLLAR_ERROR_MESSAGE = (
    "🦇 گاتهام فعلاً به بازار دلار دسترسی ندارد.\n\n"
    "🔄 چند لحظه بعد دوباره امتحان کن."
)


def _dollar_refresh_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 بروزرسانی قیمت", callback_data="gdollar:refresh")]])


def _cooldown_hit(chat_id) -> bool:
    now = time.time()
    last = _last_request_by_chat.get(chat_id, 0)
    if now - last < _REFRESH_COOLDOWN_SECONDS:
        return True
    _last_request_by_chat[chat_id] = now
    return False


# ---------------- Handlerها ----------------

def register_dollar_price(app):

    async def send_dollar_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        msg = update.effective_message
        if _cooldown_hit(chat_id):
            return  # جلوگیری از اسپم؛ بی‌سروصدا نادیده می‌گیریم چون پیام قبلی هنوز تازه‌ست
        try:
            data = await get_dollar_data()
        except Exception as e:
            log.warning(f"[dollar_price] گرفتن قیمت دلار شکست خورد: {e}")
            await msg.reply_text(_dollar_error_message(), parse_mode="HTML")
            return
        text = build_dollar_message(data)
        await msg.reply_text(text, reply_markup=_dollar_refresh_keyboard(), parse_mode="HTML")

    async def dollar_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send_dollar_price(update, context)

    async def dollar_menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        chat_id = update.effective_chat.id
        if _cooldown_hit(chat_id):
            await query.answer("چند لحظه صبر کن و دوباره امتحان کن ⏳", show_alert=True)
            return
        await query.answer()
        try:
            data = await get_dollar_data()
        except Exception as e:
            log.warning(f"[dollar_price] گرفتن قیمت دلار (از منوی ابزارها) شکست خورد: {e}")
            await query.message.reply_text(_dollar_error_message(), parse_mode="HTML")
            return
        text = build_dollar_message(data)
        await query.message.reply_text(text, reply_markup=_dollar_refresh_keyboard(), parse_mode="HTML")

    async def dollar_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        chat_id = update.effective_chat.id
        if _cooldown_hit(chat_id):
            await query.answer("چند لحظه صبر کن، تازه بروزرسانی شد ⏳", show_alert=True)
            return
        await query.answer("🔄 در حال بروزرسانی...")
        try:
            data = await get_dollar_data(force=True)
        except Exception as e:
            log.warning(f"[dollar_price] بروزرسانی قیمت دلار شکست خورد: {e}")
            await query.answer(DOLLAR_ERROR_MESSAGE, show_alert=True)
            return
        text = build_dollar_message(data)
        try:
            await query.edit_message_text(text, reply_markup=_dollar_refresh_keyboard(), parse_mode="HTML")
        except Exception as e:
            # پیام «Message is not modified» یا موارد مشابه — بی‌خطر، فقط لاگ می‌کنیم
            log.info(f"[dollar_price] edit_message_text نتونست پیام رو بروز کنه (احتمالاً بدون تغییر): {e}")

    app.add_handler(MessageHandler(filters.Regex(DOLLAR_TRIGGER_RE), dollar_message_handler), group=22)
    app.add_handler(CallbackQueryHandler(dollar_menu_button_handler, pattern=r"^gdollar:show$"), group=22)
    app.add_handler(CallbackQueryHandler(dollar_refresh_callback, pattern=r"^gdollar:refresh$"), group=22)

    log.info("🦇💵 ماژول قیمت دلار گاتهام ثبت شد (منبع: TGJU).")
