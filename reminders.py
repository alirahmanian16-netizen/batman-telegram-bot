# -*- coding: utf-8 -*-
"""
reminders.py
================
🗓 یادآور واقعی با زمان‌بندی (نه فقط کپسول زمان یک‌باره‌ی قدیمی).

فرمت‌های پشتیبانی‌شده:
    «یادآور 10 دقیقه فلان کار رو بکن»      -> ۱۰ دقیقه‌ی دیگه
    «یادآور 2 ساعت جلسه دارم»               -> ۲ ساعت دیگه
    «یادآور 1 روز تولد بگیر»                -> ۱ روز دیگه
    «یادآور 14:30 قرص بخور»                 -> امروز (یا اگه گذشته، فردا) ساعت ۱۴:۳۰
    «یادآور فردا 9:00 جلسه»                 -> فردا ساعت ۹
    «یادآورهای من»                          -> لیست یادآورهای فعال
    «حذف یادآور <شماره>»                    -> کنسل کردن یه یادآور

با JobQueue کار می‌کنه و تو دیتابیس (bot.db) هم ذخیره می‌شه تا بعد از ریستارت
ربات (مثلاً دیپلوی جدید رو Railway) یادآورهای فعال دوباره زمان‌بندی بشن.

register_reminders(app, deps):
    deps = {
        "db_path": ...,   # str, مسیر همون bot.db
    }
"""

import re
import sqlite3
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

REL_RE = re.compile(r"(?i)^\s*یادآور\s+(\d+)\s*(دقیقه|ساعت|روز)\s+(.+)$")
TOMORROW_ABS_RE = re.compile(r"(?i)^\s*یادآور\s+فردا\s+(\d{1,2}):(\d{2})\s+(.+)$")
TODAY_ABS_RE = re.compile(r"(?i)^\s*یادآور\s+(\d{1,2}):(\d{2})\s+(.+)$")
LIST_RE = re.compile(r"(?i)^\s*یادآور(ها)?ی?\s*من\s*$")
DELETE_RE = re.compile(r"(?i)^\s*(حذف|کنسل)\s+یادآور\s+(\d+)\s*$")


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
            done INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def _add_reminder(db_path, chat_id, user_id, username, text, fire_at_ts):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO reminders (chat_id, user_id, username, text, fire_at) VALUES (?,?,?,?,?)",
        (chat_id, user_id, username, text, fire_at_ts),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def _mark_done(db_path, reminder_id):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
    conn.commit()
    conn.close()


def _list_pending(db_path, user_id=None):
    conn = sqlite3.connect(db_path)
    if user_id is None:
        rows = conn.execute(
            "SELECT id, chat_id, user_id, username, text, fire_at FROM reminders WHERE done=0"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, chat_id, user_id, username, text, fire_at FROM reminders "
            "WHERE done=0 AND user_id=? ORDER BY fire_at ASC",
            (user_id,),
        ).fetchall()
    conn.close()
    return rows


def _get_reminder(db_path, reminder_id):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id, chat_id, user_id, username, text, fire_at FROM reminders WHERE id=? AND done=0",
        (reminder_id,),
    ).fetchone()
    conn.close()
    return row


def _delete_reminder(db_path, reminder_id, user_id):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, user_id))
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


async def _fire_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    user_id = job.data["user_id"]
    username = job.data.get("username") or ""
    text = job.data["text"]
    reminder_id = job.data["reminder_id"]
    db_path = job.data["db_path"]

    mention = f"@{username}" if username else f'<a href="tg://user?id={user_id}">این شهروند</a>'
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ {mention} یادآوری: {text}\n🦇 گاتهام هیچ‌وقت فراموش نمی‌کنه.",
            parse_mode="HTML",
        )
    except Exception as e:
        log.error(f"reminder send failed: {e}")
    _mark_done(db_path, reminder_id)


def _schedule(app, db_path, reminder_id, chat_id, user_id, username, text, fire_at_dt):
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
        },
        name=f"reminder:{reminder_id}",
    )


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


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
        f"⏰ باشه، سر ساعت {_fmt_dt(fire_at)} (به وقت تهران) یادت می‌ندازم:\n«{body}»\n"
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
    for rid, chat_id, uid, username, text, fire_at in rows:
        dt = datetime.fromtimestamp(fire_at, TEHRAN_TZ)
        lines.append(f"#{rid} — {_fmt_dt(dt)} — {text}")
    lines.append("\nبرای حذف: «حذف یادآور <شماره>»")
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
        for job in context.application.job_queue.get_jobs_by_name(f"reminder:{reminder_id}"):
            job.schedule_removal()
        await update.effective_message.reply_text(f"🗑 یادآور #{reminder_id} حذف شد.")
    else:
        await update.effective_message.reply_text("⚠️ همچین یادآوری پیدا نکردم (یا مال تو نیست).")


def _reload_pending_on_startup(app, db_path):
    """بعد از هر ریستارت (دیپلوی جدید رو Railway) یادآورهای هنوز فعال رو
    دوباره تو JobQueue می‌ذاره تا گم نشن."""
    rows = _list_pending(db_path)
    for rid, chat_id, user_id, username, text, fire_at in rows:
        dt = datetime.fromtimestamp(fire_at, TEHRAN_TZ)
        _schedule(app, db_path, rid, chat_id, user_id, username, text, dt)
    if rows:
        log.info(f"reminders: {len(rows)} یادآور فعال دوباره زمان‌بندی شد.")


def register_reminders(app, deps):
    db_path = deps["db_path"]
    _init_table(db_path)
    app.bot_data["reminders_deps"] = deps

    app.add_handler(MessageHandler(filters.Regex(LIST_RE), reminder_list_handler), group=27)
    app.add_handler(MessageHandler(filters.Regex(DELETE_RE), reminder_delete_handler), group=27)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r"(?i)^\s*یادآور\b"),
                        reminder_set_handler),
        group=27,
    )

    _reload_pending_on_startup(app, db_path)
