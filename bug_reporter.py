# -*- coding: utf-8 -*-
"""Gotham Bot - centralized error reporter.
Sends concise diagnostics to OWNER_ID and keeps a small in-memory recent-error list.
Never includes API keys or Authorization headers.
"""
import os
import time
import traceback
from collections import deque
from datetime import datetime, timezone

from telegram import Bot

from custom_emojis import ce, CUSTOM_EMOJIS

# پوشه‌ی خودِ پروژه — برای اینکه بین فریم‌های تراسبک «کد خودمون» و «کتابخونه‌های
# نصب‌شده (site-packages/telegram/...)» فرق بذاریم و محل واقعی وقوع خطا رو تو
# کدِ خودمون (نه عمق کتابخونه) گزارش کنیم.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _is_project_frame(filename: str) -> bool:
    try:
        return os.path.abspath(filename).startswith(_PROJECT_DIR) and (
            "site-packages" not in filename and "dist-packages" not in filename
        )
    except Exception:
        return False


def locate_exception(exc: BaseException) -> dict:
    """تراسبک یه exception رو دونه‌دونه می‌گرده و دو نقطه رو برمی‌گردونه:

    - 'handler': اولین فریمِ کدِ خودِ پروژه از بالای استک (یعنی نزدیک‌ترین
      تابعی که واقعاً توسط python-telegram-bot به‌عنوان Handler صدا زده شده).
    - 'origin': آخرین فریمِ کدِ خودِ پروژه (یعنی دقیقاً همون خطی که Exception
      واقعاً توش رخ داده — فایل/شماره‌خط/نام تابع).

    اگه اصلاً فریمی از کد خودمون تو تراسبک نبود (خیلی بعیده)، مقادیر None
    برمی‌گردن؛ چیزی جعل نمی‌شه.
    """
    result = {
        "handler_function": None, "handler_file": None,
        "origin_function": None, "origin_file": None, "origin_line": None,
    }
    try:
        tb = exc.__traceback__
        frames = traceback.extract_tb(tb)
        project_frames = [f for f in frames if _is_project_frame(f.filename)]
        if project_frames:
            first = project_frames[0]
            last = project_frames[-1]
            result["handler_function"] = first.name
            result["handler_file"] = os.path.basename(first.filename)
            result["origin_function"] = last.name
            result["origin_file"] = os.path.basename(last.filename)
            result["origin_line"] = last.lineno
    except Exception:
        pass
    return result

RECENT_ERRORS = deque(maxlen=20)

# دسته‌بندی خطاها طبق مشخصات (Exception/API/Handler/Database/AI/Downloader).
# چون فیلد kind یه رشته‌ی آزاده (هر فراخوانی remember_error هرچی بخواد می‌ده)،
# دسته‌بندی با تطبیق کلیدواژه رو خودِ همون داده‌ی واقعی انجام می‌شه — چیزی
# جعل نمی‌شه، فقط داده‌ی موجود مرتب می‌شه.
BUG_CATEGORIES = {
    # 🌐 خطاهای شبکه/اتصال (NetworkError، httpx.ReadError، TimedOut، Conflict و...) —
    # قبل از دسته‌های دیگه چک می‌شه چون معمولاً موقتی‌ان و نباید مثل باگ واقعی
    # برنامه با Owner گزارش بشن (throttle جدا داره — پایین‌تر، ببین is_network_error).
    "network": ("Network", ("network", "timeout", "timedout", "connect", "readerror",
                             "httpx", "httpcore", "connection", "readtimeout", "pooltimeout")),
    "api": ("API", ("api", "groq", "http", "download", "yt-dlp", "instaloader")),
    "handler": ("Handler", ("handler", "callback", "button", "keyboard")),
    "database": ("Database", ("db", "database", "sqlite", "sql")),
    "ai": ("AI", ("ai", "groq", "llm", "voice", "transcribe", "recognition")),
    "downloader": ("Downloader", ("downloader", "youtube", "instagram", "tiktok", "twitter", "pinterest", "soundcloud")),
    "exception": ("Exception", ()),  # پیش‌فرض/باقی‌مونده
}

