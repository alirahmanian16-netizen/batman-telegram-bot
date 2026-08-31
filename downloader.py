# -*- coding: utf-8 -*-
"""
downloader.py
================
دانلودر اینستاگرام / یوتیوب / پینترست با منوی سه‌گزینه‌ای.

نحوه‌ی کار:
    ۱. کاربر کلمه‌ی «دانلودر» رو می‌نویسه (بدون / و بدون اسم ربات).
    ۲. یه منوی سه‌دکمه‌ای میاد: 📸 اینستاگرام / ▶️ یوتیوب / 📌 پینترست
    ۳. کاربر یکی رو انتخاب می‌کنه.
    ۴. کاربر لینک رو تو همون چت می‌فرسته.
    ۵. ربات با yt-dlp دانلود می‌کنه و ویدیو/عکس رو مستقیم تو چت می‌فرسته.
       برای یوتیوب، حجم فایل هم تو کپشن نشون داده می‌شه.

نیازمندی: yt-dlp باید تو requirements.txt باشه (اضافه شده).

نحوه‌ی اتصال (کنار بقیه‌ی register_ها تو bot.py):

    from downloader import register_downloader
    register_downloader(app)     # <-- این خط رو اضافه کن
"""

import os
import re
import json
import glob
import time
import uuid
import shutil
import sqlite3
import asyncio
import logging
import mimetypes
import tempfile
import subprocess
import urllib.parse

import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo,
)
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.error import RetryAfter, TimedOut

log = logging.getLogger(__name__)

try:
    import yt_dlp
except ImportError:  # اگه نصب نشده باشه، پیام واضح می‌دیم به‌جای کرش
    yt_dlp = None


PENDING_DL = {}  # user_id -> "instagram" | "youtube" | "pinterest" | "tiktok" | "twitter" | "soundcloud"

PLATFORM_LABELS = {
    "instagram": "📸 اینستاگرام",
    "youtube": "▶️ یوتیوب",
    "pinterest": "📌 پینترست",
    "tiktok": "🎵 تیک‌تاک",
    "twitter": "🐦 ایکس/توییتر",
    "soundcloud": "🎧 ساندکلاود",
}

# برای هر پلتفرم، محدود کردن دانلود فقط به دامنه‌های همون پلتفرم (جلوی سوءاستفاده رو می‌گیره)
# نکته: از substring match استفاده می‌کنیم، پس زیردامنه‌ها (uk.pinterest.com,
# www.instagram.com, m.youtube.com و ...) خودکار پوشش داده می‌شن.
PLATFORM_DOMAINS = {
    "instagram": ("instagram.com", "instagr.am"),
    "youtube": ("youtube.com", "youtu.be"),
    "pinterest": ("pinterest.com", "pin.it", "pinimg.com"),
    "tiktok": ("tiktok.com",),
    "twitter": ("twitter.com", "x.com"),
    "soundcloud": ("soundcloud.com", "snd.sc"),
}

def text_contains_supported_link(text: str) -> bool:
    """می‌گه آیا یه متن (کپشن/پیام) حاوی لینکیه که دانلودر پشتیبانی می‌کنه یا نه.

    🐛 باگ اصلی «لینک اینستا تو گروه نمی‌ره»: وقتی آنتی‌لینک یا فیلتر کلمات
    گروه فعال بود، همین لینکی که کاربر برای دانلودر می‌فرستاد به‌عنوان
    اسپم شناسایی و پیامش حذف می‌شد — قبل از این‌که اصلاً به downloader_link_handler
    برسه. این تابع تو bot.py/security_tools.py استفاده می‌شه تا لینک‌های
    پلتفرم‌های پشتیبانی‌شده (اینستاگرام/یوتیوب/تیک‌تاک/ایکس/پینترست/ساندکلاود)
    از این حذف خودکار معاف بشن — چون این‌ها یه قابلیت رسمی ربات‌ان، نه اسپم.
    """
    if not text:
        return False
    m = URL_RE.search(text)
    if not m:
        return False
    return _detect_platform_from_url(m.group(0)) is not None


def _detect_platform_from_url(url: str):
    """اگه کاربر بدون انتخاب پلتفرم (بدون زدن «دانلودر» و بدون کلیک دکمه) مستقیم
    یه لینک پشتیبانی‌شده بفرسته، از روی دامنه‌ش پلتفرم رو خودکار تشخیص بده.
    این دقیقاً همون چیزیه که تو Group لازمه: کاربر لینک می‌فرسته، ربات خودش
    Platform رو تشخیص می‌ده و بدون نیاز به مرحله‌ی انتخاب منو دانلود می‌کنه.
    اگه دامنه به هیچ پلتفرمی نخوره، None برمی‌گرده (پیام بی‌سروصدا به بقیه‌ی
    هندلرها سپرده می‌شه، همون رفتار قبلی برای لینک‌های غیرمرتبط)."""
    u = url.lower()
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(d in u for d in domains):
            return platform
    return None


# فایل کوکی اختیاری برای هر پلتفرم — بعضی لینک‌های یوتیوب/اینستاگرام پشت قفل
# ضد-ربات‌ان («Sign in to confirm you're not a bot» / «empty media response»)
# و بدون کوکیِ یه اکانت لاگین‌شده اصلاً قابل دانلود نیستن؛ این یه محدودیت سمت
# خود پلتفرمه، نه باگ کد. اگه این env varها ست بشن (مسیر یه فایل cookies.txt به
# فرمت Netscape که از مرورگر export شده)، ازشون استفاده می‌کنیم.
# اگه env var ست نشده بود، دنبال یه فایل کوکیِ پیش‌فرض کنار خودِ bot.py می‌گردیم؛
# این‌جوری کافیه فایل cookies.txt خروجی مرورگر (فرمت Netscape) رو با همین اسم‌ها
# تو ریشه‌ی پروژه بذاری، بدون نیاز به تنظیم متغیر محیطی رو هاست.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_cookie_path(*names):
    for name in names:
        path = os.path.join(_BASE_DIR, name)
        if os.path.exists(path):
            return path
    return None


COOKIES_FILES = {
    "instagram": os.getenv("IG_COOKIES_FILE") or _default_cookie_path(
        "instagram_cookies.txt", "ig_cookies.txt"
    ),
    "youtube": os.getenv("YT_COOKIES_FILE") or _default_cookie_path(
        "youtube_cookies.txt", "yt_cookies.txt"
    ),
    "pinterest": os.getenv("PIN_COOKIES_FILE") or _default_cookie_path(
        "pinterest_cookies.txt", "pin_cookies.txt"
    ),
    "tiktok": os.getenv("TT_COOKIES_FILE") or _default_cookie_path(
        "tiktok_cookies.txt", "tt_cookies.txt"
    ),
    "twitter": os.getenv("TW_COOKIES_FILE") or _default_cookie_path(
        "twitter_cookies.txt", "tw_cookies.txt", "x_cookies.txt"
    ),
    "soundcloud": os.getenv("SC_COOKIES_FILE") or _default_cookie_path(
        "soundcloud_cookies.txt", "sc_cookies.txt"
    ),
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

URL_RE = re.compile(r"https?://\S+")

# 🩺 Production-hardening (بازبینی کامل دانلودر):
#   - JOB_TIMEOUT_SEC: هر Job حداکثر این‌قدر وقت داره؛ اگه دانلود گیر کرد (نه
#     Exception بده، نه تموم بشه)، به‌جای اینکه برای همیشه یه Thread رو اشغال
#     کنه، با Timeout واضح قطع می‌شه و کاربر پیام روشن می‌گیره.
#   - MAX_TELEGRAM_UPLOAD_BYTES: همون سقفی که تو _base_ydl_opts هم به yt-dlp
#     داده می‌شه؛ برای چک زودهنگام حجم (قبل از شروع دانلود واقعی) هم استفاده می‌شه.
#   - NETWORK_RETRY_DELAYS: فقط برای خطاهای واقعاً موقت (شبکه/Timeout/۵xx) Retry
#     با Backoff انجام می‌شه؛ خطاهای دائمی (Private/Deleted/Invalid) هرگز Retry نمی‌شن.
# 🚨 رفع باگ «ویدیوهای طولانی/حجیم سیاه و 00:00 می‌شوند»: قبلاً این عدد ثابت
# ۲۴۰ ثانیه بود که برای فایل‌های حجیم (چند صدمگابایتی/چندگیگابایتی) روی شبکه‌ی
# متوسط به‌راحتی کم میاد؛ وقتی دانلود واقعی هنوز تموم نشده و Timeout می‌خوره،
# Job لغو می‌شه ولی Thread پس‌زمینه (yt-dlp) هنوز داره می‌نویسه — دقیقاً منشأ
# فایل‌های ناقص/نیمه‌نوشته که بعداً (تو تلاش بعدی یا از قبل موجود در دیسک)
# باعث duration=00:00 و Preview سیاه می‌شن. عدد رو بزرگ‌تر کردیم (نه بی‌نهایت)
# تا فایل‌های حجیم/طولانی هم فرصت کامل‌شدن داشته باشن.
JOB_TIMEOUT_SEC = 600
MAX_TELEGRAM_UPLOAD_BYTES = 49 * 1024 * 1024
NETWORK_RETRY_DELAYS = (2, 5)

# 🦇 محدودسازی هم‌زمانیِ دانلود واقعی (Concurrency/Queue — چک‌لیست #21):
# قبلاً هیچ سقفی برای تعداد دانلودهای هم‌زمان (بین همه‌ی کاربران/پلتفرم‌ها)
# وجود نداشت؛ چند دانلود سنگین هم‌زمان (چند کاربر + چند فرگمنت موازی هرکدوم)
# می‌تونست RAM/CPU روی Railway رو کامل اشغال کنه. این Semaphore سراسری فقط
# دور *اجرای واقعی* yt-dlp (تو _download_with_retry) رو می‌گیره — نه پروب
# متادیتا (probe_youtube_qualities/_yt_dlp_probe) که سبک و بی‌خطره. اگه ظرفیت
# پر باشه، Job جدید بی‌صدا صبر می‌کنه تا یکی آزاد بشه (نه رد می‌شه، نه خطا
# می‌ده) — دقیقاً همون رفتار «Queue» که چک‌لیست خواسته، بدون نیاز به پیاده‌سازی
# یه سیستم صف جداگانه که با معماری فعلی (asyncio + per-job tempdir) رقابت کنه.
MAX_CONCURRENT_DOWNLOADS = 3
_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# 🧪 آیتم ۳ چک‌لیست («تست بسیار مهم Telegram»): وقتی این env var روی "1" ست
# بشه، بعد از ارسال موفق FINAL FILE به‌صورت send_video، دقیقاً همون فایل
# یک‌بار دیگه هم به‌صورت send_document فرستاده می‌شه — تا خودِ توسعه‌دهنده
# بتونه موقع دیباگ زنده مقایسه کنه: اگه Document سالمه ولی Video سیاه/۰۰:۰۰
# بود، مشکل از send_video/thumbnail/metadata تلگرامه نه از خودِ فایل؛ اگه
# Document هم خراب بود، مشکل از خودِ Pipeline دانلود/FFmpeg است.
# پیش‌فرض خاموشه (کاربر عادی هیچ فایل تکراری نمی‌بینه)؛ فقط برای دیباگ دستی
# با ست‌کردن env var DL_DEBUG_COMPARE_UPLOAD=1 روشن می‌شه.
DEBUG_COMPARE_UPLOAD = os.getenv("DL_DEBUG_COMPARE_UPLOAD") == "1"

DOWNLOADER_HELP_TEXT = (
    "📥 دانلودر — بنویس «دانلودر»، پلتفرم (اینستاگرام / یوتیوب / تیک‌تاک / ایکس / "
    "پینترست / ساندکلاود) رو با دکمه انتخاب کن، بعد لینک رو همونجا بفرست.\n"
    "حجم فایل همیشه تو کپشن نشون داده می‌شه.\n"
)


def _dl_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(PLATFORM_LABELS["instagram"], callback_data="dl:pick:instagram"),
         InlineKeyboardButton(PLATFORM_LABELS["youtube"], callback_data="dl:pick:youtube")],
        [InlineKeyboardButton(PLATFORM_LABELS["tiktok"], callback_data="dl:pick:tiktok"),
         InlineKeyboardButton(PLATFORM_LABELS["twitter"], callback_data="dl:pick:twitter")],
        [InlineKeyboardButton(PLATFORM_LABELS["pinterest"], callback_data="dl:pick:pinterest"),
         InlineKeyboardButton(PLATFORM_LABELS["soundcloud"], callback_data="dl:pick:soundcloud")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ])


def _dl_after_pick_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 انتخاب پلتفرم دیگه", callback_data="panel:downloader")],
    ])


# نام عمومی (بدون آندرلاین) که ماژول‌های دیگه (مثلاً bot.py برای پنل اصلی) می‌تونن
# مستقیم importش کنن، بدون اینکه به جزئیات داخلی این ماژول وابسته باشن.
dl_menu_markup = _dl_menu_markup


def _human_size(num_bytes):
    if not num_bytes:
        return "نامشخص"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# =========================================================
#  پینترست — استخراج مستقیم به‌جای yt-dlp
# =========================================================
# روش پینترست تو yt-dlp قدیمیه و اغلب یا هیچی برنمی‌گردونه یا فقط کیفیت پایین.
# این تابع همون تکنیکی رو پیاده می‌کنه که بات‌های اختصاصی پینترست استفاده می‌کنن:
# ۱) اگه لینک کوتاه pin.it باشه، ریدایرکت رو دنبال می‌کنیم تا لینک اصلی pin/<id>
#    به‌دست بیاد.
# ۲) اول resource API خود پینترست (همون APIای که سایتش برای لود کردن پین ازش
#    استفاده می‌کنه) رو صدا می‌زنیم — بهترین کیفیت ویدیو/عکس اورجینال رو می‌ده.
# ۳) اگه جواب نداد، خود صفحه‌ی HTML پین رو می‌گیریم و لینک ویدیو/عکس رو باهاش
#    regex پیدا می‌کنیم.
# اگه هر دو شکست خوردن، کد صدا زننده به yt-dlp به‌عنوان آخرین راه برمی‌گرده.

_PIN_ID_RE = re.compile(r"/pin/(\d+)")


async def _resolve_pinterest_pin_url(url: str, client: httpx.AsyncClient) -> str:
    if "pin.it" in url.lower():
        try:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=15)
            return str(resp.url)
        except Exception:
            return url
    return url


async def _pinterest_resource_api(pin_id: str, client: httpx.AsyncClient):
    payload = {"options": {"id": pin_id, "field_set_key": "unauth_react_main_pin"}, "context": {}}
    params = {"data": json.dumps(payload)}
    headers = {
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*, q=0.01",
        "Referer": "https://www.pinterest.com/",
    }
    resp = await client.get(
        "https://www.pinterest.com/resource/PinResource/get/",
        params=params, headers=headers, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    pin = (data.get("resource_response") or {}).get("data") or {}
    if not pin:
        return None

    videos = pin.get("videos")
    if videos:
        video_list = (videos.get("video_list") or {})
        if video_list:
            best = max(video_list.values(), key=lambda v: v.get("width", 0))
            return {"url": best["url"], "is_video": True, "title": pin.get("title") or pin.get("grid_title") or ""}

    images = pin.get("images") or {}
    orig = images.get("orig") or {}
    if orig.get("url"):
        return {"url": orig["url"], "is_video": False, "title": pin.get("title") or pin.get("grid_title") or ""}

    return None


async def _pinterest_html_scrape(pin_url: str, client: httpx.AsyncClient):
    resp = await client.get(pin_url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20)
    resp.raise_for_status()
    html = resp.text

    m = re.search(r'https?:\\/\\/v1\.pinimg\.com\\/videos\\/[^"\\]+\.mp4', html)
    if m:
        return {"url": m.group(0).replace("\\/", "/"), "is_video": True, "title": ""}
    m = re.search(r'https?://v1\.pinimg\.com/videos/[^"\s]+\.mp4', html)
    if m:
        return {"url": m.group(0), "is_video": True, "title": ""}

    m = re.search(r'https?:\\/\\/i\.pinimg\.com\\/originals\\/[^"\\]+\.(?:jpg|jpeg|png|gif)', html)
    if m:
        return {"url": m.group(0).replace("\\/", "/"), "is_video": False, "title": ""}
    m = re.search(r'https?://i\.pinimg\.com/originals/[^"\s]+\.(?:jpg|jpeg|png|gif)', html)
    if m:
        return {"url": m.group(0), "is_video": False, "title": ""}

    return None


async def _pinterest_extract(url: str):
    """امتحان می‌کنه ویدیو/عکس رو مستقیم از پینترست دربیاره. اگه هر دو روش
    شکست خورد، None برمی‌گردونه تا صدا زننده به yt-dlp برگرده."""
    async with httpx.AsyncClient() as client:
        try:
            pin_url = await _resolve_pinterest_pin_url(url, client)
        except Exception:
            pin_url = url

        m = _PIN_ID_RE.search(pin_url)
        if m:
            try:
                result = await _pinterest_resource_api(m.group(1), client)
                if result:
                    return result
            except Exception as e:
                log.info(f"pinterest resource API failed: {e}")

        try:
            result = await _pinterest_html_scrape(pin_url, client)
            if result:
                return result
        except Exception as e:
            log.info(f"pinterest html scrape failed: {e}")

    return None


async def _download_direct_url(media_url: str, outdir: str, is_video: bool, progress_state=None) -> str:
    """استریم مستقیم روی دیسک (نه تو RAM) — با فایل موقت + rename نهایی، تا اگه
    دانلود وسط راه قطع شد، یه فایل ناقص هیچ‌وقت به‌عنوان فایل کامل شناخته نشه."""
    ext = ".mp4" if is_video else (os.path.splitext(media_url.split("?")[0])[1] or ".jpg")
    filepath = os.path.join(outdir, f"pin{ext}")
    tmp_path = filepath + ".part"
    downloaded = 0
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", media_url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length") or 0) or None
                if progress_state is not None:
                    progress_state["status"] = "downloading"
                    progress_state["total"] = total or 0
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 64):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_state is not None:
                            progress_state["downloaded"] = downloaded
        if downloaded == 0:
            raise RuntimeError("empty response body (0 bytes downloaded)")
        os.replace(tmp_path, filepath)  # rename نهایی فقط بعد از موفقیت کامل
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise
    if progress_state is not None:
        progress_state["status"] = "processing"
    return filepath


async def downloader_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📥 دانلودر گاتهام\n\nاول پلتفرم رو انتخاب کن، بعد لینک رو همینجا بفرست.",
        reply_markup=_dl_menu_markup(),
    )


async def downloader_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    platform = q.data.split(":")[2]
    PENDING_DL[q.from_user.id] = platform
    await q.edit_message_text(
        f"{PLATFORM_LABELS[platform]} انتخاب شد ✅\n🔗 حالا لینک رو همینجا بفرست تا برات دانلودش کنم.",
        reply_markup=_dl_after_pick_markup(),
    )
    await q.answer()


_FFMPEG_BIN = shutil.which("ffmpeg")

# 🐛 باگ اصلی که پیدا شد: فرمت "best[ext=mp4]/best" فقط دنبال یه فرمت
# progressive (صدا+تصویر از قبل توی یه فایل) می‌گرده. یوتیوب برای اکثر
# ویدیوهای بالای ۳۶۰p دیگه همچین فرمتی نداره (صدا و تصویر جدا سرو می‌شن)،
# پس این selector یا کیفیت خیلی پایین برمی‌گردوند یا اصلاً هیچ فرمتی پیدا
# نمی‌کرد → دقیقاً همون «یوتیوب اصلاً کار نمی‌کنه». Fix: به yt-dlp اجازه
# می‌دیم بهترین ویدیو + بهترین صدا رو جدا بگیره و با ffmpeg merge کنه
# (merge_output_format=mp4)، با چند سطح Fallback تا هیچ‌وقت درخواست فرمتی
# که اصلاً وجود نداره باعث شکست کامل نشه.
_YOUTUBE_FORMAT = (
    "bestvideo[ext=mp4][filesize<{cap}]+bestaudio[ext=m4a]/"
    "bestvideo+bestaudio/"
    "best[ext=mp4]/best"
).format(cap=MAX_TELEGRAM_UPLOAD_BYTES)

_DEFAULT_FORMAT = "best[ext=mp4]/best"

# 🦇 GOTHAM FAST YOUTUBE DOWNLOADER — MAX SPEED MODE
# فقط برای مسیر سریع یوتیوب (بدون منوی کیفیت): به‌جای bestvideo+bestaudio که
# نیاز به merge با ffmpeg داره، فقط دنبال یه فرمت از قبل آماده (صدا+تصویر تو
# یه فایل واحد، بدون نیاز به Merge/Post-processing) می‌گرده. این یعنی صفر
# مرحله‌ی اضافه بین «دانلود» و «ارسال» — دقیقاً هدف Max Speed Mode. ممکنه سقف
# رزولوشن پایین‌تر از حالت merge باشه (چون فرمت‌های Progressive یوتیوب معمولاً
# حداکثر ۷۲۰p هستن)، ولی سرعت مهم‌تر از کیفیته.
_YOUTUBE_FAST_FORMAT = (
    f"best[ext=mp4][filesize<{MAX_TELEGRAM_UPLOAD_BYTES}]/"
    f"best[filesize<{MAX_TELEGRAM_UPLOAD_BYTES}]/"
    f"best[ext=mp4]/best"
)


