# -*- coding: utf-8 -*-
"""
ttt_gotham.py
================
دوز گاتهام — با نوشتن «دوز گاتهام» تو چت (بدون نیاز به inline mode):
    ۱. سایز برد رو با دکمه انتخاب می‌کنی (۳×۳ تا ۸×۸)
    ۲. حریف رو انتخاب می‌کنی: 🙋 با دوست (لابی/پیوستن) یا 🤖 با ربات (فوری شروع می‌شه)
    ۳. بازی با دکمه‌های شیشه‌ای انجام می‌شه.

برای بردهای بزرگ‌تر از ۳×۳، تعداد لازم برای برد بیشتر می‌شه (وگرنه بازی رو
گریدهای بزرگ خیلی زود و بی‌معنی تموم می‌شه):
    سایز ۳          -> ۳ پشت‌سرهم
    سایز ۴ یا ۵      -> ۴ پشت‌سرهم
    سایز ۶ تا ۸      -> ۵ پشت‌سرهم

این ماژول باید هم از games.py ایمپورت بشه (TRIGGER_TEXT / gotham_ttt_start)
و هم تو bot.py رجیستر بشه (register_ttt_gotham).
"""

import random
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

log = logging.getLogger(__name__)

TRIGGER_TEXT = "دوز گاتهام"

SETUP_STATE = {}   # token -> {"creator": User, "size": int|None}
LOBBY_GTTT = {}     # token -> {"creator": User, "size": int}
GTTT_GAMES = {}      # gid -> {...}


def _gttt_win_length(size: int) -> int:
    if size <= 3:
        return 3
    if size <= 5:
        return 4
    return 5


async def gotham_ttt_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creator = update.effective_user
    token = f"{update.effective_chat.id}_{creator.id}_{random.randint(100000, 999999)}"
    SETUP_STATE[token] = {"creator": creator, "size": None}
    rows, row = [], []
    for n in range(3, 9):
        row.append(InlineKeyboardButton(f"{n}×{n}", callback_data=f"gttt:size:{token}:{n}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await update.effective_message.reply_text(
        f"🎯 دوز گاتهام\n\n{creator.first_name}، اول سایز برد رو انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def gotham_ttt_setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, token = parts[1], parts[2]
    setup = SETUP_STATE.get(token)
    if not setup:
        await q.answer("این دعوت منقضی شده.", show_alert=True); return
    if update.effective_user.id != setup["creator"].id:
        await q.answer("فقط سازنده می‌تونه انتخاب کنه.", show_alert=True); return

    if action == "size":
        size = int(parts[3])
        setup["size"] = size
        await q.edit_message_text(
            f"🎯 دوز گاتهام ({size}×{size})\n\nحالا حریف رو انتخاب کن:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🙋 با دوست", callback_data=f"gttt:opp:{token}:friend"),
                InlineKeyboardButton("🤖 با ربات", callback_data=f"gttt:opp:{token}:bot"),
            ]]),
        )
        await q.answer(); return

    if action == "opp":
        opp = parts[3]
        size = setup["size"]
        creator = setup["creator"]
        del SETUP_STATE[token]
        if opp == "bot":
            await _launch_gotham_ttt(q.message, creator, None, size, edit=True)
        else:
            LOBBY_GTTT[token] = {"creator": creator, "size": size}
            await q.edit_message_text(
                f"🎯 دوز گاتهام ({size}×{size})\n\n{creator.first_name} منتظر حریفه!\nروی دکمه بزن تا بپیوندی.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🙋 پیوستن", callback_data=f"gttt:join:{token}")]]),
            )
        await q.answer(); return


async def gttt_lobby_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    token = q.data.split(":", 2)[2]
    lobby = LOBBY_GTTT.get(token)
    if not lobby:
        await q.answer("این دعوت منقضی شده یا یکی دیگه قبلاً پیوسته.", show_alert=True); return
    creator = lobby["creator"]
    joiner = update.effective_user
    if joiner.id == creator.id:
        await q.answer("نمی‌تونی با خودت بازی کنی 🙂", show_alert=True); return
    del LOBBY_GTTT[token]
    await _launch_gotham_ttt(q.message, creator, joiner, lobby["size"], edit=True)
    await q.answer()


async def _launch_gotham_ttt(target_msg, p1, p2, size, edit=False):
    gid = f"{target_msg.chat.id}_{p1.id}_{random.randint(1000, 9999)}"
    vs_bot = p2 is None
    win_len = _gttt_win_length(size)
    GTTT_GAMES[gid] = {
        "chat_id": target_msg.chat.id,
        "size": size, "win_len": win_len, "board": [""] * (size * size),
        "players": {"X": p1.id, "O": ("BOT" if vs_bot else p2.id)},
        "names": {p1.id: p1.first_name, **({} if vs_bot else {p2.id: p2.first_name})},
        "turn": "X", "vs_bot": vs_bot,
    }
    game = GTTT_GAMES[gid]
    text = _gttt_text(game)
    markup = _gttt_markup(gid, game)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
    else:
        await target_msg.reply_text(text, reply_markup=markup)


def _gttt_text(game):
    size, win_len = game["size"], game["win_len"]
    xname = game["names"].get(game["players"]["X"], "X")
    oname = "🤖 ربات" if game["vs_bot"] else game["names"].get(game["players"]["O"], "O")
    turn_name = xname if game["turn"] == "X" else oname
    return (
        f"🎯 دوز گاتهام ({size}×{size} — {win_len} تا پشت‌سرهم برای برد)\n"
        f"❌ {xname}  در برابر  ⭕ {oname}\n\n"
        f"🎯 نوبت: {turn_name}"
    )