# NetworkError/ConnectionError/httpx/httpcore — طبق دستور کار، این‌ها خطای
# «موقتی شبکه»ان، نه Bug اصلی برنامه. برای جلوگیری از اسپم شدن Owner با
# ده‌ها پیام یکسان (مثلاً وقتی اینترنت Railway چند دقیقه قطع/وصل می‌شه)،
# فقط یه‌بار در هر بازه‌ی _NETWORK_REPORT_COOLDOWN گزارش می‌فرستیم؛ در همون
# بازه هر تکرار بعدی فقط شمارش و لاگ می‌شه، نه ارسال پیام جدید.
_NETWORK_REPORT_COOLDOWN = 300  # ثانیه (۵ دقیقه)
_network_error_state = {"last_report_ts": 0.0, "count_since_report": 0}


def is_network_error(exc: BaseException) -> bool:
    """تشخیص می‌ده یه Exception از جنس خطای موقتی شبکه/اتصال به تلگرام هست یا نه —
    بدون نیاز به import مستقیم telegram.error (تا این ماژول به هیچ نسخه‌ی
    خاصی از کتابخونه وابسته نشه)، فقط از روی نام کلاس‌های شناخته‌شده."""
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                             httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
                             httpx.PoolTimeout, httpx.NetworkError)):
            return True
    except Exception:
        pass
    try:
        from telegram.error import NetworkError as TgNetworkError, TimedOut as TgTimedOut
        if isinstance(exc, (TgNetworkError, TgTimedOut)):
            return True
    except Exception:
        pass
    name = type(exc).__name__.lower()
    return any(k in name for k in ("networkerror", "timedout", "connecterror", "readerror",
                                    "connecttimeout", "readtimeout", "writetimeout", "pooltimeout"))


def should_report_network_error():
    """throttle: (should_send: bool, skipped_count: int) برمی‌گردونه.
    فقط اگه از آخرین گزارشِ فرستاده‌شده بیشتر از cooldown گذشته باشه
    should_send=True می‌شه (و شمارنده صفر می‌شه)؛ وگرنه فقط شمارنده بالا
    می‌ره و should_send=False می‌مونه — یعنی این‌بار به Owner گزارش
    نمی‌فرستیم، فقط لاگ می‌کنیم."""
    now = time.monotonic()
    if now - _network_error_state["last_report_ts"] >= _NETWORK_REPORT_COOLDOWN:
        _network_error_state["last_report_ts"] = now
        skipped = _network_error_state["count_since_report"]
        _network_error_state["count_since_report"] = 0
        return True, skipped
    _network_error_state["count_since_report"] += 1
    return False, _network_error_state["count_since_report"]


def _categorize(kind: str) -> str:
    k = (kind or "").lower()
    for cat_key, (_label, keywords) in BUG_CATEGORIES.items():
        if cat_key == "exception":
            continue
        if any(kw in k for kw in keywords):
            return cat_key
    return "exception"


def _clean(value, limit=1200):
    text = str(value or "").replace("`", "'")
    # قبلاً فقط GROQ_API_KEY و BOT_TOKEN سانسور می‌شدن؛ GROQ_API_KEY هیچ‌جای
    # پروژه استفاده نمی‌شه (باقی‌مونده‌ی یه تغییر قبلیه) و کلید واقعی که همه‌جا
    # به کار می‌ره (OPENROUTER_API_KEY) اصلاً تو این لیست نبود — یعنی اگه یه
    # خطای شبکه/HTTP متن کلید واقعی رو تو خودش داشت (مثلاً تو URL یا هدر)،
    # بدون سانسور مستقیم به پیام خطای اونر می‌رفت. الان همه‌ی کلیدهای حساس
    # پروژه سانسور می‌شن.
    for secret_name in (
        "OPENROUTER_API_KEY", "BOT_TOKEN", "TMDB_API_KEY", "AUDD_API_TOKEN", "GROQ_API_KEY",
    ):
        secret = os.getenv(secret_name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:limit]


