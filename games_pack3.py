# -*- coding: utf-8 -*-
"""
games_pack3.py
================
دو قابلیت جدید، هم‌خانواده با بقیه‌ی فایل‌ها (کلمه‌محرک، بدون /):

    ۱. لیست پرحرف‌ها  -> کلمه: "لیست پرحرفا" یا "لیست پرحرف ها"
       (شمارش خودکار تعداد پیام هر نفر تو گروه، بدون نیاز به کار دستی)

    ۲. لیست پسرا / دخترا (جدا از هم) — عضویت خودشونه، نه تشخیص خودکار:
       عضویت پسرا   -> کلمه: "عضویت پسرا"
       عضویت دخترا  -> کلمه: "عضویت دخترا"
       لیست پسرا    -> کلمه: "لیست پسرا"
       لیست دخترا   -> کلمه: "لیست دخترا"

هر آیتم تو لیست‌ها شامل: اسم، یوزرنیم (@...) و آیدی عددی (User ID) هست.

نحوه‌ی اتصال (کنار register_games و register_extra_games تو bot.py):

    from games import register_games
    from games_pack2 import register_extra_games
    from games_pack3 import register_extra_lists

    register_games(app)
    register_extra_games(app)
    register_extra_lists(app)     # <-- این خط رو هم اضافه کن

نکته‌ی مهم ترتیب: هندلرهای کلمه‌محرک (لیست پرحرفا / عضویت پسرا و...) باید
قبل از هندلر عمومی filters.TEXT (همون‌جایی که handle_message ثبت می‌شه)
اضافه بشن، وگرنه هیچ‌وقت اجرا نمی‌شن. شمارش پیام‌ها (count_message) این
محدودیت رو نداره چون تو گروه جدا (group=5) ثبت می‌شه و کنار بقیه اجرا می‌شه.
"""

from collections import defaultdict

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes, MessageHandler, filters


def _kw(text: str):
    return filters.Regex(rf"(?i)^\s*{text}\s*$")


# =========================================================
#  استیت‌های داخل‌حافظه
# =========================================================

CHATTER_COUNTS = defaultdict(lambda: defaultdict(int))   # chat_id -> {user_id: count}
CHATTER_INFO = defaultdict(dict)                          # chat_id -> {user_id: {"name","username"}}

BOYS_LIST = defaultdict(dict)    # chat_id -> {user_id: {"name","username"}}
GIRLS_LIST = defaultdict(dict)   # chat_id -> {user_id: {"name","username"}}


def _person_line(rank, user_id, info, extra=""):
    username = f"@{info['username']}" if info.get("username") else "بدون یوزرنیم"
    prefix = f"{rank}. " if rank else "• "
    return f"{prefix}{info['name']} ({username}) — آیدی: {user_id}{extra}"


# =========================================================
#  ۱. شمارش خودکار پیام‌ها (برای لیست پرحرف‌ها)
# =========================================================

async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not msg.text or not chat or not user:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if user.is_bot:
        return

    chat_id = chat.id
    CHATTER_COUNTS[chat_id][user.id] += 1
    CHATTER_INFO[chat_id][user.id] = {
        "name": user.first_name or user.username or "ناشناس",
        "username": user.username,
    }


async def chatters_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    counts = CHATTER_COUNTS.get(chat_id, {})
    if not counts:
        await update.effective_message.reply_text("هنوز آماری از پیام‌ها ثبت نشده.")
        return

    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
    lines = ["🗣️ *لیست پرحرف‌های گروه*\n"]
    for rank, (uid, cnt) in enumerate(top, start=1):
        info = CHATTER_INFO[chat_id].get(uid, {"name": "ناشناس", "username": None})
        lines.append(_person_line(rank, uid, info, extra=f" — {cnt} پیام"))

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


# =========================================================
#  ۲. لیست پسرا / دخترا (عضویت خودشونه، جدا از هم)
# =========================================================

async def join_boys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    info = {"name": user.first_name or user.username or "ناشناس", "username": user.username}
    BOYS_LIST[chat_id][user.id] = info
    GIRLS_LIST[chat_id].pop(user.id, None)
    await update.effective_message.reply_text(f"✅ {info['name']} به لیست پسرا اضافه شد.")


async def join_girls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    info = {"name": user.first_name or user.username or "ناشناس", "username": user.username}
    GIRLS_LIST[chat_id][user.id] = info
    BOYS_LIST[chat_id].pop(user.id, None)
    await update.effective_message.reply_text(f"✅ {info['name']} به لیست دخترا اضافه شد.")


async def boys_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    people = BOYS_LIST.get(chat_id, {})
    if not people:
        await update.effective_message.reply_text(
            "لیست پسرا خالیه. برای اضافه‌شدن بنویس «عضویت پسرا»."
        )
        return
    lines = ["👦 *لیست پسرا*\n"]
    for uid, info in people.items():
        lines.append(_person_line(None, uid, info))
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


async def girls_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    people = GIRLS_LIST.get(chat_id, {})
    if not people:
        await update.effective_message.reply_text(
            "لیست دخترا خالیه. برای اضافه‌شدن بنویس «عضویت دخترا»."
        )
        return
    lines = ["👧 *لیست دخترا*\n"]
    for uid, info in people.items():
        lines.append(_person_line(None, uid, info))
    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_extra_lists(app):
    # شمارش خودکار پیام‌ها — تو گروه جدا (5) تا با هندلرهای دیگه تصادم نکنه
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, count_message),
        group=5,
    )

    # group=3: گروه اختصاصی این ۵ تا، وگرنه handle_message ربات (group=0) که رو هر
    # متنی match می‌کنه جلوشون رو می‌گرفت و هیچ‌وقت اجرا نمی‌شدن.
    app.add_handler(MessageHandler(_kw("لیست پرحرفا|لیست پرحرف\u200cها|پرحرفا|پرحرف\u200cها"), chatters_list_cmd), group=3)
    app.add_handler(MessageHandler(_kw("عضویت پسرا|ثبت پسرا"), join_boys), group=3)
    app.add_handler(MessageHandler(_kw("عضویت دخترا|ثبت دخترا"), join_girls), group=3)
    app.add_handler(MessageHandler(_kw("لیست پسرا"), boys_list_cmd), group=3)
    app.add_handler(MessageHandler(_kw("لیست دخترا"), girls_list_cmd), group=3)
