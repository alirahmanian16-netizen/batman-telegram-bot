# -*- coding: utf-8 -*-
"""
temp_mail.py
================
📧 ایمیل موقت — با Mail.tm (رایگان، بدون نیاز به API Key).

هر کاربر (چه تو PV چه تو گروه) می‌تونه یه ایمیل موقت برای خودش بسازه، صندوقش
رو چک کنه، و هروقت خواست پاکش کنه. جلسه‌ی هر کاربر کاملاً جدا از بقیه‌ست
(تو دیتابیس با user_id ذخیره می‌شه) و هیچ کاربری نمی‌تونه ایمیل کاربر دیگه
رو ببینه یا مدیریت کنه.

🔐 حریم خصوصی تو گروه:
    چون آدرس ایمیل و محتوای صندوق، اطلاعات شخصی‌ان، وقتی این قابلیت تو یه
    گروه استفاده بشه، ربات به‌جای چاپ کردنشون وسط گروه، همه‌چیز رو تو پیوی
    خودِ همون کاربر می‌فرسته (و تو گروه فقط یه تاییدیه‌ی کوتاه می‌ده). اگه
    کاربر هنوز تو پیوی با ربات شروع نکرده باشه، بهش می‌گه اول پیوی رو باز کنه.

register_temp_mail(app, deps):
    deps = {"db_path": ...}
"""

import time
import random
import string
import logging
import sqlite3

import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

MAILTM_BASE = "https://api.mail.tm"
START_TRIGGER = filters.Regex(r"(?i)^\s*ایمیل\s*موقت\s*$")

MAIL_MENU_TEXT = (
    "📧 *ایمیل موقت*\n\n"
    "یه ایمیل موقت برای خودت بساز، صندوق ورودیش رو چک کن، یا هر وقت کارت تموم "
    "شد پاکش کن. کاملاً رایگانه و مال خودته — هیچ‌کس دیگه بهش دسترسی نداره."
)


# ------------------------------------------------------------------
# 🗄️ دیتابیس — یه ردیف برای هر کاربر
# ------------------------------------------------------------------