def remember_error(kind, exc, *, chat_id=None, user_id=None, extra=None,
                    update_type=None, callback_data=None):
    """ثبت کامل یه خطا — شامل traceback کامل، محل دقیق وقوع (فایل/خط/تابع)،
    و context آپدیتی که باعثش شده. هیچ‌کدوم از این‌ها با try/except قورت داده
    نمی‌شن؛ اگه دیتایی موجود نباشه فقط None/خالی می‌مونه، جعل نمی‌شه."""
    loc = locate_exception(exc)
    # NetworkError/httpx.ReadError/TimedOut و... همیشه دسته‌ی "network" می‌گیرن،
    # صرف‌نظر از اینکه kind (مثلاً "handle_update") چه کلیدواژه‌ای داره —
    # چون این‌ها خطای موقتی اتصال‌ان، نه باگ واقعی همون بخش از برنامه.
    category = "network" if is_network_error(exc) else _categorize(kind)
    item = {
        "time": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": _clean(kind, 80),
        "category": category,
        "exc_type": type(exc).__name__,
        "error": _clean(f"{type(exc).__name__}: {exc}", 1000),
        "chat_id": chat_id,
        "user_id": user_id,
        "update_type": _clean(update_type, 60) if update_type else "",
        "callback_data": _clean(callback_data, 200) if callback_data else "",
        "handler_function": loc["handler_function"],
        "origin_file": loc["origin_file"],
        "origin_line": loc["origin_line"],
        "origin_function": loc["origin_function"],
        "extra": _clean(extra, 500) if extra else "",
        # از exc.__traceback__ مستقیم استفاده می‌شه (نه traceback.format_exc())
        # چون format_exc() فقط وقتی دقیقه که هنوز تو یه except فعال باشیم؛
        # exc.__traceback__ مستقل از اینه و همیشه traceback واقعیِ همون
        # exception رو می‌ده، حتی اگه remember_error دیرتر/از یه لایه‌ی
        # دیگه صدا زده بشه.
        "traceback": _clean(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), 3500
        ),
    }
    RECENT_ERRORS.appendleft(item)
    return item


def _h(value) -> str:
    """html.escape کوتاه، برای فیلدهای دینامیک قبل از رفتن تو پیام HTML."""
    import html as _html
    return _html.escape(str(value), quote=False)


def format_error(item, skipped_count=0):
    """گزارش با parse_mode="HTML" فرستاده می‌شه (برای پشتیبانی از Custom
    Emoji مرکزی)؛ برای همین همه‌ی فیلدهای دینامیک اینجا escape می‌شن تا
    traceback/متن خطا (که ممکنه کاراکتر < > & داشته باشه) پارس HTML رو
    خراب نکنه."""
    is_net = item.get("category") == "network"
    header_emoji = ce(CUSTOM_EMOJIS["warning"][0], "⚠️") if is_net else ce(CUSTOM_EMOJIS["breaking_news"][0], "🚨")
    header = "خطای موقتی شبکه (Transient Network Error)" if is_net else "خطای جدید ربات گاتهام"
    lines = [
        f"{header_emoji} <b>{header}</b>",
        "",
        f"🧩 بخش: {_h(item['kind'])}",
        f"❌ خطا: <code>{_h(item['error'])}</code>",
        f"🕐 زمان: {_h(item['time'])}",
    ]
    if is_net and skipped_count:
        lines.append(
            f"🔁 در {_NETWORK_REPORT_COOLDOWN // 60} دقیقه‌ی اخیر {skipped_count} بار دیگه هم تکرار شده (بی‌صدا نادیده گرفته شدن)."
        )
    # محل دقیق وقوع خطا (فایل/خط/تابع) — اگه از روی traceback پیدا شده باشه.
    if item.get("origin_file"):
        lines.append(
            f"📍 محل: <code>{_h(item['origin_file'])}</code> خط <code>{_h(item.get('origin_line'))}</code> — "
            f"تابع <code>{_h(item.get('origin_function'))}</code>"
        )
    if item.get("handler_function") and item.get("handler_function") != item.get("origin_function"):
        lines.append(f"🎯 Handler: <code>{_h(item['handler_function'])}</code> (<code>{_h(item.get('handler_file', ''))}</code>)")
    if item.get("update_type"):
        lines.append(f"📨 نوع Update: <code>{_h(item['update_type'])}</code>")
    if item.get("chat_id") is not None:
        lines.append(f"💬 Chat ID: <code>{_h(item['chat_id'])}</code>")
    if item.get("user_id") is not None:
        lines.append(f"👤 User ID: <code>{_h(item['user_id'])}</code>")
    if item.get("callback_data"):
        lines.append(f"🔘 Callback Data: <code>{_h(item['callback_data'])}</code>")
    if item.get("extra"):
        lines += ["", f"📌 جزئیات: {_h(item['extra'])}"]
    lines += ["", "📄 Traceback:", f"<pre>{_h(item['traceback'])}</pre>"]
    return "\n".join(lines)


