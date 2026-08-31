# -*- coding: utf-8 -*-
"""
new_features_extra.py
================
سه امکان برای بخش «🧩 امکانات جدید»:

    🎡 چرخ گردون روزانه — یه‌بار در روز، «چرخ گردون» رو بگیر و امتیاز جایزه بگیر
       (روی همون سیستم امتیاز/players موجود سوار شده، اقتصاد جدید نساخته).
    🎫 تیکت پشتیبانی — تو چت خصوصی ربات بنویس «تیکت <متن>»، پیامت مستقیم برای
       سازنده‌ی ربات (OWNER_ID) فرستاده می‌شه.
    🎂 یادآور تولد یار بتمن — کاملاً دکمه‌ای و تعاملی؛ روز/ماه (و اختیاری سال)
       رو با دکمه انتخاب می‌کنی، تقویم شمسیه، و قبل از ذخیره تأیید می‌گیره.

--------------------------------------------------------------------
باگ قدیمی که این نسخه رفعش می‌کنه
--------------------------------------------------------------------
تاریخ همیشه به‌صورت شمسی (ماه‌های فارسی مثل مرداد، اردیبهشت...) درست ذخیره
می‌شد (مثلاً «۱۵ مرداد» -> "05-15")، ولی تابعی که هر روز چک می‌کرد امروز
تولد کیه، تاریخ امروز رو با تقویم *میلادی* می‌ساخت (`datetime.now()`) و
همون رو با اون "05-15" شمسی مقایسه می‌کرد. یعنی ماه شمسی با ماه میلادی
قاطی می‌شد و عملاً تبریک هیچ‌وقت سر تاریخ درست فعال نمی‌شد. الان همه‌جا
(هم ذخیره، هم چک روزانه) از `jdatetime` و تقویم شمسی استفاده می‌شه، پس
دیگه جابه‌جایی ماه/روز یا نمایش یه تاریخ پیش‌فرض اشتباه پیش نمی‌آد.

این ماژول مثل بقیه، مستقیم به دیتابیس bot.py وصل نیست؛
register_new_features(app, deps) این‌ها رو می‌گیره:

    deps = {
        "get_player": ...,       # (chat_id, user_id, username) -> dict
        "save_player": ...,      # (player) -> None
        "get_inventory": ...,    # (player) -> dict
        "set_inventory": ...,    # (player, dict) -> None
        "db_run": ...,           # async(fn, *args) -> نتیجه fn تو ترد جدا
        "owner_id": ...,         # int
        "db_path": ...,          # str, مسیر همون bot.db برای جدول تولدها
    }
"""

import random
import sqlite3
import logging
from datetime import date, time as dtime

try:
    import jdatetime
except ImportError:
    jdatetime = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

WHEEL_RE = filters.Regex(r"(?i)^\s*چرخ گردون\s*$")
TICKET_RE = filters.Regex(r"(?i)^\s*تیکت(\s+.+)?$")
# نگه‌داشته شده برای سازگاری با قدیم: هرکی عادت کرده «تولدم ۱۵ مرداد» بنویسه،
# هنوز کار می‌کنه — ولی راه اصلی الان دکمه‌ی «🎂 یادآور تولد یار بتمن»‌ه.
BIRTHDAY_SET_RE = filters.Regex(r"(?i)^\s*تولدم\s+(.+)$")

WHEEL_PRIZES = [10, 15, 20, 25, 30, 50, 5, 100]  # آخری (۱۰۰) نادرتر می‌شه با وزن‌دهی پایین
WHEEL_WEIGHTS = [20, 18, 16, 14, 12, 8, 20, 2]

FA_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def _jalali_today():
    if jdatetime:
        return jdatetime.date.today()
    return None  # نباید پیش بیاد، jdatetime تو requirements.txt هست


def _days_in_month(month_idx: int) -> int:
    """۱ تا ۶: ۳۱ روز، ۷ تا ۱۱: ۳۰ روز، ۱۲ (اسفند): تا ۳۰ (برای پوشش سال کبیسه)."""
    if 1 <= month_idx <= 6:
        return 31
    return 30


# =========================================================
#  دیتابیس
# =========================================================