def _format_selector_for_quality(quality) -> str:
    """🦇 PHASE 2/3: format selector یوتیوب بر اساس کیفیت انتخابیِ کاربر.

    quality می‌تونه یکی از این‌ها باشه:
        - "audio"   -> بهترین صدای خام (بدون Re-encode، دقیقاً همون الگویی
                       که همین الان برای SoundCloud استفاده می‌شه — سازگار
                       با معماری فعلی، بدون نیاز به postprocessor جدید).
        - عدد (360/480/720/1080) -> بهترین ویدیو تا همون ارتفاع + بهترین
          صدا، با همون زنجیره‌ی fallback سه‌سطحیِ الگوی فعلی (_YOUTUBE_FORMAT)
          ولی این‌بار سقف‌خورده به height به‌جای اینکه فقط filesize محدودش کنه.
    اگه quality چیزی غیر از این دو حالت باشه (یا None)، این تابع اصلاً صدا
    زده نمی‌شه — فراخوان (پایین‌تر) در اون حالت از همون _YOUTUBE_FORMAT
    پیش‌فرض استفاده می‌کنه، دقیقاً رفتار قبل از Phase 2."""
    if quality == "audio":
        return "bestaudio/best"
    if quality == "fast":
        # 🦇 GOTHAM FAST MODE — رجوع کن به توضیح بالای _YOUTUBE_FAST_FORMAT.
        return _YOUTUBE_FAST_FORMAT
    if quality == "best":
        # ⚡ «بهترین کیفیت»: همون زنجیره‌ی پیش‌فرض فعلی (_YOUTUBE_FORMAT) —
        # بهترین ویدیو+صدای موجود، با همون سقف MAX_TELEGRAM_UPLOAD_BYTES و
        # همون سه‌سطح Fallback. یه انتخاب جدا نیست، فقط اسمِ صریح روی همون
        # رفتاریه که «quality=None» قبلاً بی‌صدا انجام می‌داد — این‌جوری دکمه‌ی
        # «بهترین کیفیت» تو منو هم از همون مسیر امن و تست‌شده استفاده می‌کنه.
        return _YOUTUBE_FORMAT
    height = int(quality)
    return (
        f"bestvideo[height<={height}][ext=mp4][filesize<{MAX_TELEGRAM_UPLOAD_BYTES}]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]/best"
    )


def _base_ydl_opts(outdir: str, platform: str, quality=None) -> dict:
    """🦇 پارامتر quality (Phase 2/3) کاملاً اختیاریه — پیش‌فرضش None است،
    یعنی همه‌ی فراخوان‌های فعلی/قبلی این تابع (بدون دادن این آرگومان) دقیقاً
    همون رفتار قبل از Phase 2 رو دارن، بدون هیچ تغییری."""
    opts = {
        "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
        "format": (
            _format_selector_for_quality(quality)
            if (platform == "youtube" and quality is not None)
            else (_YOUTUBE_FORMAT if platform == "youtube" else _DEFAULT_FORMAT)
        ),
        "quiet": True,
        "no_warnings": True,
        # noplaylist=True یعنی «فقط یه آیتم رو بگیر، نه کل لیست». برای یوتیوب لازمه
        # (وگرنه لینکِ یه ویدیوی داخل Playlist ممکنه کل Playlist رو دانلود کنه)، ولی
        # همین تنظیم رو اینستاگرام/توییتر باعث می‌شد Carousel (چند عکس/ویدیو تو یه
        # پست) یا چند-مدیای یه توییت فقط اسلاید/مدیای اول دانلود بشه. این‌جا فقط
        # برای instagram/twitter noplaylist رو False می‌ذاریم تا همه‌ی entryهای یه
        # پست/توییت دانلود بشن؛ روی یه پست تک‌مدیا این تنظیم هیچ اثری نداره.
        "noplaylist": platform not in ("instagram", "twitter"),
        "max_filesize": MAX_TELEGRAM_UPLOAD_BYTES,  # سقف آپلود بات‌های تلگرام
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {"User-Agent": USER_AGENT},
        "geo_bypass": True,
    }
    if platform == "youtube":
        # 🐛 رفع «پلتفرم فایل خروجی معتبری برنگردوند»: merge_output_format فقط
        # وقتی ست می‌شه که ffmpeg واقعاً در دسترسه. قبلاً merge_output_format
        # همیشه ست می‌شد، حتی بدون ffmpeg؛ تو اون حالت yt-dlp merge رو سایلنت
        # Skip می‌کنه و بسته به فرمت انتخابی ممکنه فقط جریان تصویر (بدون صدا)
        # یا هیچ فایل واحد قابل‌شناساییِ نهایی روی دیسک نمونه — دقیقاً همون
        # خطایی که کاربر می‌دید. بدون ffmpeg، format رو به یه گزینه‌ی
        # progressive (صدا+تصویر از قبل تو یه فایل، بدون نیاز به merge) محدود
        # می‌کنیم تا حداقل یه فایل واحد و سالم (هرچند کیفیت پایین‌تر) بشه.
        if _FFMPEG_BIN:
            opts["merge_output_format"] = "mp4"
            opts["ffmpeg_location"] = _FFMPEG_BIN
        else:
            # 🦇 بدون ffmpeg نمی‌شه merge کرد؛ اگه کیفیت مشخصی خواسته شده،
            # همون سقف height رو روی فرمت progressive هم حفظ می‌کنیم (نه
            # اینکه بی‌قید و شرط به «best» برگردیم و انتخاب کاربر نادیده
            # گرفته بشه). برای quality=None دقیقاً رفتار قبلی حفظ شده.
            if quality == "fast":
                # بدون ffmpeg هم مسیر سریع نیازی به merge نداره — همون فرمت
                # آماده‌ی progressive استفاده می‌شه.
                opts["format"] = _YOUTUBE_FAST_FORMAT
            elif quality is not None and quality not in ("audio", "best"):
                opts["format"] = f"best[height<={int(quality)}][ext=mp4]/best[height<={int(quality)}]/best[ext=mp4]/best"
            else:
                opts["format"] = "best[ext=mp4]/best"
        # ⚡ سرعت یوتیوب: فرمت‌های DASH (بالای ۳۶۰p) به‌صورت چندتکه (fragment)
        # سرو می‌شن؛ پیش‌فرض yt-dlp این تکه‌ها رو یکی‌یکی و پشت‌سرهم دانلود
        # می‌کنه. با دانلود موازیِ چند فرگمنت هم‌زمان، سرعت واقعی دانلود (نه
        # کیفیت، نه فرمت) به‌طور محسوس بالا می‌ره، بدون اینکه هیچ رفتار دیگه‌ای
        # عوض بشه.
        opts["concurrent_fragment_downloads"] = 4
    cookies_file = COOKIES_FILES.get(platform)
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file
    return opts


_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".ts")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".wav")

# 🐛 رفع باگ «اینستاگرام یه Image URL می‌ده ولی Downloader اشتباهاً Video یا
# فایل نامعتبر تشخیصش می‌ده»: قبلاً تشخیص عکس *فقط* از روی پسوند فایل بود
# (`ext in (".jpg", ".jpeg", ".png", ".webp")`) — اگه yt-dlp/اینستاگرام یه
# عکس رو با پسوند غیرمعمول یا بدون پسوند مشخص می‌نوشت، همون else-branch
# قدیمی بدون چک اضافه به‌عنوان Video فرستاده می‌شد (خطای تلگرام یا فایل
# نامعتبر). این تابع دقیقاً طبق درخواست («Content-Type/Container را هم
# بررسی کن، نه فقط Extension») یه لایه‌ی Fallback اضافه می‌کنه:
#   ۱. اول پسوندهای شناخته‌شده (سریع، بدون هیچ I/O اضافه — رفتار قبلی حفظ).
#   ۲. اگه پسوند ناشناخته بود، mimetypes (از روی همون اسم فایل) چک می‌شه.
#   ۳. اگه بازم معلوم نشد، بایت‌های اول فایل (Magic Number واقعی JPEG/PNG/
#      WEBP/GIF) خونده می‌شه — این دقیقاً «Container واقعی» رو چک می‌کنه، نه
#      اسم فایل. این I/O فقط برای پسوندهای ناشناخته انجام می‌شه، پس هیچ
#      overhead ای برای حالت عادی (jpg/mp4 و...) اضافه نمی‌کنه — سرعت حفظ می‌شه.
def _looks_like_image(filepath: str, ext: str) -> bool:
    if ext in _IMAGE_EXTS:
        return True
    if ext in _VIDEO_EXTS or ext in _AUDIO_EXTS:
        return False
    guessed, _ = mimetypes.guess_type(filepath)
    if guessed:
        if guessed.startswith("image/"):
            return True
        if guessed.startswith("video/") or guessed.startswith("audio/"):
            return False
    try:
        with open(filepath, "rb") as f:
            head = f.read(16)
    except Exception:
        return False
    if head.startswith(b"\xff\xd8\xff"):
        return True  # JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True  # PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True  # WEBP
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return True  # GIF (تلگرام این رو هم به‌عنوان عکس قبول می‌کنه)
    return False

_FFMPEG_OK = shutil.which("ffmpeg") is not None
_FFPROBE_OK = shutil.which("ffprobe") is not None
if not (_FFMPEG_OK and _FFPROBE_OK):
    log.warning(
        "ffmpeg/ffprobe پیدا نشد — رفع باگ «۰۰:۰۰ و صفحه سیاه» ویدیوهای اینستاگرام "
        "غیرفعال می‌مونه (ویدیو خام و بدون remux فرستاده می‌شه). "
        "روی Railway، nixPkgs = [\"ffmpeg\"] تو nixpacks.toml باید همینو حل کنه."
    )


def _ffprobe_json(filepath: str, _rc_out: dict = None):
    """بلاک‌کننده‌ست — با asyncio.to_thread صدا زده می‌شه.
    خروجی ffprobe رو به‌صورت dict برمی‌گردونه، یا None اگه فایل اصلاً قابل‌خوندن نبود.

    🩺 چک‌لیست آیتم ۱ (Diagnostic): تا قبل از این، exit code واقعیِ ffprobe
    هیچ‌جا ثبت نمی‌شد — فقط "موفق شد یا نه" (True/False) لاگ می‌شد. اگه
    _rc_out (یه dict خالی) داده بشه، exit code واقعی (یا شرح Exception، اگه
    ffprobe اصلاً اجرا نشد) توش پر می‌شه تا فراخوان بتونه دقیقاً همون رو تو
    لاگ هر Stage ثبت کنه — نه فقط true/false."""
    if not _FFPROBE_OK:
        if _rc_out is not None:
            _rc_out["returncode"] = "ffprobe-not-installed"
        return None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", filepath],
            capture_output=True, text=True, timeout=30,
        )
        if _rc_out is not None:
            _rc_out["returncode"] = proc.returncode
        if proc.returncode != 0 or not proc.stdout:
            return None
        return json.loads(proc.stdout)
    except Exception as e:
        if _rc_out is not None:
            _rc_out["returncode"] = f"exception:{e}"
        log.info(f"ffprobe failed for {filepath}: {e}")
        return None


def _video_meta(probe):
    """duration (float یا None), width, height رو از خروجی ffprobe استخراج می‌کنه."""
    if not probe:
        return None, None, None
    duration = None
    fmt = probe.get("format") or {}
    try:
        duration = float(fmt["duration"]) if fmt.get("duration") else None
    except (TypeError, ValueError):
        duration = None
    width = height = None
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            width, height = s.get("width"), s.get("height")
            if not duration and s.get("duration"):
                try:
                    duration = float(s["duration"])
                except (TypeError, ValueError):
                    pass
            break
    return duration, width, height


