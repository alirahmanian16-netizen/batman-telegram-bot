# -*- coding: utf-8 -*-
"""
ttt_inline.py
================
دوز inline — تو هر چتی (حتی چت‌هایی که ربات توشون عضو نیست) بنویس:

    @نام_ربات

و یه لیست سایز (۳×۳ تا ۸×۸) میاد؛ هرکدوم رو انتخاب کنی، یه بازی دوز با اون
سایز تو همون چت پست می‌شه. اولین نفری که رو یه خونه بزنه ❌ می‌شه، اولین نفر
دیگه‌ای که بزنه ⭕ می‌شه؛ بعدش فقط همون دو نفر می‌تونن بازی کنن.

⚠️ نیازمندی: تو BotFather باید «Inline Mode» برای این ربات فعال باشه
   (/setinline تو چت با @BotFather).

نحوه‌ی اتصال (کنار بقیه‌ی register_ها تو bot.py):

    from ttt_inline import register_ttt_inline
    register_ttt_inline(app)     # <-- این خط رو اضافه کن
"""

import logging
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent,
)
from telegram.ext import ContextTypes, InlineQueryHandler, CallbackQueryHandler

log = logging.getLogger(__name__)

GAMES_INLINE = {}  # inline_message_id -> {...}


def _win_length(size: int) -> int:
    if size <= 3:
        return 3
    if size <= 5:
        return 4
    return 5


def _render_text(size, win_len, next_name=None, winner_name=None, draw=False):
    if draw:
        return f"🎯 دوز {size}×{size} — مساوی شد! 🤝"
    if winner_name:
        return f"🎯 دوز {size}×{size} — 🏆 برنده: {winner_name}"
    if next_name is None:
        return f"🎯 دوز {size}×{size}\n\nاولین نفری که رو یه خونه بزنه ❌ می‌شه."
    return f"🎯 دوز {size}×{size} — {win_len} تا پشت‌سرهم برای برد\n\n🎯 نوبت: {next_name}"


def _markup(size, board):
    rows = []
    for r in range(size):
        row = []
        for c in range(size):
            idx = r * size + c
            v = board[idx]
            label = "❌" if v == "X" else ("⭕" if v == "O" else "・")
            row.append(InlineKeyboardButton(label, callback_data=f"ittt:{size}:{idx}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _check_win(board, size, win_len, mark):
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


async def ttt_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = []
    for n in range(3, 9):
        win_len = _win_length(n)
        results.append(
            InlineQueryResultArticle(
                id=f"ttt{n}",
                title=f"🎯 دوز {n}×{n}",
                description=f"شروع بازی دوز {n}×{n} — {win_len} تا پشت‌سرهم برای برد",
                input_message_content=InputTextMessageContent(_render_text(n, win_len)),
                reply_markup=_markup(n, [""] * (n * n)),
            )
        )
    try:
        await update.inline_query.answer(results, cache_time=0)
    except Exception as e:
        log.warning(f"inline query answer failed: {e}")


async def ttt_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    size, idx = int(parts[1]), int(parts[2])
    imid = q.inline_message_id
    if not imid:
        await q.answer("این دکمه مال یه بازی inline نیست.", show_alert=True)
        return

    win_len = _win_length(size)
    game = GAMES_INLINE.get(imid)
    uid = update.effective_user.id
    name = update.effective_user.first_name or update.effective_user.username or "بازیکن"

    if not game:
        game = {"size": size, "win_len": win_len, "board": [""] * (size * size),
                "players": {"X": uid}, "names": {uid: name}, "turn": "X"}
        GAMES_INLINE[imid] = game

    if uid not in game["players"].values():
        if len(game["players"]) >= 2:
            await q.answer("این بازی پره؛ فقط اون دو نفر می‌تونن بازی کنن.", show_alert=True)
            return
        game["players"]["O"] = uid
        game["names"][uid] = name

    mark = "X" if game["players"].get("X") == uid else ("O" if game["players"].get("O") == uid else None)
    if mark is None:
        await q.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if game["turn"] != mark:
        await q.answer("نوبت تو نیست.", show_alert=True)
        return
    if game["board"][idx]:
        await q.answer("این خونه پره.", show_alert=True)
        return

    game["board"][idx] = mark

    if _check_win(game["board"], size, win_len, mark):
        text = _render_text(size, win_len, winner_name=game["names"].get(uid, mark))
        await context.bot.edit_message_text(inline_message_id=imid, text=text, reply_markup=_markup(size, game["board"]))
        del GAMES_INLINE[imid]
        await q.answer(); return

    if all(game["board"]):
        text = _render_text(size, win_len, draw=True)
        await context.bot.edit_message_text(inline_message_id=imid, text=text, reply_markup=_markup(size, game["board"]))
        del GAMES_INLINE[imid]
        await q.answer(); return

    game["turn"] = "O" if mark == "X" else "X"
    next_uid = game["players"].get(game["turn"])
    next_name = game["names"].get(next_uid, "بازیکن دوم") if next_uid else "بازیکن دوم (هنوز نپیوسته)"
    text = _render_text(size, win_len, next_name=next_name)
    await context.bot.edit_message_text(inline_message_id=imid, text=text, reply_markup=_markup(size, game["board"]))
    await q.answer()


def register_ttt_inline(app):
    app.add_handler(InlineQueryHandler(ttt_inline_query))
    app.add_handler(CallbackQueryHandler(ttt_inline_callback, pattern=r"^ittt:"), group=14)
