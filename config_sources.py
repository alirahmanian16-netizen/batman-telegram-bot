# -*- coding: utf-8 -*-
"""
config_sources.py
==================
🦇 کانفیگ گاتهام — لایه‌ی دریافت خام (HTTP) از Sourceهای عمومی و رایگان.

فقط همین دو Source (طبق مشخصات) — هیچ URL حدسی یا اضافه‌ای اینجا نیست:
    SOURCE 1: 0xRadikal/Free-v2ray-Configs  (verified/configs_base64.txt)
    SOURCE 2: MatinGhanbari/v2ray-configs   (subscriptions/v2ray/all_sub.txt)

هر دو Source مستقل از هم Fetch می‌شن؛ اگه یکی Down بود، اون یکی همچنان کار
می‌کنه (مسئولیت این ماژول فقط گزارش دقیق نتیجه‌ی هر Source جداست، تصمیم
درباره‌ی ادامه‌ی کار با config_manager.py هست).

محافظت‌ها:
    - Timeout مشخص روی هر Request
    - Retry محدود (حداکثر ۲ بار) با یه‌کم فاصله بین تلاش‌ها
    - محدودیت حجم Response (که سرور رو یا حافظه‌ی ربات رو با یه فایل غول‌پیکر
      اشتباهی/مخرب به‌خطر نندازه)
    - هیچ Exception ای از این ماژول بیرون درز نمی‌کنه؛ همیشه دیکشنری نتیجه
      برمی‌گرده (ok=True/False)
"""

import asyncio
import logging
import re

import httpx

log = logging.getLogger(__name__)

# استاندارد رایج Subscription (v2rayN/Clash/...): یه خط کامنت شامل
# آمار واقعی ترافیک خودِ Source، مثل:
#   #subscription-userinfo: upload=123; download=456; total=789; expire=111
_USERINFO_RE = re.compile(
    r"subscription-userinfo\s*:\s*(.+)", re.IGNORECASE
)


def parse_subscription_userinfo(raw_text: str):
    """اگه Source واقعاً یه خط subscription-userinfo استاندارد داشته باشه،
    مقادیر واقعی upload/download/total/expire رو برمی‌گردونه (به بایت/Unix
    timestamp، همون‌طور که در Source اومده). اگه خط وجود نداشت یا همه‌ی
    مقادیرش صفر بود (یعنی هیچ اطلاعات واقعی معناداری نداره)، None برمی‌گردونه
    — هیچ مقدار جایگزین/حدسی ساخته نمی‌شه."""
    if not raw_text:
        return None
    m = _USERINFO_RE.search(raw_text[:2000])  # این خط همیشه نزدیک ابتدای فایله
    if not m:
        return None
    fields = {}
    for part in m.group(1).split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip().lower()
        val = val.strip()
        if key in ("upload", "download", "total", "expire"):
            try:
                fields[key] = int(val)
            except ValueError:
                continue
    if not fields:
        return None
    if not any(fields.get(k) for k in ("upload", "download", "total")):
        # همه صفر یا نامشخص -> چیزی برای نمایش واقعی نیست
        return None
    return fields

SOURCE_1_URL = "https://raw.githubusercontent.com/0xRadikal/Free-v2ray-Configs/main/verified/configs_base64.txt"
SOURCE_2_URL = "https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/v2ray/all_sub.txt"

SOURCES = [
    {"key": "radikal", "name": "0xRadikal", "url": SOURCE_1_URL},
    {"key": "matin", "name": "MatinGhanbari", "url": SOURCE_2_URL},
]

HTTP_TIMEOUT_SECONDS = 12.0
MAX_RETRIES = 2  # یعنی حداکثر ۳ تلاش کل (۱ اصلی + ۲ Retry)
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # ۵ مگابایت سقف، برای جلوگیری از دانلود حجیم ناخواسته


async def fetch_source(client: httpx.AsyncClient, src: dict) -> dict:
    """یه Source رو Fetch می‌کنه. هیچ‌وقت Exception پرتاب نمی‌کنه."""
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = await client.get(src["url"], timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
            resp.raise_for_status()
            content = resp.content or b""
            if len(content) > MAX_RESPONSE_BYTES:
                content = content[:MAX_RESPONSE_BYTES]
            text = content.decode("utf-8", errors="ignore")
            userinfo = parse_subscription_userinfo(text)
            return {
                "key": src["key"], "name": src["name"], "url": src["url"],
                "ok": True, "text": text, "error": None, "userinfo": userinfo,
            }
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"[:200]
            log.info(f"gotham-config: fetch failed for {src['name']} (attempt {attempt + 1}): {last_err}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.6 * (attempt + 1))
    return {
        "key": src["key"], "name": src["name"], "url": src["url"],
        "ok": False, "text": "", "error": last_err, "userinfo": None,
    }


async def fetch_all_sources() -> dict:
    """هر دو Source رو موازی Fetch می‌کنه. برگشتی: {source_key: result_dict}."""
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=5)) as client:
        results = await asyncio.gather(*[fetch_source(client, s) for s in SOURCES])
    return {r["key"]: r for r in results}