def _probe_diagnostics(filepath: str, probe=None, ffprobe_rc=None) -> dict:
    """طبق چک‌لیست (آیتم ۴۵): برای هر مرحله از Pipeline خلاصه‌ی کامل تشخیصی
    می‌سازه — file size, duration, width, height, video codec, audio codec,
    container, pixel format, stream count, و این‌که ffprobe اصلاً موفق بود یا نه.
    این تابع فقط dict رو می‌سازه (بدون خودِ ffprobe زدن، مگر probe داده نشده
    باشه) تا بشه یه probe رو هم برای duration/width/height و هم برای لاگ
    استفاده کرد — بدون این‌که برای هر مرحله دوبار ffprobe صدا زده بشه."""
    try:
        size = os.path.getsize(filepath)
    except OSError:
        size = None
    if probe is None:
        rc_holder = {}
        probe = _ffprobe_json(filepath, _rc_out=rc_holder)
        ffprobe_rc = rc_holder.get("returncode")
    diag = {
        "file": os.path.basename(filepath), "size": size,
        "ffprobe_ok": probe is not None,
        "ffprobe_exit_code": ffprobe_rc,
        "duration": None, "width": None, "height": None,
        "container": None, "vcodec": None, "acodec": None,
        "pix_fmt": None, "stream_count": None, "has_audio": None,
    }
    if probe is None:
        return diag
    fmt = probe.get("format") or {}
    diag["container"] = fmt.get("format_name")
    try:
        diag["duration"] = float(fmt["duration"]) if fmt.get("duration") else None
    except (TypeError, ValueError):
        pass
    streams = probe.get("streams", []) or []
    diag["stream_count"] = len(streams)
    diag["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
    for s in streams:
        if s.get("codec_type") == "video" and diag["vcodec"] is None:
            diag["vcodec"] = s.get("codec_name")
            diag["pix_fmt"] = s.get("pix_fmt")
            diag["width"], diag["height"] = s.get("width"), s.get("height")
            if not diag["duration"] and s.get("duration"):
                try:
                    diag["duration"] = float(s["duration"])
                except (TypeError, ValueError):
                    pass
        elif s.get("codec_type") == "audio" and diag["acodec"] is None:
            diag["acodec"] = s.get("codec_name")
    return diag


def _log_stage(stage: str, filepath: str, job_id: str = None):
    """بلاک‌کننده — تو asyncio.to_thread صدا زده بشه (از داخل توابع بلاک‌کننده‌ی
    دیگه صدا زده می‌شه، خودش asyncio نمی‌شناسه).

    پیاده‌سازی مستقیم درخواست Audit: قبل/بعد هر مرحله‌ی Pipeline
    (RAW DOWNLOAD → REMUX → RE-ENCODE → FINAL) دقیقاً همون فیلدهایی که خواسته
    شده (file size, duration, width, height, video codec, audio codec,
    container, pixel format, stream count, ffprobe exit/readability) رو لاگ
    می‌کنه — تا اگه باگ دوباره رخ داد، از روی لاگ Job دقیقاً مشخص باشه کدوم
    مرحله مقصره، نه حدس زدن.
    خروجی: (diag_dict, raw_probe_dict_یا_None) — probe خام هم برمی‌گرده تا
    فراخوان مجبور نباشه دوباره ffprobe بزنه."""
    rc_holder = {}
    probe = _ffprobe_json(filepath, _rc_out=rc_holder)
    diag = _probe_diagnostics(filepath, probe, ffprobe_rc=rc_holder.get("returncode"))
    prefix = f"[dl:{job_id}] " if job_id else ""
    log.info(f"{prefix}STAGE={stage} {diag}")
    return diag, probe


def _validate_media_file(filepath: str):
    """بلاک‌کننده — تو asyncio.to_thread صدا زده بشه.

    🔴 این تابع تنها Gate واقعی قبل از ارسال به تلگرامه (نه thumbnail، نه
    هیچ‌چیز دیگه). طبق چک‌لیست آیتم‌های ۷ تا ۱۰:
        - فایل باید وجود داشته باشه و zero-byte نباشه.
        - ffprobe باید container رو با موفقیت بخونه.
        - برای ویدیو: حتماً یه video stream با codec مشخص، width/height
          معتبر (>0)، و duration > 0 داشته باشه — دقیقاً همون سه چیزی که
          نبودشون باعث «۰۰:۰۰ / صفحه سیاه» می‌شه.
        - اگه audio stream هم وجود داره، باید codec آن مشخص/سالم باشه
          (stream صوتی با codec نامشخص یعنی merge/demux ناقص بوده).
    اگه هرکدوم fail بشه، فایل INVALID اعلام می‌شه — و هیچ‌جای این ماژول
    اجازه نداره به‌جای فایل رد‌شده، فایل خامِ اصلاح‌نشده رو «سالم» فرض کنه؛
    قبلاً duration=None از این گیت رد می‌شد چون فقط readability چک می‌شد،
    نه خودِ duration/ابعاد — این دقیقاً همون سوراخی بود که فایل خراب از توش
    به تلگرام می‌رفت.
    خروجی: (ok: bool, دلیل_فارسی_یا_None)"""
    if not filepath or not os.path.exists(filepath):
        return False, "فایل خروجی روی دیسک پیدا نشد."
    size = os.path.getsize(filepath)
    if size == 0:
        return False, "فایل خروجی صفر بایت است (دانلود ناقص)."

    ext = os.path.splitext(filepath)[1].lower()
    is_video = ext in _VIDEO_EXTS
    is_audio_only = ext in (".mp3", ".m4a", ".opus", ".ogg", ".wav")
    if not (is_video or is_audio_only):
        return True, None  # عکس و مشابه — نیاز به ffprobe نداره

    if not _FFPROBE_OK:
        # بدون ffprobe نصب‌شده نمی‌شه duration/stream رو تضمین کرد. این حالت
        # از قبل تو لاگِ startup هشدار داده شده (ffmpeg/ffprobe پیدا نشد)؛
        # این‌جا فقط اجازه می‌دیم رد بشه تا کل دانلودر بی‌دلیل از کار نیفته —
        # ولی این یعنی محافظت در برابر باگ ۰۰:۰۰ عملاً غیرفعاله.
        log.warning(f"validate: ffprobe not installed, skipping strict checks for {filepath!r}")
        return True, None

    probe = _ffprobe_json(filepath)
    if probe is None:
        return False, "container فایل توسط ffprobe قابل‌خواندن نیست."
    streams = probe.get("streams", []) or []
    if not streams:
        return False, "هیچ stream صوتی/تصویری در فایل پیدا نشد."

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if is_video:
        if not video_streams:
            return False, "فایل ویدیویی فاقد stream تصویری است."
        vs = video_streams[0]
        if not vs.get("codec_name"):
            return False, "codec ویدیو نامشخص/خراب است."
        width, height = vs.get("width"), vs.get("height")
        if not width or not height or width <= 0 or height <= 0:
            return False, "ابعاد ویدیو (width/height) نامعتبر است."
        duration, _, _ = _video_meta(probe)
        if not duration or duration <= 0:
            return False, "duration ویدیو صفر/نامعتبر است (همون باگ ۰۰:۰۰)."
        for a in audio_streams:
            if not a.get("codec_name"):
                return False, "stream صوتی موجود ولی codec آن خراب/نامشخص است."
    else:  # صوتی محض (ساندکلاود و مشابه)
        if not audio_streams:
            return False, "فایل صوتی فاقد stream صوتی سالم است."
        if not audio_streams[0].get("codec_name"):
            return False, "codec صوتی نامشخص/خراب است."

    return True, None


def _log_ffmpeg_failure(op: str, filepath: str, proc=None, exc=None, timeout=None):
    """چک‌لیست آیتم ۴۷: هر شکست FFmpeg باید با exit code/stderr واقعی لاگ بشه،
    نه سایلنت. قبلاً وقتی FFmpeg با returncode != 0 (نه Exception) شکست
    می‌خورد، هیچ لاگی ثبت نمی‌شد و علت شکست غیرقابل‌ردیابی بود — دقیقاً همون
    چیزی که برای ریشه‌یابی باگ ۰۰:۰۰/سیاه لازمه."""
    try:
        size = os.path.getsize(filepath)
    except OSError:
        size = "N/A"
    if exc is not None:
        log.warning(f"{op} EXCEPTION file={filepath!r} size={size} timeout={timeout} err={exc}")
    elif proc is not None:
        log.warning(
            f"{op} FAILED file={filepath!r} size={size} timeout={timeout} "
            f"rc={proc.returncode} stderr_tail={(proc.stderr or '')[-800:]!r}"
        )


def _extract_audio_track(filepath: str, job_id: str = None):
    """بلاک‌کننده‌ست — تو asyncio.to_thread صدا زده بشه.

    🎵 برای «Instagram Video → Audio» و «TikTok Audio» (هر دو الزامی طبق
    درخواست): صدای اصلیِ ویدیوی دانلودشده رو استخراج می‌کنه، دقیقاً طبق قانون
    «Remux رو به Re-encode ترجیح بده / تبدیل غیرضروری انجام نده»:
        ۱. اول Stream-Copy (بدون Re-encode) به‌عنوان .m4a امتحان می‌شه — اکثر
           ویدیوهای این پلتفرم‌ها صدای AAC دارن که مستقیم تو container .m4a
           جا می‌شه، بدون هیچ افت کیفیت/زمان اضافه.
        ۲. فقط اگه Copy شکست خورد (Codec صوتی با .m4a سازگار نبود — مثلاً
           Opus)، به mp3 (libmp3lame) Re-encode می‌شیم — تنها موقعی که واقعاً
           لازمه.
    خروجی: مسیر فایل صوتی، یا None اگه ffmpeg نصب نباشه یا هر دو روش شکست بخورن."""
    if not _FFMPEG_BIN:
        return None
    base, _ = os.path.splitext(filepath)

    copy_path = base + "_audio.m4a"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-vn", "-acodec", "copy", copy_path],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode == 0 and os.path.exists(copy_path) and os.path.getsize(copy_path) > 0:
            return copy_path
        _log_ffmpeg_failure("audio-extract-copy", filepath, proc=proc, timeout=180)
    except Exception as e:
        _log_ffmpeg_failure("audio-extract-copy", filepath, exc=e, timeout=180)
    if os.path.exists(copy_path):
        try:
            os.remove(copy_path)
        except Exception:
            pass

    mp3_path = base + "_audio.mp3"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-vn", "-acodec", "libmp3lame", "-b:a", "192k", mp3_path],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode == 0 and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            return mp3_path
        _log_ffmpeg_failure("audio-extract-mp3", filepath, proc=proc, timeout=180)
    except Exception as e:
        _log_ffmpeg_failure("audio-extract-mp3", filepath, exc=e, timeout=180)
    if os.path.exists(mp3_path):
        try:
            os.remove(mp3_path)
        except Exception:
            pass
    return None


def _remux_faststart(filepath: str):
    """بلاک‌کننده‌ست. Remux سریع (بدون Re-encode، فقط -c copy) با +faststart تا
    moov atom بیاد اول فایل و duration/metadata درست تشخیص داده بشه. اگه موفق
    نشد None برمی‌گردونه (نه Exception) تا فراخوان بره سراغ Re-encode.

    🚨 رفع باگ ویدیوهای «طولانی/حجیم»: قبلاً Timeout این مرحله ثابت روی ۱۲۰
    ثانیه بود. Remux با -c copy تقریباً کاملاً I/O-bound‌ه (فقط بایت‌ها رو
    کپی می‌کنه، نه Re-encode)، پس زمان لازمش با حجم فایل رشد می‌کنه — روی
    دیسک/شبکه‌ی کند یه فایل چندصدمگابایتی/چندگیگابایتی به‌راحتی از ۱۲۰ ثانیه
    بیشتر طول می‌کشه. وقتی Timeout می‌خورد، فراخوان (بی‌خبر از علت واقعی) به
    فایل خام (با moov atom خراب) سقوط می‌کرد — دقیقاً همون Root Cause ۰۰:۰۰/
    سیاه برای فایل‌های حجیم. حالا Timeout متناسب با حجم فایل محاسبه می‌شه."""
    if not _FFMPEG_OK:
        return None
    base, _ = os.path.splitext(filepath)
    out_path = base + "_fx.mp4"
    try:
        size = os.path.getsize(filepath)
    except OSError:
        size = 0
    # حداقل ۱۸۰ ثانیه، به‌علاوه‌ی زمان کپی با فرض حداقل ۲ مگابایت/ثانیه throughput
    # دیسک (خیلی محافظه‌کارانه برای هاست‌های کم‌منبع)، با سقف بالا برای جلوگیری
    # از Job که هیچ‌وقت تموم نشه.
    timeout = min(1800, max(180, int(size / (2 * 1024 * 1024))))
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath, "-c", "copy", "-movflags", "+faststart", out_path],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        _log_ffmpeg_failure("faststart-remux", filepath, proc=proc, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        _log_ffmpeg_failure("faststart-remux", filepath, exc=e, timeout=timeout)
    except Exception as e:
        _log_ffmpeg_failure("faststart-remux", filepath, exc=e, timeout=timeout)
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except Exception:
            pass
    return None


def _reencode_video(filepath: str, expected_duration=None):
    """بلاک‌کننده‌ست. فقط وقتی صدا زده می‌شه که remux ساده کافی نبوده (نادر —
    مثلاً استریم‌های ناجور/خراب منبع). کیفیت رو تا حد امکان حفظ می‌کنه
    (CRF ثابت به‌جای بیت‌ریت پایین‌ی ثابت) تا حجم و کیفیت بی‌دلیل بد نشه.

    🚨 رفع باگ ویدیوهای «طولانی»: Re-encode برخلاف Remux به‌شدت CPU-bound‌ه.
    Timeout ثابت قبلی (۳۰۰ ثانیه) برای یه ویدیوی طولانی (مثلاً ۲۰-۳۰ دقیقه‌ای)
    روی CPU مشترک/ضعیفِ هاست (حالت رایج Railway/سرورهای کوچیک) به‌راحتی کافی
    نیست، حتی با preset=veryfast. وقتی Timeout می‌خورد، خروجی duration=None
    برمی‌گشت و همون فایل خام (بدون fix) با ۰۰:۰۰ به تلگرام می‌رفت. حالا
    Timeout بر اساس مدت‌زمان واقعی ویدیو (اگه از ffprobe موجود باشه) محاسبه
    می‌شه، با ضریب امنیت بالا برای CPUهای ضعیف."""
    if not _FFMPEG_OK:
        return None
    base, _ = os.path.splitext(filepath)
    out_path = base + "_enc.mp4"
    if expected_duration and expected_duration > 0:
        # فرض بدبینانه: حتی با veryfast ممکنه رمزگذاری تا ۶ برابر کندتر از
        # real-time روی CPU ضعیف/مشترک طول بکشه.
        timeout = min(3600, max(300, int(expected_duration * 6)))
    else:
        timeout = 300
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", filepath,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", out_path],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        _log_ffmpeg_failure("re-encode", filepath, proc=proc, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        _log_ffmpeg_failure("re-encode", filepath, exc=e, timeout=timeout)
    except Exception as e:
        _log_ffmpeg_failure("re-encode", filepath, exc=e, timeout=timeout)
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except Exception:
            pass
    return None


def _make_thumbnail(filepath: str, duration=None, job_id: str = None):
    """بلاک‌کننده‌ست — تو asyncio.to_thread صدا زده بشه.

    🚨 رفع باگ آیتم ۵۲ چک‌لیست («صفحه سیاه» در همه‌ی پلتفرم‌ها، مخصوصاً
    فایل‌های حجیم/طولانی): قبلاً این ربات هیچ‌وقت thumbnail صریح به تلگرام
    نمی‌داد — پارامتر thumbnail هیچ‌جا ست نمی‌شد. برای فایل‌های کوچیک تلگرام
    خودش سریع کل فایل رو می‌گیره و thumbnail می‌سازه، ولی برای فایل‌های
    حجیم/طولانی، سرور تلگرام preview رو قبل از این‌که کل فایل از سمت ربات
    Upload بشه (یا قبل از این‌که کلاینت کاربر کامل دانلودش کنه) نشون می‌ده —
    و چون thumbnail صریحی نداشت، دقیقاً همون Preview سیاه رخ می‌داد.

    این تابع دقیقاً طبق پایپ‌لاین چک‌لیست (FINAL FILE → FFPROBE PASS →
    EXTRACT FRAME → THUMBNAIL) فقط از فایلِ نهاییِ VALIDATED یه فریم از وسط
    ویدیو می‌گیره (نه فریم اول که ممکنه مشکی/فید‌این باشه، نه از فایل موقت).
    اگه شکست بخوره None برمی‌گردونه — نباید خودِ ویدیو رو خراب کنه.

    🔴 بعد از گزارش «Duration/پخش سالمه ولی پیش‌نمایش مشکیه» (یعنی فایل خودش
    سالمه، فقط این مرحله مشکوکه)، دو تا نقطه‌ضعف احتمالی رفع شد:
      ۱. `-ss` قبل از `-i` روی بعضی فایل‌ها (مخصوصاً remux‌شده با keyframe کم یا
         intra-refresh) می‌تونه دقیقاً رو یه فریم غیرکامل/گذار (نه یه keyframe
         واقعی) بشینه و یه عکس مشکی/خاکستریِ تقریباً تک‌رنگ بده — بدون این‌که
         ffmpeg exit code غیرصفر بده (یعنی از دید کد قبلی «موفق» بود ولی
         عملاً تصویر بی‌ارزش بود). حالا اگه فایل خروجی مشکوک کوچیکه (کمتر از
         ۱۵۰۰ بایت — نشونه‌ی قوی یه JPEG تقریباً تک‌رنگ/خالی)، به‌جای قبول
         کردنش، یه‌بار دیگه از ثانیه‌ی ۱ (که معمولاً فریم واقعی و decode‌شده‌ست)
         با seek دقیق‌تر (بعد از -i) امتحان می‌شه.
      ۲. `-pix_fmt yuvj420p` صریح اضافه شد تا انکودر MJPEG/image2 (خروجی .jpg)
         روی فایل‌هایی با pixel format نامتعارف (مثلاً yuv420p10le یا profile
         عجیب بعد از remux) به‌جای warning/تبدیل ضمنی مشکوک، صریحاً رنج رنگ
         درست رو بگیره.
    هر دو تلاش (و نتیجه‌ی نهایی: مسیر/حجم فایل موفق، یا None) صریح لاگ می‌شه
    تا تو تلاش بعدی از روی لاگ واقعی مشخص باشه کدوم مسیر اجرا شده، نه حدس."""
    if not _FFMPEG_OK:
        log.info(f"[dl:{job_id}] thumbnail: ffmpeg not available, skipped")
        return None
    out_path = filepath + "_thumb.jpg"
    prefix = f"[dl:{job_id}] " if job_id else ""

    def _try_extract(seek_s: float, fast_seek: bool) -> bool:
        """یه تلاش فریم‌گیری. fast_seek=True یعنی -ss قبل از -i (سریع ولی گاهی
        نادقیق روی keyframe)؛ fast_seek=False یعنی -ss بعد از -i (کندتر ولی
        دقیقاً همون فریمِ decode‌شده رو می‌ده — برای Fallback مطمئن‌تره)."""
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass
        cmd = ["ffmpeg", "-y"]
        if fast_seek:
            cmd += ["-ss", f"{seek_s:.2f}", "-i", filepath]
        else:
            cmd += ["-i", filepath, "-ss", f"{seek_s:.2f}"]
        cmd += ["-frames:v", "1", "-vf", "scale=320:-2", "-pix_fmt", "yuvj420p", out_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            _log_ffmpeg_failure("thumbnail", filepath, exc=e, timeout=30)
            return False
        if proc.returncode != 0 or not os.path.exists(out_path):
            _log_ffmpeg_failure("thumbnail", filepath, proc=proc, timeout=30)
            return False
        size = os.path.getsize(out_path)
        # فایل خیلی کوچیک (< 1500 بایت) برای یه JPEG واقعیِ 320px عملاً یعنی
        # تصویر تقریباً تک‌رنگ/خالی (مشکی محض بیشتر از این فشرده می‌شه) —
        # به‌عنوان شکست حساب می‌شه تا Fallback بعدی امتحان بشه.
        if size < 1500:
            log.info(f"{prefix}thumbnail: extracted frame suspiciously tiny "
                      f"(size={size}B, seek={seek_s:.2f}, fast_seek={fast_seek}) -> treating as failed")
            return False
        log.info(f"{prefix}thumbnail: extracted OK (path={out_path!r} size={size}B "
                  f"seek={seek_s:.2f} fast_seek={fast_seek})")
        return True

    mid_seek = 1.0
    if duration and duration > 4:
        mid_seek = min(duration / 2.0, duration - 1)

    # تلاش ۱: fast-seek از وسط ویدیو (رفتار قبلی)
    if _try_extract(mid_seek, fast_seek=True):
        return out_path

    # تلاش ۲: seek دقیق (بعد از -i) از همون نقطه — کندتره ولی روی keyframeهای
    # کم/intra-refresh معمولاً درست‌کار می‌کنه.
    if _try_extract(mid_seek, fast_seek=False):
        return out_path

    # تلاش ۳: ثانیه‌ی ۱ با seek دقیق — برای ویدیوهای خیلی کوتاه یا فایل‌های
    # عجیب که حتی seek دقیق روی نقطه‌ی وسط هم جواب نداد.
    if mid_seek != 1.0 and _try_extract(1.0, fast_seek=False):
        return out_path

    log.warning(f"{prefix}thumbnail: all extraction attempts failed for {filepath!r} "
                f"(duration={duration}); sending without explicit thumbnail")
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except Exception:
            pass
    return None


def _fix_video_for_telegram(filepath: str, job_id: str = None):
    """بلاک‌کننده‌ست — حتماً باید تو asyncio.to_thread صدا زده بشه.

    مشکل: بعضی ویدیوهای دانلودی (مخصوصاً ویدیوهای نسبتاً طولانی/حجیم) moov
    atom‌شون آخر فایل‌ه یا duration/metadata‌شون کامل نیست؛ تلگرام قبل از
    کامل شدن دانلودِ کاربر، پلیر رو با 00:00 و صفحه‌ی سیاه نشون می‌ده.

    🔴 تغییر مهم بعد از Audit: قبلاً موفقیت remux/re-encode فقط با «آیا
    duration خونده شد؟» (`if d2: ...`) سنجیده می‌شد — که سطحی بود و ممکن بود
    فایلی با duration درست ولی width/height=0 یا audio stream خراب رو
    «موفق» حساب کنه. حالا هر خروجی (raw/remux/reencode) با همون Gate
    سخت‌گیرانه‌ای که قبل از Upload هم اجرا می‌شه (`_validate_media_file`)
    سنجیده می‌شه — یعنی معیار «فایل خوبه یا نه» دقیقاً یکیه، نه دو تا معیار
    جدا که ممکنه با هم فرق کنن.

    راه‌حل: اول خودِ فایل خام رو با Gate چک می‌کنیم (بعضی وقتا از اول سالمه
    و اصلاً نیازی به remux نیست). اگه رد شد، Remux سریع (-c copy) با
    +faststart امتحان می‌کنیم. اگه خروجی remux هم رد شد، Re-encode واقعی رو
    امتحان می‌کنیم. هر مرحله با file size/duration/width/height/codecs/
    container/pixel format/stream count لاگ می‌شه (چک‌لیست آیتم ۴۵).

    اگه هیچ‌کدوم جواب نداد، همون فایل خام (نامعتبر) برگردونده می‌شه — ولی
    این‌جا آخرش نیست: فراخوان دوباره همین Gate رو صدا می‌زنه و اگه رد بشه،
    فایل خام هرگز به‌عنوان «سالم» به تلگرام فرستاده نمی‌شه، طبق قانون صریح
    «اگر FFmpeg fail شد، فایل خام به‌عنوان فایل سالم ارسال نشود».

    توجه: این تابع دیگه thumbnail نمی‌سازه — طبق ترتیب صحیح Pipeline
    (FIX → FINAL FILE → FFPROBE/VALIDATE → THUMBNAIL → UPLOAD)، ساخت
    thumbnail باید فقط بعد از اینکه فراخوان با _validate_media_file فایل
    نهایی رو Pass کرد انجام بشه، نه قبلش.

    خروجی: (مسیر_نهایی_فایل, duration_یا_None, width_یا_None, height_یا_None)
    """
    diag, probe = _log_stage("raw-download", filepath, job_id)
    duration, width, height = diag["duration"], diag["width"], diag["height"]

    if probe is None:
        log.warning(f"[dl:{job_id}] ffprobe نتونست فایل خام رو اصلاً بخونه: {filepath!r}")
        return filepath, None, None, None

    final_path = filepath
    final_duration, final_width, final_height = duration, width, height

    raw_ok, raw_reason = _validate_media_file(filepath)
    if raw_ok:
        log.info(f"[dl:{job_id}] فایل خام از قبل Gate رو Pass کرد — نیازی به remux/reencode نیست")
        return final_path, final_duration, final_width, final_height

    log.info(f"[dl:{job_id}] فایل خام Gate رو رد شد ({raw_reason}) -> تلاش برای remux")
    remuxed = _remux_faststart(filepath)
    if remuxed:
        rdiag, _ = _log_stage("after-remux", remuxed, job_id)
        ok, reason = _validate_media_file(remuxed)
        if ok:
            final_path = remuxed
            final_duration, final_width, final_height = rdiag["duration"], rdiag["width"], rdiag["height"]
        else:
            log.info(f"[dl:{job_id}] خروجی remux هم Gate رو رد شد ({reason}) -> تلاش برای re-encode")
            try:
                os.remove(remuxed)
            except Exception:
                pass

    if final_path == filepath:  # یعنی remux کافی نبود یا انجام نشد
        reencoded = _reencode_video(filepath, expected_duration=duration)
        if reencoded:
            ediag, _ = _log_stage("after-reencode", reencoded, job_id)
            ok, reason = _validate_media_file(reencoded)
            if ok:
                final_path = reencoded
                final_duration, final_width, final_height = ediag["duration"], ediag["width"], ediag["height"]
            else:
                log.error(
                    f"[dl:{job_id}] خروجی re-encode هم Gate رو رد شد ({reason}) — "
                    f"Pipeline تمام گزینه‌هاش تموم شد؛ فایل نهایی توسط فراخوان دوباره "
                    f"Validate و در صورت نامعتبر بودن رد می‌شه (نه ارسال به‌عنوان سالم)."
                )
                try:
                    os.remove(reencoded)
                except Exception:
                    pass

    _log_stage("fix-pipeline-final", final_path, job_id)
    return final_path, final_duration, final_width, final_height


# 🐛 رفع باگ «پلتفرم فایل خروجی معتبری برنگردوند» (بدون Exception از yt-dlp،
# ولی filepath محاسبه‌شده رو دیسک وجود نداره): این معمولاً وقتی رخ می‌ده که
# yt-dlp واقعاً چیزی روی دیسک نوشته، ولی مسیر واقعی‌ش با هیچ‌کدوم از دو روش
# قبلی (requested_downloads / prepare_filename+merge_output_format) یکی
# درنمیاد — مثلاً وقتی entry از یه ساختار Playlist/Mix/Tab برمی‌گرده (حتی
# برای یه لینک تک‌ویدیوی یوتیوب که یوتیوب داخلی به‌عنوان بخشی از یه Mix
# می‌شناستش) یا وقتی نسخه‌ی yt-dlp کلید filepath رو تو requested_downloads
# پر نمی‌کنه. آخرین راه قبل از تسلیم‌شدن: خودِ outdir (که مخصوص همین Job‌ه،
# هیچ فایل قدیمی توش نیست) رو می‌گردیم و بزرگ‌ترین فایل رسانه‌ایِ واقعی
# (نه فایل‌های جانبیِ .part/.ytdl/.json/.jpg) رو به‌عنوان خروجی واقعی برمی‌گردونیم.
_SIDECAR_EXTS = (".part", ".ytdl", ".description", ".json", ".jpg", ".jpeg", ".png", ".webp", ".txt")


def _find_downloaded_file(outdir: str, prefer_id: str = None):
    try:
        candidates = [
            p for p in glob.glob(os.path.join(outdir, "*"))
            if os.path.isfile(p) and not p.lower().endswith(_SIDECAR_EXTS)
        ]
    except Exception:
        return None
    if not candidates:
        return None
    if prefer_id:
        id_matches = [p for p in candidates if prefer_id in os.path.basename(p)]
        if id_matches:
            candidates = id_matches
    try:
        return max(candidates, key=os.path.getsize)
    except Exception:
        return candidates[0]


def _yt_dlp_download(url: str, outdir: str, platform: str, progress_state=None, quality=None):
    """بلاک‌کننده‌ست — حتماً باید تو asyncio.to_thread صدا زده بشه.

    🦇 پارامتر quality (Phase 2/3): کاملاً اختیاری، پیش‌فرض None. فقط وقتی
    مسیر جدیدِ انتخاب کیفیت (Phase 2) صدا می‌زنه مقدار می‌گیره؛ برای همه‌ی
    فراخوان‌های قبلی/فعلی (بدون این آرگومان) رفتار دقیقاً مثل قبل می‌مونه.

    برای یوتیوب چند تا player_client رو پشت‌سرهم امتحان می‌کنیم، چون بعضی‌هاشون
    (مثل android/ios) گاهی قفل «Sign in to confirm you're not a bot» رو دور
    می‌زنن حتی بدون کوکی، ولی تضمینی نیست — اگه یوتیوب واقعاً لینک رو قفل کرده
    باشه، تنها راه قطعی فایل کوکیِ یه اکانت لاگین‌شده‌ست (YT_COOKIES_FILE).
    آخرین تلاش هیچ extractor_args ای نمی‌ذاره (رفتار پیش‌فرض خودِ yt-dlp، که
    خودش داخلی بین clientها و PO-token هماهنگ می‌کنه) — چون قفل کردن به سه
    client ثابت باعث می‌شد اگه هر سه با نسخه‌ی نصب‌شده‌ی yt-dlp ناسازگار بودن،
    دانلود کلاً شکست بخوره بدون این‌که راه‌حل پیش‌فرض/جدیدتر امتحان بشه.
    """
    # 🐛 رفع باگ واقعی «Instagram → Media number out of range»: وقتی کاربر یه
    # عکسِ خاص از یه Carousel رو تو اپ اینستاگرام باز می‌کنه و لینکش رو
    # می‌فرسته، اینستاگرام خودش به URL یه پارامتر img_index=N اضافه می‌کنه
    # (یعنی «این آیتم مشخص از Carousel رو نشون بده»). چون تو این پروژه برای
    # اینستاگرام noplaylist=False هست (تا کل Carousel دانلود بشه، نه فقط یه
    # آیتم)، yt-dlp سعی می‌کنه هم «کل Carousel» و هم «همون index مشخص» رو
    # هم‌زمان اعمال کنه؛ اگه شمارش داخلیِ yt-dlp با ایندکس اینستاگرام (که
    # گاهی 0-based و گاهی با تعداد واقعیِ آیتم‌های برگشتی نمی‌خونه) جور در
    # نیاد، خطای «Media number out of range» می‌ده و کل دانلود شکست می‌خوره —
    # درحالی‌که ما اصلاً به این ایندکس نیازی نداریم چون همیشه *کل* Carousel
    # رو می‌گیریم و می‌فرستیم. راه‌حل: این پارامتر رو قبل از دادن URL به
    # yt-dlp حذف می‌کنیم تا از کل پست/Carousel (بدون محدودیت به یه ایندکس
    # خاص) دانلود بشه.
    if platform == "instagram":
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.query:
                q = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                q = [(k, v) for k, v in q if k != "img_index"]
                url = urllib.parse.urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(q), parsed.fragment)
                )
        except Exception:
            pass  # اگه پارس URL هر دلیلی شکست خورد، همون URL اصلی بدون تغییر استفاده می‌شه

    base = _base_ydl_opts(outdir, platform, quality=quality)
    attempts = [{}]
    if platform == "youtube":
        attempts = [
            {"extractor_args": {"youtube": {"player_client": ["android", "web"]}}},
            {"extractor_args": {"youtube": {"player_client": ["ios"]}}},
            # 🦇 تلاش اضافه: کلاینت tv_embedded معمولاً مسیر دیگه‌ای رو طی
            # می‌کنه که بعضی وقتا می‌تونه قفل «Sign in to confirm you're not
            # a bot» رو برای ویدیوهای عمومی (غیر Age-Restricted) دور بزنه —
            # بدون نیاز به کوکی. تضمینی نیست (این یه محدودیت سمت یوتیوبه)،
            # ولی هزینه‌ی این تلاش صفره: اگه شکست بخوره، دقیقاً می‌ره سراغ
            # تلاش پیش‌فرض بعدی، بدون این‌که چیزی خراب کنه.
            {"extractor_args": {"youtube": {"player_client": ["tv_embedded"]}}},
            {},  # پیش‌فرض کامل yt-dlp، بدون هیچ محدودیت client
        ]
    if platform == "soundcloud":
        # ساندکلاود صوتیه؛ فرمت ویدیویی معنی نداره، بهترین فایل صوتی رو می‌گیریم
        base = {**base, "format": "bestaudio/best"}

    def _hook(d):
        if progress_state is None:
            return
        try:
            if d.get("status") == "downloading":
                progress_state["status"] = "downloading"
                progress_state["downloaded"] = d.get("downloaded_bytes") or 0
                progress_state["total"] = (
                    d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                )
                progress_state["speed"] = d.get("speed") or 0
                progress_state["eta"] = d.get("eta")
            elif d.get("status") == "finished":
                progress_state["status"] = "processing"
        except Exception:
            pass

    last_err = None
    for extra in attempts:
        opts = {**base, **extra, "progress_hooks": [_hook]}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # 🐛 باگ واقعی مسیر فایل: prepare_filename() اسم رو از روی
                # info_dict *قبل* از دانلود می‌سازه و از postprocessorها
                # (merge/remux/re-encode) خبر نداره، پس اگه پسوند نهایی عوض
                # بشه (مثلاً ویدیو bestvideo[webm]+bestaudio[m4a] بوده و بعد
                # merge به mp4 شده) یه مسیر با پسوند غلط/ناموجود برمی‌گردونه.
                # منبع درست و رسمیِ خودِ yt-dlp برای مسیر واقعیِ فایل روی دیسک
                # بعد از دانلود، کلید info["requested_downloads"] هست (لیستی
                # از دیکشنری‌ها با کلید filepath/filename که *بعد* از تمام
                # postprocessing پر می‌شه) — این‌جا اول اون رو امتحان می‌کنیم.
                filepath = None
                requested = info.get("requested_downloads") if isinstance(info, dict) else None
                if requested:
                    # 🐛 قبلاً فقط آخرین entry (requested[-1]) چک می‌شد. بسته به
                    # نسخه‌ی yt-dlp و این‌که merge/remux کدوم entry رو دقیقاً
                    # آپدیت می‌کنه (اول یا آخر لیست)، این می‌تونست همون entryِ
                    # معتبرِ بعد از postprocessing رو از دست بده و باعث «خروجی
                    # معتبر نیست» بشه با این‌که فایل واقعاً روی دیسک بود. حالا
                    # همه‌ی entryها (از آخر به اول) چک می‌شن.
                    for entry in reversed(requested):
                        cand = entry.get("filepath") or entry.get("_filename") or entry.get("filename")
                        if cand and os.path.exists(cand):
                            filepath = cand
                            break
                if not filepath:
                    # بعضی نسخه‌های yt-dlp بعد از تمام postprocessing، مسیر
                    # نهایی رو مستقیم رو خودِ info_dict سطح‌بالا هم می‌ذارن.
                    for key in ("filepath", "_filename"):
                        cand = info.get(key) if isinstance(info, dict) else None
                        if cand and os.path.exists(cand):
                            filepath = cand
                            break
                if not filepath:
                    filepath = ydl.prepare_filename(info)
                    # همون منطق قبلی به‌عنوان Fallback دوم، برای نسخه‌های
                    # yt-dlp که requested_downloads رو پر نمی‌کنن.
                    if opts.get("merge_output_format") and not os.path.exists(filepath):
                        alt = os.path.splitext(filepath)[0] + "." + opts["merge_output_format"]
                        if os.path.exists(alt):
                            filepath = alt
                if not filepath or not os.path.exists(filepath):
                    # 🩹 Fallback سوم و آخر: هر دو روش بالا مسیر درستی ندادن،
                    # ولی احتمالاً yt-dlp واقعاً یه فایل تو همین outdir نوشته —
                    # مستقیم رو دیسک می‌گردیم به‌جای اینکه بی‌دلیل «خروجی
                    # معتبر نیست» اعلام کنیم.
                    vid = info.get("id") if isinstance(info, dict) else None
                    found = _find_downloaded_file(outdir, prefer_id=vid)
                    if found:
                        log.info(f"yt-dlp filepath fallback via directory scan: {found!r} "
                                  f"(computed path was {filepath!r})")
                        filepath = found
            return filepath, info
        except Exception as e:
            last_err = e
            continue
    raise last_err