def _init_birthday_table(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS birthdays (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            month_day TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    # ستون سال، اختیاری — روی دیتابیس‌های قدیمی که این ستون رو ندارن هم اضافه می‌شه.
    try:
        conn.execute("ALTER TABLE birthdays ADD COLUMN birth_year INTEGER")
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود داره
    conn.commit()
    conn.close()


def _set_birthday(db_path, chat_id, user_id, username, first_name, month_day, birth_year=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO birthdays (chat_id, user_id, username, first_name, month_day, birth_year) "
        "VALUES (?,?,?,?,?,?)",
        (chat_id, user_id, username, first_name, month_day, birth_year),
    )
    conn.commit()
    conn.close()


def _get_birthday(db_path, chat_id, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT month_day, birth_year FROM birthdays WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _delete_birthday(db_path, chat_id, user_id):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM birthdays WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()


def _get_today_birthdays(db_path, month_day):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT DISTINCT chat_id, user_id, username, first_name FROM birthdays WHERE month_day=?",
        (month_day,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _parse_persian_month_day(text: str):
    """یه ورودی مثل «۱۵ مرداد» یا «15 مرداد» رو به «۵-۱۵» (ماه-روز شمسی) تبدیل می‌کنه."""
    text = text.strip()
    digits_map = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    text = text.translate(digits_map)
    parts = text.split()
    day = None
    month = None
    for p in parts:
        if p.isdigit():
            day = int(p)
        else:
            for i, m in enumerate(FA_MONTHS, 1):
                if p.strip("،.!") == m:
                    month = i
                    break
    if day and month and 1 <= day <= _days_in_month(month):
        return f"{month:02d}-{day:02d}"
    return None


# =========================================================
#  متن‌ها و کیبوردهای «🎂 یادآور تولد یار بتمن»
# =========================================================

BDAY_TITLE = "🎂 یادآور تولد یار بتمن"


def _bday_status_text(bday):
    month_idx, day = (int(x) for x in bday["month_day"].split("-"))
    date_txt = f"{day} {FA_MONTHS[month_idx - 1]}"
    if bday.get("birth_year"):
        date_txt += f" {bday['birth_year']}"
    return (
        f"{BDAY_TITLE}\n\n"
        "🎂 تولد یار بتمن ثبت شد!\n\n"
        f"📅 تاریخ: {date_txt}\n\n"
        "سر همین تاریخ (شمسی)، تو همین گروه براتون تبریک می‌گم."
    )


def _bday_status_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر تاریخ", callback_data="bday:edit"),
         InlineKeyboardButton("🗑 حذف", callback_data="bday:delete")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="bday:back")],
    ])