def _gttt_markup(gid, game):
    size = game["size"]
    rows = []
    for r in range(size):
        row = []
        for c in range(size):
            idx = r * size + c
            v = game["board"][idx]
            label = "❌" if v == "X" else ("⭕" if v == "O" else "・")
            row.append(InlineKeyboardButton(label, callback_data=f"gttt:mv:{gid}:{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _gttt_check_win(board, size, win_len, mark):
    def get(r, c):
        if 0 <= r < size and 0 <= c < size:
            return board[r * size + c]
        return None
    dirs = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for r in range(size):
        for c in range(size):
            if get(r, c) != mark:
                continue
            for dr, dc in dirs:
                count = 1
                rr, cc = r + dr, c + dc
                while get(rr, cc) == mark:
                    count += 1; rr += dr; cc += dc
                if count >= win_len:
                    return True
    return False


def _gttt_bot_move(game):
    size, board, win_len = game["size"], game["board"], game["win_len"]
    empties = [i for i, v in enumerate(board) if not v]
    for idx in empties:  # ۱) اگه ربات می‌تونه ببره
        board[idx] = "O"
        if _gttt_check_win(board, size, win_len, "O"):
            board[idx] = ""; return idx
        board[idx] = ""
    for idx in empties:  # ۲) اگه حریف می‌تونه ببره، جلوش رو بگیر
        board[idx] = "X"
        if _gttt_check_win(board, size, win_len, "X"):
            board[idx] = ""; return idx
        board[idx] = ""
    center = size // 2  # ۳) وگرنه نزدیک مرکز
    empties.sort(key=lambda i: abs(i // size - center) + abs(i % size - center))
    return empties[0]


def _gttt_record_result(game, winner_uid, loser_uid):
    """اتصال به Score موجود (bot._record_game_result) — فقط برای بازی‌های
    واقعاً دونفره؛ برد/باخت مقابل ربات حدس‌زده نشد و ثبت نمی‌شه چون تو
    مشخصات چیزی درباره‌ش گفته نشده بود."""
    if game.get("vs_bot"):
        return
    try:
        import bot as _bot
        _bot._record_game_result(game["chat_id"], winner_uid, loser_uid)
    except Exception as e:
        log.warning(f"ثبت نتیجه‌ی بازی دوز گاتهام (ttt_gotham) شکست خورد: {e}")


async def gotham_ttt_move_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    gid, idx = parts[2], int(parts[3])
    game = GTTT_GAMES.get(gid)
    if not game:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    mark = "X" if game["players"]["X"] == uid else ("O" if game["players"]["O"] == uid else None)
    if mark is None:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return
    if game["turn"] != mark:
        await q.answer("نوبت تو نیست.", show_alert=True); return
    if game["board"][idx]:
        await q.answer("این خونه پره.", show_alert=True); return

    game["board"][idx] = mark
    size, win_len = game["size"], game["win_len"]

    if _gttt_check_win(game["board"], size, win_len, mark):
        winner_name = game["names"].get(uid, mark)
        loser_uid = game["players"]["O"] if mark == "X" else game["players"]["X"]
        _gttt_record_result(game, uid, loser_uid)
        await q.edit_message_text(f"🎯 دوز گاتهام تمام شد!\n\n🏆 برنده: {winner_name} ({mark})", reply_markup=_gttt_markup(gid, game))
        del GTTT_GAMES[gid]; await q.answer(); return
    if all(game["board"]):
        await q.edit_message_text("🎯 دوز گاتهام مساوی شد! 🤝", reply_markup=_gttt_markup(gid, game))
        del GTTT_GAMES[gid]; await q.answer(); return

    game["turn"] = "O" if mark == "X" else "X"

    if game["vs_bot"] and game["turn"] == "O":
        bot_idx = _gttt_bot_move(game)
        game["board"][bot_idx] = "O"
        if _gttt_check_win(game["board"], size, win_len, "O"):
            await q.edit_message_text("🎯 دوز گاتهام تمام شد!\n\n🏆 برنده: 🤖 ربات", reply_markup=_gttt_markup(gid, game))
            del GTTT_GAMES[gid]; await q.answer(); return
        if all(game["board"]):
            await q.edit_message_text("🎯 دوز گاتهام مساوی شد! 🤝", reply_markup=_gttt_markup(gid, game))
            del GTTT_GAMES[gid]; await q.answer(); return
        game["turn"] = "X"

    await q.edit_message_text(_gttt_text(game), reply_markup=_gttt_markup(gid, game))
    await q.answer()


def register_ttt_gotham(app):
    # نکته: خود کلمه‌ی محرک «دوز گاتهام» از قبل تو games.py/keyword_router هندل
    # می‌شه (که gotham_ttt_start رو صدا می‌زنه)، اینجا فقط دکمه‌های شیشه‌ای رو
    # رجیستر می‌کنیم.
    app.add_handler(CallbackQueryHandler(gotham_ttt_setup_callback, pattern=r"^gttt:(size|opp):"), group=13)
    app.add_handler(CallbackQueryHandler(gttt_lobby_join_callback, pattern=r"^gttt:join:"), group=13)
    app.add_handler(CallbackQueryHandler(gotham_ttt_move_callback, pattern=r"^gttt:mv:"), group=13)
