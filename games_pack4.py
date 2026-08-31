# -*- coding: utf-8 -*-
"""
games_pack4.py
================
۵ بازی جدید، هم‌خانواده با بقیه‌ی فایل‌های games*.py (کلمه‌محرک، بدون /،
دکمه‌های شیشه‌ای).

بازی‌ها:
    ۱. مین روب      -> کلمه: "مین روب" یا "مین یاب"        (گروهی، همه می‌تونن بزنن)
    ۲. نقطه بازی    -> کلمه: "نقطه بازی"                    (روی پیام حریف ریپلای کن)
    ۳. تیکو         -> کلمه: "تیکو"                          (روی پیام حریف ریپلای کن، ۴ در ردیف روی گرید ۵×۵)
    ۴. جمشید        -> کلمه: "جمشید"                         (روی پیام حریف ریپلای کن، بازی مسیر/تاس)
    ۵. گیر بازار    -> کلمه: "گیر بازار"                     (روی پیام حریف ریپلای کن، مسابقه‌ی رسیدن به گوشه‌ی مقابل)

نکته‌ی مهم: تیکو / جمشید / گیر بازار قوانین رسمی مشخص و مستندی ندارن، پس یه
نسخه‌ی قابل‌بازی و ساده الهام‌گرفته از عکس‌هایی که فرستادی پیاده‌سازی شده.
اگه قانون دقیق‌تری تو ذهنته بگو تا دقیقاً همون رو پیاده کنم.

نحوه‌ی اتصال (کنار register_games / register_extra_games / register_extra_lists):

    from games import register_games
    from games_pack2 import register_extra_games
    from games_pack3 import register_extra_lists
    from games_pack4 import register_extra_games2

    register_games(app)
    register_extra_games(app)
    register_extra_lists(app)
    register_extra_games2(app)     # <-- این خط رو اضافه کن
"""

import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters


EXTRA_GAMES_LIST_TEXT4 = (
    "مین روب — رو خونه‌ها بزن، ۶ بمب مخفی روی گرید ۶×۶\n"
    "نقطه بازی — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن، خط بکش، جعبه ببند\n"
    "تیکو — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن، ۴ تا پشت‌سرهم روی گرید ۵×۵\n"
    "جمشید — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن، تاس بنداز، دور مسیر بچرخ\n"
    "گیر بازار — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن، زودتر به گوشه‌ی مقابل برس\n"
)


def _kw(text: str):
    return filters.Regex(rf"(?i)^\s*{text}\s*$")


async def _noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# =========================================================
#  لابی مشترک (دکمه‌ی «بپیوند») برای بازی‌های دو نفره‌ی این پک
# =========================================================

LOBBIES4 = {}   # token -> {"game": key, "creator": User}

GAME_LABELS4 = {
    "dots": "نقطه بازی",
    "tiko": "تیکو",
    "jamshid": "جمشید",
    "bazar": "گیر بازار",
}


