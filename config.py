# -*- coding: utf-8 -*-
"""
gotham_ai/config.py
====================
تنظیمات ماژول «✨ امکانات جدید گاتهام» — همه چیز از Environment Variables
خونده می‌شه، هیچ API Key‌ای داخل کد hard-code نشده.

FreeLLMAPI (https://github.com/tashfeenahmed/freellmapi) یه سرور خودمیزبانه
(self-hosted) که پشت یه endpoint سازگار با OpenAI (/v1/...) قرار می‌گیره.
یعنی کاربر خودش یه instance از FreeLLMAPI رو (لوکال، روی سرور خودش، یا هر جای
دیگه) بالا می‌آره و کلیدهای providerها رو اونجا تنظیم می‌کنه؛ این ربات فقط به
همون endpoint وصل می‌شه — دقیقاً مثل یه کلاینت OpenAI معمولی.
"""

import os

# --- اتصال ---
FREELLMAPI_BASE_URL = os.getenv("FREELLMAPI_BASE_URL", "").rstrip("/")
FREELLMAPI_API_KEY = os.getenv("FREELLMAPI_API_KEY", "")
FREELLMAPI_TIMEOUT = float(os.getenv("FREELLMAPI_TIMEOUT", "120"))
FREELLMAPI_STREAM = os.getenv("FREELLMAPI_STREAM", "0") == "1"

# --- مدل‌های پیش‌فرض برای هر نقش (قابل override با env) ---
# اسم‌های "auto" / "auto-fast" / "auto-smart" مدل‌های مجازی روتینگ خودِ
# FreeLLMAPI هستن. اگه provider/مدلی که انتخاب شده در دسترس نبود، سیستم
# routing.py خودش به‌ترتیب باقی گزینه‌ها رو امتحان می‌کنه (فیل‌اوور).
FREELLMAPI_DEFAULT_MODEL = os.getenv("FREELLMAPI_DEFAULT_MODEL", "auto")
FREELLMAPI_FAST_MODEL = os.getenv("FREELLMAPI_FAST_MODEL", "auto-fast")
FREELLMAPI_SMART_MODEL = os.getenv("FREELLMAPI_SMART_MODEL", "auto-smart")
FREELLMAPI_CODING_MODEL = os.getenv("FREELLMAPI_CODING_MODEL", "auto")
FREELLMAPI_VISION_MODEL = os.getenv("FREELLMAPI_VISION_MODEL", "auto")
FREELLMAPI_REASONING_MODEL = os.getenv("FREELLMAPI_REASONING_MODEL", "auto-smart")
FREELLMAPI_IMAGE_MODEL = os.getenv("FREELLMAPI_IMAGE_MODEL", "")  # خالی = از /v1/models تشخیص بده
FREELLMAPI_VIDEO_MODEL = os.getenv("FREELLMAPI_VIDEO_MODEL", "")
FREELLMAPI_TTS_MODEL = os.getenv("FREELLMAPI_TTS_MODEL", "")
FREELLMAPI_STT_MODEL = os.getenv("FREELLMAPI_STT_MODEL", "")
FREELLMAPI_TTS_VOICE = os.getenv("FREELLMAPI_TTS_VOICE", "alloy")

# --- محدودیت‌ها ---
MAX_HISTORY_TURNS = int(os.getenv("GOTHAM_AI_MAX_HISTORY_TURNS", "12"))   # چند pair user/assistant تو context بمونه
MAX_HISTORY_CHARS = int(os.getenv("GOTHAM_AI_MAX_HISTORY_CHARS", "12000"))
SESSION_IDLE_TIMEOUT = int(os.getenv("GOTHAM_AI_SESSION_TIMEOUT", str(60 * 60)))  # ۱ ساعت بی‌کاری = پایان خودکار
MAX_FILE_CHARS = int(os.getenv("GOTHAM_AI_MAX_FILE_CHARS", "20000"))
TELEGRAM_TEXT_LIMIT = 4000  # کمی زیر محدودیت واقعی ۴۰۹۶ برای جای امن
CACHE_TTL_SECONDS = int(os.getenv("GOTHAM_AI_CACHE_TTL", "600"))

# ترتیب failover برای Chat: اول مدل انتخابی خودِ کاربر/نقش، بعد بقیه به ترتیب
CHAT_FAILOVER_CHAIN = [
    FREELLMAPI_DEFAULT_MODEL,
    FREELLMAPI_SMART_MODEL,
    FREELLMAPI_FAST_MODEL,
]

# کدهای خطا که باید failover رو فعال کنن
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

MODEL_LABELS = {
    "auto": "🧭 خودکار (Auto)",
    "auto-fast": "⚡ خودکار سریع (Auto Fast)",
    "auto-smart": "🧠 بهترین مدل موجود (Auto Smart)",
}


def is_configured() -> bool:
    return bool(FREELLMAPI_BASE_URL)


def missing_config_message() -> str:
    return (
        "🦇 *امکانات جدید گاتهام* هنوز وصل نیست.\n\n"
        "برای فعال‌سازی، این متغیرهای محیطی رو تو تنظیمات سرور (مثلاً Railway) اضافه کن:\n\n"
        "`FREELLMAPI_BASE_URL` — آدرس instance خودت از FreeLLMAPI (مثلاً "
        "`https://your-freellmapi.example.com/v1`)\n"
        "`FREELLMAPI_API_KEY` — کلید/توکن دسترسی (اگه instance‌ت نیاز داره)\n\n"
        "بعد از تنظیم و ری‌استارت ربات، این بخش خودش فعال می‌شه."
    )
