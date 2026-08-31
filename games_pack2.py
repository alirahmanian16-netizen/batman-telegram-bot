# -*- coding: utf-8 -*-
"""
games_pack2.py
================
۵ بازی گریدی جدید، هم‌خانواده با ماینزوییپر و بقیه‌ی بازی‌های games.py
همه با «کلمه‌ی محرک» کار می‌کنن (بدون /) و از دکمه‌های شیشه‌ای (inline keyboard) استفاده می‌کنن.

بازی‌ها:
    ۱. ۲۰۴۸           -> کلمه: "2048" یا "بازی ۲۰۴۸"
    ۲. چراغ‌ها         -> کلمه: "چراغ‌ها" یا "بازی چراغها"
    ۳. حافظه          -> کلمه: "حافظه" یا "بازی حافظه"
    ۴. نبرد دریایی      -> کلمه: "نبرد دریایی" (روی پیام حریف ریپلای کن)
    ۵. گنج پنهان       -> کلمه: "گنج پنهان"

نحوه‌ی اتصال به فایل اصلی (batbot.py) — کنار register_games همین کارو بکن:

    from games import register_games
    from games_pack2 import register_extra_games, EXTRA_GAMES_LIST_TEXT

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    register_games(app)
    register_extra_games(app)     # <-- این خط رو اضافه کن
    ...
    app.run_polling()

اگه می‌خوای این ۵ بازی تو لیست اصلی «لیست بازی‌ها» هم دیده بشن، محتوای
EXTRA_GAMES_LIST_TEXT رو به آخر GAMES_LIST_TEXT تو games.py اضافه کن.
"""

import random
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters


EXTRA_GAMES_LIST_TEXT = (
    "2048 — گرید ۴×۴، دکمه‌های جهت‌دار\n"
    "چراغ‌ها — همه‌ی چراغ‌ها رو خاموش کن\n"
    "حافظه — جفت‌های مخفی رو پیدا کن\n"
    "نبرد دریایی — (روی پیام حریف ریپلای کن) ناوگان واقعی ۶×۶، با اصابت نوبت ادامه داری\n"
    "گنج پنهان — رو خونه‌ها کلیک کن، بمب نزن!\n"
)


def _kw(text: str):
    """هندلر متنی که فقط با تطابق کامل کلمه فعال می‌شه (case/space حساس نیست)."""
    return filters.Regex(rf"(?i)^\s*{text}\s*$")


# =========================================================
#  ۱. بازی ۲۰۴۸
# =========================================================

GAME2048_STATE = {}   # chat_id -> {"board": 4x4, "score": int}


def _g2048_new_board():
    board = [[0] * 4 for _ in range(4)]
    for _ in range(2):
        _g2048_add_tile(board)
    return board


def _g2048_add_tile(board):
    empties = [(r, c) for r in range(4) for c in range(4) if board[r][c] == 0]
    if not empties:
        return
    r, c = random.choice(empties)
    board[r][c] = 4 if random.random() < 0.1 else 2


def _g2048_compress_merge(row):
    nums = [x for x in row if x != 0]
    merged, score, i = [], 0, 0
    while i < len(nums):
        if i + 1 < len(nums) and nums[i] == nums[i + 1]:
            merged.append(nums[i] * 2)
            score += nums[i] * 2
            i += 2
        else:
            merged.append(nums[i])
            i += 1
    merged += [0] * (len(row) - len(merged))
    return merged, score


def _g2048_transpose(board):
    return [list(row) for row in zip(*board)]


def _g2048_move(board, direction):
    score_total = 0

    def apply_left(b):
        nonlocal score_total
        out = []
        for row in b:
            newrow, sc = _g2048_compress_merge(row)
            score_total += sc
            out.append(newrow)
        return out

    if direction == "left":
        newboard = apply_left(board)
    elif direction == "right":
        rev = [r[::-1] for r in board]
        merged = apply_left(rev)
        newboard = [r[::-1] for r in merged]
    elif direction == "up":
        t = _g2048_transpose(board)
        merged = apply_left(t)
        newboard = _g2048_transpose(merged)
    else:  # down
        t = _g2048_transpose(board)
        rev = [r[::-1] for r in t]
        merged = apply_left(rev)
        unrev = [r[::-1] for r in merged]
        newboard = _g2048_transpose(unrev)

    changed = newboard != board
    return newboard, changed, score_total


