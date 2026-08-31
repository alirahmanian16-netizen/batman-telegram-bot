# -*- coding: utf-8 -*-
"""
games/menu.py
================
منوی دکمه‌ای بازی‌ها — دقیقاً مثل انتخاب شخصیت: به‌جای اینکه مجبور باشی اسم
دقیق هر بازی رو تایپ کنی، بنویس «گیم» و از بین دکمه‌ها انتخاب کن.

نکته‌ی مهم: نوشتنِ اسم بازی هم مثل قبل کار می‌کنه — این فایل چیزی رو از رفتار
قبلی حذف نمی‌کنه، فقط یه راه ورودیِ دکمه‌ای اضافه می‌کنه که هندلرهای همون بازیِ
اصلی رو مستقیم صدا می‌زنه.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

from games import (
    rps_game, dice_game, coinflip_game, trivia_game, riddle_game, wyr_game,
    guess_start, hangman_start, wordchain_start, story_start, roulette_start,
    wordle_start, crossword_start, typerace_start, mafia_join,
    tictactoe_start, connect4_start,
)
from ttt_gotham import gotham_ttt_start
from board_games import chess_start, ludo_start, snakes_start, go_start
from games_pack2 import g2048_start, lightsout_start, memory_start, battleship_start, treasure_start
from games_pack4 import minesweeper_start, dots_start, tiko_start, jamshid_start, bazar_start
from games_pack5 import uno_start, territory_start, billiards_start, racing_start
from group_rps import group_rps_start
from card_room import card_room_start


# =========================================================
#  کلمه‌ای که این منو رو باز می‌کنه
# =========================================================

MENU_TRIGGER_RE = filters.Regex(
    r"(?i)^\s*(گیم|لیست بازی\u200cها|لیست بازی ها|بازی\u200cها|بازی ها|"
    r"منوی بازی\u200cها|منو بازی\u200cها|منوی بازی|منو بازی)\s*$"
)

# =========================================================
#  رجیستری بازی‌ها: کلید کوتاه -> (برچسب دکمه، تابع شروع)
# =========================================================

CATEGORIES = [
    ("quick", "⚡️ سریع و ساده", [
        ("rps", "🪨 سنگ کاغذ قیچی", rps_game),
        ("dice", "🎲 تاس", dice_game),
        ("coin", "🪙 شیر یا خط", coinflip_game),
        ("triv", "❓ کوییز", trivia_game),
        ("ridl", "🧩 معما", riddle_game),
        ("wyr", "🤔 ترجیح میدی", wyr_game),
        ("guess", "🔢 حدس عدد", guess_start),
    ]),
    ("word", "🔤 کلمه‌ای", [
        ("hang", "🪢 دار", hangman_start),
        ("wordle", "🟩 وردل", wordle_start),
        ("cross", "📰 جدول کلمات", crossword_start),
        ("type", "⌨️ مسابقه تایپ", typerace_start),
        ("wchain", "🔗 زنجیره کلمات", wordchain_start),
        ("story", "📖 داستان گروهی", story_start),
    ]),
    ("duel", "⚔️ دو نفره", [
        ("ttt", "❌⭕ دوز", tictactoe_start),
        ("gttt", "🦇 دوز گاتهام", gotham_ttt_start),
        ("c4", "🔴🟡 چهار در ردیف", connect4_start),
        ("chess", "♟ شطرنج", chess_start),
        ("ludo", "🎯 منچ", ludo_start),
        ("snake", "🐍 مار و پله", snakes_start),
        ("go", "⚫⚪ گو (Go)", go_start),
        ("uno", "🎴 یونو", uno_start),
        ("terr", "🗺 قلمرو", territory_start),
        ("bill", "🎱 بیلیارد", billiards_start),
        ("race", "🏎 مسابقه ماشین", racing_start),
        ("cards", "🃏 پاسور", card_room_start),
    ]),
    ("puzzle", "🧩 پازل و فکری", [
        ("g2048", "🔢 ۲۰۴۸", g2048_start),
        ("light", "💡 چراغ‌ها", lightsout_start),
        ("mem", "🧠 حافظه", memory_start),
        ("batt", "🚢 نبرد دریایی", battleship_start),
        ("treas", "💰 گنج پنهان", treasure_start),
        ("mine", "💣 مین‌روب", minesweeper_start),
        ("dots", "🔵 نقطه بازی", dots_start),
        ("tiko", "🎨 تیکو", tiko_start),
        ("jam", "🏺 جمشید", jamshid_start),
        ("bazar", "🛒 گیر بازار", bazar_start),
    ]),
    ("group", "👥 گروهی", [
        ("mafia", "🎭 مافیا", mafia_join),
        ("roul", "🔫 رولت روسی", roulette_start),
        ("grps", "🎮 سنگ کاغذ قیچی", group_rps_start),
    ]),
]

CATEGORY_LABELS = {key: label for key, label, _ in CATEGORIES}
CATEGORY_GAMES = {key: games for key, _, games in CATEGORIES}

# دسترسی سریع به (برچسب، تابع، دسته) از روی کلید بازی
GAME_REGISTRY = {}
for _cat_key, _cat_label, _games in CATEGORIES:
    for _game_key, _game_label, _handler in _games:
        GAME_REGISTRY[_game_key] = (_game_label, _handler, _cat_key)


GAMES_MENU_MAIN_TEXT = (
    "🎮 *لیست بازی‌ها*\n\n"
    "یه دسته رو انتخاب کن، بعد بازی مورد نظرت رو بزن.\n"
    "(نوشتنِ اسم بازی هم مثل همیشه کار می‌کنه.)"
)


def build_games_menu_root_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, label, _games in CATEGORIES:
        row.append(InlineKeyboardButton(label, callback_data=f"gm:cat:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # ⚡ بازی سریع (Phase 5) — همون منطق cr:quick تو card_room.py رو صدا می‌زنه؛
    # بازی جدیدی ساخته نشده، فقط یه مسیر میان‌بر به همون قابلیتِ از قبل موجوده.
    rows.append([InlineKeyboardButton("⚡ بازی سریع", callback_data="cr:quick"),
                 InlineKeyboardButton("🎮 بازی‌های فعال من", callback_data="panel:active_games")])
    rows.append([InlineKeyboardButton("🎟️ بازی‌های استیکری بتمن", callback_data="gg:root")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)


def build_category_keyboard(cat_key: str) -> InlineKeyboardMarkup:
    games = CATEGORY_GAMES.get(cat_key, [])
    rows = []
    row = []
    for game_key, label, _handler in games:
        row.append(InlineKeyboardButton(label, callback_data=f"gm:go:{game_key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 دسته‌ها", callback_data="gm:root")])
    return InlineKeyboardMarkup(rows)


# =========================================================
#  هندلرها
# =========================================================

async def games_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        GAMES_MENU_MAIN_TEXT, reply_markup=build_games_menu_root_keyboard(), parse_mode="Markdown"
    )


async def games_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data  # فرمت: gm:root | gm:cat:<key> | gm:go:<key>
    parts = data.split(":")
    action = parts[1]

    if action == "root":
        await query.edit_message_text(
            GAMES_MENU_MAIN_TEXT, reply_markup=build_games_menu_root_keyboard(), parse_mode="Markdown"
        )
        await query.answer()
        return

    if action == "cat":
        cat_key = parts[2]
        label = CATEGORY_LABELS.get(cat_key, "")
        await query.edit_message_text(
            f"{label}\n\nیه بازی رو انتخاب کن:", reply_markup=build_category_keyboard(cat_key)
        )
        await query.answer()
        return

    if action == "go":
        game_key = parts[2]
        entry = GAME_REGISTRY.get(game_key)
        if not entry:
            await query.answer("این بازی پیدا نشد.", show_alert=True)
            return
        label, handler, cat_key = entry
        await query.answer(f"{label} شروع شد ⬇️")
        try:
            # هندلرهای بازی همه از update.effective_message / effective_user /
            # effective_chat استفاده می‌کنن که رو آپدیت‌های callback هم درست
            # resolve می‌شن، برای همین می‌شه مستقیم صداشون زد - انگار کاربر
            # خودش اسم بازی رو تایپ کرده.
            await handler(update, context)
        except Exception:
            await query.message.reply_text(
                f"⚠️ نشد {label} رو شروع کنم. دوباره امتحان کن یا اسمش رو تایپ کن."
            )
            return
        # منو رو نگه می‌داریم تا بشه بازی بعدی رو هم بدون دوباره نوشتن «گیم» انتخاب کرد
        try:
            await query.edit_message_reply_markup(reply_markup=build_category_keyboard(cat_key))
        except Exception:
            pass
        return

    await query.answer()


def register_games_menu(app):
    # این باید قبل از register_games(app) ثبت بشه (یا حداقل تو group=1 اول
    # اضافه بشه) وگرنه keyword_router (که هم‌گروهه و روی همه‌ی متن‌ها match
    # می‌شه) زودتر کلمه‌ی «گیم» رو می‌قاپه.
    app.add_handler(MessageHandler(MENU_TRIGGER_RE, games_menu_cmd), group=1)
    app.add_handler(CallbackQueryHandler(games_menu_callback, pattern=r"^gm:"), group=1)
