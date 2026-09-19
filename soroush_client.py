# -*- coding: utf-8 -*-
"""
soroush_client.py
==================
لایه‌ی جداگانه‌ی اتصال به Soroush Plus — طبق قانون پروژه، هیچ منطقی از این
ماژول مستقیم داخل bot.py کپی نشده. فقط از اینجا import می‌شه.

از کتابخونه‌ی واقعی و مستندشده‌ی SplusLib استفاده می‌کنه (نه یک API ساختگی):
    pip install spluslib
    https://github.com/Erfan-Mirdehghan/SplusLib

فقط از متدهای واقعاً مستندشده‌ی این کتابخونه استفاده شده:
    SplusClient(session_name)         -> یک اکانت لاگین‌شده (یک فایل Session)
    client.start(phone, code_callback=..., password=...)  -> لاگین اولیه
    client.stop()                      -> قطع تمیز اتصال بعد از لاگین
    async with SplusClient(...) as c:  -> Context Manager مستندشده برای
                                           استفاده‌های بعدی (Session از قبل موجود)
    c.get_me() / c.has_story() / c.get_user_stories() / c.download_story()

نکات امنیتی طبق درخواست:
    - شماره تلفن فقط از Environment Variable "SOROUSH_PHONE" خونده می‌شه،
      هیچ‌جا هاردکد نشده.
    - کد ورود هیچ‌وقت لاگ یا داخل دیتابیس ذخیره نمی‌شه؛ فقط لحظه‌ای برای
      احراز هویت به SplusLib پاس داده می‌شه (از طریق code_callback).
    - فایل Session یه Credential کامله (طبق مستندات خودِ SplusLib)؛ برای
      همین باید بیرون از GitHub بمونه و ایده‌آل روی یه Railway Volume ذخیره
      بشه تا بعد از هر Restart/Redeploy از بین نره.
"""

import os
import logging

log = logging.getLogger("batbot.soroush")

# --- شماره تلفن: فقط از ENV، هرگز هاردکد ---
SOROUSH_PHONE = os.getenv("SOROUSH_PHONE", "").strip()

# --- محل ذخیره‌ی Session ---
# دقیقاً همون قرارداد پروژه برای DB_PATH تو bot.py: اگه یه Railway Volume به
# مسیر /data وصل باشه ازش استفاده کن (پایدار بین Restart/Redeploy)، وگرنه یه
# پوشه‌ی محلی (که روی Railway بدون Volume با هر Deploy پاک می‌شه — هشدار تو
# گزارش نهایی داده شده).
_SESSION_DIR_DEFAULT = "/data/soroush_session" if os.path.isdir("/data") else "soroush_session_data"
SOROUSH_SESSION_DIR = os.getenv("SOROUSH_SESSION_DIR", _SESSION_DIR_DEFAULT)

try:
    os.makedirs(SOROUSH_SESSION_DIR, exist_ok=True)
except Exception as e:
    log.warning(f"⚠️ نتونستم پوشه‌ی Session سروش رو بسازم ({SOROUSH_SESSION_DIR}): {e}")

SOROUSH_SESSION_PATH = os.path.join(SOROUSH_SESSION_DIR, "soroush_plus")

if not os.path.isdir("/data"):
    log.warning(
        f"⚠️ SOROUSH_SESSION_DIR = {SOROUSH_SESSION_DIR} — پوشه‌ی /data پیدا نشد! "
        "Session سروش‌پلاس داره رو فایل‌سیستم موقتِ کانتینر ذخیره می‌شه و با هر "
        "Redeploy/Restart روی Railway از بین می‌ره. رفع دائمی: تو Railway یه Volume "
        "با Mount Path=/data بساز (دقیقاً مثل چیزی که برای دیتابیس ربات لازمه)."
    )

_lib_import_error = None
try:
    from spluslib import SplusClient, errors as splus_errors  # noqa: F401
except Exception as e:  # pragma: no cover - فقط وقتی dependency نصب نشده
    SplusClient = None
    splus_errors = None
    _lib_import_error = e
    log.error(f"⚠️ کتابخونه‌ی spluslib در دسترس نیست: {e}")


class SoroushNotConfigured(Exception):
    """SOROUSH_PHONE تنظیم نشده یا کتابخونه‌ی spluslib نصب نیست."""


def is_library_available() -> bool:
    return SplusClient is not None


def session_file_exists() -> bool:
    return os.path.exists(SOROUSH_SESSION_PATH + ".session")


async def get_me_safe():
    """اگه Session معتبره، اطلاعات اکانت رو برمی‌گردونه؛ وگرنه None (بدون
    Exception به بیرون -- برای دکمه‌های «وضعیت اتصال» و «تست اتصال»)."""
    if SplusClient is None or not session_file_exists():
        return None
    try:
        async with SplusClient(SOROUSH_SESSION_PATH) as client:
            me = await client.get_me()
            return me if me and me.get("id") else None
    except Exception as e:
        log.warning(f"⚠️ تست اتصال سروش ناموفق بود: {type(e).__name__}")
        return None


async def is_connected() -> bool:
    return await get_me_safe() is not None


async def start_login(code_callback, password=None):
    """روند واقعی لاگین: شماره (از ENV) -> ارسال کد -> code_callback منتظر کد
    وارد شده تو تلگرام می‌مونه -> احراز هویت -> ساخت/ذخیره‌ی خودکار Session
    توسط خودِ SplusLib روی دیسک (SOROUSH_SESSION_PATH + '.session').

    هیچ‌جای این تابع کد ورود یا شماره تلفن چاپ/لاگ نمی‌شه.
    """
    if SplusClient is None:
        raise SoroushNotConfigured(f"spluslib در دسترس نیست: {_lib_import_error}")
    if not SOROUSH_PHONE:
        raise SoroushNotConfigured("SOROUSH_PHONE تنظیم نشده")

    client = SplusClient(SOROUSH_SESSION_PATH)
    kwargs = {"code_callback": code_callback}
    if password is not None:
        kwargs["password"] = password
    try:
        me = await client.start(SOROUSH_PHONE, **kwargs)
        return me
    finally:
        try:
            await client.stop()
        except Exception:
            pass


async def with_client(fn):
    """یک عملیات دلخواه (fn: async def(client) -> ...) رو با Session موجود
    اجرا می‌کنه. برای has_story/get_user_stories/download_story استفاده می‌شه.
    اگه Session وجود نداشته باشه، SoroushNotConfigured raise می‌کنه."""
    if SplusClient is None:
        raise SoroushNotConfigured(f"spluslib در دسترس نیست: {_lib_import_error}")
    if not session_file_exists():
        raise SoroushNotConfigured("اکانت سروش‌پلاس متصل نیست")
    async with SplusClient(SOROUSH_SESSION_PATH) as client:
        return await fn(client)