def _g2048_over(board):
    for r in range(4):
        for c in range(4):
            if board[r][c] == 0:
                return False
            if c + 1 < 4 and board[r][c] == board[r][c + 1]:
                return False
            if r + 1 < 4 and board[r][c] == board[r + 1][c]:
                return False
    return True


def _g2048_render(state):
    board, score = state["board"], state["score"]
    lines = []
    for row in board:
        lines.append(" ".join(f"{(v or '·'):>4}" for v in row))
    return f"امتیاز: {score}\n```\n" + "\n".join(lines) + "\n```"


def _g2048_markup():
    rows = [
        [InlineKeyboardButton(" ", callback_data="noop"),
         InlineKeyboardButton("⬆️", callback_data="g2048:up"),
         InlineKeyboardButton(" ", callback_data="noop")],
        [InlineKeyboardButton("⬅️", callback_data="g2048:left"),
         InlineKeyboardButton("🔁", callback_data="g2048:restart"),
         InlineKeyboardButton("➡️", callback_data="g2048:right")],
        [InlineKeyboardButton(" ", callback_data="noop"),
         InlineKeyboardButton("⬇️", callback_data="g2048:down"),
         InlineKeyboardButton(" ", callback_data="noop")],
    ]
    return InlineKeyboardMarkup(rows)


async def g2048_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = {"board": _g2048_new_board(), "score": 0}
    GAME2048_STATE[chat_id] = state
    await update.effective_message.reply_text(
        "🎮 بازی ۲۰۴۸ شروع شد!\n\n" + _g2048_render(state),
        reply_markup=_g2048_markup(),
        parse_mode="Markdown",
    )


async def g2048_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    _, direction = query.data.split(":")

    if direction == "restart":
        state = {"board": _g2048_new_board(), "score": 0}
        GAME2048_STATE[chat_id] = state
        await query.edit_message_text(
            "🔁 بازی از نو شروع شد!\n\n" + _g2048_render(state),
            reply_markup=_g2048_markup(),
            parse_mode="Markdown",
        )
        await query.answer()
        return

    state = GAME2048_STATE.get(chat_id)
    if not state:
        await query.answer("بازی‌ای در جریان نیست. بنویس «2048».", show_alert=True)
        return

    newboard, changed, gained = _g2048_move(state["board"], direction)
    if not changed:
        await query.answer("این طرف جا نداره!")
        return

    state["board"] = newboard
    state["score"] += gained
    _g2048_add_tile(state["board"])

    won = any(v >= 2048 for row in state["board"] for v in row)
    over = _g2048_over(state["board"])

    text = _g2048_render(state)
    if won:
        text += "\n\n🏆 به ۲۰۴۸ رسیدی! می‌تونی ادامه بدی یا با 🔁 از نو شروع کنی."
    elif over:
        text += "\n\n💀 جا برای حرکت نیست. بازی تمومه! برای شروع دوباره 🔁 رو بزن."

    await query.edit_message_text(text, reply_markup=_g2048_markup(), parse_mode="Markdown")
    await query.answer()


# =========================================================
#  ۲. چراغ‌ها (Lights Out) — گرید ۵×۵
# =========================================================

LIGHTSOUT_STATE = {}   # chat_id -> {"grid": 5x5 bool, "moves": int}
LO_SIZE = 5


def _lo_toggle(grid, r, c):
    for dr, dc in [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)]:
        rr, cc = r + dr, c + dc
        if 0 <= rr < LO_SIZE and 0 <= cc < LO_SIZE:
            grid[rr][cc] = not grid[rr][cc]


