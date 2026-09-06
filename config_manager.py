# -*- coding: utf-8 -*-
"""
config_manager.py
==================
🦇 کانفیگ گاتهام — ارکستریشن کامل قابلیت:
    Source رایگان → دریافت واقعی → Decode → Parse → حذف Duplicate →
    تست واقعی (TCP) → انتخاب Client → Link/QR/TXT

register_gotham_config(app, deps):
    deps = {"owner_id": OWNER_ID}

هیچ VPS/سرور شخصی لازم نیست: کانفیگ‌ها مستقیم از دو Source عمومیِ گیت‌هاب
گرفته می‌شن (config_sources.py)، Parse/Dedupe می‌شن (config_parser.py) و با
یه اتصال TCP واقعی تست می‌شن (config_tester.py). نتیجه برای CONFIG_REFRESH_MINUTES
دقیقه Cache می‌شه تا هر بار کاربر دکمه بزنه دوباره از گیت‌هاب دانلود نشه.
"""

import io
import os
import time
import logging
from datetime import datetime
from urllib.parse import quote

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from config_sources import SOURCES, SOURCE_1_URL, SOURCE_2_URL, fetch_all_sources
from config_parser import extract_configs_from_text, dedupe
from config_tester import test_configs

log = logging.getLogger(__name__)

CACHE_KEY = "gotham_config_cache"
CONFIG_REFRESH_MINUTES = float(os.getenv("CONFIG_REFRESH_MINUTES", "20") or "20")
MAX_DISPLAY = 10  # سقف تعداد کانفیگ نمایش‌داده‌شده در هر بار، برای جلوگیری از شلوغیِ چت

GOTHAM_APPS = [
    ("npv", "NPV Tunnel"),
    ("v2raytun", "v2RayTun"),
    ("hiddify", "Hiddify"),
]
_APP_LABELS = dict(GOTHAM_APPS)

GOTHAM_CONFIG_TEXT = (
    "🦇 *کانفیگ گاتهام*\n\n"
    "کانفیگ‌های رایگان و عمومی V2Ray/Xray رو از منابع آزاد می‌گیرم، "
    "واقعاً تست می‌کنم و بهت می‌دم — بدون سرور شخصی، بدون هزینه.\n\n"
    "⚠️ این کانفیگ‌ها عمومی و رایگانن؛ پایداری و امنیت‌شون تضمین‌شده نیست، "
    "فقط هرچی واقعاً پیدا و تست بشه رو نشونت می‌دم."
)

APP_SELECT_TEXT = "📱 برنامه موردنظر را انتخاب کن:"

SUB_TEXT = (
    "🔄 *Subscription*\n\n"
    "هر دو Source خودشون یه فایل عمومیِ حاوی کانفیگ هستن و می‌تونی مستقیم "
    "به‌عنوان Subscription داخل Hiddify یا v2RayTun اضافه‌شون کنی:\n\n"
    f"1️⃣ `{SOURCE_1_URL}`\n\n"
    f"2️⃣ `{SOURCE_2_URL}`"
)

SOURCE_DOWN_TEXT = (
    "⚠️ در حال حاضر منبع کانفیگ در دسترس نیست.\n"
    "لطفاً بعداً دوباره تلاش کنید."
)


# ------------------------------------------------------------------
# ⌨️ کیبوردها
# ------------------------------------------------------------------

def gotham_config_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔧 دریافت کانفیگ", callback_data="gconf:apps")],
        [InlineKeyboardButton("📱 انتخاب برنامه", callback_data="gconf:apps")],
        [InlineKeyboardButton("📊 وضعیت منابع", callback_data="gconf:status")],
        [InlineKeyboardButton("🔄 بروزرسانی منابع", callback_data="gconf:refresh")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ])


def _app_select_keyboard():
    rows = [[InlineKeyboardButton(label, callback_data=f"gconf:pick:{key}")] for key, label in GOTHAM_APPS]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="gconf:menu")])
    return InlineKeyboardMarkup(rows)