async def report_error(bot: Bot, kind, exc, *, chat_id=None, user_id=None, extra=None):
    item = remember_error(kind, exc, chat_id=chat_id, user_id=user_id, extra=extra)
    owner_id = os.getenv("OWNER_ID") or os.getenv("ADMIN_ID")
    if not owner_id:
        # bot.py currently has a hard-coded OWNER_ID; caller can pass it through extra
        return item
    if is_network_error(exc):
        should_send, skipped = should_report_network_error()
        if not should_send:
            return item
    else:
        skipped = 0
    try:
        await bot.send_message(chat_id=int(owner_id), text=format_error(item, skipped_count=skipped), parse_mode="HTML")
    except Exception:
        pass
    return item


def recent_errors_text(limit=8):
    if not RECENT_ERRORS:
        return "🛠 *رفع باگ ربات*\n\n✅ فعلاً خطای ثبت‌شده‌ای در این اجرای ربات نداریم."
    lines = ["🛠 *رفع باگ ربات*", "", "🚨 آخرین خطاهای ثبت‌شده:", ""]
    for i, item in enumerate(list(RECENT_ERRORS)[:limit], 1):
        lines.append(f"{i}. `{item['time']}` — *{item['kind']}* — `{item['error'][:180]}`")
    lines += ["", "⚡️ خطاهای جدید به‌صورت خودکار برای مالک ربات ارسال می‌شوند."]
    return "\n".join(lines)


def category_counts():
    """چند تا خطا (تو حافظه‌ی همین اجرای ربات) تو هر دسته ثبت شده."""
    counts = {cat_key: 0 for cat_key in BUG_CATEGORIES}
    for item in RECENT_ERRORS:
        cat = item.get("category") or _categorize(item.get("kind", ""))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def errors_by_category_text(cat_key: str, limit=15):
    label = BUG_CATEGORIES.get(cat_key, (cat_key, ()))[0]
    items = [it for it in RECENT_ERRORS if (it.get("category") or _categorize(it.get("kind", ""))) == cat_key]
    if not items:
        return f"📜 *لاگ خطاها — {label}*\n\n✅ خطایی تو این دسته ثبت نشده."
    lines = [f"📜 *لاگ خطاها — {label}*", ""]
    for i, item in enumerate(items[:limit], 1):
        lines.append(f"{i}. `{item['time']}` — *{item['kind']}* — `{item['error'][:180]}`")
    return "\n".join(lines)


def clear_log():
    n = len(RECENT_ERRORS)
    RECENT_ERRORS.clear()
    return n