def _yt_dlp_probe(url: str, platform: str):
    """بلاک‌کننده — باید تو asyncio.to_thread صدا زده بشه. فقط Metadata (عنوان/
    مدت/حجم تقریبی) رو بدون دانلود واقعی می‌گیره (download=False)، تا قبل از
    شروع دانلود واقعی بشه به کاربر نشون داد. اگه شکست خورد، None برمی‌گردونه —
    این یه مسیر Best-effort‌ه و نباید جلوی دانلود اصلی رو بگیره."""
    if yt_dlp is None:
        return None
    opts = {**_base_ydl_opts(tempfile.gettempdir(), platform), "skip_download": True}
    opts.pop("max_filesize", None)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.info(f"metadata probe failed for {platform} ({url}): {e}")
        return None


def _log_job(job_id: str, **fields):
    """لاگ ساخت‌یافته‌ی هر مرحله‌ی یه Job دانلود — platform/url/user_id/job_id/
    output path/file size/duration/exit status و... طبق چک‌لیست موردنیاز، ولی
    هیچ‌وقت این جزئیات مستقیم به کاربر نشون داده نمی‌شه، فقط تو Log می‌مونه."""
    parts = " ".join(f"{k}={v!r}" for k, v in fields.items())
    log.info(f"[dl:{job_id}] {parts}")


def _classify_download_error(err_text: str):
    """متن خام Exception (yt-dlp/httpx) رو به یه پیام فارسیِ دقیق و بدون جزئیات
    فنی تبدیل می‌کنه، به‌علاوه‌ی این‌که آیا این خطا موقتیه و ارزش Retry داره.
    هیچ‌وقت به‌جای این‌ها پیام عمومی «فایل پیدا نشد» برنمی‌گردونه — طبق قانون
    اصلی دانلودر، هر خطا باید علت واقعی خودش رو نشون بده."""
    t = (err_text or "").lower()

    if "sign in to confirm" in t or "not a bot" in t:
        return (
            "🔐 نیاز به ورود/احراز هویت — یوتیوب این لینک رو پشت قفل ضد-ربات "
            "گذاشته و بدون کوکیِ یه اکانت لاگین‌شده قابل دانلود نیست.",
            False,
        )
    if "empty media response" in t or ("login" in t and "instagram" in t):
        return ("🔒 محتوای خصوصی است یا پلتفرم بدون لاگین اجازه‌ی دسترسی نمی‌ده.", False)
    if "private video" in t or "private account" in t or "this profile is private" in t:
        return ("🔒 محتوای خصوصی است.", False)
    if ("video unavailable" in t or "has been removed" in t or "no longer available" in t
            or "content isn't available" in t or "post unavailable" in t):
        return ("🚫 محتوا حذف شده یا در دسترس نیست.", False)
    if "not available in your country" in t or ("geo" in t and "restrict" in t):
        return ("🌍 این محتوا در منطقه‌ی سرور ربات قابل دسترسی نیست.", False)
    if "requires authentication" in t or "join this channel" in t or "subscribers only" in t:
        return ("🔐 این محتوا نیاز به ورود/احراز هویت یا عضویت ویژه داره.", False)
    if "max_filesize" in t or "max-filesize" in t or "file is larger than" in t:
        return ("📦 حجم فایل بیشتر از محدودیت مجاز ارسال است.", False)
    if ("unsupported url" in t or "no video formats found" in t
            or "requested format is not available" in t or "no formats found" in t):
        return ("⚠️ فرمت این رسانه توسط ربات پشتیبانی نمی‌شود.", False)
    if "invalid url" in t or "is not a valid url" in t or "unable to extract" in t:
        return ("⚠️ لینک نامعتبر است یا محتوایی داخلش پیدا نشد.", False)
    if "403" in t or "forbidden" in t:
        return ("🛑 دانلود توسط پلتفرم مسدود شد.", False)
    if any(k in t for k in (
        "connecterror", "connect error", "connection reset", "temporary failure",
        "name or service not known", "network is unreachable", "connectionerror",
    )):
        return ("🌐 خطای شبکه هنگام دانلود پیش اومد.", True)
    if "timed out" in t or "timeout" in t:
        return ("⏱ زمان دانلود تمام شد.", True)
    if any(k in t for k in ("502", "503", "504", "server error", "internal error")):
        return ("⚙️ خطای موقت سرویس؛ کمی دیگه دوباره امتحان کن.", True)

    return ("❌ خطای نامشخص در دانلود؛ جزئیاتش تو لاگ ربات ثبت شد.", False)


async def _download_with_retry(url: str, tmpdir: str, platform: str, job_id: str, progress_state=None, quality=None):
    """دور _yt_dlp_download رو با Timeout و Retry-با-Backoff (فقط برای خطاهای
    موقت) می‌پیچه. خطاهای دائمی (Private/Deleted/Invalid/...) بدون تلف‌کردن وقت
    فوراً بالا پرتاب می‌شن.

    🦇 پارامتر quality (Phase 2/3) اختیاریه؛ فراخوان‌های فعلی/قبلی بدون این
    آرگومان دقیقاً همون رفتار قبلی (فرمت پیش‌فرض پلتفرم) رو دارن."""
    attempt = 0
    waited_slot = False
    while True:
        attempt += 1
        try:
            if not waited_slot and _DOWNLOAD_SEMAPHORE.locked():
                _log_job(job_id, platform=platform, url=url, stage="queued",
                          max_concurrent=MAX_CONCURRENT_DOWNLOADS)
            async with _DOWNLOAD_SEMAPHORE:
                waited_slot = True
                return await asyncio.wait_for(
                    asyncio.to_thread(_yt_dlp_download, url, tmpdir, platform, progress_state, quality),
                    timeout=JOB_TIMEOUT_SEC,
                )
        except asyncio.TimeoutError:
            _log_job(job_id, platform=platform, url=url, stage="timeout", attempt=attempt)
            raise
        except Exception as e:
            _, retryable = _classify_download_error(str(e))
            if retryable and attempt <= len(NETWORK_RETRY_DELAYS):
                delay = NETWORK_RETRY_DELAYS[attempt - 1]
                _log_job(job_id, platform=platform, url=url, stage="retry",
                          attempt=attempt, delay=delay, error=str(e)[:200])
                await asyncio.sleep(delay)
                continue
            raise


# 🩺 رفع باگ «تایمر/حجم روی 00:00 و اطلاعات اشتباه گیر می‌کنه»: قبلاً پیام
# وضعیت فقط یه‌بار قبل از دانلود ست می‌شد و تا پایان کار دیگه هیچ‌وقت آپدیت
# نمی‌شد — یعنی در طول کل دانلود (که ممکنه چند ده ثانیه طول بکشه) کاربر همون
# پیام اولیه‌ی ثابت رو می‌دید. این تابع هر ۱.۵ ثانیه (نه هر chunk — طبق قانون
# ضدکندی/race condition) پیام رو با درصد/حجم/سرعت/ETA واقعیِ progress_state
# (که از progress_hooks یوتیوب/ساندکلاود یا از _download_direct_url پینترست پر
# می‌شه) آپدیت می‌کنه. تا وقتی metadata واقعی نیومده، صریحاً «در حال دریافت
# اطلاعات...» نشون می‌ده — هیچ‌وقت 00:00 یا حجم جعلی نمی‌سازه.
async def _progress_ticker(status_msg, progress_state: dict, header: str, stop_event: asyncio.Event):
    last_text = None
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.5)
            break  # stop_event ست شد
        except asyncio.TimeoutError:
            pass  # وقت tick رسید، ادامه بده و یه‌بار دیگه آپدیت کن

        status = progress_state.get("status")
        lines = [header]
        if status == "downloading" and progress_state.get("total"):
            downloaded = progress_state.get("downloaded", 0)
            total = progress_state["total"]
            pct = min(100, downloaded / total * 100) if total else 0
            lines.append("⬇️ در حال دانلود...")
            lines.append(f"📦 حجم: {_human_size(downloaded)} / {_human_size(total)}")
            lines.append(f"📊 پیشرفت: {pct:.0f}%")
            speed = progress_state.get("speed")
            if speed:
                lines.append(f"⚡ سرعت: {_human_size(speed)}/s")
            eta = progress_state.get("eta")
            if eta is not None:
                m, s = divmod(int(eta), 60)
                lines.append(f"⏱ زمان باقی‌مانده: {m:02d}:{s:02d}")
        elif status == "downloading":
            lines.append("⬇️ در حال دانلود...")
            lines.append("📦 در حال دریافت اطلاعات حجم...")
        elif status == "processing":
            lines.append("⚙️ در حال پردازش نهایی فایل...")
        else:
            lines.append("⏳ در حال دریافت اطلاعات...")

        text = "\n".join(lines)
        if text != last_text:
            try:
                await status_msg.edit_text(text)
                last_text = text
            except Exception:
                pass  # (مثلاً "message not modified") — بی‌اهمیت، tick بعدی درستش می‌کنه


# =========================================================
#  🦇 FAST MEDIA PATH — YouTube file_id CACHE (no re-download/re-upload)
# =========================================================
# هدف: اگه یه ویدیوی یوتیوب قبلاً یه‌بار با موفقیت دانلود و برای تلگرام
# آپلود شده، همون file_id تلگرامش رو نگه داریم؛ دفعه‌ی بعد که همون ویدیو
# (حتی از طرف یه کاربر دیگه) درخواست شد، به‌جای دانلود دوباره از یوتیوب و
# آپلود دوباره به تلگرام، مستقیم همون file_id رو با send_video/send_document
# می‌فرستیم — تلگرام خودش فایل رو از سرورهای خودش Copy می‌کنه (Server-to-
# Server)، هیچ دانلود/آپلود/پردازشی سمت ربات ما انجام نمی‌شه.
#
# نکته‌ی مهم (چک‌لیست): این یه Cache واقعیه، نه Preview. فقط file_idِ خروجیِ
# خودِ send_video/send_document (بعد از آپلود موفق توسط خودمون) ذخیره می‌شه؛
# هیچ‌جا از پیش‌نمایش لینک (Web Preview) به‌عنوان منبع Media استفاده نمی‌شه،
# چون Bot API چنین چیزی رو اصلاً نمی‌ده.
#
# ذخیره‌سازی: همون sqlite دیتابیسی که reminders.py هم ازش استفاده می‌کنه
# (env var مشترک DB_PATH، دقیقاً همون منطق تشخیص مسیر تو bot.py) — یه جدول
# جدا، بدون تداخل با جدول‌های دیگه. اگه دیتابیس هر دلیلی در دسترس نبود، کش
# فقط غیرفعال می‌شه (خطا Swallow می‌شه) و مسیر دانلود عادی/مجاز فعلی بدون
# تغییر ادامه پیدا می‌کنه — یعنی هیچ Crash/قطعی‌ای از کارنکردن کش سرچشمه
# نمی‌گیره.
_YT_CACHE_DB_PATH = os.getenv("DB_PATH", "/data/bot.db" if os.path.isdir("/data") else "bot.db")
_yt_cache_ready = False

# آی‌دی ۱۱کاراکتریِ ویدیوی یوتیوب رو مستقیم از روی متن URL دربیار — بدون
# هیچ Network call/yt-dlp probe (خودِ این استخراج باید سبک/فوری باشه چون
# قبل از هر تصمیمی صدا زده می‌شه). پارامترهای اضافه‌ی URL (مثل si=, t=,
# list= کنار v=) روی نتیجه اثر نمی‌ذارن چون Group دقیقاً همون ۱۱ کاراکتر رو
# می‌گیره؛ اگه فرمت لینک غیرمعمول بود و آی‌دی پیدا نشد، None برمی‌گرده —
# یعنی کل مسیر کش بی‌صدا رد می‌شه و رفتار قبلی (دانلود عادی) دست‌نخورده
# می‌مونه.
_YT_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|v/)|[?&]v=)([A-Za-z0-9_-]{11})"
)


