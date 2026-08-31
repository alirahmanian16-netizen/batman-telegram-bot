# -*- coding: utf-8 -*-
"""
security_tools.py
================
ابزارهای امنیتی گروه: آنتی‌لینک، آنتی‌فلود، و منوی «امنیت» تو پنل تنظیمات.

طرز کار:
    - هر گروه تنظیمات امنیتی خودش رو داره (فعال/غیرفعال)، ذخیره‌شده تو همون
      جدول group_lists (list_type="setting") که بقیه‌ی تنظیمات هم توش هستن —
      بدون نیاز به تغییر اسکیمای دیتابیس.
    - آنتی‌لینک: وقتی فعاله، لینک/آیدی-گروه (t.me، @یوزرنیم، http...) از سمت
      غیرادمین‌ها خودکار پاک می‌شه.
    - آنتی‌فلود: وقتی فعاله، اگه یه کاربر بیش از حد پشت‌سرهم پیام بده
      (پیش‌فرض ۶ پیام تو ۸ ثانیه)، چند دقیقه خودکار میوت می‌شه.
    - این ماژول مثل admin_panel.py مستقیم به bot.py وصل نمی‌شه (جلوی import
      چرخه‌ای)؛ به‌جاش register_security(app, deps) یه دیکشنری از توابع مورد
      نیاز می‌گیره:

        deps = {
            "is_group_admin": ...,        # async(update, context) -> bool
            "list_add": ...,              # (chat_id, list_type, key, value)
            "list_remove": ...,           # (chat_id, list_type, key)
            "list_get_one": ...,          # (chat_id, list_type, key) -> value|None
            "db_run": ...,                # async(fn, *args) -> نتیجه fn تو ترد جدا
        }
        register_security(app, deps)
"""

import re
import time
from collections import defaultdict, deque

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ChatType
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

_DEPS_KEY = "security_deps"

TRIGGER_RE = filters.Regex(r"(?i)^\s*(امنیت|پنل امنیت|منو امنیت)\s*$")

LINK_RE = re.compile(
    r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|@[A-Za-z]\w{4,31}\b)", re.IGNORECASE
)

# ردیاب فلود: (chat_id, user_id) -> deque از timestamp پیام‌های اخیر
FLOOD_TRACKER = defaultdict(lambda: deque(maxlen=20))
FLOOD_MAX_MSGS = 6
FLOOD_WINDOW_SECONDS = 8
FLOOD_MUTE_MINUTES = 5

DEFAULT_SETTINGS = {"antilink": "off", "antiflood": "off", "downloader_links": "on"}


def _get_setting(deps, chat_id, key):
    val = deps["list_get_one"](chat_id, "setting", key)
    return val if val else DEFAULT_SETTINGS.get(key, "off")


def _set_setting(deps, chat_id, key, value):
    deps["list_add"](chat_id, "setting", key, value)


def _status_label(value):
    return "✅ روشن" if value == "on" else "❌ خاموش"


async def build_security_text_and_kb(deps, chat_id):
    antilink = _get_setting(deps, chat_id, "antilink")
    antiflood = _get_setting(deps, chat_id, "antiflood")
    dl_links = _get_setting(deps, chat_id, "downloader_links")
    text = (
        "🔐 *امنیت گروه*\n\n"
        f"🔗 آنتی‌لینک: {_status_label(antilink)}\n"
        "   وقتی روشنه، لینک/آیدی گروه دیگه که غیرادمین‌ها بفرستن خودکار پاک می‌شه.\n\n"
        f"📥 لینک‌های دانلودر (یوتیوب/اینستا/تیک‌تاک/ایکس/پینترست/ساندکلاود): {_status_label(dl_links)}\n"
        "   وقتی روشنه، این لینک‌ها حتی با آنتی‌لینک روشن، برای همه (نه فقط ادمین) مجازن — "
        "فقط لینک کانال/گروه دیگه طبق آنتی‌لینک پاک می‌مونه.\n\n"
        f"🚫 آنتی‌فلود: {_status_label(antiflood)}\n"
        f"   وقتی روشنه، اگه یکی بیش از {FLOOD_MAX_MSGS} پیام تو {FLOOD_WINDOW_SECONDS} ثانیه بفرسته، "
        f"{FLOOD_MUTE_MINUTES} دقیقه خودکار میوت می‌شه.\n\n"
        "🆕 کپچای عضو جدید و فیلتر کلمات هم از بخش «مدیریت گروه» قابل تنظیمن."
    )
    rows = [
        [InlineKeyboardButton(
            f"🔗 آنتی‌لینک: {_status_label(antilink)}", callback_data="sec:toggle:antilink"
        )],
        [InlineKeyboardButton(
            f"📥 لینک‌های دانلودر: {_status_label(dl_links)}", callback_data="sec:toggle:downloader_links"
        )],
        [InlineKeyboardButton(
            f"🚫 آنتی‌فلود: {_status_label(antiflood)}", callback_data="sec:toggle:antiflood"
        )],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ]
    return text, InlineKeyboardMarkup(rows)


