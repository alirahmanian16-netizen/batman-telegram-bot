# -*- coding: utf-8 -*-
"""
reminders.py
================
🗓 یادآور واقعی با زمان‌بندی — با دو تا راه ورودی که کنار هم کار می‌کنن:

۱) متنی (روش قدیمی، دست‌نخورده مونده):
    «یادآور 10 دقیقه فلان کار رو بکن»      -> ۱۰ دقیقه‌ی دیگه
    «یادآور 2 ساعت جلسه دارم»               -> ۲ ساعت دیگه
    «یادآور 1 روز تولد بگیر»                -> ۱ روز دیگه
    «یادآور 14:30 قرص بخور»                 -> امروز (یا اگه گذشته، فردا) ساعت ۱۴:۳۰
    «یادآور فردا 9:00 جلسه»                 -> فردا ساعت ۹
    «یادآورهای من»                          -> لیست یادآورهای فعال
    «حذف یادآور <شماره>»                    -> کنسل کردن یه یادآور

۲) دکمه‌ای (جدید): از دکمه‌ی ⏰ یادآور تو «امکانات دیگر» یه منوی کامل باز
   می‌شه با ➕ ساخت، 📋 لیست، ✏️ ویرایش، 🗑 حذف — همراه با انتخاب تاریخ
   شمسی و تکرار (بدون تکرار / روزانه / هفتگی / ماهانه).

با JobQueue کار می‌کنه و تو دیتابیس (bot.db، رو Volume دائمیِ Railway) هم
ذخیره می‌شه تا بعد از ریستارت/دیپلوی جدید یادآورهای فعال (و تکرارشونده‌ها)
دوباره زمان‌بندی بشن و از بین نرن.

register_reminders(app, deps):
    deps = {
        "db_path": ...,   # str, مسیر همون bot.db
    }
"""

import re
import sqlite3
import logging
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import jdatetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ApplicationHandlerStop,
)

log = logging.getLogger(__name__)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

REL_RE = re.compile(r"(?i)^\s*یادآور\s+(\d+)\s*(دقیقه|ساعت|روز)\s+(.+)$")
TOMORROW_ABS_RE = re.compile(r"(?i)^\s*یادآور\s+فردا\s+(\d{1,2}):(\d{2})\s+(.+)$")
TODAY_ABS_RE = re.compile(r"(?i)^\s*یادآور\s+(\d{1,2}):(\d{2})\s+(.+)$")
LIST_RE = re.compile(r"(?i)^\s*یادآور(ها)?ی?\s*من\s*$")
DELETE_RE = re.compile(r"(?i)^\s*(حذف|کنسل)\s+یادآور\s+(\d+)\s*$")