def _yt_extract_video_id(url: str):
    if not url:
        return None
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def _yt_cache_init():
    """جدول کش رو فقط یه‌بار در طول عمر Process می‌سازه (IF NOT EXISTS، پس
    اجرای چندباره هم بی‌خطره) — هر Exception (مثلاً دیتابیس Read-only یا
    دیسک پر) فقط باعث غیرفعال‌شدن کش می‌شه، نه Crash."""
    global _yt_cache_ready
    if _yt_cache_ready:
        return
    try:
        conn = sqlite3.connect(_YT_CACHE_DB_PATH)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS yt_media_cache (
                video_id   TEXT PRIMARY KEY,
                file_id    TEXT NOT NULL,
                media_kind TEXT NOT NULL,
                width      INTEGER,
                height     INTEGER,
                duration   INTEGER,
                title      TEXT,
                source_url TEXT,
                created_at REAL NOT NULL,
                hit_count  INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()
        _yt_cache_ready = True
    except Exception as e:
        log.warning(f"[yt-cache] init failed, fast media cache disabled: {e}")


def _yt_cache_get(video_id: str):
    if not video_id:
        return None
    _yt_cache_init()
    if not _yt_cache_ready:
        return None
    try:
        conn = sqlite3.connect(_YT_CACHE_DB_PATH)
        row = conn.execute(
            "SELECT file_id, media_kind, width, height, duration, title "
            "FROM yt_media_cache WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE yt_media_cache SET hit_count = hit_count + 1 WHERE video_id = ?",
                (video_id,),
            )
            conn.commit()
        conn.close()
        if not row:
            return None
        file_id, media_kind, width, height, duration, title = row
        return {
            "file_id": file_id, "media_kind": media_kind,
            "width": width, "height": height, "duration": duration, "title": title,
        }
    except Exception as e:
        log.warning(f"[yt-cache] read failed for video_id={video_id!r}: {e}")
        return None


def _yt_cache_set(video_id: str, file_id: str, media_kind: str,
                   width=None, height=None, duration=None, title=None, source_url=None):
    if not video_id or not file_id:
        return
    _yt_cache_init()
    if not _yt_cache_ready:
        return
    try:
        conn = sqlite3.connect(_YT_CACHE_DB_PATH)
        conn.execute(
            "INSERT INTO yt_media_cache "
            "(video_id, file_id, media_kind, width, height, duration, title, source_url, created_at, hit_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,0) "
            "ON CONFLICT(video_id) DO UPDATE SET "
            "file_id=excluded.file_id, media_kind=excluded.media_kind, width=excluded.width, "
            "height=excluded.height, duration=excluded.duration, title=excluded.title, "
            "source_url=excluded.source_url, created_at=excluded.created_at",
            (video_id, file_id, media_kind, width, height, duration, title, source_url, time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[yt-cache] write failed for video_id={video_id!r}: {e}")


def _yt_cache_invalidate(video_id: str):
    """وقتی file_idِ کش‌شده دیگه سمت تلگرام معتبر نیست (نادر — مثلاً خیلی
    قدیمی/پاک‌شده)، از کش حذفش کن تا دفعه‌ی بعد دوباره امتحان نشه و مسیر
    دانلود عادی جایگزینش بشه."""
    if not video_id:
        return
    try:
        conn = sqlite3.connect(_YT_CACHE_DB_PATH)
        conn.execute("DELETE FROM yt_media_cache WHERE video_id = ?", (video_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[yt-cache] invalidate failed for video_id={video_id!r}: {e}")


async def _try_send_cached_media(msg, cached: dict, job_id: str):
    """با file_idِ آماده (بدون دانلود/آپلود) مستقیم برای همین Message/Chat
    می‌فرسته — همون Message که کاربر لینک رو توش فرستاده، پس همیشه به
    User/Chat خودش وصل می‌مونه. اگه به هر دلیلی (مثلاً file_id دیگه سمت
    تلگرام معتبر نیست) ارسال شکست بخوره، False برمی‌گردونه تا فراخوان بی‌صدا
    به مسیر دانلود عادی/مجاز fallback کنه — نه Exception بالا می‌ره، نه
    Crash می‌شه."""
    caption = f"🎬 {cached['title']}\n\n🦇 Gotham Downloader" if cached.get("title") else "🦇 Gotham Downloader"
    try:
        if cached["media_kind"] == "video":
            await msg.reply_video(
                cached["file_id"], caption=caption, supports_streaming=True,
                duration=cached.get("duration") or None,
                width=cached.get("width") or None,
                height=cached.get("height") or None,
            )
        else:
            await msg.reply_document(cached["file_id"], caption=caption)
        return True
    except Exception as e:
        log.info(f"[dl:{job_id}] cached file_id send failed, falling back to normal download path: {e}")
        return False


# =========================================================
#  🦇 GOTHAM FAST YOUTUBE DOWNLOADER — MAX SPEED MODE
# =========================================================
# مسیر کاملاً جدا و افزوده، فقط برای یوتیوب. منوی کیفیت قبلی
# (_offer_youtube_quality_menu و توابع مرتبطش پایین‌تر تو همین فایل) و مسیر
# عمومیِ اشتراکی‌ی بقیه‌ی پلتفرم‌ها (پایین‌تر تو downloader_link_handler)
# دست‌نخورده باقی می‌مونن — این تابع فقط جایگزینِ فراخوانیِ منوی کیفیت برای
# یوتیوب می‌شه، هیچ پلتفرم دیگه‌ای رو لمس نمی‌کنه.
#
# رفتار: لینک -> بدون منو/بدون سوال -> دانلود سریع‌ترین فرمت آماده (بدون
# نیاز به merge) -> بدون MP3/فشرده‌سازی/تغییر رزولوشن/لوگو -> ارسال مستقیم.

# جلوگیری از دانلود چندباره‌ی هم‌زمان برای همون کاربر/همون لینک (مثلاً پیام
# تکراری سریع یا صدا زده‌شدن دوباره‌ی هندلر).
_YT_FAST_INFLIGHT = set()  # {(user_id, url)}


async def _send_with_telegram_retry(coro_factory, job_id: str, max_attempts: int = 3):
    """coro_factory: تابع بدون-آرگومان که هر بار یه coroutine تازه می‌سازه
    (چون فایل باید هر تلاش از اول باز بشه). RetryAfter (Flood control) و
    TimedOut تلگرام رو با Backoff محدود (نه بی‌نهایت) مدیریت می‌کنه؛ بقیه‌ی
    خطاها مستقیم بالا پرتاب می‌شن تا فراخوان به Fallback بعدی (مثلاً Document)
    بره."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await coro_factory()
        except RetryAfter as e:
            wait = float(getattr(e, "retry_after", 5) or 5)
            if attempt >= max_attempts:
                log.warning(f"[dl:{job_id}] RetryAfter بعد از {attempt} تلاش رها شد (wait={wait}s)")
                raise
            log.info(f"[dl:{job_id}] Telegram RetryAfter — {wait}s صبر (تلاش {attempt}/{max_attempts})")
            await asyncio.sleep(wait + 0.5)
        except TimedOut:
            if attempt >= max_attempts:
                log.warning(f"[dl:{job_id}] TimedOut بعد از {attempt} تلاش رها شد")
                raise
            await asyncio.sleep(1.5 * attempt)


async def _fast_youtube_download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, job_id: str):
    """🦇 GOTHAM FAST MODE: بدون منوی کیفیت، بدون MP3، بدون فشرده‌سازی/تغییر
    رزولوشن/لوگو/FFmpeg processing روتین. لینک -> بهترین فایل MP4 آماده
    (ترجیحاً بدون نیاز به merge) -> ارسال مستقیم."""
    msg = update.effective_message
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    dedup_key = (uid, url)
    if dedup_key in _YT_FAST_INFLIGHT:
        _log_job(job_id, platform="youtube", url=url, user_id=uid, stage="fast-duplicate-ignored")
        return
    _YT_FAST_INFLIGHT.add(dedup_key)

    try:
        video_id = _yt_extract_video_id(url)

        # ⚡ FAST MEDIA PATH: اگه همین ویدیو قبلاً یه‌بار دانلود/آپلود شده و
        # file_id تلگرامش تو کش داریم، بدون هیچ دانلود/آپلود/FFmpeg/پردازشی
        # مستقیم همون Media رو برای همین کاربر می‌فرستیم. این کل بلوک
        # try بعدی (دانلود واقعی) رو کاملاً Skip می‌کنه — کمترین Latency
        # ممکن، دقیقاً طبق هدف.
        if video_id:
            cached = _yt_cache_get(video_id)
            if cached:
                sent_ok = await _try_send_cached_media(msg, cached, job_id)
                if sent_ok:
                    _log_job(job_id, platform="youtube", url=url, user_id=uid, chat_id=chat_id,
                              video_id=video_id, stage="fast-cache-hit-sent")
                    return
                # file_id دیگه معتبر نیست (نادر) -> از کش پاکش کن و بذار
                # مسیر عادی/مجاز زیر (همون دانلود واقعی) جایگزینش بشه.
                _yt_cache_invalidate(video_id)
                _log_job(job_id, platform="youtube", url=url, user_id=uid, chat_id=chat_id,
                          video_id=video_id, stage="fast-cache-stale-fallback")

        try:
            status = await msg.reply_text("🦇 Gotham Downloader\n\n🎬 دریافت ویدئو...")
        except Exception as e:
            log.warning(f"[dl:{job_id}] fast-yt initial status reply failed ({e}); falling back to plain send_message")
            status = await context.bot.send_message(chat_id, "🦇 Gotham Downloader\n\n🎬 دریافت ویدئو...")

        progress_state = {"status": "downloading", "total": 0, "downloaded": 0}
        stop_event = asyncio.Event()
        ticker = asyncio.create_task(
            _progress_ticker(status, progress_state, "🦇 Gotham Downloader", stop_event)
        )

        start_ts = time.monotonic()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                filepath, info = await _download_with_retry(
                    url, tmpdir, "youtube", job_id, progress_state, quality="fast"
                )
            except asyncio.TimeoutError:
                log.warning(f"[dl:{job_id}] fast-yt timeout url={url} user_id={uid} "
                            f"after={time.monotonic() - start_ts:.1f}s")
                stop_event.set()
                try:
                    await ticker
                except Exception:
                    pass
                await status.edit_text("❌ دانلود انجام نشد\nعلت: ⏱ زمان دانلود تمام شد.")
                return
            except Exception as e:
                log.exception(f"[dl:{job_id}] fast-yt download failed url={url} user_id={uid}")
                stop_event.set()
                try:
                    await ticker
                except Exception:
                    pass
                reason, _ = _classify_download_error(str(e))
                await status.edit_text(f"❌ دانلود انجام نشد\nعلت: {reason}")
                return
            finally:
                stop_event.set()
            try:
                await ticker
            except Exception:
                pass

            download_duration = time.monotonic() - start_ts

            if not filepath or not os.path.exists(filepath):
                log.error(f"[dl:{job_id}] fast-yt output missing url={url} user_id={uid}")
                await status.edit_text(
                    "❌ دانلود انجام نشد\nعلت: پلتفرم فایل خروجی معتبری برنگردوند — معمولاً یعنی فرمت "
                    "این ویدیو توسط ربات پشتیبانی نمی‌شه."
                )
                return

            real_size = os.path.getsize(filepath)
            title = (info.get("title") or "").strip() if isinstance(info, dict) else ""
            caption = f"🎬 {title}\n\n🦇 Gotham Downloader" if title else "🦇 Gotham Downloader"

            _log_job(
                job_id, platform="youtube", url=url, user_id=uid, chat_id=chat_id,
                output_path=filepath, file_size=real_size,
                download_duration=round(download_duration, 1),
                extractor=info.get("extractor") if isinstance(info, dict) else None,
                selected_format=info.get("format_id") if isinstance(info, dict) else None,
                stage="fast-downloaded",
            )

            # ⚡ بدون پردازش روتین: این فقط یه Safety-Net پایداریه — اگه فایل
            # خام از قبل سالم باشه (Gate رو Pass کنه، که برای فایل MP4 آماده‌ی
            # یوتیوب معمولاً همینه)، هیچ remux/re-encode ای انجام نمی‌شه و همون
            # فایل خام مستقیم فرستاده می‌شه؛ فقط وقتی فایل واقعاً خراب باشه
            # (moov atom ناقص و...) یه remux سریع (بدون افت کیفیت) امتحان می‌شه.
            ext = os.path.splitext(filepath)[1].lower()
            send_path = filepath
            v_duration = v_width = v_height = None
            if ext in _VIDEO_EXTS:
                try:
                    send_path, v_duration, v_width, v_height = await asyncio.to_thread(
                        _fix_video_for_telegram, filepath, job_id
                    )
                except Exception as e:
                    log.warning(f"[dl:{job_id}] fast-yt video fixup failed, sending raw file: {e}")
                    send_path = filepath

            ok, reason = await asyncio.to_thread(_validate_media_file, send_path)
            if not ok:
                log.error(f"[dl:{job_id}] fast-yt output failed validation: {reason} path={send_path!r}")
                await status.edit_text(f"❌ دانلود انجام نشد\nعلت: 🩹 فایل دریافتی سالم نبود ({reason})")
                return

            v_thumb = None
            try:
                v_thumb = await asyncio.to_thread(_make_thumbnail, send_path, v_duration, job_id)
            except Exception as e:
                log.info(f"[dl:{job_id}] fast-yt thumbnail step failed: {e}")

            async def _do_send_video(with_thumb: bool):
                tf = None
                if with_thumb and v_thumb and os.path.exists(v_thumb):
                    tf = open(v_thumb, "rb")
                try:
                    with open(send_path, "rb") as f:
                        return await msg.reply_video(
                            f, caption=caption, supports_streaming=True,
                            duration=int(v_duration) if v_duration else None,
                            width=v_width or None, height=v_height or None,
                            thumbnail=tf,
                        )
                finally:
                    if tf:
                        try:
                            tf.close()
                        except Exception:
                            pass

            async def _do_send_document():
                with open(send_path, "rb") as f:
                    return await msg.reply_document(f, caption=caption)

            sent_msg = None
            try:
                try:
                    sent_msg = await _send_with_telegram_retry(lambda: _do_send_video(True), job_id)
                    _log_job(job_id, platform="youtube", url=url, stage="fast-sent")
                except Exception as e_thumb:
                    log.warning(f"[dl:{job_id}] fast-yt send with thumbnail failed ({e_thumb}); "
                                f"retrying same file without thumbnail")
                    sent_msg = await _send_with_telegram_retry(lambda: _do_send_video(False), job_id)
                    _log_job(job_id, platform="youtube", url=url, stage="fast-sent-without-thumbnail")
            except Exception as e:
                log.warning(f"[dl:{job_id}] fast-yt video send failed, fallback to document: {e}")
                try:
                    sent_msg = await _send_with_telegram_retry(lambda: _do_send_document(), job_id)
                    _log_job(job_id, platform="youtube", url=url, stage="fast-sent-as-document")
                except Exception:
                    log.exception(f"[dl:{job_id}] fast-yt document fallback also failed")
                    try:
                        await status.edit_text(
                            "❌ ارسال فایل انجام نشد\nعلت: 📦 حجم فایل بیشتر از حد مجاز است یا تلگرام "
                            "موقتاً پاسخ نداد."
                        )
                    except Exception:
                        pass
                    return

            # ⚡ FAST MEDIA PATH: بعد از ارسال موفق، file_id همین آپلود رو تو
            # کش ذخیره کن تا دفعه‌ی بعد همین ویدیو (حتی از طرف کاربر دیگه)
            # بدون هیچ دانلود/آپلود دوباره‌ای مستقیم فرستاده بشه. کاملاً
            # Best-effort: هر خطا فقط Log می‌شه، هیچ‌وقت روی نتیجه‌ی ارسالی
            # که کاربر همین الان گرفته اثر نمی‌ذاره.
            if video_id and sent_msg is not None:
                try:
                    if getattr(sent_msg, "video", None):
                        v = sent_msg.video
                        _yt_cache_set(video_id, v.file_id, "video",
                                      width=v.width, height=v.height, duration=v.duration,
                                      title=title or None, source_url=url)
                    elif getattr(sent_msg, "document", None):
                        _yt_cache_set(video_id, sent_msg.document.file_id, "document",
                                      title=title or None, source_url=url)
                except Exception as e:
                    log.info(f"[dl:{job_id}] fast-yt cache store skipped: {e}")

            # 🧹 خروج از بلوک with tempfile.TemporaryDirectory() (پایین‌تر از
            # اینجا) خودکار کل پوشه‌ی Job (فایل خام + فایل remux احتمالی) رو
            # پاک می‌کنه — فقط بعد از ارسال موفق به این نقطه می‌رسیم.
            try:
                await status.delete()
            except Exception:
                pass
    finally:
        _YT_FAST_INFLIGHT.discard(dedup_key)


async def downloader_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    # 🐛 لینک داخل caption (عکس/ویدیوی فرستاده‌شده با کپشن حاوی لینک) قبلاً
    # اصلاً چک نمی‌شد چون فقط msg.text خونده می‌شد؛ caption روی msg.text نیست،
    # روی msg.caption‌ه. لینک فوروارد‌شده (از جمله از Saved Messages) هم متن/
    # کپشن خودش رو حفظ می‌کنه، پس نیازی به منطق جدا نداره — همین‌جا پوشش داده می‌شه.
    text = (msg.text or msg.caption or "").strip()

    match = URL_RE.search(text)
    if not match:
        return  # این پیام لینک نیست، به بقیه‌ی هندلرها بسپار

    url = match.group(0)
    # 🆔 هر درخواست یه Job ID مستقل داره — فقط برای رهگیری تو Logها (چک‌لیست
    # Persistence/Debug)، نه برای مسیر فایل (اون یه TemporaryDirectory جدا و
    # منحصربه‌فرده که خودِ tempfile می‌سازه، پس هر دانلود کاملاً مستقله).
    job_id = uuid.uuid4().hex[:10]

    # 🔧 رفع باگ Downloader داخل Group/Private: قبلاً این هندلر فقط وقتی کار
    # می‌کرد که کاربر اول «دانلودر» رو می‌زد و از منو پلتفرم رو دستی انتخاب
    # می‌کرد (PENDING_DL). اگه کاربر مستقیم لینک می‌فرستاد (دقیقاً کاری که تو
    # گروه انجام می‌شه)، uid تو PENDING_DL نبود و کل پیام بی‌صدا نادیده گرفته
    # می‌شد — چه تو گروه، چه تو پیوی. حالا اگه پلتفرم از قبل انتخاب نشده باشه،
    # از روی خودِ دامنه‌ی لینک تشخیص داده می‌شه تا نیازی به مرحله‌ی انتخاب منو
    # نباشه؛ اگه از قبل انتخاب شده باشه (PENDING_DL)، همون رفتار قبلی (با چک
    # تطبیق دامنه) دست‌نخورده می‌مونه.
    if uid in PENDING_DL:
        platform = PENDING_DL.pop(uid)
        allowed_domains = PLATFORM_DOMAINS[platform]
        if not any(d in url.lower() for d in allowed_domains):
            await msg.reply_text(
                f"⚠️ این لینک برای {PLATFORM_LABELS[platform]} نیست. دوباره «دانلودر» رو بزن و پلتفرم درست رو انتخاب کن."
            )
            return
    else:
        platform = _detect_platform_from_url(url)
        if platform is None:
            return  # لینک به هیچ‌کدوم از پلتفرم‌های پشتیبانی‌شده نمی‌خوره؛ به بقیه‌ی هندلرها سپرده می‌شه

    _log_job(job_id, platform=platform, url=url, user_id=uid, chat_id=chat_id, stage="start")

    # برای پینترست به yt-dlp نیازی نیست (روش مستقیم پایین‌تر کارو انجام می‌ده)،
    # فقط برای یوتیوب/اینستاگرام (و fallback خود پینترست) لازمه.
    if yt_dlp is None and platform != "pinterest":
        await msg.reply_text("⚠️ ماژول دانلود نصب نشده. باید yt-dlp تو requirements.txt باشه (اضافه شده، فقط دیپلوی دوباره لازمه).")
        return

    # ⚠️ لینک Playlist کاملِ یوتیوب (نه یه ویدیوی مشخص، نه Shorts) رو عمداً رد
    # می‌کنیم به‌جای این‌که بی‌صدا فقط یه ویدیوی اول رو بفرستیم — طبق قانون
    # «هیچ خطایی نباید با پیام گمراه‌کننده پوشونده بشه»، سکوت هم همون‌قدر
    # گمراه‌کننده‌ست.
    if platform == "youtube" and "list=" in url.lower() and "v=" not in url.lower() and "youtu.be" not in url.lower() and "/shorts/" not in url.lower():
        await msg.reply_text(
            "⚠️ این یه لینک Playlist کامل یوتیوبه. برای جلوگیری از دانلود ناخواسته‌ی حجم زیاد، فعلاً "
            "فقط دانلود تک‌ویدیو/Shorts پشتیبانی می‌شه — لینک مستقیم همون ویدیویی که می‌خوای رو بفرست."
        )
        return

    # 🦇 PHASE 2 — Quality UI: فقط برای یوتیوب، به‌جای دانلود فوری با فرمت
    # پیش‌فرض، اول تلاش می‌کنیم منوی انتخاب کیفیت (360/480/720/1080/Audio)
    # رو نشون بدیم. این بخش کاملاً «افزوده» است: اگه probe به هر دلیلی
    # شکست بخوره یا هیچ کیفیت/صدایی برنگردونه، handled=False می‌شه و کد
    # دقیقاً به همون مسیر قدیمیِ زیر (دانلود مستقیم با فرمت پیش‌فرض فعلی)
    # سقوط می‌کنه — یعنی هیچ رفتار قبلی از دست نمی‌ره، فقط یه لایه‌ی UI
    # اختیاری روی مسیر یوتیوب اضافه شده.
    if platform == "youtube":
        # 🦇 GOTHAM FAST YOUTUBE DOWNLOADER — MAX SPEED MODE: دیگه منوی
        # انتخاب کیفیت نشون داده نمی‌شه. مستقیم می‌ره سراغ سریع‌ترین مسیر
        # ممکن: بدون Merge، بدون MP3، بدون فشرده‌سازی/تغییر رزولوشن.
        # (_offer_youtube_quality_menu و توابع مرتبطش پایین‌تر تو همین فایل
        # دست‌نخورده باقی موندن، فقط دیگه از این‌جا صدا زده نمی‌شن.)
        await _fast_youtube_download_and_send(update, context, url, job_id)
        return

    # 🎬🎵 اینستاگرام Video/Reel و تیک‌تاک: طبق درخواست («اگر کاربر Instagram
    # Video/Reel/Post Video فرستاد، گزینه‌ی 🎵 Audio وجود داشته باشد» — قابلیت
    # الزامی؛ همین‌طور برای تیک‌تاک). این بخش هم دقیقاً مثل منوی کیفیت یوتیوب
    # کاملاً «افزوده»‌ست: فقط برای Videoهای تکی (نه عکس، نه Carousel) پیشنهاد
    # می‌شه؛ اگه probe عکس/Carousel/چیز نامشخصی تشخیص بده یا هر جای دیگه شکست
    # بخوره، handled=False می‌شه و کد دقیقاً به مسیر قدیمیِ دانلود مستقیم زیر
    # سقوط می‌کنه — هیچ رفتار فعلی (عکس/Carousel) از دست نمی‌ره.
    if platform in ("instagram", "tiktok"):
        handled = await _offer_media_choice_menu(update, context, url, platform, job_id)
        if handled:
            return

    # 🐛 رفع باگ «کاربر لینک می‌فرسته و ربات کاملاً ساکت می‌مونه»: قبلاً این
    # reply_text مستقیم و بدون try/except بود؛ اگه به هر دلیلی (مثلاً پیام
    # اصلی هم‌زمان توسط یه فیچر دیگه‌ی گروه حذف شده بود) ریپلای‌کردن به همون
    # پیام شکست می‌خورد، کل تابع همون‌جا با یه Exception ناتموم می‌موند — نه
    # پیامی به کاربر، نه هیچ ردی تو چت گروه، دقیقاً همون «لینک می‌فرستم و
    # هیچی نمی‌شه». حالا اگه reply مستقیم شکست بخوره، به یه پیام معمولی
    # (بدون رفرنس به پیام حذف‌شده) افت می‌کنیم تا کاربر همیشه یه واکنش ببینه.
    try:
        status = await msg.reply_text(f"⏳ در حال دانلود از {PLATFORM_LABELS[platform]}...")
    except Exception as e:
        log.warning(f"[dl:{job_id}] initial status reply failed ({e}); falling back to plain send_message")
        status = await context.bot.send_message(
            chat_id, f"⏳ در حال دانلود از {PLATFORM_LABELS[platform]}..."
        )

    # 📊 پیش‌نمایش Metadata (عنوان/مدت/حجم تقریبی) قبل از شروع دانلود واقعی —
    # فقط برای ساندکلاود.
    # ⚡ برای یوتیوب عمداً حذف شد: این یه extract_info(download=False) کامل و
    # جدا بود که دقیقاً همون استخراج Metadataای که خودِ دانلود واقعی
    # (extract_info(download=True) تو _yt_dlp_download) انجام می‌ده رو یه‌بار
    # دیگه از اول تکرار می‌کرد — یعنی هر لینک یوتیوب قبل از این‌که حتی یه بایت
    # از ویدیو دانلود بشه، دو بار کامل Extract می‌شد. حذفش دقیقاً همون
    # «دانلود/درخواست غیرضروری» است. محدودیت حجم همچنان از طریق فیلتر
    # filesize تو selector فرمت (_YOUTUBE_FORMAT) و گزینه‌ی max_filesize حین
    # دانلود واقعی اعمال می‌شه، پس هیچ حفاظتی از دست نرفت — فقط یه Round-trip
    # اضافه حذف شد.
    if platform == "soundcloud" and yt_dlp is not None:
        try:
            probe_info = await asyncio.wait_for(
                asyncio.to_thread(_yt_dlp_probe, url, platform), timeout=15
            )
        except Exception:
            probe_info = None
        if probe_info:
            approx_bytes = probe_info.get("filesize") or probe_info.get("filesize_approx")
            if approx_bytes and approx_bytes > MAX_TELEGRAM_UPLOAD_BYTES:
                _log_job(job_id, platform=platform, url=url, stage="pre-check-filesize-reject",
                          approx_bytes=approx_bytes)
                await status.edit_text(
                    "❌ دانلود انجام نشد\nعلت: 📦 حجم فایل برای ارسال مستقیم توسط ربات بیش از حد مجاز است."
                )
                return
            title = (probe_info.get("title") or "").strip()
            duration_s = probe_info.get("duration")
            lines = ["▶️ YouTube" if platform == "youtube" else "🎧 SoundCloud"]
            if title:
                lines.append(f"{'🎬 عنوان' if platform == 'youtube' else '🎵 Track'}: {title}")
            if duration_s:
                m, s = divmod(int(duration_s), 60)
                lines.append(f"⏱ مدت: {m:02d}:{s:02d}")
            lines.append(f"📦 حجم تقریبی: {_human_size(approx_bytes)}" if approx_bytes else "📦 حجم: در حال محاسبه...")
            lines.append("")
            lines.append("⏳ در حال دانلود...")
            try:
                await status.edit_text("\n".join(lines))
            except Exception:
                pass

    with tempfile.TemporaryDirectory() as tmpdir:
        # پینترست: اول روش مستقیم (resource API / اسکرپ HTML) رو امتحان کن،
        # چون خیلی سریع‌تر و مطمئن‌تر از yt-dlp برای این پلتفرمه. فقط اگه
        # شکست خورد میره سراغ yt-dlp (مسیر قدیمی، پایین همین تابع).
        if platform == "pinterest":
            try:
                media = await _pinterest_extract(url)
            except Exception as e:
                log.info(f"[dl:{job_id}] pinterest direct extract error: {e}")
                media = None
            if media:
                # 🐛 باگ پیدا‌شده: این مسیر (روش مستقیم Pinterest) فایل رو مستقیم
                # می‌فرستاد بدون هیچ progress، بدون ffprobe validation و بدون
                # remux/faststart fix — دقیقاً همون مسیری که کاربر می‌گفت
                # «Preview سیاه می‌شه / تایمر 00:00 می‌مونه». الان همون validation
                # + fix + progress واقعی که برای مسیر yt-dlp هست، این‌جا هم اجرا می‌شه.
                progress_state = {"status": "downloading", "total": 0, "downloaded": 0}
                stop_event = asyncio.Event()
                ticker = asyncio.create_task(
                    _progress_ticker(status, progress_state, "📌 Pinterest", stop_event)
                )
                try:
                    filepath = await _download_direct_url(
                        media["url"], tmpdir, media["is_video"], progress_state
                    )
                except Exception as e:
                    stop_event.set()
                    try:
                        await ticker
                    except Exception:
                        pass
                    log.info(f"[dl:{job_id}] pinterest direct download failed, falling back to yt-dlp: {e}")
                    filepath = None
                else:
                    stop_event.set()
                    try:
                        await ticker
                    except Exception:
                        pass

                if filepath:
                    caption = media.get("title") or None
                    send_path = filepath
                    v_duration = v_width = v_height = None
                    if media["is_video"]:
                        try:
                            send_path, v_duration, v_width, v_height = await asyncio.to_thread(
                                _fix_video_for_telegram, filepath, job_id
                            )
                        except Exception as e:
                            log.warning(f"[dl:{job_id}] pinterest video fixup failed: {e}")
                            send_path = filepath
                    # 🔴 Gate واقعی: اگه فایل (چه خام، چه بعد از remux/reencode)
                    # duration/stream سالم نداشته باشه، همین‌جا رد می‌شه — هرگز
                    # به‌عنوان «سالم» به تلگرام فرستاده نمی‌شه.
                    ok, reason = await asyncio.to_thread(_validate_media_file, send_path)
                    if not ok:
                        log.error(f"[dl:{job_id}] pinterest output failed validation: {reason} path={send_path!r}")
                        await status.edit_text(f"❌ دانلود انجام نشد\nعلت: 🩹 فایل دریافتی خراب بود ({reason})")
                        return
                    # 🖼️ آیتم ۵۲/۱۱: thumbnail فقط از فایل نهاییِ Gate-Passed ساخته
                    # می‌شه — نه قبل از Validate، نه از فایل موقت.
                    v_thumb = None
                    if media["is_video"]:
                        try:
                            v_thumb = await asyncio.to_thread(_make_thumbnail, send_path, v_duration, job_id)
                        except Exception as e:
                            log.info(f"[dl:{job_id}] pinterest thumbnail step failed: {e}")
                    thumb_f = None
                    thumbnail_used = False

                    async def _send_pinterest(with_thumb: bool):
                        tf = thumb_f if with_thumb else None
                        with open(send_path, "rb") as f:
                            if media["is_video"]:
                                await msg.reply_video(
                                    f, caption=caption, supports_streaming=True,
                                    duration=int(v_duration) if v_duration else None,
                                    width=v_width or None, height=v_height or None,
                                    thumbnail=tf,
                                )
                            else:
                                await msg.reply_photo(f, caption=caption)

                    try:
                        if v_thumb and os.path.exists(v_thumb):
                            thumb_f = open(v_thumb, "rb")
                            thumbnail_used = True
                        log.info(f"[dl:{job_id}] pinterest send: thumbnail_attached={thumb_f is not None} "
                                  f"v_thumb_path={v_thumb!r}")
                        try:
                            await _send_pinterest(with_thumb=True)
                            _log_job(job_id, platform=platform, url=url, stage="sent",
                                      output_path=send_path, file_size=os.path.getsize(send_path))
                        except Exception as e_thumb:
                            if not thumbnail_used:
                                raise
                            # 🔁 آیتم ۱۳/۱۴ چک‌لیست: قبل از افت به Document، همون
                            # فایل رو با همون نوع اصلی (video/photo) ولی بدون
                            # thumbnail دوباره امتحان کن.
                            log.warning(f"[dl:{job_id}] pinterest send with thumbnail failed "
                                        f"({e_thumb}); retrying without thumbnail")
                            if thumb_f:
                                try:
                                    thumb_f.close()
                                except Exception:
                                    pass
                                thumb_f = None
                            await _send_pinterest(with_thumb=False)
                            _log_job(job_id, platform=platform, url=url, stage="sent-without-thumbnail",
                                      output_path=send_path, file_size=os.path.getsize(send_path))
                        try:
                            await status.delete()
                        except Exception:
                            pass
                        return
                    except Exception as e:
                        # فقط وقتی حتی بدون thumbnail هم شکست خورد (یا thumbnail
                        # اصلاً درگیر نبود) به Document افت می‌کنیم، نه مستقیم شکست.
                        log.warning(f"[dl:{job_id}] pinterest send failed, trying document fallback: {e}")
                        try:
                            with open(send_path, "rb") as f:
                                await msg.reply_document(f, caption=caption)
                            _log_job(job_id, platform=platform, url=url, stage="sent-as-document")
                            try:
                                await status.delete()
                            except Exception:
                                pass
                        except Exception as e2:
                            # همون فالبک نهایی «بدون reply» که تو مسیر اصلی هم اضافه شد —
                            # اگه پیام کاربر پاک شده باشه، reply_document شکست می‌خوره
                            # ولی send_document ساده (بدون رفرنس) هنوز جواب می‌ده.
                            try:
                                with open(send_path, "rb") as f:
                                    await context.bot.send_document(chat_id, f, caption=caption)
                                _log_job(job_id, platform=platform, url=url, stage="sent-as-document-no-reply")
                                try:
                                    await status.delete()
                                except Exception:
                                    pass
                            except Exception as e3:
                                log.exception(f"[dl:{job_id}] pinterest document fallback also failed")
                                try:
                                    await status.edit_text(
                                        "❌ ارسال فایل انجام نشد\nعلت: 📦 حجم فایل بیشتر از محدودیت مجاز است یا تلگرام موقتاً پاسخ نداد."
                                    )
                                except Exception:
                                    pass
                        return
                    finally:
                        if thumb_f:
                            try:
                                thumb_f.close()
                            except Exception:
                                pass
            if yt_dlp is None:
                await status.edit_text(
                    "❌ دانلود انجام نشد\nعلت: دانلود مستقیم از پینترست شکست خورد و yt-dlp هم نصب نیست تا Fallback بشه."
                )
                return

        header = {
            "youtube": "▶️ YouTube", "instagram": "📸 Instagram", "pinterest": "📌 Pinterest",
            "tiktok": "🎵 TikTok", "twitter": "🐦 X/Twitter", "soundcloud": "🎧 SoundCloud",
        }.get(platform, PLATFORM_LABELS.get(platform, ""))
        progress_state = {"status": "downloading", "total": 0, "downloaded": 0}
        stop_event = asyncio.Event()
        ticker = asyncio.create_task(_progress_ticker(status, progress_state, header, stop_event))

        start_ts = time.monotonic()
        try:
            filepath, info = await _download_with_retry(url, tmpdir, platform, job_id, progress_state)
        except asyncio.TimeoutError:
            log.warning(f"[dl:{job_id}] timeout platform={platform} url={url} user_id={uid} "
                        f"after={time.monotonic() - start_ts:.1f}s")
            stop_event.set()
            try:
                await ticker
            except Exception:
                pass
            await status.edit_text("❌ دانلود انجام نشد\nعلت: ⏱ زمان دانلود تمام شد.")
            return
        except Exception as e:
            # Traceback کامل فقط تو Log — هرگز به کاربر نشون داده نمی‌شه.
            log.exception(f"[dl:{job_id}] download failed platform={platform} url={url} user_id={uid}")
            stop_event.set()
            try:
                await ticker
            except Exception:
                pass
            reason, _ = _classify_download_error(str(e))
            await status.edit_text(f"❌ دانلود انجام نشد\nعلت: {reason}")
            return
        finally:
            stop_event.set()

        try:
            await ticker
        except Exception:
            pass

        download_duration = time.monotonic() - start_ts

        # 🃏 Carousel / چند-مدیای اینستاگرام و توییتر: اگه yt-dlp چند entry
        # برگردونده (noplaylist=False برای این دو پلتفرم، بالاتر تو _base_ydl_opts)،
        # مسیر واقعیِ هر entry رو با prepare_filename خودِ yt-dlp پیدا می‌کنیم —
        # نه Glob و نه حدسِ اسم فایل. اگه entry نبود، مسیر قدیمیِ تک‌فایل زیر
        # دست‌نخورده اجرا می‌شه.
        entries = info.get("entries") if isinstance(info, dict) else None
        if entries and platform in ("instagram", "twitter"):
            media_files = []
            try:
                with yt_dlp.YoutubeDL(_base_ydl_opts(tmpdir, platform)) as ydl2:
                    for entry in entries:
                        if not entry:
                            continue
                        try:
                            epath = ydl2.prepare_filename(entry)
                        except Exception:
                            continue
                        if epath and os.path.exists(epath):
                            media_files.append(epath)
            except Exception as e:
                log.info(f"[dl:{job_id}] carousel entry resolution failed: {e}")
                media_files = []

            if len(media_files) > 1:
                _log_job(job_id, platform=platform, url=url, stage="carousel", count=len(media_files))
                await status.edit_text(f"📦 {len(media_files)} فایل پیدا شد\n⚡ در حال پردازش و ارسال...")
                group = []
                opened = []
                try:
                    for epath in media_files[:10]:  # سقف Media Group تلگرام = ۱۰
                        eext = os.path.splitext(epath)[1].lower()
                        send_epath = epath
                        # 🐛 همون باگ «صفحه سیاه» تو مسیر Carousel هم وجود داشت:
                        # این‌جا هم قبلاً هیچ remux/faststart fix و هیچ validation
                        # اجرا نمی‌شد، برخلاف مسیر تک‌فایل زیر که داشت. الان یکسان شد.
                        if eext in _VIDEO_EXTS:
                            try:
                                send_epath, _, _, _ = await asyncio.to_thread(
                                    _fix_video_for_telegram, epath, job_id
                                )
                            except Exception as e:
                                log.warning(f"[dl:{job_id}] carousel entry fixup failed: {e}")
                                send_epath = epath
                        # 🔴 همون Gate سخت‌گیرانه: آیتم خراب/بدون duration معتبر رد
                        # می‌شه (و بقیه‌ی گالری ارسال می‌شه)، نه اینکه به‌عنوان سالم بره.
                        ok, reason = await asyncio.to_thread(_validate_media_file, send_epath)
                        if not ok:
                            log.warning(f"[dl:{job_id}] carousel entry skipped, invalid: {reason} path={send_epath!r}")
                            continue  # آیتم خراب رد می‌شه، بقیه‌ی گالری ارسال می‌شه
                        # 🖼️ آیتم ۵۲/۱۱: thumbnail فقط بعد از Gate و فقط برای ویدیو.
                        e_thumb = None
                        if eext in _VIDEO_EXTS:
                            try:
                                e_thumb = await asyncio.to_thread(_make_thumbnail, send_epath, None, job_id)
                            except Exception as e:
                                log.info(f"[dl:{job_id}] carousel thumbnail step failed: {e}")
                        f = open(send_epath, "rb")
                        opened.append(f)
                        if _looks_like_image(send_epath, eext):
                            group.append(InputMediaPhoto(f))
                        else:
                            thumb_f = None
                            if e_thumb and os.path.exists(e_thumb):
                                thumb_f = open(e_thumb, "rb")
                                opened.append(thumb_f)
                            log.info(f"[dl:{job_id}] carousel entry thumbnail_attached={thumb_f is not None} "
                                      f"e_thumb_path={e_thumb!r}")
                            group.append(InputMediaVideo(f, supports_streaming=True, thumbnail=thumb_f))
                    if not group:
                        await status.edit_text(
                            "❌ ارسال انجام نشد\nعلت: 🩹 هیچ‌کدام از فایل‌های این پست معتبر/سالم نبودند."
                        )
                        return
                    await msg.reply_media_group(media=group)
                    _log_job(job_id, platform=platform, url=url, stage="sent",
                              count=len(group), download_duration=round(download_duration, 1))
                finally:
                    for f in opened:
                        try:
                            f.close()
                        except Exception:
                            pass
                try:
                    await status.delete()
                except Exception:
                    pass
                return
            elif len(media_files) == 1:
                # فقط یه entry معتبر Resolve شد — همون رو به‌جای مسیر سطح‌بالا
                # (که برای دیکشنری‌های نوع Playlist ممکنه معتبر نباشه) به‌عنوان
                # filepath واقعی استفاده می‌کنیم و با همون منطق تک‌فایلِ زیر ادامه می‌دیم.
                filepath = media_files[0]
            # اگه اصلاً entry معتبری Resolve نشد، از همین‌جا با filepath سطح‌بالای
            # قبلی ادامه می‌دیم؛ اگه اونم نامعتبر باشه، چک زیر (Output Missing)
            # با پیام صادقانه (نه «فایل پیدا نشد») گزارشش می‌ده.

        if not filepath or not os.path.exists(filepath):
            # 🧠 دقیقاً همون چیزی که نباید بشه: هیچ‌وقت «فایل پیدا نشد» ساده به
            # کاربر نشون داده نمی‌شه. اینجا Debug کامل تو Log ثبت می‌شه (لیست
            # واقعیِ پوشه‌ی Job + کلیدهای info) تا علت واقعی قابل‌ردیابی باشه،
            # و به کاربر یه علت مشخص (نه یه پیام عمومی گمراه‌کننده) داده می‌شه.
            try:
                tmp_listing = os.listdir(tmpdir)
            except Exception:
                tmp_listing = "N/A"
            log.error(
                f"[dl:{job_id}] output missing platform={platform} url={url} user_id={uid} "
                f"expected_path={filepath!r} tmpdir_listing={tmp_listing!r} "
                f"info_keys={sorted(info.keys()) if isinstance(info, dict) else 'N/A'}"
            )
            await status.edit_text(
                "❌ دانلود انجام نشد\nعلت: پلتفرم فایل خروجی معتبری برنگردوند — معمولاً یعنی فرمت "
                "این پست/رسانه توسط ربات پشتیبانی نمی‌شه."
            )
            return

        real_size = os.path.getsize(filepath)
        mime_type, _ = mimetypes.guess_type(filepath)
        title = (info.get("title") or "").strip()
        size_line = f"📦 حجم: {_human_size(real_size)}"
        caption = f"{title}\n{size_line}" if title else size_line

        _log_job(
            job_id, platform=platform, url=url, user_id=uid, chat_id=chat_id,
            output_path=filepath, file_size=real_size, mime_type=mime_type,
            download_duration=round(download_duration, 1), extractor=info.get("extractor"),
            selected_format=info.get("format_id"), stage="downloaded",
        )

        ext = os.path.splitext(filepath)[1].lower()
        send_path = filepath
        v_duration = v_width = v_height = None
        if ext in _VIDEO_EXTS and platform != "soundcloud":
            # رفع باگ «۰۰:۰۰ / صفحه سیاه»: قبل از ارسال، duration/width/height/
            # codec/container رو با ffprobe چک و در صورت نیاز با ffmpeg
            # (remux سریع، یا در آخرین حالت re-encode) درست می‌کنیم. این کار
            # تو ترد جدا انجام می‌شه تا event loop ربات قفل نشه.
            try:
                send_path, v_duration, v_width, v_height = await asyncio.to_thread(
                    _fix_video_for_telegram, filepath, job_id
                )
            except Exception as e:
                log.warning(f"[dl:{job_id}] video fixup failed, sending raw file: {e}")
                send_path = filepath

        # 🔴 Gate واقعی و نهایی: چه فایل خام باشه، چه بعد از remux/reencode —
        # اگه duration/stream/ابعاد سالم نباشه، همین‌جا رد می‌شه و هیچ‌وقت
        # به‌عنوان «سالم» به تلگرام نمی‌ره (آیتم‌های ۷-۱۰ چک‌لیست).
        ok, reason = await asyncio.to_thread(_validate_media_file, send_path)
        if not ok:
            log.error(f"[dl:{job_id}] final output failed validation: {reason} path={send_path!r}")
            await status.edit_text(f"❌ دانلود انجام نشد\nعلت: 🩹 فایل دریافتی سالم نبود ({reason})")
            return

        # 🖼️ آیتم ۵۲/۱۱: thumbnail صریح فقط از فایلِ نهاییِ Gate-Passed ساخته
        # می‌شه (نه قبل از Validate، نه از فایل موقت) — مخصوصاً برای فایل‌های
        # حجیم/طولانی که تلگرام خودش نمی‌تونه زود thumbnail بسازه.
        v_thumb = None
        if ext in _VIDEO_EXTS and platform != "soundcloud":
            try:
                v_thumb = await asyncio.to_thread(_make_thumbnail, send_path, v_duration, job_id)
            except Exception as e:
                log.info(f"[dl:{job_id}] thumbnail step failed: {e}")

        thumb_f = None
        thumbnail_used = False

        async def _send_main(with_thumb: bool):
            """یه تلاش ارسال فایل اصلی؛ with_thumb=False یعنی همون نوع فایل
            (video/audio/photo) ولی صریحاً بدون پارامتر thumbnail — برای
            Retry بعد از شکست مخصوص thumbnail (چک‌لیست آیتم ۱۳/۱۴)، بدون
            این‌که به Document افت کنیم."""
            tf = thumb_f if with_thumb else None
            with open(send_path, "rb") as f:
                if _looks_like_image(send_path, ext):
                    await msg.reply_photo(f, caption=caption or None)
                elif platform == "soundcloud" or ext in _AUDIO_EXTS:
                    await msg.reply_audio(f, caption=caption or None, title=title or None)
                else:
                    await msg.reply_video(
                        f, caption=caption or None, supports_streaming=True,
                        duration=int(v_duration) if v_duration else None,
                        width=v_width or None, height=v_height or None,
                        thumbnail=tf,
                    )

        try:
            if v_thumb and os.path.exists(v_thumb):
                thumb_f = open(v_thumb, "rb")
                thumbnail_used = True
            if ext in _VIDEO_EXTS and platform != "soundcloud":
                log.info(f"[dl:{job_id}] main send: thumbnail_attached={thumb_f is not None} "
                          f"v_thumb_path={v_thumb!r}")
            try:
                await _send_main(with_thumb=True)
                _log_job(job_id, platform=platform, url=url, stage="sent")
            except Exception as e_thumb:
                if not thumbnail_used:
                    raise  # thumbnail اصلاً درگیر نبوده -> مستقیم بره به fallback بیرونی (document)
                # 🔁 آیتم ۱۳/۱۴ چک‌لیست: شکست ارسال با thumbnail نباید مستقیم به
                # Document افت کنه — چون معمولاً خودِ thumbnail (نه فایل اصلی)
                # باعث BadRequest تلگرام بوده، اول همون فایل با همون نوع اصلی
                # (video/audio/photo) ولی بدون thumbnail دوباره امتحان می‌شه.
                log.warning(f"[dl:{job_id}] send with thumbnail failed ({e_thumb}); "
                            f"retrying same file without thumbnail")
                if thumb_f:
                    try:
                        thumb_f.close()
                    except Exception:
                        pass
                    thumb_f = None
                await _send_main(with_thumb=False)
                _log_job(job_id, platform=platform, url=url, stage="sent-without-thumbnail")
            # 🧪 آیتم ۳ چک‌لیست — فقط وقتی DL_DEBUG_COMPARE_UPLOAD=1 باشه، و فقط
            # برای ویدیو (نه عکس/صدا): همون FINAL FILE رو یه‌بار دیگه هم با
            # send_document می‌فرستیم تا مقایسه‌ی Video در مقابل Document انجام
            # بشه. خطای این بخش هرگز نباید ارسال اصلی (که موفق شده) رو خراب کنه.
            if DEBUG_COMPARE_UPLOAD and ext in _VIDEO_EXTS and platform != "soundcloud":
                try:
                    with open(send_path, "rb") as fdoc:
                        await msg.reply_document(
                            fdoc, caption="🧪 DEBUG: همین فایل به‌صورت Document (برای مقایسه با Video بالا)"
                        )
                    _log_job(job_id, platform=platform, url=url, stage="debug-document-compare-sent")
                except Exception as e:
                    log.warning(f"[dl:{job_id}] debug compare-upload (document) failed: {e}")
        except Exception as e:
            # اینجا یعنی: یا thumbnail اصلاً درگیر نبود و ارسال اصلی خودش
            # شکست خورد، یا حتی بعد از Retry-بدون-thumbnail هم شکست خورد —
            # فقط تو همین حالت واقعاً به Document افت می‌کنیم.
            log.warning(f"[dl:{job_id}] send failed, fallback to document: {e}")
            try:
                with open(filepath, "rb") as f:
                    await msg.reply_document(f, caption=caption or None)
                _log_job(job_id, platform=platform, url=url, stage="sent-as-document")
            except Exception as e2:
                # 🐛 اگه حتی reply_document هم شکست خورد (مثلاً چون پیام اصلی
                # کاربر دیگه رو دیسک تلگرام وجود نداره — حذف‌شده توسط یه
                # فیچر دیگه‌ی گروه)، قبل از تسلیم کامل، یه‌بار بدون رفرنس به
                # پیام اصلی (send_document ساده، نه reply) امتحان می‌کنیم؛
                # این‌جوری فایل بالاخره به‌دست کاربر می‌رسه، نه یه پیام خطای
                # گمراه‌کننده («حجم زیاده») درحالی‌که مشکل چیز دیگه‌ای بود.
                try:
                    with open(filepath, "rb") as f:
                        await context.bot.send_document(chat_id, f, caption=caption or None)
                    _log_job(job_id, platform=platform, url=url, stage="sent-as-document-no-reply")
                except Exception as e3:
                    log.exception(f"[dl:{job_id}] document fallback also failed")
                    try:
                        await status.edit_text(
                            "❌ ارسال فایل انجام نشد\nعلت: 📦 حجم فایل بیشتر از محدودیت مجاز است یا تلگرام "
                            "موقتاً پاسخ نداد."
                        )
                    except Exception:
                        pass
                    return
        finally:
            if thumb_f:
                try:
                    thumb_f.close()
                except Exception:
                    pass

        # 🧹 Cleanup: فقط بعد از ارسال موفق فایل حذف می‌شه؛ چون فایل داخل
        # TemporaryDirectory هست، خروج از بلوک with همین‌جا کل پوشه‌ی Job رو
        # (فایل خام + فایل‌های remux/re-encode احتمالی) پاک می‌کنه — نه زودتر.
        try:
            await status.delete()
        except Exception:
            pass


# =========================================================
#  🦇 BATMAN DOWNLOADER — PHASE 1: YouTube Quality Probe
# =========================================================
# این بخش کاملاً افزوده (additive) است و به هیچ Handler یا مسیر فعلی وصل
# نشده — یعنی رفتار دانلودر فعلی (منو، لینک مستقیم، دانلود، ارسال، Fallback)
# دقیقاً دست‌نخورده می‌ماند. فقط زیرساختی که Phase 2 (UI انتخاب کیفیت) و
# Phase 3 (دانلود با کیفیت انتخابی) بهش نیاز دارن، اینجا آماده می‌شه.
#
# هدف Phase 1: از روی فرمت‌های واقعیِ yt-dlp (با extract_info و
# download=False — یعنی بدون دانلود واقعی، دقیقاً طبق قانون پروژه) یک
# لیست تمیز از کیفیت‌های واقعاً موجود (360/480/720/1080 + Audio) بسازیم.
# هیچ کیفیت فیک/حدسی ساخته نمی‌شه: اگه یه سطح (مثلاً 480) واقعاً تو
# فرمت‌های ویدیو نبود، اصلاً تو خروجی این تابع هم نمی‌آد — همون‌طور که
# قرار بود Phase 2 دکمه‌ش رو نسازه.

_QUALITY_BUCKETS = (360, 480, 720, 1080, 1440, 2160)


def _closest_quality_bucket(height):
    """نزدیک‌ترین سطح استاندارد (360/480/720/1080) به یه height واقعی رو
    برمی‌گردونه (مثلاً 640x360 -> 360). اختلاف مجاز تا ۲۰٪ height هدفه تا
    از یه طرف height های نامتعارف (240p قدیمی، 4K) به یه باکت غلط
    نچسبن، از طرف دیگه height های خیلی‌نزدیک-ولی-نه-دقیق (مثل 714 برای
    720p) درست تشخیص داده بشن. اگه به هیچ باکتی نزدیک نبود، None
    برمی‌گردونه (یعنی این فرمت اصلاً تو منو نشون داده نمی‌شه)."""
    if not height:
        return None
    best, best_diff = None, None
    for bucket in _QUALITY_BUCKETS:
        diff = abs(height - bucket)
        if diff <= bucket * 0.2 and (best_diff is None or diff < best_diff):
            best, best_diff = bucket, diff
    return best


def _extract_quality_options(info: dict) -> dict:
    """از خروجی خامِ yt-dlp (extract_info با download=False) یه dict تمیز
    و مصرف‌محورِ Phase 2/3 می‌سازه.

    خروجی:
        {
          "title": str یا None,
          "duration": ثانیه (float/int) یا None,
          "thumbnail": URL یا None,
          "qualities": [
              {"height": 360|480|720|1080, "format_id": "...",
               "ext": "mp4"/"webm"/..., "has_audio": bool,
               "approx_size": بایت یا None, "fps": عدد یا None},
              ...
          ],  # مرتب‌شده از کیفیت پایین به بالا؛ فقط سطح‌های واقعاً موجود
          "audio_available": bool,  # آیا حداقل یه فرمت audio-only واقعی هست
        }

    نکته‌ی مهم برای Phase 3: format_id اینجا صرفاً برای شناسایی/نمایشه.
    خودِ انتخاب نهایی دانلود در Phase 3 باید طبق همون الگوی فعلی پروژه
    (bestvideo[height<=X]+bestaudio/best...) با fallback ساخته بشه، نه
    قفل‌شدن مستقیم به یه format_id واحد و شکننده.
    """
    if not isinstance(info, dict):
        return {"title": None, "duration": None, "thumbnail": None,
                "qualities": [], "audio_available": False}

    formats = info.get("formats") or []
    by_bucket = {}
    audio_available = False

    for f in formats:
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")

        is_audio_only = (vcodec in (None, "none")) and acodec not in (None, "none")
        if is_audio_only:
            audio_available = True
            continue

        if vcodec in (None, "none"):
            continue  # نه ویدیو نه صدای مستقل (مثلاً storyboard/mhtml) — رد کن

        bucket = _closest_quality_bucket(f.get("height"))
        if bucket is None:
            continue

        candidate = {
            "height": bucket,
            "real_height": f.get("height"),
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "has_audio": acodec not in (None, "none"),
            "approx_size": f.get("filesize") or f.get("filesize_approx"),
            "fps": f.get("fps"),
        }

        prev = by_bucket.get(bucket)
        if prev is None:
            by_bucket[bucket] = candidate
            continue
        # اگه چند فرمت رو یه باکت افتادن (مثلاً چند رمزینه‌ی مختلف برای
        # همون 720p)، اولویت با اونی که approx_size مشخص‌تر/fps بالاتر
        # داره — نماینده‌ی مطمئن‌تری برای Phase 3 خواهد بود.
        prev_score = (prev["approx_size"] or 0, prev["fps"] or 0)
        cur_score = (candidate["approx_size"] or 0, candidate["fps"] or 0)
        if cur_score > prev_score:
            by_bucket[bucket] = candidate

    qualities = [by_bucket[b] for b in _QUALITY_BUCKETS if b in by_bucket]

    thumb = info.get("thumbnail")
    if not thumb:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url")

    return {
        "title": (info.get("title") or "").strip() or None,
        "duration": info.get("duration"),
        "thumbnail": thumb,
        "qualities": qualities,
        "audio_available": audio_available,
    }


async def probe_youtube_qualities(url: str, timeout: int = 20):
    """Phase 1 — فقط برای YouTube. Metadata کامل + همه‌ی فرمت‌های واقعی رو
    بدون دانلود واقعی (download=False) می‌گیره و با _extract_quality_options
    به یه لیست کیفیت تمیز تبدیل می‌کنه.

    عمداً از _yt_dlp_probe فعلی (که فقط برای پیش‌نمایش SoundCloud صدا زده
    می‌شه، بدون formats کامل) جدا نوشته شده — تا هیچ رفتار فعلی (منو/لینک
    مستقیم/دانلود/ارسال/SoundCloud preview) تغییر نکنه. این یه تابع کاملاً
    جدید و افزوده‌ست که فعلاً به هیچ Handler وصل نیست؛ Phase 2 آن را به UI
    وصل خواهد کرد.

    خروجی موفق: dict طبق _extract_quality_options.
    خروجی شکست (لینک نامعتبر/شبکه/Timeout و ...): None — فراخوان (Phase 2)
    باید حالت «کیفیت‌ها قابل‌دریافت نیست» را خودش با پیام مناسب مدیریت کند؛
    این تابع هیچ Exception خامی رو بیرون نمی‌ده."""
    if yt_dlp is None:
        return None

    opts = {**_base_ydl_opts(tempfile.gettempdir(), "youtube"), "skip_download": True}
    # سقف حجم (max_filesize) و format selector فقط برای دانلود واقعی
    # معنی دارن (Phase 3)؛ اینجا فقط داریم لیست فرمت‌ها رو می‌خونیم، پس
    # هیچ‌کدوم نباید جلوی extract_info رو بگیرن.
    opts.pop("max_filesize", None)
    opts.pop("format", None)

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
    except Exception as e:
        log.info(f"[quality-probe] youtube probe failed for {url!r}: {e}")
        return None

    if not isinstance(info, dict):
        return None

    return _extract_quality_options(info)


# =========================================================
#  🦇 BATMAN DOWNLOADER — PHASE 2: Quality Selection UI (YouTube)
# =========================================================
# این بخش هم کاملاً افزوده است. مسیرهای قبلی (پینترست، اینستاگرام/توییتر
# Carousel، تیک‌تاک، ساندکلاود، و حتی خودِ یوتیوب در صورت شکست Probe)
# دقیقاً همون Pipeline قبلی (تک‌فایل، خط ۱۵۱۶ به بعد) رو طی می‌کنن — این
# بخش فقط یه مسیر جایگزین و اختیاری برای یوتیوب اضافه می‌کنه.

# token(str) -> {"url","uid","chat_id","qualities","audio_available","title","duration"}
_QUALITY_SESSIONS = {}
_QUALITY_SESSION_MAX = 200  # جلوگیری از رشد بی‌نهایت حافظه در RAM Railway؛
                             # وقتی پر شد، قدیمی‌ترین Session (احتمالاً رهاشده) حذف می‌شه.


def _store_quality_session(data: dict) -> str:
    if len(_QUALITY_SESSIONS) >= _QUALITY_SESSION_MAX:
        oldest = next(iter(_QUALITY_SESSIONS), None)
        if oldest is not None:
            _QUALITY_SESSIONS.pop(oldest, None)
    token = uuid.uuid4().hex[:10]
    _QUALITY_SESSIONS[token] = data
    return token


def _quality_menu_markup(token: str, qualities: list, audio_available: bool) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for q in qualities:
        row.append(InlineKeyboardButton(f"🦇 {q['height']}P", callback_data=f"dlq:pick:{token}:{q['height']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # ⚡ «بهترین کیفیت»: انتخاب خودکار بهترین فرمت موجود (مطابق چک‌لیست #6)،
    # همیشه نشون داده می‌شه وقتی حداقل یه کیفیت ویدیویی موجوده — چون فرمت
    # پیش‌فرض _YOUTUBE_FORMAT همیشه یه fallback امن (حتی best[ext=mp4]/best) داره.
    if qualities:
        rows.append([InlineKeyboardButton("⚡ BEST QUALITY", callback_data=f"dlq:pick:{token}:best")])
    if audio_available:
        rows.append([InlineKeyboardButton("🎧 AUDIO", callback_data=f"dlq:pick:{token}:audio")])
    rows.append([InlineKeyboardButton("❌ CANCEL", callback_data=f"dlq:cancel:{token}")])
    return InlineKeyboardMarkup(rows)


def _quality_preview_text(data: dict) -> str:
    lines = ["🦇 GOTHAM DOWNLOADER", "", "🎬 Video detected"]
    title = data.get("title")
    if title:
        lines.append(f"Title: {title}")
    dur = data.get("duration")
    if dur:
        total = int(dur)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        lines.append(f"Duration: {dur_str}")
    lines.append("")
    lines.append("⚡ SELECT QUALITY")
    return "\n".join(lines)


async def _offer_youtube_quality_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, job_id: str) -> bool:
    """تلاش می‌کنه منوی انتخاب کیفیت رو برای یه لینک یوتیوب نشون بده.

    خروجی True یعنی «منو با موفقیت نشون داده شد، فراخوان (downloader_link_handler)
    باید همین‌جا return کنه — ادامه‌ی کار به downloader_quality_callback سپرده
    شده». خروجی False یعنی «به هر دلیلی (probe شکست خورد، هیچ کیفیتی نبود،
    یا حتی نمایش خودِ منو شکست خورد) — فراخوان باید دقیقاً به مسیر قدیمیِ
    دانلود مستقیم با فرمت پیش‌فرض ادامه بده»؛ یعنی این تابع هیچ‌وقت باعث
    نمی‌شه یه لینک یوتیوبِ معتبر بدون پاسخ بمونه."""
    msg = update.effective_message
    try:
        probe_msg = await msg.reply_text("🦇 GOTHAM DOWNLOADER\n🔍 در حال دریافت اطلاعات ویدیو...")
    except Exception as e:
        log.info(f"[dl:{job_id}] quality probe status message failed, falling back to default flow: {e}")
        return False

    data = await probe_youtube_qualities(url)
    if not data or (not data["qualities"] and not data["audio_available"]):
        _log_job(job_id, platform="youtube", url=url, stage="quality-probe-empty")
        try:
            await probe_msg.delete()
        except Exception:
            pass
        return False

    session = {
        "url": url,
        "uid": update.effective_user.id,
        "chat_id": update.effective_chat.id,
        "qualities": data["qualities"],
        "audio_available": data["audio_available"],
        "title": data["title"],
        "duration": data["duration"],
    }
    token = _store_quality_session(session)
    preview_text = _quality_preview_text(data)
    markup = _quality_menu_markup(token, data["qualities"], data["audio_available"])

    # 🖼️ رفع کمبود «Thumbnail قبل از دانلود کامل نمایش داده شود»: قبلاً این
    # منو فقط متن (عنوان/مدت) بود؛ data["thumbnail"] (که _extract_quality_options
    # از قبل استخراج می‌کرد) هیچ‌جا استفاده نمی‌شد. حالا اگه URL تصویر موجود
    # باشه، مستقیم با همون URL (بدون دانلود دستی توسط ربات) به‌عنوان عکس
    # فرستاده می‌شه — تلگرام خودش سریع می‌گیردش، هیچ ffmpeg/درخواست اضافه‌ای
    # لازم نیست. اگه ارسال عکس به هر دلیلی (URL نامعتبر/شبکه) شکست خورد،
    # بی‌صدا به همون منوی متنیِ قبلی برمی‌گردیم — منو هیچ‌وقت گم نمی‌شه.
    thumb_url = data.get("thumbnail")
    menu_shown = False
    if thumb_url:
        try:
            await msg.reply_photo(photo=thumb_url, caption=preview_text, reply_markup=markup)
            menu_shown = True
            try:
                await probe_msg.delete()
            except Exception:
                pass
        except Exception as e:
            log.info(f"[dl:{job_id}] thumbnail preview send failed, falling back to text menu: {e}")

    if not menu_shown:
        try:
            await probe_msg.edit_text(preview_text, reply_markup=markup)
            menu_shown = True
        except Exception as e:
            log.info(f"[dl:{job_id}] quality menu render failed, falling back to default flow: {e}")
            _QUALITY_SESSIONS.pop(token, None)
            try:
                await probe_msg.delete()
            except Exception:
                pass
            return False

    _log_job(job_id, platform="youtube", url=url, stage="quality-menu-shown",
              qualities=[q["height"] for q in data["qualities"]], audio=data["audio_available"],
              thumbnail_shown=bool(thumb_url))
    return True


async def _run_youtube_quality_download(context: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int,
                                          url: str, quality, job_id: str, status):
    """🦇 Phase 2/3 orchestration برای مسیر «انتخاب کیفیت». از همون توابع
    کمکیِ سطح‌پایینِ Pipeline اصلی (validate/fix/thumbnail/retry/classify)
    استفاده می‌کنه — هیچ منطق پردازش فایل تکراری نوشته نشده، فقط ترتیب
    فراخوانی برای این مسیر (که با دکمه شروع می‌شه، نه با ریپلای مستقیم به
    پیام کاربر) پیاده‌سازی شده؛ برای همین از context.bot.send_* به‌جای
    msg.reply_* استفاده می‌شه — دقیقاً همون الگوی «ارسال بدون reply» که
    برای حالت‌های لبه‌ای (پیام اصلی حذف‌شده) در Pipeline قدیمی هم وجود داره."""
    header = "▶️ YouTube"
    _log_job(job_id, platform="youtube", url=url, user_id=uid, chat_id=chat_id, stage="start", quality=quality)

    with tempfile.TemporaryDirectory() as tmpdir:
        progress_state = {"status": "downloading", "total": 0, "downloaded": 0}
        stop_event = asyncio.Event()
        ticker = asyncio.create_task(_progress_ticker(status, progress_state, header, stop_event))

        start_ts = time.monotonic()
        try:
            filepath, info = await _download_with_retry(url, tmpdir, "youtube", job_id, progress_state, quality=quality)
        except asyncio.TimeoutError:
            stop_event.set()
            try:
                await ticker
            except Exception:
                pass
            await status.edit_text("❌ دانلود انجام نشد\nعلت: ⏱ زمان دانلود تمام شد.")
            return
        except Exception as e:
            log.exception(f"[dl:{job_id}] quality-download failed url={url} user_id={uid} quality={quality}")
            stop_event.set()
            try:
                await ticker
            except Exception:
                pass
            reason, _ = _classify_download_error(str(e))
            await status.edit_text(f"❌ دانلود انجام نشد\nعلت: {reason}")
            return
        finally:
            stop_event.set()
        try:
            await ticker
        except Exception:
            pass

        if not filepath or not os.path.exists(filepath):
            log.error(f"[dl:{job_id}] quality-download output missing url={url} user_id={uid} quality={quality}")
            await status.edit_text("❌ دانلود انجام نشد\nعلت: پلتفرم فایل خروجی معتبری برنگردوند.")
            return

        real_size = os.path.getsize(filepath)
        title = (info.get("title") or "").strip() if isinstance(info, dict) else ""
        caption = f"{title}\n📦 حجم: {_human_size(real_size)}" if title else f"📦 حجم: {_human_size(real_size)}"

        # ⚠️ اطلاع شفاف «کیفیت فallback» (چک‌لیست #17): وقتی کاربر مثلاً 1080p
        # رو انتخاب کرده ولی زنجیره‌ی fallback داخلیِ format selector (مثلاً
        # به‌خاطر سقف حجم تلگرام یا نبودِ اون کیفیت برای این ویدیوی خاص) یه
        # کیفیت پایین‌تر واقعی برگردونده، این‌جا صراحتاً به کاربر گفته می‌شه —
        # هیچ‌وقت بی‌صدا/مخفی داون‌گرید نمی‌شه.
        if isinstance(quality, int) and isinstance(info, dict):
            actual_height = info.get("height")
            if actual_height and actual_height < quality * 0.9:
                actual_bucket = _closest_quality_bucket(actual_height) or actual_height
                caption = (
                    f"⚠️ کیفیت {quality}p قابل ارسال نبود.\n"
                    f"🦇 کیفیت {actual_bucket}p جایگزین شد.\n\n" + caption
                )

        ext = os.path.splitext(filepath)[1].lower()
        send_path = filepath
        v_duration = v_width = v_height = None
        if ext in _VIDEO_EXTS:
            try:
                send_path, v_duration, v_width, v_height = await asyncio.to_thread(_fix_video_for_telegram, filepath, job_id)
            except Exception as e:
                log.warning(f"[dl:{job_id}] quality video fixup failed, sending raw file: {e}")
                send_path = filepath

        ok, reason = await asyncio.to_thread(_validate_media_file, send_path)
        if not ok:
            log.error(f"[dl:{job_id}] quality output failed validation: {reason} path={send_path!r}")
            await status.edit_text(f"❌ دانلود انجام نشد\nعلت: 🩹 فایل دریافتی سالم نبود ({reason})")
            return

        v_thumb = None
        if ext in _VIDEO_EXTS:
            try:
                v_thumb = await asyncio.to_thread(_make_thumbnail, send_path, v_duration, job_id)
            except Exception as e:
                log.info(f"[dl:{job_id}] quality thumbnail step failed: {e}")

        thumb_f = None
        try:
            if v_thumb and os.path.exists(v_thumb):
                thumb_f = open(v_thumb, "rb")
            with open(send_path, "rb") as f:
                if ext in (".mp3", ".m4a", ".opus", ".ogg", ".wav"):
                    await context.bot.send_audio(chat_id, f, caption=caption or None, title=title or None)
                elif ext in _VIDEO_EXTS:
                    await context.bot.send_video(
                        chat_id, f, caption=caption or None, supports_streaming=True,
                        duration=int(v_duration) if v_duration else None,
                        width=v_width or None, height=v_height or None, thumbnail=thumb_f,
                    )
                else:
                    await context.bot.send_document(chat_id, f, caption=caption or None)
            _log_job(job_id, platform="youtube", url=url, stage="sent", quality=quality)
        except Exception as e:
            log.warning(f"[dl:{job_id}] quality send failed, fallback to document: {e}")
            try:
                if thumb_f:
                    try:
                        thumb_f.close()
                    except Exception:
                        pass
                    thumb_f = None
                with open(filepath, "rb") as f:
                    await context.bot.send_document(chat_id, f, caption=caption or None)
                _log_job(job_id, platform="youtube", url=url, stage="sent-as-document")
            except Exception:
                log.exception(f"[dl:{job_id}] quality document fallback also failed")
                try:
                    await status.edit_text(
                        "❌ ارسال فایل انجام نشد\nعلت: 📦 حجم فایل بیشتر از محدودیت مجاز است یا تلگرام موقتاً پاسخ نداد."
                    )
                except Exception:
                    pass
                return
        finally:
            if thumb_f:
                try:
                    thumb_f.close()
                except Exception:
                    pass

        try:
            await status.delete()
        except Exception:
            pass


async def downloader_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر دکمه‌های منوی کیفیت (dlq:pick:<token>:<height|audio> یا
    dlq:cancel:<token>). دقیقاً یک‌بار‌مصرف: بعد از اولین کلیک، Session پاک
    می‌شه تا دوبار دانلود روی یه دکمه اتفاق نیفته."""
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else None
    token = parts[2] if len(parts) > 2 else None

    if action == "cancel":
        _QUALITY_SESSIONS.pop(token, None)
        try:
            await q.edit_message_text("❌ لغو شد.")
        except Exception:
            pass
        await q.answer()
        return

    session = _QUALITY_SESSIONS.get(token)
    if not session:
        try:
            await q.edit_message_text("⚠️ این منو منقضی شده. دوباره لینک رو بفرست.")
        except Exception:
            pass
        await q.answer()
        return

    if session["uid"] != q.from_user.id:
        await q.answer("این منو برای شما نیست.", show_alert=True)
        return

    choice = parts[3] if len(parts) > 3 else None
    _QUALITY_SESSIONS.pop(token, None)  # یک‌بار‌مصرف
    await q.answer()

    if choice == "audio":
        quality = "audio"
        label = "🎧 Audio"
    elif choice == "best":
        quality = "best"
        label = "⚡ Best"
    else:
        try:
            quality = int(choice)
        except (TypeError, ValueError):
            try:
                await q.edit_message_text("⚠️ گزینه‌ی نامعتبر.")
            except Exception:
                pass
            return
        label = f"{quality}P"

    job_id = uuid.uuid4().hex[:10]
    try:
        status = await q.edit_message_text(f"⏳ در حال دانلود ({label})...")
    except Exception:
        status = await context.bot.send_message(session["chat_id"], f"⏳ در حال دانلود ({label})...")

    await _run_youtube_quality_download(
        context, session["chat_id"], session["uid"], session["url"], quality, job_id, status,
    )


# =========================================================
#  🎬🎵 اینستاگرام Video/Reel و تیک‌تاک — منوی انتخاب Video/Audio
# =========================================================
# دقیقاً همون الگوی PHASE 2 یوتیوب (بالاتر): یه لایه‌ی UI اختیاریِ افزوده.
# اگه probe شکست بخوره یا عکس/Carousel تشخیص داده بشه، handled=False می‌شه
# و کد به همون مسیر قدیمیِ دانلود مستقیم (که خودش عکس/Carousel رو درست
# مدیریت می‌کنه) سقوط می‌کنه — هیچ رفتار فعلی از دست نمی‌ره.

_MEDIA_CHOICE_SESSIONS = {}
_MEDIA_CHOICE_MAX = 200


def _store_media_choice_session(data: dict) -> str:
    if len(_MEDIA_CHOICE_SESSIONS) >= _MEDIA_CHOICE_MAX:
        oldest = next(iter(_MEDIA_CHOICE_SESSIONS), None)
        if oldest is not None:
            _MEDIA_CHOICE_SESSIONS.pop(oldest, None)
    token = uuid.uuid4().hex[:10]
    _MEDIA_CHOICE_SESSIONS[token] = data
    return token


async def _offer_media_choice_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, platform: str, job_id: str) -> bool:
    msg = update.effective_message
    try:
        probe_msg = await msg.reply_text("🦇 GOTHAM DOWNLOADER\n🔍 در حال دریافت اطلاعات...")
    except Exception as e:
        log.info(f"[dl:{job_id}] media-choice probe status failed, falling back to default flow: {e}")
        return False

    try:
        info = await asyncio.wait_for(asyncio.to_thread(_yt_dlp_probe, url, platform), timeout=20)
    except Exception as e:
        log.info(f"[dl:{job_id}] media-choice probe failed/timeout: {e}")
        info = None

    if not isinstance(info, dict):
        try:
            await probe_msg.delete()
        except Exception:
            pass
        return False

    # 📚 Carousel (چند entry) — این مسیر UI فقط برای Video تکیه؛ Carousel
    # دقیقاً با همون منطق قدیمیِ اثبات‌شده (پایین‌تر تو downloader_link_handler)
    # مدیریت می‌شه، پس همین‌جا بی‌صدا کنار می‌کشیم.
    entries = info.get("entries")
    entry = None
    if entries:
        try:
            entries_list = [e for e in entries if e]
        except Exception:
            entries_list = []
        if len(entries_list) > 1:
            try:
                await probe_msg.delete()
            except Exception:
                pass
            return False
        if len(entries_list) == 1:
            entry = entries_list[0]

    probe_target = entry or info
    formats = probe_target.get("formats") or []
    has_video = any((f.get("vcodec") not in (None, "none")) for f in formats)
    if not has_video:
        # 🖼️ عکس تکی — مسیر قدیمیِ ارسال مستقیم (reply_photo) دست‌نخورده می‌مونه.
        try:
            await probe_msg.delete()
        except Exception:
            pass
        return False

    title = (probe_target.get("title") or probe_target.get("description") or "").strip()
    if len(title) > 200:
        title = title[:200]
    thumb = probe_target.get("thumbnail")
    if not thumb:
        thumbs = probe_target.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url")
    duration = probe_target.get("duration")

    session = {
        "url": url, "platform": platform,
        "uid": update.effective_user.id, "chat_id": update.effective_chat.id,
    }
    token = _store_media_choice_session(session)

    header = "📸 Instagram" if platform == "instagram" else "🎵 TikTok"
    lines = [f"🦇 {header}", ""]
    if title:
        lines.append(title)
    if duration:
        total = int(duration)
        m, s = divmod(total, 60)
        lines.append(f"⏱ مدت: {m:02d}:{s:02d}")
    lines.append("")
    lines.append("گزینه رو انتخاب کن:")
    preview_text = "\n".join(lines)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Video", callback_data=f"dlm:pick:{token}:video"),
         InlineKeyboardButton("🎵 Audio", callback_data=f"dlm:pick:{token}:audio")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"dlm:cancel:{token}")],
    ])

    menu_shown = False
    if thumb:
        try:
            await msg.reply_photo(photo=thumb, caption=preview_text, reply_markup=markup)
            menu_shown = True
            try:
                await probe_msg.delete()
            except Exception:
                pass
        except Exception as e:
            log.info(f"[dl:{job_id}] media-choice thumbnail send failed, falling back to text menu: {e}")

    if not menu_shown:
        try:
            await probe_msg.edit_text(preview_text, reply_markup=markup)
            menu_shown = True
        except Exception as e:
            log.info(f"[dl:{job_id}] media-choice menu render failed, falling back to default flow: {e}")
            _MEDIA_CHOICE_SESSIONS.pop(token, None)
            try:
                await probe_msg.delete()
            except Exception:
                pass
            return False

    _log_job(job_id, platform=platform, url=url, stage="media-choice-menu-shown", thumbnail_shown=bool(thumb))
    return True


