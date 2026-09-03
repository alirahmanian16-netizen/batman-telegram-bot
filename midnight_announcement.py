# -*- coding: utf-8 -*-
"""
Midnight Gotham Announcement
-----------------------------
هر شب دقیقاً ساعت ۰۰:۰۰ به وقت تهران، برای همه‌ی گروه‌هایی که ربات توشونه
(خودکار از دیتابیس ربات پیدا می‌شن، نیازی به وارد کردن دستی chat_id نیست)،
تاریخ (فارسی + انگلیسی) + یه اسم رویداد گاتهامی + یه دیالوگ بتمنی می‌فرسته.

نصب پکیج تاریخ شمسی (اختیاری ولی پیشنهادی):
    pip install jdatetime

نحوه‌ی وصل کردن به bot.py (داخل تابع main، بعد از ساخت app):

    from midnight_announcement import register_midnight_job
    register_midnight_job(app)
"""

import time as time_module
import logging
import random
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from gotham_content import pick_event_name, pick_dialogue_line
from custom_emojis import ce, CUSTOM_EMOJIS, esc

try:
    import jdatetime
    HAS_JDATETIME = True
except ImportError:
    HAS_JDATETIME = False

try:
    from hijri_converter import Gregorian as _Gregorian
    HAS_HIJRI = True
except ImportError:
    HAS_HIJRI = False

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

WEEKDAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یک‌شنبه"]
MONTHS_FA = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
HIJRI_MONTHS_FA = [
    "محرم", "صفر", "ربیع‌الاول", "ربیع‌الثانی", "جمادی‌الاول", "جمادی‌الثانی",
    "رجب", "شعبان", "رمضان", "شوال", "ذی‌القعده", "ذی‌الحجه",
]


def _format_persian_date(now: datetime) -> str:
    if not HAS_JDATETIME:
        return "(برای تاریخ شمسی: pip install jdatetime)"
    jnow = jdatetime.datetime.fromgregorian(datetime=now)
    # 🐛 باگ روز هفته: WEEKDAYS_FA با ترتیب weekday() پایتونِ معمولی ساخته شده
    # (دوشنبه=۰ … یکشنبه=۶)، اما jdatetime.weekday() ترتیب خودش رو داره
    # (شنبه=۰ … جمعه=۶ — طبق خودِ کتابخونه). قبلاً با jnow.weekday() ایندکس
    # می‌خورد که غلط بود؛ روز هفته واقعی که ربات میلادی/شمسی نداره، همونیه
    # که تو خودِ آبجکت now (میلادی) هست، پس با now.weekday() (که با ترتیب
    # همین لیست هم‌خونه) درست می‌شه — دقیقاً همون کاری که _format_hijri_date
    # پایین‌تر همیشه درست انجام می‌داد.
    weekday = WEEKDAYS_FA[now.weekday()]
    month = MONTHS_FA[jnow.month - 1]
    return f"{weekday} {jnow.day} {month} {jnow.year} ساعت {now.strftime('%H:%M')}"


def _format_english_date(now: datetime) -> str:
    return now.strftime("%A, %B %d, %Y - %H:%M")


def _format_hijri_date(now: datetime) -> str:
    if not HAS_HIJRI:
        return None
    h = _Gregorian(now.year, now.month, now.day).to_hijri()
    weekday = WEEKDAYS_FA[now.weekday()]  # همون روز هفته‌ی میلادی/شمسی
    month = HIJRI_MONTHS_FA[h.month - 1]
    return f"{weekday} - {h.year}/{h.month:02d}/{h.day:02d} ({month})"


def _bar(fraction: float, length: int = 5) -> str:
    filled = max(0, min(length, round(fraction * length)))
    return "▰" * filled + "▱" * (length - filled)


def _jalali_year_progress(now: datetime):
    if not HAS_JDATETIME:
        return None
    jnow = jdatetime.datetime.fromgregorian(datetime=now)
    start = jdatetime.date(jnow.year, 1, 1)
    next_start = jdatetime.date(jnow.year + 1, 1, 1)
    total = (next_start - start).days
    passed = (jnow.date() - start).days + 1
    remaining = total - passed
    pct = passed / total
    return passed, remaining, pct


def _gregorian_year_progress(now: datetime):
    passed = now.timetuple().tm_yday
    year = now.year
    is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    total = 366 if is_leap else 365
    remaining = total - passed
    pct = passed / total
    return passed, remaining, pct