async def health_check_text(context) -> str:
    """🩺 GOTHAM HEALTH — بررسی سلامت اجزای اصلی ربات. هم زیر «رفع باگ ربات
    ← وضعیت ربات» و هم به‌عنوان Health Check مستقل (Phase 5) از همین یه تابع
    استفاده می‌شه — سیستم موازی ساخته نشده."""
    checks = []

    # Database — یه کوئری واقعی و سبک روی همون دیتابیس فعلی
    try:
        import bot as _bot
        conn = _bot._connect()
        conn.cursor().execute("SELECT 1")
        conn.close()
        checks.append(("Database", "🟢 OK", ""))
    except Exception as e:
        checks.append(("Database", "🔴 ERROR", _clean(e, 120)))

    # Handlers — تعداد Handlerهای واقعاً ثبت‌شده رو Application
    try:
        app = getattr(context, "application", None)
        total = sum(len(v) for v in app.handlers.values()) if app and getattr(app, "handlers", None) else 0
        checks.append(("Handlers", f"🟢 OK ({total} handler)" if total else "🟡 WARNING", ""))
    except Exception as e:
        checks.append(("Handlers", "🔴 ERROR", _clean(e, 120)))

    # Game Sessions — جمع بازی‌های فعال تو حافظه، از تمام ماژول‌های بازی
    try:
        active = 0
        for mod_name, attr_names in (
            ("card_room", ("WAR_GAMES", "BJ21_GAMES", "BLACKJACK_GAMES", "HOKM_GAMES",
                           "HAFT_GAMES", "CHARBARG_GAMES", "RUMMY_GAMES", "POKER_GAMES")),
            ("games_pack5", ("UNO_GAMES", "TER_GAMES", "BIL_GAMES", "RACE_GAMES")),
            ("group_rps", ("GRPS_GAMES",)),
            ("ttt_gotham", ("GTTT_GAMES",)),
        ):
            try:
                mod = __import__(mod_name)
                for attr in attr_names:
                    active += len(getattr(mod, attr, {}) or {})
            except Exception:
                continue
        checks.append(("Game Sessions", f"🟢 OK ({active} فعال)", ""))
    except Exception as e:
        checks.append(("Game Sessions", "🔴 ERROR", _clean(e, 120)))

    # Downloader
    try:
        import downloader  # noqa: F401
        checks.append(("Downloader", "🟢 OK", ""))
    except Exception as e:
        checks.append(("Downloader", "🔴 ERROR", _clean(e, 120)))

    # AI — قبلاً این چک روی GROQ_API_KEY بود، در حالی که موتور واقعی AI تو کل
    # پروژه (bot.py: call_ai، و media_recognition.py: تشخیص فیلم/آهنگ/خلاصه)
    # همه‌جا از OPENROUTER_API_KEY استفاده می‌کنن و GROQ_API_KEY هیچ‌جای دیگه‌ای
    # به کار نمی‌ره. نتیجه‌ی این باگ: حتی وقتی AI کاملاً سالم و فعال بود (چون
    # OPENROUTER_API_KEY ست شده)، این صفحه دروغ می‌گفت و WARNING نشون می‌داد.
    checks.append((
        "AI", "🟢 OK" if os.getenv("OPENROUTER_API_KEY") else "🟡 WARNING (بدون کلید OPENROUTER_API_KEY)", ""
    ))

    # Bug Reporter — همیشه OK چون داریم توش اجرا می‌شیم
    checks.append(("Bug Reporter", f"🟢 OK ({len(RECENT_ERRORS)} خطای اخیر تو حافظه)", ""))

    # Security
    try:
        import security_tools  # noqa: F401
        checks.append(("Security", "🟢 OK", ""))
    except Exception as e:
        checks.append(("Security", "🔴 ERROR", _clean(e, 120)))

    # Scheduler
    try:
        app = getattr(context, "application", None)
        has_jq = bool(app and getattr(app, "job_queue", None) is not None)
        checks.append(("Scheduler", "🟢 OK" if has_jq else "🟡 WARNING", ""))
    except Exception as e:
        checks.append(("Scheduler", "🔴 ERROR", _clean(e, 120)))

    lines = ["🩺 *GOTHAM HEALTH*", ""]
    for name, status, note in checks:
        lines.append(f"{status} — {name}" + (f" ({note})" if note else ""))
    return "\n".join(lines)