async def _send_media_choice_result(context, chat_id, send_path, kind, title, job_id, status,
                                      v_duration=None, v_width=None, v_height=None, v_thumb=None, note=None):
    """ارسال نهایی برای مسیر Video/Audio (اینستاگرام/تیک‌تاک) — دقیقاً همون
    الگوی send+fallback-to-document که مسیر یوتیوب/تک‌فایل هم استفاده می‌کنه."""
    real_size = os.path.getsize(send_path)
    caption = f"{title}\n📦 حجم: {_human_size(real_size)}" if title else f"📦 حجم: {_human_size(real_size)}"
    if note:
        caption = f"{note}\n{caption}"
    thumb_f = None
    try:
        if v_thumb and os.path.exists(v_thumb):
            thumb_f = open(v_thumb, "rb")
        with open(send_path, "rb") as f:
            if kind == "audio":
                await context.bot.send_audio(chat_id, f, caption=caption or None, title=title or None)
            else:
                await context.bot.send_video(
                    chat_id, f, caption=caption or None, supports_streaming=True,
                    duration=int(v_duration) if v_duration else None,
                    width=v_width or None, height=v_height or None, thumbnail=thumb_f,
                )
        _log_job(job_id, stage="sent", kind=kind)
    except Exception as e:
        log.warning(f"[dl:{job_id}] media-choice send failed, fallback to document: {e}")
        try:
            if thumb_f:
                try:
                    thumb_f.close()
                except Exception:
                    pass
                thumb_f = None
            with open(send_path, "rb") as f:
                await context.bot.send_document(chat_id, f, caption=caption or None)
            _log_job(job_id, stage="sent-as-document")
        except Exception:
            log.exception(f"[dl:{job_id}] media-choice document fallback also failed")
            try:
                await status.edit_text(
                    "❌ ارسال فایل انجام نشد\nعلت: 📦 حجم فایل بیشتر از محدودیت مجاز است یا تلگرام موقتاً پاسخ نداد."
                )
            except Exception:
                pass
            return
    finally:
        if thumb_f:
            try:
                thumb_f.close()
            except Exception:
                pass
    try:
        await status.delete()
    except Exception:
        pass