JALALI_DATE_RE = re.compile(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

_REPEAT_LABELS = {
    "none": "بدون تکرار",
    "daily": "🔁 روزانه",
    "weekly": "🔁 هفتگی",
    "monthly": "🔁 ماهانه",
}

REMINDER_MENU_TEXT = (
    "⏰ *یادآوری‌های گاتهام*\n\n"
    "از دکمه‌ها استفاده کن، یا مثل قبل با متن بنویس:\n"
    "«یادآور 10 دقیقه/ساعت/روز <متن>»\n"
    "«یادآور 14:30 <متن>» یا «یادآور فردا 9:00 <متن>»\n\n"
    "🦇 گاتهام هیچ‌وقت فراموش نمی‌کنه."
)


# ------------------------------------------------------------------
# 🗄️ دیتابیس
# ------------------------------------------------------------------

def _init_table(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            text TEXT NOT NULL,
            fire_at REAL NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            repeat_type TEXT NOT NULL DEFAULT 'none'
        )
        """
    )
    # 🔁 مهاجرت امن برای دیتابیس‌هایی که از قبل هستن و ستون repeat_type رو ندارن
    try:
        conn.execute(
            "ALTER TABLE reminders ADD COLUMN repeat_type TEXT NOT NULL DEFAULT 'none'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود داره — مشکلی نیست
    conn.commit()
    conn.close()


def _add_reminder(db_path, chat_id, user_id, username, text, fire_at_ts, repeat_type="none"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO reminders (chat_id, user_id, username, text, fire_at, repeat_type) "
        "VALUES (?,?,?,?,?,?)",
        (chat_id, user_id, username, text, fire_at_ts, repeat_type),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def _update_reminder(db_path, reminder_id, text, fire_at_ts, repeat_type):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE reminders SET text=?, fire_at=?, repeat_type=?, done=0 WHERE id=?",
        (text, fire_at_ts, repeat_type, reminder_id),
    )
    conn.commit()
    conn.close()


def _update_reminder_fire_at(db_path, reminder_id, fire_at_ts):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE reminders SET fire_at=? WHERE id=?", (fire_at_ts, reminder_id))
    conn.commit()
    conn.close()


def _mark_done(db_path, reminder_id):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
    conn.commit()
    conn.close()


def _list_pending(db_path, user_id=None):
    conn = sqlite3.connect(db_path)
    if user_id is None:
        rows = conn.execute(
            "SELECT id, chat_id, user_id, username, text, fire_at, repeat_type "
            "FROM reminders WHERE done=0"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, chat_id, user_id, username, text, fire_at, repeat_type "
            "FROM reminders WHERE done=0 AND user_id=? ORDER BY fire_at ASC",
            (user_id,),
        ).fetchall()
    conn.close()
    return rows


def _get_reminder_owned(db_path, reminder_id, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, chat_id, user_id, username, text, fire_at, repeat_type "
        "FROM reminders WHERE id=? AND user_id=? AND done=0",
        (reminder_id, user_id),
    ).fetchone()
    conn.close()
    return row


def _delete_reminder(db_path, reminder_id, user_id):
    conn = sqlite3.connect(db_path)
    cur = conn.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id))
    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed > 0


# ------------------------------------------------------------------
# 🕰️ کمک‌کننده‌های تاریخ/تکرار
# ------------------------------------------------------------------

def _fmt_dt(dt: datetime) -> str:
    """فرمت میلادی قدیمی — فقط برای سازگاری، جایی دیگه استفاده نمی‌شه."""
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_jalali(dt: datetime) -> str:
    naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    jd = jdatetime.datetime.fromgregorian(datetime=naive)
    return jd.strftime("%Y/%m/%d - %H:%M")


def _add_month(dt: datetime) -> datetime:
    month = dt.month + 1
    year = dt.year
    if month > 12:
        month = 1
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


def _next_occurrence(dt: datetime, repeat_type: str) -> datetime:
    if repeat_type == "daily":
        return dt + timedelta(days=1)
    if repeat_type == "weekly":
        return dt + timedelta(weeks=1)
    if repeat_type == "monthly":
        return _add_month(dt)
    return dt


def _repeat_label(repeat_type: str) -> str:
    return _REPEAT_LABELS.get(repeat_type, "بدون تکرار")


# ------------------------------------------------------------------
# ⏱️ زمان‌بندی و آتش‌گیری
# ------------------------------------------------------------------

async def _fire_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    user_id = job.data["user_id"]
    username = job.data.get("username") or ""
    text = job.data["text"]
    reminder_id = job.data["reminder_id"]
    db_path = job.data["db_path"]
    repeat_type = job.data.get("repeat_type", "none")
    fire_at_ts = job.data.get("fire_at_ts")

    mention = f"@{username}" if username else f'<a href="tg://user?id={user_id}">این شهروند</a>'
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ {mention} یادآوری: {text}\n🦇 گاتهام هیچ‌وقت فراموش نمی‌کنه.",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"reminder send failed: {e}")

    if repeat_type and repeat_type != "none" and fire_at_ts:
        old_dt = datetime.fromtimestamp(fire_at_ts, TEHRAN_TZ)
        next_dt = _next_occurrence(old_dt, repeat_type)
        _update_reminder_fire_at(db_path, reminder_id, next_dt.timestamp())
        _schedule(
            context.application, db_path, reminder_id, chat_id, user_id, username, text,
            next_dt, repeat_type,
        )
    else:
        _mark_done(db_path, reminder_id)


def _schedule(app, db_path, reminder_id, chat_id, user_id, username, text, fire_at_dt, repeat_type="none"):
    delay = (fire_at_dt - datetime.now(TEHRAN_TZ)).total_seconds()
    if delay < 0:
        delay = 5
    app.job_queue.run_once(
        _fire_reminder,
        when=delay,
        data={
            "chat_id": chat_id,
            "user_id": user_id,
            "username": username,
            "text": text,
            "reminder_id": reminder_id,
            "db_path": db_path,
            "repeat_type": repeat_type,
            "fire_at_ts": fire_at_dt.timestamp(),
        },
        name=f"reminder:{reminder_id}",
    )


def _cancel_job(app, reminder_id):
    for job in app.job_queue.get_jobs_by_name(f"reminder:{reminder_id}"):
        job.schedule_removal()


# ------------------------------------------------------------------
# ⌨️ کیبوردها
# ------------------------------------------------------------------

def build_reminder_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ یادآور جدید", callback_data="rem:new")],
            [InlineKeyboardButton("📋 لیست یادآورهای من", callback_data="rem:list")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:new")],
        ]
    )


def _build_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="rem:cancel")]])


def _build_date_choice_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 امروز", callback_data="rem:date:today"),
                InlineKeyboardButton("📅 فردا", callback_data="rem:date:tomorrow"),
            ],
            [InlineKeyboardButton("✍️ تاریخ دلخواه (شمسی)", callback_data="rem:date:custom")],
            [InlineKeyboardButton("❌ انصراف", callback_data="rem:cancel")],
        ]
    )


def _build_repeat_choice_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 بدون تکرار", callback_data="rem:repeat:none")],
            [
                InlineKeyboardButton("🔁 روزانه", callback_data="rem:repeat:daily"),
                InlineKeyboardButton("🔁 هفتگی", callback_data="rem:repeat:weekly"),
            ],
            [InlineKeyboardButton("🔁 ماهانه", callback_data="rem:repeat:monthly")],
            [InlineKeyboardButton("❌ انصراف", callback_data="rem:cancel")],
        ]
    )


# ------------------------------------------------------------------
# 📩 هندلرهای متنی (روش قدیمی — دست‌نخورده)
# ------------------------------------------------------------------

async def reminder_set_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deps = context.application.bot_data["reminders_deps"]
    db_path = deps["db_path"]
    msg = update.effective_message
    text_raw = msg.text or ""
    text_norm = text_raw.translate(PERSIAN_DIGITS)
    now = datetime.now(TEHRAN_TZ)

    m = REL_RE.match(text_norm)
    if m:
        amount, unit, body = int(m.group(1)), m.group(2), m.group(3).strip()
        if unit == "دقیقه":
            fire_at = now + timedelta(minutes=amount)
        elif unit == "ساعت":
            fire_at = now + timedelta(hours=amount)
        else:
            fire_at = now + timedelta(days=amount)
    else:
        m = TOMORROW_ABS_RE.match(text_norm)
        if m:
            hh, mm, body = int(m.group(1)), int(m.group(2)), m.group(3).strip()
            fire_at = (now + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        else:
            m = TODAY_ABS_RE.match(text_norm)
            if m:
                hh, mm, body = int(m.group(1)), int(m.group(2)), m.group(3).strip()
                fire_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if fire_at <= now:
                    fire_at += timedelta(days=1)
            else:
                return  # این پیام یادآور نبود، به هندلر بعدی بسپرش

    if not body:
        await msg.reply_text(
            "✏️ متن یادآور رو هم بنویس. مثال: «یادآور 10 دقیقه یادت نره زنگ بزنی»"
        )
        return

    if len(body) > 300:
        body = body[:300]

    user = update.effective_user
    reminder_id = _add_reminder(
        db_path, update.effective_chat.id, user.id, user.username or "", body, fire_at.timestamp()
    )
    _schedule(
        context.application, db_path, reminder_id, update.effective_chat.id, user.id,
        user.username or "", body, fire_at,
    )
    await msg.reply_text(
        f"⏰ باشه، سر ساعت {_fmt_jalali(fire_at)} (به وقت تهران) یادت می‌ندازم:\n«{body}»\n"
        f"🔖 شماره‌ی یادآور: {reminder_id}"
    )


async def reminder_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deps = context.application.bot_data["reminders_deps"]
    db_path = deps["db_path"]
    user_id = update.effective_user.id
    rows = _list_pending(db_path, user_id)
    if not rows:
        await update.effective_message.reply_text("📭 یادآور فعالی برات ثبت نشده.")
        return
    lines = ["🗓 *یادآورهای فعال تو:*"]
    for rid, chat_id, uid, username, text, fire_at, repeat_type in rows:
        dt = datetime.fromtimestamp(fire_at, TEHRAN_TZ)
        lines.append(f"#{rid} — {_fmt_jalali(dt)} — {_repeat_label(repeat_type)} — {text}")
    lines.append("\nبرای حذف: «حذف یادآور <شماره>»، یا از منوی دکمه‌ای ⏰ یادآور استفاده کن.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def reminder_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deps = context.application.bot_data["reminders_deps"]
    db_path = deps["db_path"]
    m = DELETE_RE.match(update.effective_message.text or "")
    if not m:
        return
    reminder_id = int(m.group(2))
    user_id = update.effective_user.id
    ok = _delete_reminder(db_path, reminder_id, user_id)
    if ok:
        _cancel_job(context.application, reminder_id)
        await update.effective_message.reply_text(f"🗑 یادآور #{reminder_id} حذف شد.")
    else:
        await update.effective_message.reply_text("⚠️ همچین یادآوری پیدا نکردم (یا مال تو نیست).")


# ------------------------------------------------------------------
# 🆕 منوی تعاملی (دکمه‌ای) — ساخت/لیست/ویرایش/حذف
# ------------------------------------------------------------------

async def _render_list(query, db_path, user_id):
    rows = _list_pending(db_path, user_id)
    if not rows:
        text = "📭 یادآور فعالی نداری."
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➕ یادآور جدید", callback_data="rem:new")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="rem:menu")],
            ]
        )
    else:
        lines = ["🗓 *یادآورهای فعال تو:*\n"]
        buttons = []
        for rid, chat_id, uid, username, rtext, fire_at, repeat_type in rows:
            dt = datetime.fromtimestamp(fire_at, TEHRAN_TZ)
            lines.append(f"#{rid} — {_fmt_jalali(dt)} — {_repeat_label(repeat_type)}\n«{rtext}»\n")
            buttons.append(
                [
                    InlineKeyboardButton(f"✏️ ویرایش #{rid}", callback_data=f"rem:edit:{rid}"),
                    InlineKeyboardButton(f"🗑 حذف #{rid}", callback_data=f"rem:del:{rid}"),
                ]
            )
        buttons.append([InlineKeyboardButton("➕ یادآور جدید", callback_data="rem:new")])
        buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="rem:menu")])
        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(buttons)

    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")


async def _finalize_reminder(query, context, db_path, chat_id, user_id, flow, repeat_type):
    text = flow["text"]
    fire_at = flow["fire_at"]
    edit_id = flow.get("edit_id")
    username = query.from_user.username or ""

    if edit_id:
        _cancel_job(context.application, edit_id)
        _update_reminder(db_path, edit_id, text, fire_at.timestamp(), repeat_type)
        rid = edit_id
    else:
        rid = _add_reminder(
            db_path, chat_id, user_id, username, text, fire_at.timestamp(), repeat_type
        )

    _schedule(context.application, db_path, rid, chat_id, user_id, username, text, fire_at, repeat_type)
    context.user_data.pop("rem_flow", None)

    await query.edit_message_text(
        f"✅ یادآور #{rid} ثبت شد.\n"
        f"📅 زمان: {_fmt_jalali(fire_at)} (به وقت تهران)\n"
        f"🔁 تکرار: {_repeat_label(repeat_type)}\n"
        f"«{text}»",
        reply_markup=build_reminder_menu_keyboard(),
    )


async def reminder_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    deps = context.application.bot_data["reminders_deps"]
    db_path = deps["db_path"]
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if data == "rem:menu":
        await query.edit_message_text(
            REMINDER_MENU_TEXT, reply_markup=build_reminder_menu_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "rem:cancel":
        context.user_data.pop("rem_flow", None)
        await query.edit_message_text(
            REMINDER_MENU_TEXT, reply_markup=build_reminder_menu_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "rem:new":
        context.user_data["rem_flow"] = {"step": "text", "edit_id": None}
        await query.edit_message_text(
            "✏️ بنویس چی رو یادت بندازم؟ (یه پیام متنی بفرست)",
            reply_markup=_build_cancel_keyboard(),
        )
        return

    if data == "rem:list":
        await _render_list(query, db_path, user_id)
        return

    if data.startswith("rem:date:"):
        flow = context.user_data.get("rem_flow")
        if not flow or flow.get("step") != "date":
            await query.answer("⚠️ اول باید متن یادآوری رو بفرستی.", show_alert=True)
            return
        choice = data.split(":", 2)[2]
        now = datetime.now(TEHRAN_TZ)
        if choice == "today":
            flow["date"] = now.date()
            flow["step"] = "time"
            context.user_data["rem_flow"] = flow
            await query.edit_message_text(
                "⏰ ساعت رو بنویس (مثلاً 14:30):", reply_markup=_build_cancel_keyboard()
            )
        elif choice == "tomorrow":
            flow["date"] = (now + timedelta(days=1)).date()
            flow["step"] = "time"
            context.user_data["rem_flow"] = flow
            await query.edit_message_text(
                "⏰ ساعت رو بنویس (مثلاً 14:30):", reply_markup=_build_cancel_keyboard()
            )
        elif choice == "custom":
            flow["step"] = "date_custom"
            context.user_data["rem_flow"] = flow
            await query.edit_message_text(
                "📅 تاریخ شمسی رو بنویس (مثلاً 1404/07/20):",
                reply_markup=_build_cancel_keyboard(),
            )
        return

    if data.startswith("rem:repeat:"):
        flow = context.user_data.get("rem_flow")
        if not flow or flow.get("step") != "repeat":
            await query.answer("⚠️ اول باید تاریخ و ساعت رو مشخص کنی.", show_alert=True)
            return
        repeat_type = data.split(":", 2)[2]
        await _finalize_reminder(query, context, db_path, chat_id, user_id, flow, repeat_type)
        return

    if data.startswith("rem:edit:"):
        rid = int(data.split(":")[2])
        row = _get_reminder_owned(db_path, rid, user_id)
        if not row:
            await query.answer("⚠️ این یادآور پیدا نشد (یا مال تو نیست).", show_alert=True)
            return
        context.user_data["rem_flow"] = {"step": "text", "edit_id": rid}
        await query.edit_message_text(
            f"✏️ متن جدید یادآور #{rid} رو بنویس.\nمتن قبلی: «{row['text']}»",
            reply_markup=_build_cancel_keyboard(),
        )
        return

    if data.startswith("rem:delok:"):
        rid = int(data.split(":")[2])
        ok = _delete_reminder(db_path, rid, user_id)
        if ok:
            _cancel_job(context.application, rid)
            await query.answer("🗑 حذف شد.")
        else:
            await query.answer("⚠️ این یادآور پیدا نشد.", show_alert=True)
        await _render_list(query, db_path, user_id)
        return

    if data.startswith("rem:del:"):
        rid = int(data.split(":")[2])
        row = _get_reminder_owned(db_path, rid, user_id)
        if not row:
            await query.answer("⚠️ این یادآور پیدا نشد.", show_alert=True)
            return
        await query.edit_message_text(
            f"❗️ مطمئنی می‌خوای یادآور #{rid} رو حذف کنی؟\n«{row['text']}»",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"rem:delok:{rid}"),
                        InlineKeyboardButton("❌ نه", callback_data="rem:list"),
                    ]
                ]
            ),
        )
        return


async def reminder_flow_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گرفتن ورودی متنیِ مراحل ساخت/ویرایشِ منوی دکمه‌ای. اگه کاربر تو این
    فلو نباشه، بدون هیچ کاری برمی‌گرده تا بقیه‌ی هندلرهای ربات عادی کار کنن."""
    flow = context.user_data.get("rem_flow")
    if not flow:
        return

    msg = update.effective_message
    text = (msg.text or "").strip()
    step = flow.get("step")

    if step == "text":
        if not text:
            await msg.reply_text("✏️ لطفاً یه متن بفرست.", reply_markup=_build_cancel_keyboard())
            raise ApplicationHandlerStop
        flow["text"] = text[:300]
        flow["step"] = "date"
        context.user_data["rem_flow"] = flow
        await msg.reply_text("📅 کِی یادت بندازم؟", reply_markup=_build_date_choice_keyboard())
        raise ApplicationHandlerStop

    if step == "date_custom":
        norm = text.translate(PERSIAN_DIGITS).replace(" ", "")
        m = JALALI_DATE_RE.match(norm)
        if not m:
            await msg.reply_text(
                "⚠️ فرمت درست نیست. مثال: 1404/07/20", reply_markup=_build_cancel_keyboard()
            )
            raise ApplicationHandlerStop
        try:
            jy, jm, jd = int(m.group(1)), int(m.group(2)), int(m.group(3))
            jalali_date = jdatetime.date(jy, jm, jd)
        except ValueError:
            await msg.reply_text(
                "⚠️ همچین تاریخی وجود نداره، دوباره بنویس.", reply_markup=_build_cancel_keyboard()
            )
            raise ApplicationHandlerStop
        flow["date"] = jalali_date.togregorian()
        flow["step"] = "time"
        context.user_data["rem_flow"] = flow
        await msg.reply_text("⏰ ساعت رو بنویس (مثلاً 14:30):", reply_markup=_build_cancel_keyboard())
        raise ApplicationHandlerStop

    if step == "time":
        norm = text.translate(PERSIAN_DIGITS)
        m = TIME_RE.match(norm)
        if not m:
            await msg.reply_text(
                "⚠️ فرمت درست نیست. مثال: 14:30", reply_markup=_build_cancel_keyboard()
            )
            raise ApplicationHandlerStop
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            await msg.reply_text("⚠️ ساعت معتبر نیست.", reply_markup=_build_cancel_keyboard())
            raise ApplicationHandlerStop
        d = flow["date"]
        fire_at = datetime(d.year, d.month, d.day, hh, mm, tzinfo=TEHRAN_TZ)
        now = datetime.now(TEHRAN_TZ)
        if fire_at <= now:
            fire_at += timedelta(days=1)
        flow["fire_at"] = fire_at
        flow["step"] = "repeat"
        context.user_data["rem_flow"] = flow
        await msg.reply_text(
            f"🔁 تکرار می‌خوای؟\n(زمان انتخابی: {_fmt_jalali(fire_at)})",
            reply_markup=_build_repeat_choice_keyboard(),
        )
        raise ApplicationHandlerStop

    if step == "date":
        await msg.reply_text(
            "لطفاً از دکمه‌ها استفاده کن، یا ❌ انصراف بزن.",
            reply_markup=_build_date_choice_keyboard(),
        )
        raise ApplicationHandlerStop

    if step == "repeat":
        await msg.reply_text(
            "لطفاً از دکمه‌ها استفاده کن، یا ❌ انصراف بزن.",
            reply_markup=_build_repeat_choice_keyboard(),
        )
        raise ApplicationHandlerStop


# ------------------------------------------------------------------
# 🔁 بازیابی بعد از ریستارت/دیپلوی
# ------------------------------------------------------------------

def _reload_pending_on_startup(app, db_path):
    """بعد از هر ریستارت (دیپلوی جدید رو Railway) یادآورهای هنوز فعال —
    شامل تکرارشونده‌ها — رو دوباره تو JobQueue می‌ذاره تا گم نشن."""
    rows = _list_pending(db_path)
    for rid, chat_id, user_id, username, text, fire_at, repeat_type in rows:
        dt = datetime.fromtimestamp(fire_at, TEHRAN_TZ)
        _schedule(app, db_path, rid, chat_id, user_id, username, text, dt, repeat_type)
    if rows:
        log.info(f"reminders: {len(rows)} یادآور فعال دوباره زمان‌بندی شد.")


def register_reminders(app, deps):
    db_path = deps["db_path"]
    _init_table(db_path)
    app.bot_data["reminders_deps"] = deps

    # روش متنی قدیمی — دست‌نخورده
    app.add_handler(MessageHandler(filters.Regex(LIST_RE), reminder_list_handler), group=27)
    app.add_handler(MessageHandler(filters.Regex(DELETE_RE), reminder_delete_handler), group=27)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(?i)^\s*یادآور\b"),
                        reminder_set_handler),
        group=27,
    )

    # 🆕 منوی تعاملی (دکمه‌ای) با تاریخ شمسی و تکرار
    app.add_handler(CallbackQueryHandler(reminder_button_callback, pattern=r"^rem:"), group=28)
    # کچرِ ورودی متنیِ فلو — تو گروه جدا (۳۰) تا رو بقیه‌ی هندلرها تاثیر نذاره
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_flow_text_handler), group=30
    )

    _reload_pending_on_startup(app, db_path)