def build_full_datetime_text() -> str:
    """گزارش کامل و خفن تاریخ/ساعت به سبک گاتهام — برای دستور «تاریخ»/«ساعت»."""
    from gotham_content import gotham_signature_line

    now = datetime.now(TEHRAN_TZ)
    fa_str = _format_persian_date(now)
    en_str = _format_english_date(now)
    hijri_str = _format_hijri_date(now)

    lines = [
        "🦇 *تاریخ و ساعت گاتهام* 🦇",
        "〰️〰️〰️〰️〰️〰️〰️",
        f"⏰ ساعت : {now.strftime('%H:%M:%S')}",
        f"📅 تاریخ شمسی : {fa_str}",
    ]
    if hijri_str:
        lines.append(f"🌙 تاریخ قمری : {hijri_str}")
    else:
        lines.append("🌙 تاریخ قمری : (برای فعال شدن: pip install hijri-converter)")
    lines.append(f"☀️ تاریخ میلادی : {en_str}")
    lines.append("〰️〰️〰️〰️〰️〰️〰️")

    jp = _jalali_year_progress(now)
    if jp:
        passed, remaining, pct = jp
        lines += [
            "🦇 تا پایان سال شمسی",
            f"┘─ 📅 روزهای سپری‌شده : {passed} روز",
            f"┘─ ⌛️ روزهای باقی‌مانده : {remaining} روز",
            f"┘─ 🦇 {_bar(pct)} {pct * 100:.0f}%",
            "",
        ]

    gp = _gregorian_year_progress(now)
    passed, remaining, pct = gp
    lines += [
        "🌃 تا پایان سال میلادی",
        f"┘─ 📅 روزهای سپری‌شده : {passed} روز",
        f"┘─ ⌛️ روزهای باقی‌مانده : {remaining} روز",
        f"┘─ 🌃 {_bar(pct)} {pct * 100:.0f}%",
        "〰️〰️〰️〰️〰️〰️〰️",
        f"«{gotham_signature_line()}»",
    ]
    return "\n".join(lines)


def _get_all_chat_ids():
    """چت‌آیدی همه‌ی گروه‌هایی که ربات توشون فعاله رو از دیتابیس خود ربات می‌گیره -
    نیازی به هاردکد کردن نیست. ایمپورت داخل تابعه تا سیکل ایمپورت با bot.py پیش نیاد.
    فیلتر chat_id<0: طبق قرارداد تلگرام گروه/سوپرگروه همیشه منفیه؛ چت‌های خصوصی
    (که با _get_chat برای هر نوع چتی تو همین جدول ساخته می‌شن) نباید پیام
    نیمه‌شب/رویداد ماهانه رو به‌عنوان «گروه» دریافت کنن."""
    import bot as _bot
    conn = _bot._connect()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM chats WHERE chat_id < 0")
    ids = [row["chat_id"] for row in c.fetchall()]
    conn.close()
    return ids


def _special_event_for_date(now: datetime):
    """اگه امروز یه مناسبت خاصه، یه رویداد/دیالوگ ویژه برمی‌گردونه؛ وگرنه None."""
    if now.month == 10 and now.day == 31:
        return ("🎃 Arkham's Halloween — هالووین آرکهام", "امشب حتی مجرم‌ها هم نقاب می‌زنن؛ من فرقی نمی‌کنم.")
    if now.month == 12 and now.day == 21:
        return ("🕯️ The Longest Night — طولانی‌ترین شب سال", "امشب طولانی‌ترین شبیه؛ برای من هر شب طولانیه.")
    if now.weekday() == 4 and now.day == 13:  # جمعه‌ی سیزدهم
        return ("🩸 Friday the 13th in Gotham — جمعه‌ی سیزدهم گاتهام", "امشب حتی شانس هم از گاتهام فرار کرده.")
    return None


# --- 🦇💵 قیمت دلار برای پیام‌های نیمه‌شب/صبح ---
# قیمت ساعت ۰۰:۰۰ رو تو دیتابیس خود ربات (جدول meta، chat_id=0 → سطل
# سراسری/غیرگروهی) ذخیره می‌کنیم تا صبح بشه با قیمت ۰۸:۰۰ مقایسه‌ش کرد.
# اگه بین این دو، ربات ری‌استارت بشه هم مشکلی نیست: فقط مقایسه انجام
# نمی‌شه و پیام صبح بدون خط تغییر (خنثی) نمایش داده می‌شه.
_DOLLAR_META_CHAT_ID = 0
_DOLLAR_META_KEY = "midnight_dollar_price"


async def _fetch_dollar_price_safe():
    """برمی‌گردونه: price_toman یا None (اگه گرفتنش شکست خورد)."""
    try:
        from dollar_price import get_dollar_data
        data = await get_dollar_data()
        return data["price_toman"]
    except Exception as e:
        logging.getLogger(__name__).info(f"گرفتن قیمت دلار برای پیام نیمه‌شب/صبح شکست خورد: {e}")
        return None