def _results_header_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 دریافت Subscription", callback_data="gconf:sub")],
        [InlineKeyboardButton("📱 برنامه دیگه", callback_data="gconf:apps")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="gconf:menu")],
    ])


def _config_block_keyboard(sig: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 دریافت لینک", callback_data=f"gconf:link:{sig}"),
        InlineKeyboardButton("🔳 QR", callback_data=f"gconf:qr:{sig}"),
        InlineKeyboardButton("📄 TXT", callback_data=f"gconf:txt:{sig}"),
    ]])


# ------------------------------------------------------------------
# 🗄️ Cache
# ------------------------------------------------------------------

def _default_cache():
    return {
        "tested_at": None,          # زمان آخرین Refresh موفق (که واقعاً تست هم انجام شد)
        "last_attempt": None,
        "last_refresh_failed": False,
        "sources": {
            s["key"]: {
                "name": s["name"], "ok": False, "last_success": None,
                "raw_count": 0, "error": None, "userinfo": None,
            }
            for s in SOURCES
        },
        "configs": [],           # لیست نهایی Verified، مرتب‌شده بر اساس Latency
        "configs_by_sig": {},
        "total_parsed": 0,
        "verified_count": 0,
        "invalid_count": 0,
        "untested_count": 0,
    }


async def _refresh_cache(bot_data: dict, force: bool = False) -> dict:
    cache = bot_data.setdefault(CACHE_KEY, _default_cache())
    now = time.time()
    is_fresh = cache["tested_at"] is not None and (now - cache["tested_at"] < CONFIG_REFRESH_MINUTES * 60)
    if is_fresh and not force:
        return cache

    cache["last_attempt"] = now
    results = await fetch_all_sources()

    any_ok = False
    parsed_all = []
    for key, res in results.items():
        source_state = cache["sources"][key]
        if res["ok"]:
            any_ok = True
            source_state["ok"] = True
            source_state["last_success"] = now
            source_state["error"] = None
            source_state["userinfo"] = res.get("userinfo")
            cfgs = extract_configs_from_text(res["text"])
            source_state["raw_count"] = len(cfgs)
            parsed_all.extend(cfgs)
        else:
            source_state["ok"] = False
            source_state["error"] = res.get("error")
            # raw_count رو دست نمی‌زنیم تا آخرین عدد موفق قبلی برای نمایش بمونه

    if not any_ok:
        # هیچ Sourceی جواب نداد — داده‌ی قبلی (اگه بود) رو دست‌نخورده نگه می‌داریم
        # تا لااقل نتیجه‌ی قبلی همچنان قابل استفاده باشه، فقط پرچم شکست رو ست می‌کنیم.
        cache["last_refresh_failed"] = True
        return cache

    cache["last_refresh_failed"] = False
    deduped = dedupe(parsed_all)
    verified, tested_count, untested_count = await test_configs(deduped)

    cache["configs"] = verified
    cache["configs_by_sig"] = {c["sig"]: c for c in verified}
    cache["total_parsed"] = len(deduped)
    cache["verified_count"] = len(verified)
    cache["untested_count"] = untested_count
    cache["invalid_count"] = max(len(deduped) - len(verified) - untested_count, 0)
    cache["tested_at"] = now
    return cache


def _fmt_ts(ts):
    if not ts:
        return "هنوز موفق نبوده"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _fmt_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{val:.2f} {unit}"
        val /= 1024
    return f"{val:.2f} TB"