def _lobby4_markup(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🙋 بپیوند به بازی", callback_data=f"lobby4:{token}")]])


async def _start_two_player(update: Update, context: ContextTypes.DEFAULT_TYPE, game_key, launch_fn):
    """اگه ریپلای به یه نفر باشه مستقیم شروع می‌کنه، وگرنه یه لابی با دکمه‌ی
    بپیوند می‌سازه تا هرکی زد حریف بشه."""
    msg = update.effective_message
    creator = update.effective_user

    if msg.reply_to_message and msg.reply_to_message.from_user and \
       not msg.reply_to_message.from_user.is_bot and msg.reply_to_message.from_user.id != creator.id:
        p2 = msg.reply_to_message.from_user
        await launch_fn(msg, creator, p2)
        return

    token = f"{update.effective_chat.id}_{creator.id}_{random.randint(100000, 999999)}"
    LOBBIES4[token] = {"game": game_key, "creator": creator}
    await msg.reply_text(
        f"🎮 {creator.first_name} می‌خواد {GAME_LABELS4[game_key]} بازی کنه!\n"
        f"حریف، دکمه‌ی زیر رو بزن تا بازی شروع بشه.",
        reply_markup=_lobby4_markup(token),
    )


async def lobby4_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    token = query.data.split(":", 1)[1]
    lobby = LOBBIES4.get(token)
    if not lobby:
        await query.answer("این دعوت منقضی شده یا یکی دیگه قبلاً پیوسته.", show_alert=True)
        return

    creator = lobby["creator"]
    joiner = query.from_user
    if joiner.id == creator.id:
        await query.answer("نمی‌تونی با خودت بازی کنی 🙂", show_alert=True)
        return

    del LOBBIES4[token]
    game = lobby["game"]
    launch_fn = {
        "dots": _launch_dots,
        "tiko": _launch_tiko,
        "jamshid": _launch_jamshid,
        "bazar": _launch_bazar,
    }[game]
    await launch_fn(query.message, creator, joiner, edit=True)
    await query.answer()


# =========================================================
#  ۱. مین روب — گرید ۵×۵، ۴ بمب، گروهی
# =========================================================

MS_SIZE = 6
MS_MINES = 6
MINESWEEPER_STATE = {}   # chat_id -> {"mines": set, "revealed": set, "over": bool}


def _ms_neighbors(idx):
    r, c = divmod(idx, MS_SIZE)
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr, cc = r + dr, c + dc
            if 0 <= rr < MS_SIZE and 0 <= cc < MS_SIZE:
                out.append(rr * MS_SIZE + cc)
    return out


def _ms_count(idx, mines):
    return sum(1 for n in _ms_neighbors(idx) if n in mines)


def _ms_reveal(state, idx):
    if idx in state["revealed"] or idx in state["mines"]:
        state["revealed"].add(idx)
        return
    stack = [idx]
    while stack:
        cur = stack.pop()
        if cur in state["revealed"]:
            continue
        state["revealed"].add(cur)
        if _ms_count(cur, state["mines"]) == 0:
            for n in _ms_neighbors(cur):
                if n not in state["revealed"] and n not in state["mines"]:
                    stack.append(n)


def _ms_markup(state):
    rows = []
    for r in range(MS_SIZE):
        row = []
        for c in range(MS_SIZE):
            idx = r * MS_SIZE + c
            if idx in state["revealed"]:
                if idx in state["mines"]:
                    label = "💣"
                else:
                    cnt = _ms_count(idx, state["mines"])
                    label = str(cnt) if cnt else "▫️"
            else:
                label = "❔"
            row.append(InlineKeyboardButton(label, callback_data=f"ms:{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def minesweeper_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    mines = set(random.sample(range(MS_SIZE * MS_SIZE), MS_MINES))
    state = {"mines": mines, "revealed": set(), "over": False}
    MINESWEEPER_STATE[chat_id] = state
    await update.effective_message.reply_text(
        f"🧨 مین‌روب شروع شد! {MS_MINES} بمب مخفی روی گرید {MS_SIZE}×{MS_SIZE}. رو خونه‌ها بزن.",
        reply_markup=_ms_markup(state),
    )


async def minesweeper_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    idx = int(query.data.split(":")[1])

    state = MINESWEEPER_STATE.get(chat_id)
    if not state or state["over"]:
        await query.answer("بازی‌ای در جریان نیست. بنویس «مین روب».", show_alert=True)
        return
    if idx in state["revealed"]:
        await query.answer("این خونه رو قبلاً باز کردی.")
        return

    user = query.from_user
    if idx in state["mines"]:
        state["over"] = True
        state["revealed"] |= set(range(MS_SIZE * MS_SIZE))
        await query.edit_message_text(
            f"💥 بوم! {user.first_name} رو بمب زد. بازی تموم شد.\nبرای دور جدید بنویس «مین روب».",
            reply_markup=_ms_markup(state),
        )
        del MINESWEEPER_STATE[chat_id]
        await query.answer()
        return

    _ms_reveal(state, idx)
    total = MS_SIZE * MS_SIZE
    if len(state["revealed"]) == total - MS_MINES:
        state["over"] = True
        await query.edit_message_text(
            f"🎉 {user.first_name} همه‌ی خونه‌های امن رو باز کرد! بردید 🏆\nبرای دور جدید بنویس «مین روب».",
            reply_markup=_ms_markup(state),
        )
        del MINESWEEPER_STATE[chat_id]
        await query.answer()
        return

    await query.edit_message_text(
        f"🧨 مین‌روب — {user.first_name} داره پیش می‌ره...", reply_markup=_ms_markup(state)
    )
    await query.answer()


# =========================================================
#  ۲. نقطه بازی (Dots & Boxes) — گرید ۲×۲ جعبه، دو نفره
# =========================================================

DOTS_N = 2   # تعداد جعبه در هر ضلع
DOTS_STATE = {}   # game_id -> {...}


def _dots_new_state(p1, p2):
    return {
        "h": [[False] * DOTS_N for _ in range(DOTS_N + 1)],
        "v": [[False] * (DOTS_N + 1) for _ in range(DOTS_N)],
        "boxes": [[None] * DOTS_N for _ in range(DOTS_N)],
        "turn": p1.id,
        "scores": {p1.id: 0, p2.id: 0},
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
        "symbols": {p1.id: "🟦", p2.id: "🟥"},
    }


def _dots_box_complete(state, r, c):
    return (
        state["h"][r][c] and state["h"][r + 1][c]
        and state["v"][r][c] and state["v"][r][c + 1]
    )


def _dots_markup(game_id, state):
    rows = []
    for r in range(DOTS_N + 1):
        row = [InlineKeyboardButton("•", callback_data="noop")]
        for c in range(DOTS_N):
            label = "━━" if state["h"][r][c] else "··"
            row.append(InlineKeyboardButton(label, callback_data=f"dots:{game_id}:h:{r}:{c}"))
            row.append(InlineKeyboardButton("•", callback_data="noop"))
        rows.append(row)
        if r < DOTS_N:
            row2 = []
            for c in range(DOTS_N + 1):
                label = "┃" if state["v"][r][c] else "·"
                row2.append(InlineKeyboardButton(label, callback_data=f"dots:{game_id}:v:{r}:{c}"))
                if c < DOTS_N:
                    owner = state["boxes"][r][c]
                    box_label = state["symbols"][owner] if owner else "▫️"
                    row2.append(InlineKeyboardButton(box_label, callback_data="noop"))
            rows.append(row2)
    return InlineKeyboardMarkup(rows)


async def _launch_dots(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}"
    state = _dots_new_state(p1, p2)
    DOTS_STATE[game_id] = state
    text = (
        f"📐 نقطه بازی: {p1.first_name} 🟦 در برابر {p2.first_name} 🟥\n"
        f"با بستن هر جعبه یه امتیاز می‌گیری و دوباره نوبت خودته.\nنوبت: {p1.first_name}"
    )
    markup = _dots_markup(game_id, state)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
    else:
        await target_msg.reply_text(text, reply_markup=markup)


async def dots_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_two_player(update, context, "dots", _launch_dots)


async def dots_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, kind, r, c = query.data.split(":")
    r, c = int(r), int(c)

    state = DOTS_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in state["scores"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return

    grid = state["h"] if kind == "h" else state["v"]
    if grid[r][c]:
        await query.answer("این خط رو قبلاً کشیدی.")
        return
    grid[r][c] = True

    completed = 0
    if kind == "h":
        if r - 1 >= 0 and _dots_box_complete(state, r - 1, c):
            state["boxes"][r - 1][c] = user_id
            completed += 1
        if r < DOTS_N and _dots_box_complete(state, r, c):
            state["boxes"][r][c] = user_id
            completed += 1
    else:
        if c - 1 >= 0 and _dots_box_complete(state, r, c - 1):
            state["boxes"][r][c - 1] = user_id
            completed += 1
        if c < DOTS_N and _dots_box_complete(state, r, c):
            state["boxes"][r][c] = user_id
            completed += 1

    if completed:
        state["scores"][user_id] += completed

    other_id = [pid for pid in state["scores"] if pid != user_id][0]
    total_boxes = DOTS_N * DOTS_N
    filled = sum(1 for row in state["boxes"] for b in row if b)

    if filled == total_boxes:
        s1, s2 = state["scores"][user_id], state["scores"][other_id]
        if s1 == s2:
            result = "🤝 مساوی شدن!"
        else:
            winner = state["names"][user_id] if s1 > s2 else state["names"][other_id]
            result = f"🏆 {winner} برد!"
        await query.edit_message_text(
            f"📐 نقطه بازی تموم شد!\n{state['names'][user_id]}: {s1} | {state['names'][other_id]}: {s2}\n{result}",
            reply_markup=_dots_markup(game_id, state),
        )
        del DOTS_STATE[game_id]
        await query.answer()
        return

    if not completed:
        state["turn"] = other_id

    await query.edit_message_text(
        f"📐 نقطه بازی\n"
        f"{state['names'][user_id]} {state['symbols'][user_id]}: {state['scores'][user_id]} | "
        f"{state['names'][other_id]} {state['symbols'][other_id]}: {state['scores'][other_id]}\n"
        f"نوبت: {state['names'][state['turn']]}",
        reply_markup=_dots_markup(game_id, state),
    )
    await query.answer("یه جعبه بستی! دوباره نوبت توئه 🎉" if completed else "")


# =========================================================
#  ۳. تیکو — گرید ۵×۵، ۴ تا پشت‌سرهم (افقی/عمودی/مورب)، دو نفره
# =========================================================

TIKO_SIZE = 5
TIKO_WIN = 4
TIKO_STATE = {}   # game_id -> {...}


def _tiko_check_win(board, r, c, symbol):
    dirs = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for dr, dc in dirs:
        count = 1
        for sign in (1, -1):
            rr, cc = r + dr * sign, c + dc * sign
            while 0 <= rr < TIKO_SIZE and 0 <= cc < TIKO_SIZE and board[rr][cc] == symbol:
                count += 1
                rr += dr * sign
                cc += dc * sign
        if count >= TIKO_WIN:
            return True
    return False


def _tiko_markup(game_id, state):
    rows = []
    for r in range(TIKO_SIZE):
        row = []
        for c in range(TIKO_SIZE):
            cell = state["board"][r][c]
            label = cell if cell else "➕"
            row.append(InlineKeyboardButton(label, callback_data=f"tiko:{game_id}:{r}:{c}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def _launch_tiko(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}"
    state = {
        "board": [[None] * TIKO_SIZE for _ in range(TIKO_SIZE)],
        "turn": p1.id,
        "symbols": {p1.id: "⚫", p2.id: "🔴"},
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
        "moves": 0,
    }
    TIKO_STATE[game_id] = state
    text = (
        f"⚫🔴 تیکو: {p1.first_name} ⚫ در برابر {p2.first_name} 🔴\n"
        f"هرکی اول ۴تا پشت‌سرهم (افقی/عمودی/مورب) بسازه برنده‌ست.\nنوبت: {p1.first_name}"
    )
    markup = _tiko_markup(game_id, state)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
    else:
        await target_msg.reply_text(text, reply_markup=markup)


async def tiko_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_two_player(update, context, "tiko", _launch_tiko)


async def tiko_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, r, c = query.data.split(":")
    r, c = int(r), int(c)

    state = TIKO_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in state["symbols"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return
    if state["board"][r][c] is not None:
        await query.answer("این خونه پره.")
        return

    symbol = state["symbols"][user_id]
    state["board"][r][c] = symbol
    state["moves"] += 1
    other_id = [pid for pid in state["symbols"] if pid != user_id][0]

    if _tiko_check_win(state["board"], r, c, symbol):
        await query.edit_message_text(
            f"🏆 {state['names'][user_id]} با تیکو برد!", reply_markup=_tiko_markup(game_id, state)
        )
        del TIKO_STATE[game_id]
        await query.answer()
        return

    if state["moves"] == TIKO_SIZE * TIKO_SIZE:
        await query.edit_message_text("🤝 گرید پر شد، مساوی شدید!", reply_markup=_tiko_markup(game_id, state))
        del TIKO_STATE[game_id]
        await query.answer()
        return

    state["turn"] = other_id
    await query.edit_message_text(
        f"⚫🔴 تیکو — نوبت: {state['names'][other_id]}", reply_markup=_tiko_markup(game_id, state)
    )
    await query.answer()


# =========================================================
#  ۴. جمشید — بازی مسیر دایره‌ای با تاس، دو نفره
# =========================================================

JAMSHID_TRACK = 12
JAMSHID_STATE = {}   # game_id -> {...}


def _jamshid_render(state, p1_id, p2_id):
    cells = ["▫️"] * JAMSHID_TRACK
    pos1, pos2 = state["pos"][p1_id] % JAMSHID_TRACK, state["pos"][p2_id] % JAMSHID_TRACK
    if state["pos"][p1_id] == pos2 == state["pos"][p2_id]:
        pass
    cells[pos1] = "🅰️" if pos1 != pos2 else "💥"
    if pos1 != pos2:
        cells[pos2] = "🅱️"
    track = " ".join(cells)
    return (
        f"🎯 مسیر: {track}\n"
        f"🅰️ {state['names'][p1_id]}: {state['pos'][p1_id]}/{JAMSHID_TRACK} دور\n"
        f"🅱️ {state['names'][p2_id]}: {state['pos'][p2_id]}/{JAMSHID_TRACK} دور\n"
        f"نوبت: {state['names'][state['turn']]}"
    )


def _jamshid_markup(game_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎲 تاس بنداز", callback_data=f"jamshid:{game_id}:roll")]])


async def _launch_jamshid(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}"
    state = {
        "pos": {p1.id: 0, p2.id: 0},
        "turn": p1.id,
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
    }
    JAMSHID_STATE[game_id] = state
    text = (
        f"🏺 جمشید: {p1.first_name} 🅰️ در برابر {p2.first_name} 🅱️\n"
        f"نوبتی تاس بنداز، اگه رو خونه‌ی حریف فرود بیای برمی‌گرده اول مسیر! اولین کسی که یه دور کامل بزنه می‌بره.\n\n"
        + _jamshid_render(state, p1.id, p2.id)
    )
    markup = _jamshid_markup(game_id)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
    else:
        await target_msg.reply_text(text, reply_markup=markup)


async def jamshid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_two_player(update, context, "jamshid", _launch_jamshid)


async def jamshid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, _action = query.data.split(":")

    state = JAMSHID_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in state["pos"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return

    other_id = [pid for pid in state["pos"] if pid != user_id][0]
    roll = random.randint(1, 6)
    state["pos"][user_id] += roll

    extra = f"🎲 {state['names'][user_id]} تاس زد: {roll}\n"

    if state["pos"][user_id] % JAMSHID_TRACK == state["pos"][other_id] % JAMSHID_TRACK and state["pos"][other_id] != 0:
        extra += f"💥 خورد به {state['names'][other_id]}! برگشت اول مسیر.\n"
        state["pos"][other_id] = 0

    if state["pos"][user_id] >= JAMSHID_TRACK:
        await query.edit_message_text(
            extra + f"\n🏆 {state['names'][user_id]} یه دور کامل زد و برد!",
            reply_markup=_jamshid_markup(game_id),
        )
        del JAMSHID_STATE[game_id]
        await query.answer()
        return

    state["turn"] = other_id
    await query.edit_message_text(
        extra + "\n" + _jamshid_render(state, *state["pos"].keys()),
        reply_markup=_jamshid_markup(game_id),
    )
    await query.answer()


# =========================================================
#  ۵. گیر بازار — مسابقه‌ی رسیدن به گوشه‌ی مقابل، گرید ۵×۵، دو نفره
# =========================================================

BAZAR_SIZE = 5
BAZAR_STATE = {}   # game_id -> {...}


def _bazar_render(state):
    size = BAZAR_SIZE
    grid = [["▫️"] * size for _ in range(size)]
    for pid, (r, c) in state["pos"].items():
        grid[r][c] = state["symbols"][pid]
    for pid, (r, c) in state["goal"].items():
        if grid[r][c] == "▫️":
            grid[r][c] = "🏁"
    board_text = "\n".join("".join(row) for row in grid)
    return board_text


def _bazar_markup(game_id):
    rows = [
        [InlineKeyboardButton(" ", callback_data="noop"),
         InlineKeyboardButton("⬆️", callback_data=f"bazar:{game_id}:up"),
         InlineKeyboardButton(" ", callback_data="noop")],
        [InlineKeyboardButton("⬅️", callback_data=f"bazar:{game_id}:left"),
         InlineKeyboardButton(" ", callback_data="noop"),
         InlineKeyboardButton("➡️", callback_data=f"bazar:{game_id}:right")],
        [InlineKeyboardButton(" ", callback_data="noop"),
         InlineKeyboardButton("⬇️", callback_data=f"bazar:{game_id}:down"),
         InlineKeyboardButton(" ", callback_data="noop")],
    ]
    return InlineKeyboardMarkup(rows)


async def _launch_bazar(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}"
    size = BAZAR_SIZE
    state = {
        "pos": {p1.id: (0, 0), p2.id: (size - 1, size - 1)},
        "goal": {p1.id: (size - 1, size - 1), p2.id: (0, 0)},
        "turn": p1.id,
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
        "symbols": {p1.id: "🔵", p2.id: "🔴"},
    }
    BAZAR_STATE[game_id] = state
    text = (
        f"🏪 گیر بازار: {p1.first_name} 🔵 در برابر {p2.first_name} 🔴\n"
        f"هرکی اول به 🏁 گوشه‌ی مقابل خودش برسه برنده‌ست. نمی‌تونی رو خونه‌ی حریف بری.\n\n"
        + _bazar_render(state) + f"\n\nنوبت: {p1.first_name}"
    )
    markup = _bazar_markup(game_id)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
    else:
        await target_msg.reply_text(text, reply_markup=markup)


async def bazar_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _start_two_player(update, context, "bazar", _launch_bazar)


async def bazar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, direction = query.data.split(":")

    state = BAZAR_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in state["pos"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return

    other_id = [pid for pid in state["pos"] if pid != user_id][0]
    r, c = state["pos"][user_id]
    dr, dc = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}[direction]
    nr, nc = r + dr, c + dc

    if not (0 <= nr < BAZAR_SIZE and 0 <= nc < BAZAR_SIZE):
        await query.answer("از گرید بیرون نمی‌تونی بری.")
        return
    if (nr, nc) == state["pos"][other_id]:
        await query.answer("این خونه رو حریفت گرفته!")
        return

    state["pos"][user_id] = (nr, nc)

    if (nr, nc) == state["goal"][user_id]:
        await query.edit_message_text(
            _bazar_render(state) + f"\n\n🏆 {state['names'][user_id]} به گوشه‌ی مقابل رسید و برد!",
            reply_markup=_bazar_markup(game_id),
        )
        del BAZAR_STATE[game_id]
        await query.answer()
        return

    state["turn"] = other_id
    await query.edit_message_text(
        _bazar_render(state) + f"\n\nنوبت: {state['names'][other_id]}",
        reply_markup=_bazar_markup(game_id),
    )
    await query.answer()


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_extra_games2(app):
    # group=4: گروه اختصاصی این فایل، وگرنه handle_message / button_handler ربات
    # (group=0) که catch-all هستن جلوی همه‌ی این‌ها رو می‌گرفتن.
    app.add_handler(MessageHandler(_kw("مین روب|مین یاب|مین‌روب|مین‌یاب"), minesweeper_start), group=4)
    app.add_handler(CallbackQueryHandler(minesweeper_callback, pattern=r"^ms:"), group=4)

    app.add_handler(MessageHandler(_kw("نقطه بازی|بازی نقطه"), dots_start), group=4)
    app.add_handler(CallbackQueryHandler(dots_callback, pattern=r"^dots:"), group=4)

    app.add_handler(MessageHandler(_kw("تیکو|بازی تیکو"), tiko_start), group=4)
    app.add_handler(CallbackQueryHandler(tiko_callback, pattern=r"^tiko:"), group=4)

    app.add_handler(MessageHandler(_kw("جمشید|بازی جمشید"), jamshid_start), group=4)
    app.add_handler(CallbackQueryHandler(jamshid_callback, pattern=r"^jamshid:"), group=4)

    app.add_handler(MessageHandler(_kw("گیر بازار|بازی گیر بازار"), bazar_start), group=4)
    app.add_handler(CallbackQueryHandler(bazar_callback, pattern=r"^bazar:"), group=4)

    # 🐛 Duplicate Handler رفع شد: قبلاً اینجا هم یه CallbackQueryHandler برای
    # "^noop$" ثبت می‌شد، درست مثل games_pack2.py. چون هر دو تو گروه‌های
    # جداگونه بودن، هر کلیک رو دکمه‌های تزئینی (مثل خونه‌های خالیِ ۲۰۴۸/دوز/
    # دوردونه) هر دو رو صدا می‌زد؛ دومی چون callback_query از قبل answer شده
    # بود می‌خورد به خطای بی‌صدا. همون هندلر مشترکِ games_pack2.py برای همه‌ی
    # دکمه‌های noopِ کل ربات کافیه — این ثبتِ تکراری حذف شد.
    app.add_handler(CallbackQueryHandler(lobby4_join_callback, pattern=r"^lobby4:"), group=4)