def register_security(app, deps):
    app.bot_data[_DEPS_KEY] = deps

    async def security_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await update.message.reply_text("🔐 این بخش فقط تو گروه معنی داره.")
            return
        if not await deps["is_group_admin"](update, context):
            await update.message.reply_text("⛔️ تنظیمات امنیتی فقط برای ادمین‌هاست.")
            return
        text, kb = await build_security_text_and_kb(deps, update.effective_chat.id)
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

    async def security_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not await deps["is_group_admin"](update, context):
            await query.answer("⛔️ فقط ادمین‌ها می‌تونن تغییرش بدن.", show_alert=True)
            return
        key = query.data.split(":")[2]
        chat_id = update.effective_chat.id
        current = _get_setting(deps, chat_id, key)
        new_val = "off" if current == "on" else "on"
        await deps["db_run"](_set_setting, deps, chat_id, key, new_val)
        text, kb = await build_security_text_and_kb(deps, chat_id)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        await query.answer(f"{'روشن' if new_val == 'on' else 'خاموش'} شد ✅")

    async def security_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """روی هر پیام گروه چک می‌کنه: آنتی‌لینک و آنتی‌فلود. اگه پیام رو مصرف کرد True برمی‌گردونه.

        🔒 موقتاً کاملاً خاموش — طبق درخواست صریح، تا وقتی تشخیص داده بشه مشکل
        «لینک تو گروه نمی‌ره» از این تابع نیست، اینجا بدون چک تنظیمات همیشه
        False برمی‌گردونه (یعنی هیچ پیامی، حتی اگه آنتی‌لینک/آنتی‌فلود از تو
        منو روشن باشه، پاک یا میوت نمی‌شه). برای برگردوندنش، این return False
        اضافه‌شده رو پاک کن.
        """
        return False
        msg = update.message
        if not msg or not update.effective_chat or update.effective_chat.type not in (
            ChatType.GROUP, ChatType.SUPERGROUP
        ):
            return False
        chat_id = update.effective_chat.id
        user = update.effective_user
        text = msg.text or msg.caption or ""

        is_admin = await deps["is_group_admin"](update, context)
        if is_admin:
            return False

        # --- آنتی‌لینک ---
        # 🐛 رفع باگ «لینک اینستا/یوتیوب/... تو گروه نمی‌ره»: وقتی آنتی‌لینک
        # روشن بود، این‌جا هر لینکی (حتی لینک‌های پلتفرم‌های پشتیبانی‌شده‌ی
        # دانلودر) به‌عنوان اسپم پاک می‌شد — قبل از این‌که اصلاً به هندلر
        # دانلودر برسه. نتیجه: دانلودر با آنتی‌لینک روشن کلاً غیرفعال می‌شد،
        # بدون هیچ پیام خطایی که علتش رو نشون بده. حالا لینک‌های پلتفرم‌های
        # پشتیبانی‌شده‌ی دانلودر (اینستاگرام/یوتیوب/تیک‌تاک/ایکس/پینترست/
        # ساندکلاود) از این حذف خودکار معاف‌ان — چون این‌ها قابلیت رسمی
        # خودِ رباتن، نه اسپم.
        if _get_setting(deps, chat_id, "antilink") == "on" and text and LINK_RE.search(text):
            if _get_setting(deps, chat_id, "downloader_links") == "on":
                try:
                    from downloader import text_contains_supported_link
                    if text_contains_supported_link(text):
                        return False
                except Exception:
                    pass
            try:
                await msg.delete()
            except Exception:
                pass
            try:
                warn = await context.bot.send_message(
                    chat_id, f"🔗 {user.first_name} لینک/آیدی تو گروه مجاز نیست."
                )
                context.application.create_task(_auto_delete(warn, 8))
            except Exception:
                pass
            return True

        # --- آنتی‌فلود ---
        if _get_setting(deps, chat_id, "antiflood") == "on":
            now = time.time()
            key = (chat_id, user.id)
            dq = FLOOD_TRACKER[key]
            dq.append(now)
            recent = [t for t in dq if now - t <= FLOOD_WINDOW_SECONDS]
            if len(recent) >= FLOOD_MAX_MSGS:
                dq.clear()
                try:
                    await context.bot.restrict_chat_member(
                        chat_id, user.id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=int(now) + FLOOD_MUTE_MINUTES * 60,
                    )
                    await context.bot.send_message(
                        chat_id,
                        f"🚫 {user.first_name} به‌خاطر فلود {FLOOD_MUTE_MINUTES} دقیقه میوت شد.",
                    )
                except Exception:
                    pass
                return True

        return False

    async def _auto_delete(message, delay_seconds):
        import asyncio
        await asyncio.sleep(delay_seconds)
        try:
            await message.delete()
        except Exception:
            pass

    app.add_handler(MessageHandler(TRIGGER_RE, security_menu_cmd), group=20)
    app.add_handler(CallbackQueryHandler(security_toggle_callback, pattern=r"^sec:toggle:"), group=20)

    # اکسپورت تابع گارد تا bot.py بتونه تو handle_message اصلی صداش بزنه
    # (به‌جای ثبت یه هندلر متنی جدا که ترتیب گروه‌ها رو پیچیده می‌کنه)
    app.bot_data["security_guard_fn"] = security_guard
    app.bot_data["build_security_text_and_kb"] = build_security_text_and_kb