def _status_text(cache: dict) -> str:
    lines = ["🦇 *وضعیت منابع*", ""]
    for key, s in cache["sources"].items():
        icon = "🟢" if s["ok"] else "🔴"
        lines.append(f"{icon} {s['name']}")
        lines.append(f"آخرین بروزرسانی: {_fmt_ts(s['last_success'])}")
        if not s["ok"] and s.get("error"):
            lines.append(f"خطا: `{s['error']}`")
        ui = s.get("userinfo")
        if s["ok"] and ui:
            # این آمار واقعیه (از هدر subscription-userinfo خودِ Source)، نه چیزی
            # که ما ساخته باشیم — و مربوط به کل فایل Subscription است، نه یک
            # کانفیگ خاص.
            if ui.get("download") is not None:
                lines.append(f"📥 دانلود (کل Source): {_fmt_bytes(ui['download'])}")
            if ui.get("upload") is not None:
                lines.append(f"📤 آپلود (کل Source): {_fmt_bytes(ui['upload'])}")
            if ui.get("total") is not None:
                lines.append(f"📦 مجموع مجاز: {_fmt_bytes(ui['total'])}")
                used = (ui.get("upload") or 0) + (ui.get("download") or 0)
                if ui["total"] > 0:
                    remaining = max(ui["total"] - used, 0)
                    lines.append(f"📊 باقی‌مانده: {_fmt_bytes(remaining)}")
            if ui.get("expire"):
                try:
                    lines.append(f"⏳ انقضا: {datetime.fromtimestamp(ui['expire']).strftime('%Y-%m-%d')}")
                except Exception:
                    pass
        lines.append("")
    lines.append(f"📦 تعداد کل کانفیگ‌ها: {cache['total_parsed']}")
    lines.append(f"🟢 کانفیگ‌های قابل استفاده: {cache['verified_count']}")
    lines.append(f"🔴 کانفیگ‌های نامعتبر: {cache['invalid_count']}")
    if cache.get("untested_count"):
        lines.append(
            f"⚪️ غیرقابل تست با روش فعلی (مثل Hysteria2 که روی UDP کار می‌کنه): {cache['untested_count']}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------
# 📤 نمایش نتایج
# ------------------------------------------------------------------

async def _show_results(query, context, app_key: str):
    label = _APP_LABELS.get(app_key, app_key)
    try:
        await query.edit_message_text(
            f"⏳ در حال دریافت و تستِ واقعیِ کانفیگ‌ها برای {label}...\nممکنه چند ثانیه طول بکشه."
        )
    except Exception:
        pass

    cache = await _refresh_cache(context.application.bot_data, force=False)

    if not cache["configs"]:
        both_down = not cache["sources"]["radikal"]["ok"] and not cache["sources"]["matin"]["ok"]
        if both_down:
            text = SOURCE_DOWN_TEXT
        else:
            text = "😕 فعلاً هیچ کانفیگ قابل‌استفاده‌ای (تست‌شده و واقعاً در دسترس) پیدا نشد. کمی بعد دوباره امتحان کن."
        await query.message.reply_text(text, reply_markup=_app_select_keyboard())
        return

    configs = cache["configs"][:MAX_DISPLAY]
    header = (
        "🦇 *کانفیگ گاتهام*\n\n"
        f"📱 برنامه: {label}\n\n"
        f"🟢 {len(cache['configs'])} کانفیگ پیدا شد."
    )
    if len(cache["configs"]) > MAX_DISPLAY:
        header += f"\n_(فقط {MAX_DISPLAY} مورد با کمترین Latency نمایش داده می‌شه)_"
    await query.message.reply_text(header, reply_markup=_results_header_keyboard(), parse_mode="Markdown")

    for i, cfg in enumerate(configs, start=1):
        block = (
            "━━━━━━━━━━━━\n"
            f"🌐 کانفیگ #{i}\n"
            f"🔌 Protocol: {cfg['protocol']}\n"
            "📡 وضعیت: Verified (TCP)\n"
            f"⚡ Latency: {int(round(cfg['latency_ms']))}ms\n"
            "━━━━━━━━━━━━"
        )
        await query.message.reply_text(block, reply_markup=_config_block_keyboard(cfg["sig"]))


def _lookup_config(bot_data: dict, sig: str):
    cache = bot_data.get(CACHE_KEY) or {}
    return (cache.get("configs_by_sig") or {}).get(sig)


# ------------------------------------------------------------------
# 🎛 Callback اصلی
# ------------------------------------------------------------------

def register_gotham_config(app, deps: dict):
    owner_id = deps.get("owner_id")

    async def gconf_callback(update, context):
        query = update.callback_query
        data = query.data or ""
        bot_data = context.application.bot_data

        if data == "gconf:menu":
            await query.answer()
            await query.edit_message_text(
                GOTHAM_CONFIG_TEXT, reply_markup=gotham_config_main_keyboard(), parse_mode="Markdown"
            )
            return

        if data == "gconf:apps":
            await query.answer()
            await query.edit_message_text(APP_SELECT_TEXT, reply_markup=_app_select_keyboard())
            return

        if data.startswith("gconf:pick:"):
            app_key = data.split(":", 2)[2]
            await query.answer()
            await _show_results(query, context, app_key)
            return

        if data == "gconf:refresh":
            if owner_id is not None and query.from_user.id != owner_id:
                await query.answer("⛔ فقط مالک ربات می‌تونه بروزرسانی اجباری منابع رو بزنه.", show_alert=True)
                return
            await query.answer()
            try:
                await query.edit_message_text("🔄 در حال بروزرسانی اجباری منابع...")
            except Exception:
                pass
            cache = await _refresh_cache(bot_data, force=True)
            await query.edit_message_text(
                _status_text(cache), reply_markup=gotham_config_main_keyboard(), parse_mode="Markdown"
            )
            return

        if data == "gconf:status":
            await query.answer()
            try:
                await query.edit_message_text("📊 در حال بررسی وضعیت منابع...")
            except Exception:
                pass
            cache = await _refresh_cache(bot_data, force=False)
            await query.edit_message_text(
                _status_text(cache), reply_markup=gotham_config_main_keyboard(), parse_mode="Markdown"
            )
            return

        if data == "gconf:sub":
            await query.answer()
            await query.message.reply_text(SUB_TEXT, parse_mode="Markdown", disable_web_page_preview=True)
            return

        if data.startswith("gconf:link:"):
            sig = data.split(":", 2)[2]
            await query.answer()
            cfg = _lookup_config(bot_data, sig)
            if not cfg:
                await query.message.reply_text("⚠️ این کانفیگ منقضی شده. دوباره «دریافت کانفیگ» رو بزن.")
                return
            await query.message.reply_text(f"🔗 لینک کانفیگ:\n`{cfg['raw']}`", parse_mode="Markdown")
            return

        if data.startswith("gconf:qr:"):
            sig = data.split(":", 2)[2]
            await query.answer()
            cfg = _lookup_config(bot_data, sig)
            if not cfg:
                await query.message.reply_text("⚠️ این کانفیگ منقضی شده. دوباره «دریافت کانفیگ» رو بزن.")
                return
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=350x350&data={quote(cfg['raw'])}"
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(qr_url)
                    resp.raise_for_status()
                    await query.message.reply_photo(resp.content, caption=f"🔳 QR کانفیگ ({cfg['protocol']})")
            except Exception as e:
                log.info(f"gotham-config: qr failed: {e}")
                await query.message.reply_text("⚠️ ساخت QR الان جواب نداد، یه‌کم بعد دوباره امتحان کن.")
            return

        if data.startswith("gconf:txt:"):
            sig = data.split(":", 2)[2]
            await query.answer()
            cfg = _lookup_config(bot_data, sig)
            if not cfg:
                await query.message.reply_text("⚠️ این کانفیگ منقضی شده. دوباره «دریافت کانفیگ» رو بزن.")
                return
            file_bytes = io.BytesIO(cfg["raw"].encode("utf-8"))
            filename = f"gotham_config_{cfg['protocol'].lower()}_{sig}.txt"
            await query.message.reply_document(
                document=file_bytes, filename=filename, caption="📄 فایل کانفیگ"
            )
            return

    app.add_handler(CallbackQueryHandler(gconf_callback, pattern=r"^gconf:"), group=21)