async def _run_media_choice_download(context: ContextTypes.DEFAULT_TYPE, chat_id: int, uid: int,
                                       url: str, platform: str, choice: str, job_id: str, status):
    header = "📸 Instagram" if platform == "instagram" else "🎵 TikTok"
    _log_job(job_id, platform=platform, url=url, user_id=uid, chat_id=chat_id, stage="start", choice=choice)

    with tempfile.TemporaryDirectory() as tmpdir:
        progress_state = {"status": "downloading", "total": 0, "downloaded": 0}
        stop_event = asyncio.Event()
        ticker = asyncio.create_task(_progress_ticker(status, progress_state, header, stop_event))

        try:
            filepath, info = await _download_with_retry(url, tmpdir, platform, job_id, progress_state)
        except asyncio.TimeoutError:
            stop_event.set()
            try:
                await ticker
            except Exception:
                pass
            await status.edit_text("❌ دانلود انجام نشد\nعلت: ⏱ زمان دانلود تمام شد.")
            return
        except Exception as e:
            log.exception(f"[dl:{job_id}] media-choice download failed url={url} choice={choice}")
            stop_event.set()
            try:
                await ticker
            except Exception:
                pass
            reason, _ = _classify_download_error(str(e))
            await status.edit_text(f"❌ دانلود انجام نشد\nعلت: {reason}")
            return
        finally:
            stop_event.set()
        try:
            await ticker
        except Exception:
            pass

        if not filepath or not os.path.exists(filepath):
            log.error(f"[dl:{job_id}] media-choice output missing url={url} choice={choice}")
            await status.edit_text("❌ دانلود انجام نشد\nعلت: پلتفرم فایل خروجی معتبری برنگردوند.")
            return

        title = (info.get("title") or "").strip() if isinstance(info, dict) else ""
        ext = os.path.splitext(filepath)[1].lower()

        if choice == "audio":
            try:
                audio_path = await asyncio.to_thread(_extract_audio_track, filepath, job_id)
            except Exception as e:
                log.warning(f"[dl:{job_id}] audio extraction crashed: {e}")
                audio_path = None
            if not audio_path:
                # 🛡️ «هیچ فایلی نباید به‌خاطر یک روش ناموفق از بین بره» —
                # استخراج صدا شکست خورد ولی خودِ ویدیو سالمه؛ به‌جای شکست کامل،
                # همون ویدیو ارسال می‌شه.
                log.warning(f"[dl:{job_id}] audio extraction failed, sending video instead")
                send_path = filepath
                v_duration = v_width = v_height = None
                if ext in _VIDEO_EXTS:
                    try:
                        send_path, v_duration, v_width, v_height = await asyncio.to_thread(
                            _fix_video_for_telegram, filepath, job_id
                        )
                    except Exception:
                        send_path = filepath
                ok, reason = await asyncio.to_thread(_validate_media_file, send_path)
                if not ok:
                    await status.edit_text(f"❌ استخراج صدا و ارسال ویدیو هم انجام نشد\nعلت: 🩹 فایل سالم نبود ({reason})")
                    return
                await _send_media_choice_result(
                    context, chat_id, send_path, "video", title, job_id, status,
                    v_duration=v_duration, v_width=v_width, v_height=v_height,
                    note="⚠️ استخراج صدا امکان‌پذیر نبود؛ ویدیوی اصلی ارسال شد.",
                )
                return
            ok, reason = await asyncio.to_thread(_validate_media_file, audio_path)
            if not ok:
                log.error(f"[dl:{job_id}] extracted audio failed validation: {reason}")
                await status.edit_text(f"❌ استخراج صدا انجام نشد\nعلت: 🩹 فایل صدا سالم نبود ({reason})")
                return
            await _send_media_choice_result(context, chat_id, audio_path, "audio", title, job_id, status)
            return

        # choice == "video"
        send_path = filepath
        v_duration = v_width = v_height = None
        if ext in _VIDEO_EXTS:
            try:
                send_path, v_duration, v_width, v_height = await asyncio.to_thread(
                    _fix_video_for_telegram, filepath, job_id
                )
            except Exception as e:
                log.warning(f"[dl:{job_id}] media-choice video fixup failed, sending raw file: {e}")
                send_path = filepath
        ok, reason = await asyncio.to_thread(_validate_media_file, send_path)
        if not ok:
            log.error(f"[dl:{job_id}] media-choice output failed validation: {reason}")
            await status.edit_text(f"❌ دانلود انجام نشد\nعلت: 🩹 فایل دریافتی سالم نبود ({reason})")
            return
        v_thumb = None
        if ext in _VIDEO_EXTS:
            try:
                v_thumb = await asyncio.to_thread(_make_thumbnail, send_path, v_duration, job_id)
            except Exception as e:
                log.info(f"[dl:{job_id}] media-choice thumbnail step failed: {e}")
        await _send_media_choice_result(
            context, chat_id, send_path, "video", title, job_id, status,
            v_duration=v_duration, v_width=v_width, v_height=v_height, v_thumb=v_thumb,
        )