def _bday_month_markup():
    rows, row = [], []
    for i, name in enumerate(FA_MONTHS, 1):
        row.append(InlineKeyboardButton(name, callback_data=f"bday:month:{i}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="bday:back")])
    return InlineKeyboardMarkup(rows)


def _bday_day_markup(month_idx):
    rows, row = [], []
    for d in range(1, _days_in_month(month_idx) + 1):
        row.append(InlineKeyboardButton(str(d), callback_data=f"bday:day:{month_idx}:{d}"))
        if len(row) == 7:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 انتخاب ماه", callback_data="bday:open")])
    return InlineKeyboardMarkup(rows)


YEARS_PER_PAGE = 12


def _bday_year_markup(month_idx, day, page):
    today = _jalali_today()
    base_year = today.year if today else 1404
    end_year = base_year - (page * YEARS_PER_PAGE)
    years = list(range(end_year, end_year - YEARS_PER_PAGE, -1))
    rows, row = [], []
    for y in years:
        row.append(InlineKeyboardButton(str(y), callback_data=f"bday:year:{month_idx}:{day}:{y}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    nav = [InlineKeyboardButton("◀️ قدیمی‌تر", callback_data=f"bday:yearpage:{month_idx}:{day}:{page + 1}")]
    if page > 0:
        nav.append(InlineKeyboardButton("جدیدتر ▶️", callback_data=f"bday:yearpage:{month_idx}:{day}:{page - 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("⏭️ رد کردن سال", callback_data=f"bday:yearskip:{month_idx}:{day}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"bday:day:{month_idx}:{day}")])
    return InlineKeyboardMarkup(rows)


def _bday_confirm_text(month_idx, day):
    return (
        f"{BDAY_TITLE}\n\n"
        f"📅 تاریخ انتخابی: {day} {FA_MONTHS[month_idx - 1]}\n\n"
        "می‌خوای سال تولدت رو هم ثبت کنی؟ (اختیاری)"
    )


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_new_features(app, deps):
    get_player = deps["get_player"]
    save_player = deps["save_player"]
    get_inventory = deps["get_inventory"]
    set_inventory = deps["set_inventory"]
    db_run = deps["db_run"]
    owner_id = deps["owner_id"]
    db_path = deps["db_path"]

    _init_birthday_table(db_path)

    # --- چرخ گردون روزانه ---
    async def wheel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        player = await db_run(get_player, chat_id, user.id, user.username or "")
        inv = get_inventory(player)
        today = date.today().isoformat()
        if inv.get("last_wheel_date") == today:
            await update.effective_message.reply_text(
                "🎡 امروز چرخ گردون رو قبلاً چرخوندی — فردا دوباره بیا."
            )
            return
        prize = random.choices(WHEEL_PRIZES, weights=WHEEL_WEIGHTS, k=1)[0]
        player["score"] = (player.get("score") or 0) + prize
        inv["last_wheel_date"] = today
        set_inventory(player, inv)
        await db_run(save_player, player)
        await update.effective_message.reply_text(
            f"🎡 چرخ گردون چرخید... 🎉 بردی: +{prize} امتیاز گاتهام!\n(فردا دوباره می‌تونی بچرخونی)"
        )

    # --- تیکت پشتیبانی ---
    async def ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type != ChatType.PRIVATE:
            await update.effective_message.reply_text(
                "🎫 برای تیکت پشتیبانی تو چت خصوصی ربات بنویس «تیکت <متنت>»."
            )
            return
        text = update.effective_message.text or ""
        body = text[len("تیکت"):].strip(" :،")
        if not body:
            await update.effective_message.reply_text("✏️ بعد از «تیکت» متن درخواستت رو بنویس.")
            return
        user = update.effective_user
        uname = f"@{user.username}" if user.username else "بدون یوزرنیم"
        try:
            await context.bot.send_message(
                owner_id,
                f"🎫 تیکت جدید از {user.first_name} ({uname} — آیدی {user.id}):\n\n{body}",
            )
        except Exception as e:
            log.warning(f"ticket forward failed: {e}")
            await update.effective_message.reply_text("⚠️ الان نشد تیکت رو بفرستم، دوباره امتحان کن.")
            return
        await update.effective_message.reply_text("🎫 تیکتت ثبت شد و برای پشتیبانی فرستاده شد. ممنون از صبرت 🦇")

    # --- تنظیم تولد (نسخه‌ی قدیمی، متنی — هنوز کار می‌کنه) ---
    async def birthday_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.effective_message.reply_text("🎂 این دستور رو تو گروه بنویس تا تو همون گروه تبریک بگیری.")
            return
        text = update.effective_message.text or ""
        raw = text[len("تولدم"):].strip()
        month_day = _parse_persian_month_day(raw)
        if not month_day:
            await update.effective_message.reply_text(
                "⚠️ فرمتش درست نبود. مثال: «تولدم ۱۵ مرداد» — یا از پنل «🎂 یادآور تولد یار بتمن» با دکمه انتخاب کن."
            )
            return
        user = update.effective_user
        chat_id = update.effective_chat.id
        await db_run(
            _set_birthday, db_path, chat_id, user.id, user.username or "", user.first_name or "", month_day, None
        )
        await update.effective_message.reply_text("🎂 ثبت شد! سر تاریخش (شمسی) تو همین گروه برات تبریک می‌گم.")

    # --- تنظیم تولد (نسخه‌ی جدید، دکمه‌ای) ---
    async def bday_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        bday = await db_run(_get_birthday, db_path, chat_id, user_id)
        if bday:
            await q.edit_message_text(_bday_status_text(bday), reply_markup=_bday_status_markup())
        else:
            await q.edit_message_text(
                f"{BDAY_TITLE}\n\n📅 تاریخ تولدت رو انتخاب کن (تقویم شمسی):\n\nاول ماه رو بزن:",
                reply_markup=_bday_month_markup(),
            )
        await q.answer()

    async def bday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        parts = q.data.split(":")
        action = parts[1]
        chat_id = update.effective_chat.id
        user = update.effective_user

        try:
            if action == "open":
                await bday_open(update, context)
                return

            if action == "edit":
                await q.edit_message_text(
                    f"{BDAY_TITLE}\n\n📅 تاریخ جدید رو انتخاب کن:\n\nاول ماه رو بزن:",
                    reply_markup=_bday_month_markup(),
                )
                await q.answer()
                return

            if action == "month":
                month_idx = int(parts[2])
                await q.edit_message_text(
                    f"{BDAY_TITLE}\n\n📅 {FA_MONTHS[month_idx - 1]}\n\nحالا روز رو انتخاب کن:",
                    reply_markup=_bday_day_markup(month_idx),
                )
                await q.answer()
                return

            if action == "day":
                month_idx, day = int(parts[2]), int(parts[3])
                await q.edit_message_text(
                    _bday_confirm_text(month_idx, day),
                    reply_markup=_bday_year_markup(month_idx, day, page=0),
                )
                await q.answer()
                return

            if action == "yearpage":
                month_idx, day, page = int(parts[2]), int(parts[3]), int(parts[4])
                await q.edit_message_text(
                    _bday_confirm_text(month_idx, day),
                    reply_markup=_bday_year_markup(month_idx, day, page=page),
                )
                await q.answer()
                return

            if action in ("year", "yearskip"):
                month_idx, day = int(parts[2]), int(parts[3])
                birth_year = int(parts[4]) if action == "year" else None
                month_day = f"{month_idx:02d}-{day:02d}"
                await db_run(
                    _set_birthday, db_path, chat_id, user.id,
                    user.username or "", user.first_name or "", month_day, birth_year,
                )
                bday = {"month_day": month_day, "birth_year": birth_year}
                await q.edit_message_text(_bday_status_text(bday), reply_markup=_bday_status_markup())
                await q.answer("🎂 ثبت شد!")
                return

            if action == "delete":
                await db_run(_delete_birthday, db_path, chat_id, user.id)
                await q.edit_message_text(
                    f"{BDAY_TITLE}\n\n🗑 تاریخ تولدت حذف شد.\n\nهر وقت خواستی از همین بخش دوباره ثبتش کن.",
                    reply_markup=_bday_month_markup(),
                )
                await q.answer("🗑 حذف شد.")
                return

            if action == "back":
                await q.edit_message_text(
                    "🔙 برگشتی به پنل. برای دیدن پنل اصلی، «تنظیمات» یا «پنل» رو بزن."
                )
                await q.answer()
                return

            await q.answer()
        except Exception as e:
            log.warning(f"bday_callback error: {e}")
            try:
                await q.answer("⚠️ یه مشکل موقت پیش اومد، دوباره امتحان کن.", show_alert=True)
            except Exception:
                pass

    async def birthday_daily_job(context: ContextTypes.DEFAULT_TYPE):
        today = _jalali_today()
        if today is None:
            log.warning("birthday_daily_job: jdatetime نصب نیست، چک روزانه‌ی تولد رد شد.")
            return
        today_key = f"{today.month:02d}-{today.day:02d}"
        rows = await db_run(_get_today_birthdays, db_path, today_key)
        for r in rows:
            name = f"@{r['username']}" if r["username"] else r["first_name"]
            try:
                await context.bot.send_message(
                    r["chat_id"],
                    f"🎂🦇 امروز تولد {name}‌ست! گاتهام امروز یه‌کم روشن‌تره، تولدت مبارک!",
                )
            except Exception:
                pass

    app.add_handler(MessageHandler(WHEEL_RE, wheel_cmd), group=25)
    app.add_handler(MessageHandler(TICKET_RE, ticket_cmd), group=25)
    app.add_handler(MessageHandler(BIRTHDAY_SET_RE, birthday_set_cmd), group=25)

    app.add_handler(CallbackQueryHandler(bday_callback, pattern=r"^bday:"), group=1)

    if getattr(app, "job_queue", None):
        try:
            from zoneinfo import ZoneInfo
            tehran = ZoneInfo("Asia/Tehran")
        except Exception:
            tehran = None
        app.job_queue.run_daily(birthday_daily_job, time=dtime(9, 0, tzinfo=tehran))