def _init_table(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS temp_mail_accounts (
            user_id INTEGER PRIMARY KEY,
            address TEXT NOT NULL,
            password TEXT NOT NULL,
            account_id TEXT,
            token TEXT,
            created_at REAL
        )
        """
    )
    conn.commit()
    conn.close()


def _save_account(db_path, user_id, address, password, account_id, token):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO temp_mail_accounts (user_id, address, password, account_id, token, created_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET address=excluded.address, password=excluded.password, "
        "account_id=excluded.account_id, token=excluded.token, created_at=excluded.created_at",
        (user_id, address, password, account_id, token, time.time()),
    )
    conn.commit()
    conn.close()


def _get_account(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM temp_mail_accounts WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _update_token(db_path, user_id, token):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE temp_mail_accounts SET token=? WHERE user_id=?", (token, user_id))
    conn.commit()
    conn.close()


def _delete_account_row(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM temp_mail_accounts WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------
# 🌐 کلاینت Mail.tm — بدون نیاز به API Key (طبق مستندات رسمیش)
# ------------------------------------------------------------------

async def _mailtm_domains():
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{MAILTM_BASE}/domains")
        r.raise_for_status()
        data = r.json()
    domains = [d["domain"] for d in data.get("hydra:member", []) if d.get("isActive", True)]
    if not domains:
        raise RuntimeError("no active mail.tm domains")
    return domains


async def _mailtm_create_account(address, password):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{MAILTM_BASE}/accounts", json={"address": address, "password": password}
        )
        r.raise_for_status()
        return r.json()


async def _mailtm_get_token(address, password):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{MAILTM_BASE}/token", json={"address": address, "password": password}
        )
        r.raise_for_status()
        return r.json()["token"]


async def _mailtm_list_messages(token):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{MAILTM_BASE}/messages", headers={"Authorization": f"Bearer {token}"}
        )
        r.raise_for_status()
        return r.json().get("hydra:member", [])


async def _mailtm_get_message(token, msg_id):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{MAILTM_BASE}/messages/{msg_id}", headers={"Authorization": f"Bearer {token}"}
        )
        r.raise_for_status()
        return r.json()


async def _mailtm_delete_account(token, account_id):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.delete(
                f"{MAILTM_BASE}/accounts/{account_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        pass  # حذف سمت Mail.tm بهترین‌تلاشه؛ حذف ردیف محلی مهم‌تره


def _random_local_part(n=10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _random_password(n=16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=n))


# ------------------------------------------------------------------
# 🔐 تحویلِ امن — تو گروه، اطلاعات شخصی رو تو پیوی می‌فرسته نه وسط گروه
# ------------------------------------------------------------------

async def _deliver(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    chat = update.effective_chat
    user = update.effective_user
    query = update.callback_query

    if chat.type == "private":
        if query:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        return

    # تو گروه — امن‌ترین راه: پیوی خودِ همون کاربر
    try:
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=reply_markup, parse_mode="Markdown"
        )
        if query:
            await query.answer("📩 برات پیوی فرستادم — چون این اطلاعات شخصیه.", show_alert=False)
        else:
            await update.effective_message.reply_text(
                "📩 برات پیوی فرستادم، چون این اطلاعات فقط باید پیش خودت بمونه."
            )
    except Exception:
        alert = (
            "⚠️ اول باید یه‌بار پیوی خودم بهم پیام بدی (دکمه‌ی Start رو بزنی) تا "
            "بتونم اطلاعات ایمیلت رو خصوصی برات بفرستم."
        )
        if query:
            await query.answer(alert, show_alert=True)
        else:
            await update.effective_message.reply_text(alert)


# ------------------------------------------------------------------
# ⌨️ کیبوردها
# ------------------------------------------------------------------

def _build_no_account_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✉️ ساخت ایمیل موقت", callback_data="mail:new")]]
    )


def _build_account_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 صندوق ورودی", callback_data="mail:inbox")],
            [
                InlineKeyboardButton("🔄 ایمیل جدید", callback_data="mail:new"),
                InlineKeyboardButton("🗑 حذف ایمیل", callback_data="mail:delete"),
            ],
        ]
    )


# ------------------------------------------------------------------
# 🆕 هندلرها
# ------------------------------------------------------------------

async def _open_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, db_path: str):
    """این پیام هیچ اطلاعات شخصی‌ای نداره (فقط اینکه ایمیل داری یا نه)، پس
    همیشه امن‌ه که همون‌جا (حتی تو گروه) نشون داده بشه."""
    account = _get_account(db_path, update.effective_user.id)
    kb = _build_account_keyboard() if account else _build_no_account_keyboard()
    query = update.callback_query
    if query:
        await query.edit_message_text(MAIL_MENU_TEXT, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(
            MAIL_MENU_TEXT, reply_markup=kb, parse_mode="Markdown"
        )


async def mail_start_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deps = context.application.bot_data["temp_mail_deps"]
    await _open_menu(update, context, deps["db_path"])


async def _create_new_account(update, context, db_path, user_id):
    try:
        domains = await _mailtm_domains()
        last_err = None
        for _ in range(3):
            address = f"{_random_local_part()}@{random.choice(domains)}"
            password = _random_password()
            try:
                created = await _mailtm_create_account(address, password)
                break
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code == 422:
                    continue  # آدرس تصادفاً تکراری بود، یه‌بار دیگه امتحان کن
                raise
        else:
            raise last_err or RuntimeError("could not create mail.tm account")

        token = await _mailtm_get_token(address, password)
        _save_account(db_path, user_id, address, password, created.get("id"), token)
        await _deliver(
            update,
            context,
            f"✅ ایمیل موقتت ساخته شد:\n\n`{address}`\n\n"
            "روش کپی: رو خودِ آدرس بالا نگه‌دار (لمس طولانی) تا کپی بشه.\n"
            "هر وقت پیام جدید اومد، از «📥 صندوق ورودی» چک کن.",
            reply_markup=_build_account_keyboard(),
        )
    except Exception as e:
        log.error(f"temp_mail create failed: {e}")
        await _deliver(
            update,
            context,
            "⚠️ ساخت ایمیل موقت الان جواب نداد (سرویس Mail.tm در دسترس نیست)، یه‌کم بعد دوباره امتحان کن.",
            reply_markup=_build_no_account_keyboard(),
        )


async def _show_inbox(update, context, db_path, user_id):
    account = _get_account(db_path, user_id)
    if not account:
        await _deliver(
            update, context, "📭 هنوز ایمیلی نساختی.", reply_markup=_build_no_account_keyboard()
        )
        return

    token = account["token"]
    try:
        try:
            messages = await _mailtm_list_messages(token)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                # توکن منقضی شده، یه‌بار دیگه لاگین کن
                token = await _mailtm_get_token(account["address"], account["password"])
                _update_token(db_path, user_id, token)
                messages = await _mailtm_list_messages(token)
            else:
                raise
    except Exception as e:
        log.error(f"temp_mail inbox failed: {e}")
        await _deliver(
            update,
            context,
            "⚠️ الان نتونستم صندوق رو بگیرم، یه‌کم بعد دوباره امتحان کن.",
            reply_markup=_build_account_keyboard(),
        )
        return

    if not messages:
        text = f"📭 صندوق `{account['address']}` خالیه.\n\nهر وقت پیامی رسید، دوباره بزن «📥 صندوق ورودی»."
        await _deliver(update, context, text, reply_markup=_build_account_keyboard())
        return

    lines = [f"📬 *صندوق* `{account['address']}`\n"]
    for m in messages[:10]:
        frm = (m.get("from") or {}).get("address", "نامشخص")
        subject = m.get("subject") or "(بدون موضوع)"
        intro = (m.get("intro") or "").strip()
        lines.append(f"👤 {frm}\n📌 {subject}\n📝 {intro[:150]}\n")
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n\n…"
    await _deliver(update, context, text, reply_markup=_build_account_keyboard())


async def _delete_account(update, context, db_path, user_id):
    account = _get_account(db_path, user_id)
    if account:
        await _mailtm_delete_account(account["token"], account.get("account_id"))
        _delete_account_row(db_path, user_id)
    await _deliver(
        update, context, "🗑 ایمیل موقتت حذف شد.", reply_markup=_build_no_account_keyboard()
    )


async def mail_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    deps = context.application.bot_data["temp_mail_deps"]
    db_path = deps["db_path"]
    user_id = update.effective_user.id

    if data == "mail:menu":
        await query.answer()
        await _open_menu(update, context, db_path)
        return

    if data == "mail:new":
        await query.answer("⏳ در حال ساخت ایمیل...")
        await _create_new_account(update, context, db_path, user_id)
        return

    if data in ("mail:inbox", "mail:refresh"):
        await query.answer("⏳ در حال چک کردن صندوق...")
        await _show_inbox(update, context, db_path, user_id)
        return

    if data == "mail:delete":
        account = _get_account(db_path, user_id)
        await query.answer()
        if not account:
            await _deliver(
                update, context, "📭 هنوز ایمیلی نساختی.", reply_markup=_build_no_account_keyboard()
            )
            return
        await _deliver(
            update,
            context,
            f"❗️ مطمئنی می‌خوای `{account['address']}` رو حذف کنی؟",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ بله، حذف کن", callback_data="mail:delok"),
                        InlineKeyboardButton("❌ نه", callback_data="mail:menu"),
                    ]
                ]
            ),
        )
        return

    if data == "mail:delok":
        await query.answer("🗑 در حال حذف...")
        await _delete_account(update, context, db_path, user_id)
        return


def register_temp_mail(app, deps):
    db_path = deps["db_path"]
    _init_table(db_path)
    app.bot_data["temp_mail_deps"] = deps

    app.add_handler(MessageHandler(START_TRIGGER, mail_start_text_handler), group=20)
    app.add_handler(CallbackQueryHandler(mail_button_callback, pattern=r"^mail:"), group=28)