def _lo_new_grid():
    grid = [[False] * LO_SIZE for _ in range(LO_SIZE)]
    for _ in range(random.randint(6, 12)):
        r, c = random.randint(0, LO_SIZE - 1), random.randint(0, LO_SIZE - 1)
        _lo_toggle(grid, r, c)
    if all(not cell for row in grid for cell in row):
        _lo_toggle(grid, LO_SIZE // 2, LO_SIZE // 2)
    return grid


def _lo_markup(grid):
    rows = []
    for r in range(LO_SIZE):
        row = []
        for c in range(LO_SIZE):
            symbol = "💡" if grid[r][c] else "⬛"
            row.append(InlineKeyboardButton(symbol, callback_data=f"lo:{r}:{c}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def lightsout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    grid = _lo_new_grid()
    LIGHTSOUT_STATE[chat_id] = {"grid": grid, "moves": 0}
    await update.effective_message.reply_text(
        "💡 همه‌ی چراغ‌ها رو خاموش کن! هر کلیک، خودش و همسایه‌هاش رو تغییر می‌ده.",
        reply_markup=_lo_markup(grid),
    )


async def lightsout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    _, r, c = query.data.split(":")
    r, c = int(r), int(c)

    state = LIGHTSOUT_STATE.get(chat_id)
    if not state:
        await query.answer("بازی‌ای در جریان نیست. بنویس «چراغ‌ها».", show_alert=True)
        return

    _lo_toggle(state["grid"], r, c)
    state["moves"] += 1

    if all(not cell for row in state["grid"] for cell in row):
        await query.edit_message_text(f"🎉 بردی! تو {state['moves']} حرکت همه رو خاموش کردی.")
        del LIGHTSOUT_STATE[chat_id]
        await query.answer()
        return

    await query.edit_message_text(
        f"💡 حرکت‌ها: {state['moves']}", reply_markup=_lo_markup(state["grid"])
    )
    await query.answer()


# =========================================================
#  ۳. حافظه (Memory Match) — گرید ۴×۴، ۸ جفت
# =========================================================

MEMORY_STATE = {}   # chat_id -> {"cards":[16], "matched":set, "temp":[], "pending":[]}
MEMORY_EMOJIS = ["🦇", "🃏", "🔫", "🎭", "💰", "🕷️", "🌙", "🔪"]


def _mm_new_cards():
    cards = MEMORY_EMOJIS * 2
    random.shuffle(cards)
    return cards


def _mm_markup(state):
    visible = state["matched"] | set(state["temp"]) | set(state["pending"])
    rows = []
    for r in range(4):
        row = []
        for c in range(4):
            idx = r * 4 + c
            label = state["cards"][idx] if idx in visible else "❓"
            row.append(InlineKeyboardButton(label, callback_data=f"mm:{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def memory_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = {"cards": _mm_new_cards(), "matched": set(), "temp": [], "pending": []}
    MEMORY_STATE[chat_id] = state
    await update.effective_message.reply_text(
        "🧠 بازی حافظه شروع شد! دو کارت هم‌شکل رو پیدا کن.",
        reply_markup=_mm_markup(state),
    )


async def memory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    idx = int(query.data.split(":")[1])

    state = MEMORY_STATE.get(chat_id)
    if not state:
        await query.answer("بازی‌ای در جریان نیست. بنویس «حافظه».", show_alert=True)
        return

    if state["pending"]:
        state["pending"] = []
        await query.edit_message_text(
            "🧠 بازی حافظه — دو کارت هم‌شکل رو پیدا کن.", reply_markup=_mm_markup(state)
        )
        await query.answer("کارت‌ها برگشتن، حالا انتخاب کن.")
        return

    if idx in state["matched"] or idx in state["temp"]:
        await query.answer("این کارت رو انتخاب کردی.")
        return

    state["temp"].append(idx)

    if len(state["temp"]) == 1:
        await query.edit_message_text(
            "🧠 بازی حافظه — یه کارت دیگه انتخاب کن.", reply_markup=_mm_markup(state)
        )
        await query.answer()
        return

    i, j = state["temp"]
    state["temp"] = []
    if state["cards"][i] == state["cards"][j]:
        state["matched"].update([i, j])
        msg = "✅ جفت شد!"
    else:
        state["pending"] = [i, j]
        msg = "❌ جفت نبود، یه کلیک دیگه بزن تا برگردن."

    if len(state["matched"]) == 16:
        await query.edit_message_text("🎉 همه‌ی جفت‌ها پیدا شدن! بردی!")
        del MEMORY_STATE[chat_id]
        await query.answer(msg)
        return

    await query.edit_message_text(f"🧠 {msg}", reply_markup=_mm_markup(state))
    await query.answer()


# =========================================================
#  ۴. نبرد دریایی حرفه‌ای — هر بازیکن ناوگان واقعی خودش رو داره
#     گرید ۶×۶، ناوگان [۳،۲،۲،۱،۱] (۹ خونه)، شلیک به تخته‌ی حریف،
#     با اصابت نوبت ادامه پیدا می‌کنه، با خطا نوبت عوض می‌شه، غرق‌شدن هر کشتی اعلام می‌شه.
# =========================================================

BATTLESHIP_STATE = {}   # game_id -> {...}
BS_SIZE = 6
BS_FLEET = [3, 2, 2, 1, 1]

BS_LOBBIES = {}   # token -> {"creator": User}


def _bs_lobby_markup(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🙋 بپیوند به بازی", callback_data=f"lobby2:{token}")]])


def _bs_place_fleet():
    """ناوگان رو بدون هم‌پوشانی، افقی یا عمودی، تصادفی رو گرید می‌چینه."""
    occupied = set()
    ships = []
    for length in BS_FLEET:
        placed = False
        for _ in range(300):
            horizontal = random.choice([True, False])
            if horizontal:
                r = random.randint(0, BS_SIZE - 1)
                c = random.randint(0, BS_SIZE - length)
                cells = {r * BS_SIZE + c + i for i in range(length)}
            else:
                r = random.randint(0, BS_SIZE - length)
                c = random.randint(0, BS_SIZE - 1)
                cells = {(r + i) * BS_SIZE + c for i in range(length)}
            if cells & occupied:
                continue
            occupied |= cells
            ships.append(cells)
            placed = True
            break
        if not placed:  # به‌ندرت پیش میاد؛ فال‌بک بدون چک هم‌پوشانی
            cells = set(random.sample(range(BS_SIZE * BS_SIZE), length))
            occupied |= cells
            ships.append(cells)
    return ships, occupied


def _bs_markup(game_id, board):
    rows = []
    for r in range(BS_SIZE):
        row = []
        for c in range(BS_SIZE):
            idx = r * BS_SIZE + c
            if idx in board["hits"]:
                label = "🔥"
            elif idx in board["misses"]:
                label = "🌊"
            else:
                label = "▫️"
            row.append(InlineKeyboardButton(label, callback_data=f"bs:{game_id}:{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _bs_new_board():
    ships, cells = _bs_place_fleet()
    return {"ships": ships, "cells": cells, "hits": set(), "misses": set()}


async def _launch_battleship(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}_{random.randint(1000,9999)}"
    BATTLESHIP_STATE[game_id] = {
        "boards": {p1.id: _bs_new_board(), p2.id: _bs_new_board()},
        "turn": p1.id,
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
    }
    state = BATTLESHIP_STATE[game_id]
    fleet_desc = "، ".join(f"{n} خونه‌ای" for n in BS_FLEET)
    text = (
        f"🚢 نبرد دریایی: {p1.first_name} در برابر {p2.first_name}\n"
        f"ناوگان هرکس: {fleet_desc} (جمعاً {sum(BS_FLEET)} خونه، مخفی)\n\n"
        f"🎯 نوبت: {p1.first_name} — رو تخته‌ی زیر (تخته‌ی حریف) بزن!"
    )
    markup = _bs_markup(game_id, state["boards"][p2.id])
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
    else:
        await target_msg.reply_text(text, reply_markup=markup)


async def battleship_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    creator = update.effective_user

    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        p2 = msg.reply_to_message.from_user
        if p2.id == creator.id:
            await msg.reply_text("نمی‌تونی با خودت بازی کنی 🙂")
            return
        await _launch_battleship(msg, creator, p2)
        return

    token = f"{update.effective_chat.id}_{creator.id}_{random.randint(100000, 999999)}"
    BS_LOBBIES[token] = {"creator": creator}
    await msg.reply_text(
        f"🎮 {creator.first_name} می‌خواد نبرد دریایی بازی کنه!\nحریف، دکمه‌ی زیر رو بزن تا بازی شروع بشه.",
        reply_markup=_bs_lobby_markup(token),
    )


async def bs_lobby_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    token = query.data.split(":", 1)[1]
    lobby = BS_LOBBIES.get(token)
    if not lobby:
        await query.answer("این دعوت منقضی شده یا یکی دیگه قبلاً پیوسته.", show_alert=True)
        return
    creator = lobby["creator"]
    joiner = query.from_user
    if joiner.id == creator.id:
        await query.answer("نمی‌تونی با خودت بازی کنی 🙂", show_alert=True)
        return
    del BS_LOBBIES[token]
    await _launch_battleship(query.message, creator, joiner, edit=True)
    await query.answer()


async def battleship_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, idx = query.data.split(":")
    idx = int(idx)

    state = BATTLESHIP_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in state["boards"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return

    opponent_id = [pid for pid in state["boards"] if pid != user_id][0]
    board = state["boards"][opponent_id]  # تخته‌ای که داریم بهش شلیک می‌کنیم

    if idx in board["hits"] or idx in board["misses"]:
        await query.answer("این خونه رو قبلاً زدی.", show_alert=True)
        return

    hit = idx in board["cells"]
    sunk_ship = None
    if hit:
        board["hits"].add(idx)
        for ship in board["ships"]:
            if idx in ship and ship <= board["hits"]:
                sunk_ship = ship
                break
        result_line = f"💥 {state['names'][user_id]} یه کشتی رو غرق کرد!" if sunk_ship else f"🔥 {state['names'][user_id]} زد!"
    else:
        board["misses"].add(idx)
        result_line = f"🌊 {state['names'][user_id]} آب زد."

    remaining = len(board["cells"]) - len(board["hits"])
    if remaining == 0:
        await query.edit_message_text(
            f"{result_line}\n\n🏆 {state['names'][user_id]} کل ناوگان {state['names'][opponent_id]} رو غرق کرد و برد!",
            reply_markup=_bs_markup(game_id, board),
        )
        del BATTLESHIP_STATE[game_id]
        await query.answer()
        return

    if not hit:
        state["turn"] = opponent_id
        next_target = state["boards"][user_id]
    else:
        next_target = board  # همون بازیکن ادامه می‌ده، همون تخته‌ی حریف

    next_shooter = state["turn"]
    await query.edit_message_text(
        f"{result_line}\n\n🎯 نوبت: {state['names'][next_shooter]}",
        reply_markup=_bs_markup(game_id, next_target),
    )
    await query.answer()


# =========================================================
#  ۵. گنج پنهان — گرید ۵×۵ گروهی، ۴ بمب مخفی
# =========================================================

TREASURE_STATE = {}   # chat_id -> {...}
TG_SIZE = 5
TG_BOMBS = 4


def _tg_markup(state):
    rows = []
    for r in range(TG_SIZE):
        row = []
        for c in range(TG_SIZE):
            idx = r * TG_SIZE + c
            if idx in state["revealed"]:
                label = "💣" if idx in state["bombs"] else "🪙"
            else:
                label = "▫️"
            row.append(InlineKeyboardButton(label, callback_data=f"tg:{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def treasure_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    bombs = set(random.sample(range(TG_SIZE * TG_SIZE), TG_BOMBS))
    state = {
        "revealed": set(),
        "bombs": bombs,
        "scores": defaultdict(int),
        "over": False,
    }
    TREASURE_STATE[chat_id] = state
    await update.effective_message.reply_text(
        f"💰 گنج پنهان! رو خونه‌ها کلیک کنین، {TG_BOMBS} بمب مخفیه. هرکی سکه پیدا کنه امتیاز می‌گیره.",
        reply_markup=_tg_markup(state),
    )


async def treasure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    idx = int(query.data.split(":")[1])

    state = TREASURE_STATE.get(chat_id)
    if not state or state["over"]:
        await query.answer("این دور تموم شده. بنویس «گنج پنهان» برای دور جدید.", show_alert=True)
        return

    if idx in state["revealed"]:
        await query.answer("این خونه رو قبلاً باز کردی.")
        return

    user = query.from_user
    state["revealed"].add(idx)

    if idx in state["bombs"]:
        state["over"] = True
        state["revealed"] |= state["bombs"]
        scoreboard = "\n".join(f"• {name}: {pts}" for name, pts in _tg_top(state)) or "کسی امتیاز نگرفت."
        await query.edit_message_text(
            f"💥 بوم! {user.first_name} روی بمب کلیک کرد. دور تموم شد.\n\n🏅 امتیازها:\n{scoreboard}",
            reply_markup=_tg_markup(state),
        )
        del TREASURE_STATE[chat_id]
        await query.answer()
        return

    coin = random.randint(1, 3)
    state["scores"][user.first_name] += coin

    total_cells = TG_SIZE * TG_SIZE
    if len(state["revealed"]) == total_cells - TG_BOMBS:
        state["over"] = True
        scoreboard = "\n".join(f"• {name}: {pts}" for name, pts in _tg_top(state))
        await query.edit_message_text(
            f"🎉 همه‌ی سکه‌ها پیدا شدن! بمب‌ها امن ماندند.\n\n🏅 امتیازها:\n{scoreboard}",
            reply_markup=_tg_markup(state),
        )
        del TREASURE_STATE[chat_id]
        await query.answer(f"+{coin} سکه!")
        return

    await query.edit_message_text(
        f"🪙 {user.first_name} یه سکه پیدا کرد (+{coin}).", reply_markup=_tg_markup(state)
    )
    await query.answer(f"+{coin} سکه!")


def _tg_top(state):
    return sorted(state["scores"].items(), key=lambda kv: kv[1], reverse=True)


async def _noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_extra_games(app):
    # همه‌ی این‌ها group=2 می‌گیرن (گروه اختصاصیِ این فایل) تا نه با handle_message /
    # button_handler ربات (group=0) تصادم کنن، نه با keyword_router خود games.py
    # (group=1، که چون فیلترش کل متن‌ها رو می‌گیره، اگه تو همون گروه بودن هیچ‌وقت اجرا نمی‌شدن).
    # ۲۰۴۸
    app.add_handler(MessageHandler(_kw("2048|بازی ?2048|بازی ۲۰۴۸|۲۰۴۸"), g2048_start), group=2)
    app.add_handler(CallbackQueryHandler(g2048_callback, pattern=r"^g2048:"), group=2)

    # چراغ‌ها
    app.add_handler(MessageHandler(_kw("چراغ\u200cها|چراغها|بازی چراغ\u200cها"), lightsout_start), group=2)
    app.add_handler(CallbackQueryHandler(lightsout_callback, pattern=r"^lo:"), group=2)

    # حافظه
    app.add_handler(MessageHandler(_kw("حافظه|بازی حافظه"), memory_start), group=2)
    app.add_handler(CallbackQueryHandler(memory_callback, pattern=r"^mm:"), group=2)

    # نبرد دریایی
    app.add_handler(MessageHandler(_kw("نبرد دریایی|نبرد کشتی\u200cها"), battleship_start), group=2)
    app.add_handler(CallbackQueryHandler(battleship_callback, pattern=r"^bs:"), group=2)
    app.add_handler(CallbackQueryHandler(bs_lobby_join_callback, pattern=r"^lobby2:"), group=2)

    # گنج پنهان
    app.add_handler(MessageHandler(_kw("گنج پنهان|گنج مخفی"), treasure_start), group=2)
    app.add_handler(CallbackQueryHandler(treasure_callback, pattern=r"^tg:"), group=2)

    # دکمه‌های خالی/تزئینی
    app.add_handler(CallbackQueryHandler(_noop_callback, pattern=r"^noop$"), group=2)