def _save_midnight_dollar_price(price):
    try:
        import bot as _bot
        _bot._list_add(_DOLLAR_META_CHAT_ID, "meta", _DOLLAR_META_KEY, str(price))
    except Exception as e:
        logging.getLogger(__name__).info(f"ذخیره‌ی قیمت دلار نیمه‌شب شکست خورد: {e}")


def _load_midnight_dollar_price():
    try:
        import bot as _bot
        val = _bot._list_get_one(_DOLLAR_META_CHAT_ID, "meta", _DOLLAR_META_KEY)
        return float(val) if val else None
    except Exception:
        return None


def _dollar_line_night(price) -> str:
    dollar_emoji = ce(CUSTOM_EMOJIS["dollar"][0], "💵")
    return f"{dollar_emoji} دلار آزاد: {price:,.0f} تومان"


def _dollar_block_morning(price, midnight_price):
    """برمی‌گردونه: (خط متن دلار, جهت "up"/"down"/"flat")."""
    dollar_emoji = ce(CUSTOM_EMOJIS["dollar"][0], "💵")
    line = f"{dollar_emoji} دلار آزاد: {price:,.0f} تومان"
    if not midnight_price:
        return line, "flat"
    diff = price - midnight_price
    pct = (diff / midnight_price * 100) if midnight_price else 0.0
    if abs(pct) < 0.05:
        return line, "flat"
    if diff > 0:
        up = ce(CUSTOM_EMOJIS["dollar_up"][0], "📈")
        line += f"\n{up} +{diff:,.0f} تومان (+{pct:.2f}٪) نسبت به گزارش نیمه‌شب"
        return line, "up"
    down = ce(CUSTOM_EMOJIS["dollar_down"][0], "📉")
    line += f"\n{down} {diff:,.0f} تومان ({pct:.2f}٪) نسبت به گزارش نیمه‌شب"
    return line, "down"


async def midnight_announcement(context):
    now = datetime.now(TEHRAN_TZ)
    fa_str = _format_persian_date(now)
    en_str = _format_english_date(now)
    special = _special_event_for_date(now)
    if special:
        event, line = special
    else:
        event = pick_event_name()
        line = pick_dialogue_line()

    dollar_price = await _fetch_dollar_price_safe()
    if dollar_price is not None:
        _save_midnight_dollar_price(dollar_price)
        dollar_block = "\n\n" + _dollar_line_night(dollar_price)
    else:
        dollar_block = ""

    text = (
        "🌑 نیمه‌شب فرا رسید\n"
        f"📅 {esc(fa_str)}\n"
        f"📅 {esc(en_str)}\n\n"
        f"{esc(event)}\n"
        f"«{esc(line)}»"
        f"{dollar_block}"
    )

    try:
        chat_ids = _get_all_chat_ids()
    except Exception:
        chat_ids = []

    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception:
            pass  # اگه ربات از یه گروه حذف شده باشه یا خطای دیگه‌ای بخوره، بی‌خیال اون یکی شو

    try:
        import bot as _bot
        _bot._log_gotham_event(event, line)
    except Exception as e:
        logging.getLogger(__name__).warning(f"ثبت رویداد نیمه‌شب گاتهام شکست خورد: {e}")

    # 🏆 اول هر ماه (میلادی)، شوالیه‌ی ماه رو برای هر گروه اعلام کن
    if now.day == 1:
        await _announce_knight_of_month(context, chat_ids)


async def _announce_knight_of_month(context, chat_ids):
    import bot as _bot
    for chat_id in chat_ids:
        try:
            rows = _bot._get_leaderboard(chat_id, limit=1)
            if not rows:
                continue
            top = rows[0]
            name = f"@{top['username']}" if top["username"] else "شهروند ناشناس"
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🏆 *شوالیه‌ی این ماه گاتهام*\n\n"
                    f"{name} با بیشترین امتیاز، لقب شوالیه‌ی ماه رو گرفت!\n"
                    "🦇 گاتهام بهت افتخار می‌کنه."
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass


_MORNING_LINES_BITTER = [
    "صبح گاتهام تلخه امروز... دلار هم بی‌خیال نشد.",
    "بیدار شو شهروند — بازار هم مثل گاتهام، امشب آروم نبود.",
]
_MORNING_LINES_POSITIVE = [
    "صبح بخیر گاتهام! امروز حداقل دلار طرف ماست.",
    "یه خبر خوب برای شروع روز — دلار عقب نشست.",
]