async def downloader_media_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر دکمه‌های dlm:pick:<token>:<video|audio> و dlm:cancel:<token>.
    منوی مادر معمولاً یه پیامِ عکسه (Preview)، پس اول edit_message_caption
    امتحان می‌شه؛ اگه پیام متنی بود (وقتی thumbnail نبوده)، به edit_message_text
    افت می‌کنه."""
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1] if len(parts) > 1 else None
    token = parts[2] if len(parts) > 2 else None

    async def _edit_menu_message(text):
        try:
            await q.edit_message_caption(caption=text)
        except Exception:
            try:
                await q.edit_message_text(text)
            except Exception:
                pass

    if action == "cancel":
        _MEDIA_CHOICE_SESSIONS.pop(token, None)
        await _edit_menu_message("❌ لغو شد.")
        await q.answer()
        return

    session = _MEDIA_CHOICE_SESSIONS.get(token)
    if not session:
        await _edit_menu_message("⚠️ این منو منقضی شده. دوباره لینک رو بفرست.")
        await q.answer()
        return

    if session["uid"] != q.from_user.id:
        await q.answer("این منو برای شما نیست.", show_alert=True)
        return

    choice = parts[3] if len(parts) > 3 else None
    _MEDIA_CHOICE_SESSIONS.pop(token, None)  # یک‌بار‌مصرف
    await q.answer()

    label = "🎬 Video" if choice == "video" else "🎵 Audio"
    try:
        status = await context.bot.send_message(session["chat_id"], f"⏳ در حال دانلود ({label})...")
    except Exception:
        status = await q.message.reply_text(f"⏳ در حال دانلود ({label})...")

    job_id = uuid.uuid4().hex[:10]
    await _run_media_choice_download(
        context, session["chat_id"], session["uid"], session["url"], session["platform"],
        choice, job_id, status,
    )


def register_downloader(app):
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^\s*(دانلودر|دانلود)\s*$"), downloader_menu), group=6)
    app.add_handler(CallbackQueryHandler(downloader_pick_callback, pattern=r"^dl:pick:"), group=6)
    # 🦇 PHASE 2: دکمه‌های منوی انتخاب کیفیت یوتیوب (dlq:pick:.../dlq:cancel:...)
    app.add_handler(CallbackQueryHandler(downloader_quality_callback, pattern=r"^dlq:"), group=6)
    # 🎬🎵 دکمه‌های منوی Video/Audio اینستاگرام و تیک‌تاک (dlm:pick:.../dlm:cancel:...)
    app.add_handler(CallbackQueryHandler(downloader_media_choice_callback, pattern=r"^dlm:"), group=6)
    # این هندلر با هر پیام متنی یا هر پیام دارای caption (عکس/ویدیو با کپشن
    # لینک‌دار) چک می‌کنه که آیا لینک پشتیبانی‌شده‌ای توشه؛ وگرنه هیچ کاری نمی‌کنه
    # و بی‌صدا به بقیه‌ی هندلرها سپرده می‌شه (بدون هیچ اثری روی پیام‌های غیرمرتبط).
    app.add_handler(
        MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, downloader_link_handler),
        group=6,
    )