async def morning_quote(context):
    """هر روز صبح ساعت ۸، یه دیالوگ کوتاه گاتهامی + گزارش دلار می‌فرسته - جدا از پیام نیمه‌شب.

    اگه قیمت دلار ساعت ۸ نسبت به گزارش ۰۰:۰۰ بیشتر شده باشه، لحن پیام
    کمی تلخ‌تر/جدی‌تر می‌شه و Custom Emoji صعودی نشون داده می‌شه؛ اگه کمتر
    شده باشه، پیام مثبت‌تره و Custom Emoji نزولی میاد؛ اگه تقریباً ثابت
    مونده، پیام خنثیه (دیالوگ معمولی گاتهام)."""
    from gotham_content import gotham_signature_line
    try:
        chat_ids = _get_all_chat_ids()
    except Exception:
        chat_ids = []

    dollar_price = await _fetch_dollar_price_safe()
    dollar_direction = "flat"
    dollar_block = ""
    if dollar_price is not None:
        midnight_price = _load_midnight_dollar_price()
        dollar_line, dollar_direction = _dollar_block_morning(dollar_price, midnight_price)
        dollar_block = "\n\n" + dollar_line

    if dollar_direction == "up":
        mood_line = random.choice(_MORNING_LINES_BITTER)
    elif dollar_direction == "down":
        mood_line = random.choice(_MORNING_LINES_POSITIVE)
    else:
        mood_line = gotham_signature_line()

    text = f"☀️ صبح گاتهام\n«{esc(mood_line)}»{dollar_block}"
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception:
            pass


async def check_quiet_groups(context):
    """هر ۲ ساعت چک می‌کنه گروه‌هایی که بیش از ۱۲ ساعته کاملاً ساکتن رو، و یه پیام
    می‌فرسته - فقط یه‌بار به ازای هر دوره‌ی سکوت (تا اسپم نشه)."""
    import bot as _bot
    from gotham_content import gotham_signature_line
    now = time_module.time()
    try:
        chat_ids = _get_all_chat_ids()
    except Exception:
        chat_ids = []
    for chat_id in chat_ids:
        try:
            last = _bot._list_get_one(chat_id, "meta", "last_msg_ts")
            already_notified = _bot._list_get_one(chat_id, "meta", "quiet_notified")
            if not last:
                continue
            gap = now - float(last)
            if gap > 12 * 3600 and already_notified != last:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🌑 گاتهام مدتیه ساکته... کسی نیست؟\n«{gotham_signature_line()}»",
                )
                _bot._list_add(chat_id, "meta", "quiet_notified", last)
        except Exception as e:
            # قبلاً این خطا کاملاً بی‌صدا گم می‌شد. نکته‌ی مهم: اگه فقط ثبتِ
            # «قبلاً اطلاع دادیم» شکست بخوره (نه خودِ ارسال پیام)، این گروه هر
            # ۲ ساعت دوباره همون پیام «گاتهام ساکته» رو می‌گیره تا وقتی ثبتش
            # موفق بشه — این لاگ حداقل قابل‌ردیابی می‌کنه که چرا این تکرار شده.
            logging.getLogger(__name__).warning(
                f"بررسی/اطلاع‌رسانی گروه ساکت برای chat_id={chat_id} با خطا مواجه شد: {e}"
            )


def register_midnight_job(application):
    """application: شیء Application ساخته‌شده تو bot.py. نیازی به chat_ids نیست،
    خودکار همه‌ی گروه‌های فعال رو از دیتابیس ربات پیدا می‌کنه."""
    if application.job_queue is None:
        # نسخه‌ی job-queue نصب نیست؛ برای فعال‌شدن این قابلیت باید نصبش کنی:
        #     pip install "python-telegram-bot[job-queue]"
        import logging
        logging.getLogger(__name__).warning(
            "job_queue در دسترس نیست — برای پیام نیمه‌شب باید pip install "
            "\"python-telegram-bot[job-queue]\" رو اجرا کنی."
        )
        return
    application.job_queue.run_daily(
        midnight_announcement,
        time=dtime(hour=0, minute=0, second=0, tzinfo=TEHRAN_TZ),
        name="midnight_gotham_announcement",
    )
    application.job_queue.run_daily(
        morning_quote,
        time=dtime(hour=8, minute=0, second=0, tzinfo=TEHRAN_TZ),
        name="gotham_morning_quote",
    )
    application.job_queue.run_repeating(
        check_quiet_groups,
        interval=2 * 3600,
        first=2 * 3600,
        name="gotham_quiet_group_check",
    )
