# -*- coding: utf-8 -*-
"""Professional board games for Gotham Telegram Bot.

Games:
- Chess: real legal move validation via python-chess.
- Ludo: 2-4 players, dice, four pieces, capture/safe squares and exact finish.
- Snakes & Ladders: 2-player Dark Gotham board with real dice, step-by-step
  movement, per-turn timers/timeout, and rematch — see the SNAKES & LADDERS
  section below for the full implementation.
- Go (باستانی/Go): 2-player 9x9 board, real capture/liberty rules, suicide
  ban, simple-ko, pass-to-end, and area scoring with komi — see the GO
  section below.

All state is in memory for the current bot process. A restart ends active games.
"""
import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

try:
    import chess
except ImportError:  # helpful startup message if dependency was forgotten
    chess = None

log = logging.getLogger(__name__)


CHESS_GAMES: Dict[str, dict] = {}
LUDO_GAMES: Dict[str, dict] = {}
SNAKES_GAMES: Dict[str, dict] = {}
GO_GAMES: Dict[str, dict] = {}

COLORS = ["🔴", "🟢", "🟡", "🔵"]
LUDO_SAFE = {0, 8, 13, 21, 26, 34, 39, 47}
LUDO_START = [0, 13, 26, 39]
LUDO_FINISH = 57

GO_SIZE = 9  # تخته‌ی ۹×۹ (سایز استاندارد برای بازی سریع/مبتدی)، مناسب کیبورد اینلاین
GO_KOMI = 5.5  # امتیاز جبرانی سفید برای اینکه سیاه اول بازی می‌کنه
GO_STAR_POINTS = {20, 24, 40, 56, 60}  # نقطه‌های ستاره‌ای (هوشی) رو تخته‌ی ۹×۹: (2,2)(2,6)(4,4)(6,2)(6,6)

SNAKES = {99: 54, 95: 75, 92: 88, 89: 68, 74: 53, 64: 60, 62: 19, 49: 11, 47: 26, 16: 6}
LADDERS = {2: 38, 7: 14, 8: 31, 15: 26, 21: 42, 28: 84, 36: 44, 51: 67, 71: 91, 78: 98}

# ------------------------- shared small helpers -------------------------

async def _safe_edit(bot, chat_id, message_id, text, reply_markup=None):
    """Edit a message and swallow the (harmless) 'message not modified' /
    already-deleted errors so a stray edit never crashes the whole bot."""
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup
        )
    except Exception as e:
        log.info(f"board_games: edit failed (harmless): {e}")


def _cancel_job(app, name):
    if not getattr(app, "job_queue", None):
        return
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()


def _save_game_record(chat_id, winner_id, loser_id):
    """رکورد برد/باخت رو تو سیستم امتیازی مشترک ربات (bot.py) ثبت می‌کنه —
    همون سیستمی که بقیه‌ی بازی‌ها (games.py) هم استفاده می‌کنن، نه یک سیستم جدا.
    ایمپورت داخل تابع نگه داشته شده تا با ایمپورت bot.py از board_games.py
    توی بالای فایل، سیکل ایجاد نشه."""
    try:
        import bot as _bot
        _bot._record_game_result(chat_id, winner_id, loser_id)
    except Exception as e:
        log.info(f"board_games: could not save game record (harmless): {e}")


def _gid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def _name(user) -> str:
    return user.first_name or user.username or "بازیکن"


def _join_markup(prefix: str, gid: str, count: int = 4):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ پیوستن", callback_data=f"{prefix}:join:{gid}")],
        [InlineKeyboardButton("🚀 شروع بازی", callback_data=f"{prefix}:start:{gid}"),
         InlineKeyboardButton("❌ لغو", callback_data=f"{prefix}:cancel:{gid}")],
    ])


# ============================== CHESS ==============================

def _chess_board_text(board, selected=None):
    rows = ["♟️ شطرنج گاتهام", ""]
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            symbol = piece.unicode_symbol() if piece else "·"
            if selected == sq:
                symbol = f"【{symbol}】"
            cells.append(symbol)
        rows.append(f"{rank+1}  " + " ".join(cells))
    rows.append("    a  b  c  d  e  f  g  h")
    rows.append("")
    rows.append("با زدن خانهٔ مبدأ و سپس مقصد، حرکت کن.")
    return "\n".join(rows)


def _chess_markup(gid, board, selected=None):
    game = CHESS_GAMES[gid]
    rows = []
    for rank in range(7, -1, -1):
        row = []
        for file in range(8):
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            label = piece.unicode_symbol() if piece else "·"
            if selected == sq:
                label = "🎯" + label
            row.append(InlineKeyboardButton(label, callback_data=f"chess:sq:{gid}:{sq}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"chess:refresh:{gid}"),
                 InlineKeyboardButton("🏳️ تسلیم", callback_data=f"chess:resign:{gid}")])
    return InlineKeyboardMarkup(rows)


async def chess_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    gid = _gid("ch")
    user = update.effective_user
    CHESS_GAMES[gid] = {
        "chat_id": chat_id, "players": [user.id], "names": {user.id: _name(user)},
        "board": chess.Board() if chess else None, "selected": None, "started": False,
    }
    text = "♟️ شطرنج گاتهام\n\n" f"👤 سفید: {_name(user)}\n" "👤 سیاه: منتظر حریف...\n\n" "نفر دوم روی «پیوستن» بزند."
    await update.effective_message.reply_text(text, reply_markup=_join_markup("chess", gid, 2))


async def _chess_callback(update, context):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    game = CHESS_GAMES.get(gid)
    if not game:
        await q.answer("این بازی دیگر وجود ندارد.", show_alert=True); return
    uid = update.effective_user.id
    if action == "join":
        if uid in game["players"]:
            await q.answer("تو همین الان داخل بازی هستی.", show_alert=True); return
        if len(game["players"]) >= 2:
            await q.answer("این شطرنج پر شده.", show_alert=True); return
        game["players"].append(uid); game["names"][uid] = _name(update.effective_user)
        await q.edit_message_text(
            f"♟️ شطرنج گاتهام\n\n👤 سفید: {game['names'][game['players'][0]]}\n👤 سیاه: {game['names'][uid]}\n\nآماده‌اید؟",
            reply_markup=_join_markup("chess", gid, 2),
        ); return
    if action == "start":
        if uid != game["players"][0] or len(game["players"]) != 2:
            await q.answer("فقط سازنده و وقتی دو نفر حاضرند می‌تواند شروع کند.", show_alert=True); return
        game["started"] = True
        await _chess_render(q, gid); return
    if action == "cancel":
        if uid != game["players"][0]:
            await q.answer("فقط سازنده می‌تواند لغو کند.", show_alert=True); return
        del CHESS_GAMES[gid]
        await q.edit_message_text("♟️ بازی شطرنج لغو شد."); return
    if action == "refresh":
        await _chess_render(q, gid); return
    if action == "resign":
        if uid not in game["players"] or not game["started"]:
            return
        winner = game["players"][1] if uid == game["players"][0] else game["players"][0]
        await q.edit_message_text(f"🏳️ {_name(update.effective_user)} تسلیم شد!\n🏆 برنده: {game['names'][winner]}")
        del CHESS_GAMES[gid]; return
    if action == "sq":
        sq = int(parts[3])
        if not game["started"] or uid not in game["players"]:
            await q.answer("این بازی برای تو نیست.", show_alert=True); return
        board = game["board"]
        color_uid = game["players"][0] if board.turn == chess.WHITE else game["players"][1]
        if uid != color_uid:
            await q.answer("الان نوبت حریفه.", show_alert=True); return
        selected = game.get("selected")
        if selected is None:
            piece = board.piece_at(sq)
            if not piece or piece.color != board.turn:
                await q.answer("اول مهره‌ی خودت رو انتخاب کن.", show_alert=True); return
            game["selected"] = sq
            await _chess_render(q, gid); return
        move = chess.Move(selected, sq)
        if move not in board.legal_moves:
            # promotion shortcut: if pawn reaches last rank, auto-queen
            piece = board.piece_at(selected)
            if piece and piece.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
                move = chess.Move(selected, sq, promotion=chess.QUEEN)
        if move not in board.legal_moves:
            if board.piece_at(sq) and board.piece_at(sq).color == board.turn:
                game["selected"] = sq
                await _chess_render(q, gid)
            else:
                game["selected"] = None
                await q.answer("این حرکت قانونی نیست.", show_alert=True)
            return
        board.push(move); game["selected"] = None
        if board.is_checkmate():
            winner = game["names"][uid]
            await q.edit_message_text(f"♟️ کیش‌ومات!\n\n🏆 برنده: {winner}\n🎉 بازی تمام شد.")
            del CHESS_GAMES[gid]; return
        if board.is_stalemate() or board.is_insufficient_material() or board.is_fifty_moves():
            await q.edit_message_text("🤝 بازی مساوی شد!\nموقعیت دیگر ادامه‌ی برد ندارد.")
            del CHESS_GAMES[gid]; return
        await _chess_render(q, gid)


async def _chess_render(q, gid):
    game = CHESS_GAMES[gid]; board = game["board"]
    turn_uid = game["players"][0] if board.turn == chess.WHITE else game["players"][1]
    turn_name = game["names"][turn_uid]
    text = _chess_board_text(board, game.get("selected")) + f"\n\n🔔 نوبت: {turn_name}"
    if board.is_check(): text += "\n⚠️ کیش!"
    await q.edit_message_text(text, reply_markup=_chess_markup(gid, board, game.get("selected")))


# ============================== LUDO ==============================
# 🎲 منچ — نسخه‌ی دونفره‌ی حرفه‌ای، Dark Gotham:
#   - تاس واقعی، ۴ مهره‌ی اختصاصی هر بازیکن، خوردن مهره‌ی حریف، خانه‌ی امن
#   - نوار پیشرفت گرافیکی برای هر مهره + تایمر نوبت + Timeout واقعی
#   - جلوگیری از حرکت غیرقانونی، بازی مجدد، خروج از بازی

LUDO_TURN_TIMEOUT_SEC = 30
LUDO_JOIN_TIMEOUT_SEC = 90
LUDO_BAR_WIDTH = 12


def _ludo_turn_job_name(gid):
    return f"ludo_turn:{gid}"


def _ludo_join_job_name(gid):
    return f"ludo_join:{gid}"


def _ludo_abs(player_index, progress):
    if progress < 0 or progress >= 52: return None
    return (LUDO_START[player_index] + progress) % 52


def _ludo_piece_bar(p):
    if p < 0:
        return "🏠" + "░" * LUDO_BAR_WIDTH
    if p >= LUDO_FINISH:
        return "🏆" + "█" * LUDO_BAR_WIDTH
    filled = max(1, round((p / LUDO_FINISH) * LUDO_BAR_WIDTH))
    return "🚩" + "█" * filled + "░" * (LUDO_BAR_WIDTH - filled)


def _ludo_text(game, note=None, timer_left=None):
    lines = ["🌑 🎲 منچ — GOTHAM BOARD", ""]
    for i, uid in enumerate(game["players"]):
        marker = "👉 " if game.get("started") and game["players"][game["turn"]] == uid else "   "
        lines.append(f"{marker}{COLORS[i]} {game['names'][uid]}")
        for j, p in enumerate(game["pieces"][uid]):
            lines.append(f"   مهره {j+1}: {_ludo_piece_bar(p)}")
    if note:
        lines.append("")
        lines.append(note)
    if game.get("started"):
        uid = game["players"][game["turn"]]
        lines.append("")
        lines.append(f"🎯 نوبت: {game['names'][uid]}")
        lines.append(f"🎲 تاس: {game['roll'] if game['roll'] else '—'}")
        if timer_left is not None:
            lines.append(f"⏳ زمان باقی‌مانده‌ی نوبت: {timer_left} ثانیه")
    return "\n".join(lines)


def _ludo_join_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ پیوستن به بازی", callback_data=f"ludo:join:{gid}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"ludo:cancel:{gid}")],
    ])


def _ludo_markup(gid, game):
    uid = game["players"][game["turn"]]
    rows = []
    if game["roll"] is None:
        rows.append([InlineKeyboardButton("🎲 انداختن تاس", callback_data=f"ludo:roll:{gid}")])
    else:
        movable = [i for i in range(4) if _ludo_can_move(game, uid, i, game["roll"])]
        rows.append([InlineKeyboardButton(f"مهره {i+1}", callback_data=f"ludo:move:{gid}:{i}") for i in movable]
                    or [InlineKeyboardButton("⏭️ رد نوبت", callback_data=f"ludo:pass:{gid}")])
    rows.append([InlineKeyboardButton("🏳️ خروج", callback_data=f"ludo:leave:{gid}")])
    return InlineKeyboardMarkup(rows)


def _ludo_end_markup(gid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 بازی مجدد", callback_data=f"ludo:rematch:{gid}"),
        InlineKeyboardButton("🏠 بازگشت", callback_data=f"ludo:home:{gid}"),
    ]])


def _ludo_can_move(game, uid, idx, roll):
    p = game["pieces"][uid][idx]
    if p >= LUDO_FINISH: return False
    if p < 0: return roll == 6
    return p + roll <= LUDO_FINISH


def _ludo_move(game, uid, idx, roll):
    p = game["pieces"][uid][idx]
    new = 0 if p < 0 else p + roll
    game["pieces"][uid][idx] = new
    captured = False
    if 0 <= new < 52:
        absolute = _ludo_abs(game["players"].index(uid), new)
        if absolute not in LUDO_SAFE:
            for other in game["players"]:
                if other == uid: continue
                for j, op in enumerate(game["pieces"][other]):
                    if 0 <= op < 52 and _ludo_abs(game["players"].index(other), op) == absolute:
                        game["pieces"][other][j] = -1
                        captured = True
    return captured


async def ludo_start(update, context):
    uid = update.effective_user.id
    gid = _gid("lu")
    game = {
        "chat_id": update.effective_chat.id, "message_id": None,
        "players": [uid], "names": {uid: _name(update.effective_user)},
        "pieces": {uid: [-1] * 4}, "turn": 0, "roll": None, "started": False, "creator": uid,
    }
    LUDO_GAMES[gid] = game
    sent = await update.effective_message.reply_text(
        "🌑 🎲 منچ گاتهام — دونفره\n\n"
        f"{COLORS[0]} بازیکن ۱: {game['names'][uid]}\n"
        "⏳ منتظر بازیکن دوم...\n\n"
        f"روی «پیوستن» بزن (تا {LUDO_JOIN_TIMEOUT_SEC} ثانیه).",
        reply_markup=_ludo_join_markup(gid),
    )
    game["message_id"] = sent.message_id
    if context.application.job_queue:
        context.application.job_queue.run_once(
            _ludo_join_timeout_job, when=LUDO_JOIN_TIMEOUT_SEC, data={"gid": gid}, name=_ludo_join_job_name(gid)
        )


async def _ludo_join_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = LUDO_GAMES.get(gid)
    if not game or game["started"]:
        return
    del LUDO_GAMES[gid]
    await _safe_edit(context.bot, game["chat_id"], game["message_id"],
                      "⏱️ زمان ورود بازیکن دوم تمام شد.\n🎲 بازی منچ لغو شد.")


def _schedule_ludo_turn_timer(app, gid):
    _cancel_job(app, _ludo_turn_job_name(gid))
    if app.job_queue:
        app.job_queue.run_once(
            _ludo_turn_timeout_job, when=LUDO_TURN_TIMEOUT_SEC, data={"gid": gid}, name=_ludo_turn_job_name(gid)
        )


async def _ludo_turn_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = LUDO_GAMES.get(gid)
    if not game or not game["started"]:
        return
    uid = game["players"][game["turn"]]
    await _ludo_auto_play_turn(context.application, context.bot, gid, game, uid)


async def _ludo_auto_play_turn(app, bot, gid, game, uid):
    """اجرای خودکار نوبت وقتی بازیکن به‌موقع اقدام نکرده (تاس و/یا حرکت)."""
    note = "⏱️ زمان تمام شد — ربات به‌جای شما بازی کرد.\n\n"
    if game["roll"] is None:
        roll = random.randint(1, 6)
        game["roll"] = roll
        note += f"🎲 تاس: {roll}"
    else:
        roll = game["roll"]
    movable = [i for i in range(4) if _ludo_can_move(game, uid, i, roll)]
    if movable:
        await _ludo_finish_move(app, bot, gid, game, uid, movable[0], roll, note)
    else:
        game["roll"] = None
        game["turn"] = (game["turn"] + (0 if roll == 6 else 1)) % len(game["players"])
        _schedule_ludo_turn_timer(app, gid)
        await _safe_edit(bot, game["chat_id"], game["message_id"],
                          _ludo_text(game, note=note + "\n😅 حرکتی نبود؛ نوبت رد شد.", timer_left=LUDO_TURN_TIMEOUT_SEC),
                          _ludo_markup(gid, game))


async def _ludo_finish_move(app, bot, gid, game, uid, idx, roll, note=None):
    captured = _ludo_move(game, uid, idx, roll)
    won = all(x >= LUDO_FINISH for x in game["pieces"][uid])
    extra_turn = roll == 6
    game["roll"] = None
    if won:
        _cancel_job(app, _ludo_turn_job_name(gid))
        game["started"] = False
        loser = next((x for x in game["players"] if x != uid), None)
        if loser:
            _save_game_record(game["chat_id"], uid, loser)
        text = _ludo_text(game) + f"\n\n🏆 برنده: {game['names'][uid]}!\n🔥 هر چهار مهره به خانه رسیدند."
        await _safe_edit(bot, game["chat_id"], game["message_id"], text, _ludo_end_markup(gid))
        return
    if captured and note is None:
        note = "💥 مهره‌ی حریف خورده شد!"
    if not extra_turn:
        game["turn"] = (game["turn"] + 1) % len(game["players"])
    _schedule_ludo_turn_timer(app, gid)
    await _safe_edit(bot, game["chat_id"], game["message_id"],
                      _ludo_text(game, note=note, timer_left=LUDO_TURN_TIMEOUT_SEC), _ludo_markup(gid, game))


async def _ludo_callback(update, context):
    q = update.callback_query
    p = q.data.split(":")
    action, gid = p[1], p[2]
    game = LUDO_GAMES.get(gid)
    uid = update.effective_user.id
    try:
        if not game:
            await q.answer("این بازی دیگر در دسترس نیست.", show_alert=True)
            return

        if action == "join":
            if game["started"]:
                await q.answer("این بازی قبلاً شروع شده.", show_alert=True); return
            if uid == game["creator"]:
                await q.answer("نمی‌تونی به بازی خودت بپیوندی؛ منتظر حریف بمان.", show_alert=True); return
            if uid in game["players"]:
                await q.answer("قبلاً وارد شدی.", show_alert=True); return
            game["players"].append(uid); game["names"][uid] = _name(update.effective_user); game["pieces"][uid] = [-1] * 4
            game["started"] = True
            _cancel_job(context.application, _ludo_join_job_name(gid))
            _schedule_ludo_turn_timer(context.application, gid)
            await q.answer("وارد بازی شدی! 🎲")
            await q.edit_message_text(_ludo_text(game, timer_left=LUDO_TURN_TIMEOUT_SEC), reply_markup=_ludo_markup(gid, game))
            return

        if action == "cancel":
            if uid != game["creator"]:
                await q.answer("فقط سازنده می‌تواند لغو کند.", show_alert=True); return
            _cancel_job(context.application, _ludo_join_job_name(gid))
            _cancel_job(context.application, _ludo_turn_job_name(gid))
            del LUDO_GAMES[gid]
            await q.edit_message_text("🎲 منچ لغو شد.")
            return

        if not game["started"]:
            await q.answer("بازی هنوز شروع نشده.", show_alert=True); return
        if uid not in game["players"]:
            await q.answer("این بازی برای تو نیست.", show_alert=True); return

        if action == "leave":
            _cancel_job(context.application, _ludo_turn_job_name(gid))
            opponent = next((x for x in game["players"] if x != uid), None)
            game["started"] = False
            if opponent:
                text = f"🏳️ {game['names'][uid]} از بازی خارج شد.\n🏆 برنده: {game['names'][opponent]}"
                _save_game_record(game["chat_id"], opponent, uid)
            else:
                text = "🎲 بازی منچ پایان یافت."
            del LUDO_GAMES[gid]
            await q.edit_message_text(text)
            return

        if action == "rematch":
            if uid not in game["players"]:
                await q.answer("این بازی برای تو نیست.", show_alert=True); return
            new_gid = _gid("lu")
            new_game = {
                "chat_id": game["chat_id"], "message_id": game["message_id"],
                "players": list(game["players"]), "names": dict(game["names"]),
                "pieces": {pl: [-1] * 4 for pl in game["players"]}, "turn": 0,
                "roll": None, "started": True, "creator": game["creator"],
            }
            LUDO_GAMES[new_gid] = new_game
            LUDO_GAMES.pop(gid, None)
            _schedule_ludo_turn_timer(context.application, new_gid)
            await q.answer("دور جدید شروع شد!")
            await q.edit_message_text(_ludo_text(new_game, timer_left=LUDO_TURN_TIMEOUT_SEC), reply_markup=_ludo_markup(new_gid, new_game))
            return

        if action == "home":
            LUDO_GAMES.pop(gid, None)
            await q.edit_message_text("🏠 از بازی منچ خارج شدی.")
            return

        # از این‌جا به بعد فقط نوبت بازیکن فعلی می‌تواند عمل کند
        if uid != game["players"][game["turn"]]:
            await q.answer("نوبت تو نیست.", show_alert=True); return

        _cancel_job(context.application, _ludo_turn_job_name(gid))

        if action == "roll":
            if game["roll"] is not None:
                await q.answer("اول مهره رو حرکت بده.", show_alert=True)
                _schedule_ludo_turn_timer(context.application, gid); return
            roll = random.randint(1, 6); game["roll"] = roll
            await q.answer(f"🎲 {roll}")
            movable = [i for i in range(4) if _ludo_can_move(game, uid, i, roll)]
            if not movable:
                game["roll"] = None
                game["turn"] = (game["turn"] + (0 if roll == 6 else 1)) % len(game["players"])
                _schedule_ludo_turn_timer(context.application, gid)
                await q.edit_message_text(_ludo_text(game, note=f"😅 تاس {roll} آمد؛ حرکتی نداری.", timer_left=LUDO_TURN_TIMEOUT_SEC), reply_markup=_ludo_markup(gid, game))
                return
            _schedule_ludo_turn_timer(context.application, gid)
            await q.edit_message_text(_ludo_text(game, note=f"🎲 عدد تاس: {roll} — مهره‌ات را انتخاب کن.", timer_left=LUDO_TURN_TIMEOUT_SEC), reply_markup=_ludo_markup(gid, game))
            return

        if action == "move":
            idx = int(p[3]); roll = game["roll"]
            if roll is None or not _ludo_can_move(game, uid, idx, roll):
                await q.answer("این حرکت مجاز نیست.", show_alert=True)
                _schedule_ludo_turn_timer(context.application, gid); return
            await q.answer()
            await _ludo_finish_move(context.application, context.bot, gid, game, uid, idx, roll)
            return

        if action == "pass":
            game["roll"] = None
            game["turn"] = (game["turn"] + 1) % len(game["players"])
            _schedule_ludo_turn_timer(context.application, gid)
            await q.edit_message_text(_ludo_text(game, timer_left=LUDO_TURN_TIMEOUT_SEC), reply_markup=_ludo_markup(gid, game))
            return

        _schedule_ludo_turn_timer(context.application, gid)
        await q.answer()
    except Exception as e:
        log.warning(f"ludo_callback error: {e}")
        try:
            await q.answer("⚠️ یک مشکل موقت پیش آمد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass


# ============================== GO (باستان‌شناسی: دو نفره، تخته‌ی ۹×۹) ==============================
# قوانین پیاده‌شده: گذاشتن سنگ، گرفتن گروه‌های بدون نفَس (liberties)، ممنوعیت
# خودکشی (suicide) مگر این‌که خودش باعث گرفتن سنگ حریف بشه، قانون کوی ساده
# (simple ko) برای جلوگیری از تکرار فوری یک وضعیت، پاس، و امتیازدهی نهایی به
# روش مساحت (area scoring): سنگ‌های روی تخته + قلمرو خالی محاصره‌شده + کومی.

def _go_idx(r, c):
    return r * GO_SIZE + c


def _go_neighbors(idx):
    r, c = divmod(idx, GO_SIZE)
    out = []
    if r > 0:
        out.append(idx - GO_SIZE)
    if r < GO_SIZE - 1:
        out.append(idx + GO_SIZE)
    if c > 0:
        out.append(idx - 1)
    if c < GO_SIZE - 1:
        out.append(idx + 1)
    return out


def _go_group(board, idx):
    """گروه هم‌رنگِ متصل به idx و مجموعه‌ی خانه‌های خالی مجاورش (نفَس‌ها) رو برمی‌گردونه."""
    color = board[idx]
    seen = {idx}
    stack = [idx]
    libs = set()
    while stack:
        cur = stack.pop()
        for n in _go_neighbors(cur):
            if board[n] == 0:
                libs.add(n)
            elif board[n] == color and n not in seen:
                seen.add(n)
                stack.append(n)
    return seen, libs


def _go_try_move(board, idx, color):
    """اگه حرکت مجاز باشه (خونه, (new_board, captured_count) رو برمی‌گردونه؛
    اگه خونه پر باشه یا حرکت خودکشی (suicide) باشه None برمی‌گردونه."""
    if board[idx] != 0:
        return None
    new_board = list(board)
    new_board[idx] = color
    opponent = 3 - color
    captured = 0
    for n in _go_neighbors(idx):
        if new_board[n] == opponent:
            seen, libs = _go_group(new_board, n)
            if not libs:
                for s in seen:
                    new_board[s] = 0
                captured += len(seen)
    seen, libs = _go_group(new_board, idx)
    if not libs:
        return None  # خودکشی و بدون گرفتن سنگ حریف => غیرمجاز
    return new_board, captured


def _go_territory(board):
    visited = set()
    territory = {1: 0, 2: 0}
    for start in range(GO_SIZE * GO_SIZE):
        if board[start] != 0 or start in visited:
            continue
        region = set()
        stack = [start]
        borders = set()
        while stack:
            cur = stack.pop()
            if cur in region:
                continue
            region.add(cur)
            for n in _go_neighbors(cur):
                if board[n] == 0:
                    if n not in region:
                        stack.append(n)
                else:
                    borders.add(board[n])
        visited |= region
        if len(borders) == 1:
            territory[next(iter(borders))] += len(region)
    return territory


def _go_score(board):
    terr = _go_territory(board)
    black = board.count(1) + terr[1]
    white = board.count(2) + terr[2] + GO_KOMI
    return black, white


def _go_cell_symbol(board, idx):
    v = board[idx]
    if v == 1:
        return "⚫"
    if v == 2:
        return "⚪"
    return "✦" if idx in GO_STAR_POINTS else "·"


def _go_board_text(game, note=None):
    board = game["board"]
    rows = ["⚫⚪ گو گاتهام (Go) — تخته ۹×۹", ""]
    for r in range(GO_SIZE - 1, -1, -1):
        cells = [_go_cell_symbol(board, _go_idx(r, c)) for c in range(GO_SIZE)]
        rows.append(f"{r+1:>2} " + " ".join(cells))
    rows.append("    " + " ".join("abcdefghi"))
    rows.append("")
    if game.get("started"):
        to_move = "⚫ سیاه" if game["turn"] == 1 else "⚪ سفید"
        rows.append(f"نوبت: {to_move}")
        rows.append(f"🎯 اسیر: ⚫ سیاه {game['captures'][1]}   ⚪ سفید {game['captures'][2]}")
    if note:
        rows.append("")
        rows.append(note)
    return "\n".join(rows)


def _go_markup(gid, game):
    board = game["board"]
    rows = []
    for r in range(GO_SIZE - 1, -1, -1):
        row = []
        for c in range(GO_SIZE):
            idx = _go_idx(r, c)
            label = _go_cell_symbol(board, idx)
            row.append(InlineKeyboardButton(label, callback_data=f"go:pt:{gid}:{idx}"))
        rows.append(row)
    rows.append([
        InlineKeyboardButton("⏭️ پاس", callback_data=f"go:pass:{gid}"),
        InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"go:refresh:{gid}"),
        InlineKeyboardButton("🏳️ تسلیم", callback_data=f"go:resign:{gid}"),
    ])
    return InlineKeyboardMarkup(rows)


async def go_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    gid = _gid("go")
    user = update.effective_user
    GO_GAMES[gid] = {
        "chat_id": chat_id, "players": [user.id], "names": {user.id: _name(user)},
        "board": [0] * (GO_SIZE * GO_SIZE), "turn": 1, "started": False,
        "captures": {1: 0, 2: 0}, "pass_count": 0, "ko_block": None,
    }
    text = (
        "⚫⚪ گو گاتهام (Go) — تخته ۹×۹\n\n"
        f"👤 سیاه: {_name(user)}\n"
        "👤 سفید: منتظر حریف...\n\n"
        "نفر دوم روی «پیوستن» بزند."
    )
    await update.effective_message.reply_text(text, reply_markup=_join_markup("go", gid, 2))


async def _go_render(q, gid, note=None):
    game = GO_GAMES[gid]
    await q.edit_message_text(_go_board_text(game, note=note), reply_markup=_go_markup(gid, game))


async def _go_finish_game(q, gid):
    game = GO_GAMES[gid]
    black, white = _go_score(game["board"])
    b_uid, w_uid = game["players"][0], game["players"][1]
    if black >= white:
        winner, loser, margin = b_uid, w_uid, black - white
    else:
        winner, loser, margin = w_uid, b_uid, white - black
    _save_game_record(game["chat_id"], winner, loser)
    text = (
        _go_board_text(game) +
        "\n\n🏁 هر دو پاس دادند — بازی تمام شد!\n"
        f"⚫ سیاه: {black:g}    ⚪ سفید: {white:g} (با کومی {GO_KOMI:g})\n"
        f"🏆 برنده: {game['names'][winner]} (اختلاف {margin:g} امتیاز)"
    )
    await q.edit_message_text(text)
    del GO_GAMES[gid]


async def _go_callback(update, context):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    game = GO_GAMES.get(gid)
    if not game:
        await q.answer("این بازی دیگر وجود ندارد.", show_alert=True); return
    uid = update.effective_user.id

    try:
        if action == "join":
            if uid in game["players"]:
                await q.answer("تو همین الان داخل بازی هستی.", show_alert=True); return
            if len(game["players"]) >= 2:
                await q.answer("این بازی گو پر شده.", show_alert=True); return
            game["players"].append(uid); game["names"][uid] = _name(update.effective_user)
            await q.edit_message_text(
                f"⚫⚪ گو گاتهام (Go) — تخته ۹×۹\n\n👤 سیاه: {game['names'][game['players'][0]]}\n"
                f"👤 سفید: {game['names'][uid]}\n\nآماده‌اید؟",
                reply_markup=_join_markup("go", gid, 2),
            ); return

        if action == "start":
            if uid != game["players"][0] or len(game["players"]) != 2:
                await q.answer("فقط سازنده و وقتی دو نفر حاضرند می‌تواند شروع کند.", show_alert=True); return
            game["started"] = True
            await _go_render(q, gid); return

        if action == "cancel":
            if uid != game["players"][0]:
                await q.answer("فقط سازنده می‌تواند لغو کند.", show_alert=True); return
            del GO_GAMES[gid]
            await q.edit_message_text("⚫ بازی گو لغو شد."); return

        if action == "refresh":
            await _go_render(q, gid); return

        if action == "resign":
            if uid not in game["players"] or not game["started"]:
                return
            winner = game["players"][1] if uid == game["players"][0] else game["players"][0]
            _save_game_record(game["chat_id"], winner, uid)
            await q.edit_message_text(f"🏳️ {_name(update.effective_user)} تسلیم شد!\n🏆 برنده: {game['names'][winner]}")
            del GO_GAMES[gid]; return

        if not game["started"] or uid not in game["players"]:
            await q.answer("این بازی برای تو نیست.", show_alert=True); return
        color_uid = game["players"][game["turn"] - 1]
        if uid != color_uid:
            await q.answer("الان نوبت حریفه.", show_alert=True); return

        if action == "pass":
            passer_name = game["names"][uid]
            game["pass_count"] += 1
            game["ko_block"] = None
            if game["pass_count"] >= 2:
                await _go_finish_game(q, gid); return
            game["turn"] = 3 - game["turn"]
            await _go_render(q, gid, note=f"⏭️ {passer_name} پاس داد."); return

        if action == "pt":
            idx = int(parts[3])
            old_board = game["board"]
            result = _go_try_move(old_board, idx, game["turn"])
            if result is None:
                await q.answer("این خونه پره یا این حرکت خودکشیه — مجاز نیست.", show_alert=True); return
            new_board, captured = result
            if game.get("ko_block") is not None and new_board == game["ko_block"]:
                await q.answer("طبق قانون کو (Ko) این حرکت الان مجاز نیست — یه جای دیگه بازی کن.", show_alert=True); return
            game["board"] = new_board
            game["ko_block"] = old_board if captured == 1 else None
            game["captures"][game["turn"]] += captured
            game["pass_count"] = 0
            note = f"💥 {captured} سنگ گرفته شد!" if captured else None
            game["turn"] = 3 - game["turn"]
            await _go_render(q, gid, note=note); return

        await q.answer()
    except Exception as e:
        log.warning(f"go_callback error: {e}")
        try:
            await q.answer("⚠️ یک مشکل موقت پیش آمد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass


# ========================== SNAKES & LADDERS ==========================
# 🐍 مار و پله — نسخه‌ی دونفره‌ی حرفه‌ای، Dark Gotham Board:
#   - تاس واقعی + حرکت مرحله‌به‌مرحله‌ی مهره (انیمیشن خانه به خانه)
#   - صفحه‌ی تصویری متنی ۱۰×۱۰ با مهره‌ی اختصاصی هر بازیکن
#   - تایمر نوبت + Timeout واقعی (اگر بازیکن تاس نیندازد، ربات خودکار می‌اندازد)
#   - جلوگیری از حرکت غیرمجاز، بازی مجدد، خروج از بازی
#   - چند بازی همزمان، هرکدام کاملاً مستقل (Game ID یکتا)

SNAKE_PIECE = ["🟠", "🔵"]  # مهره‌ی اختصاصی بازیکن ۱ و ۲
SNAKE_TURN_TIMEOUT_SEC = 30
SNAKE_JOIN_TIMEOUT_SEC = 90
SNAKE_STEP_DELAY = 0.35  # فاصله‌ی هر گام در انیمیشن حرکت مهره


def _snake_turn_job_name(gid):
    return f"snake_turn:{gid}"


def _snake_join_job_name(gid):
    return f"snake_join:{gid}"


def _snakes_cell_rows():
    """شماره‌ی خانه‌های تخته به ترتیب مارپیچ (Boustrophedon)، از خانه‌ی ۱۰۰ (بالا) تا ۱ (پایین)."""
    rows = []
    for r in range(9, -1, -1):
        row = list(range(r * 10 + 1, r * 10 + 11))
        if r % 2 == 1:
            row.reverse()
        rows.append(row)
    return rows


_SNAKE_ROWS = _snakes_cell_rows()


def _snakes_board_text(game):
    pos_map = {}
    for i, uid in enumerate(game["players"]):
        p = game["pos"][uid]
        if p > 0:
            pos_map.setdefault(p, []).append(SNAKE_PIECE[i])
    lines = []
    for row in _SNAKE_ROWS:
        cells = []
        for n in row:
            if n in pos_map:
                cells.append(pos_map[n][-1])
            elif n == 100:
                cells.append("🏁")
            elif n in SNAKES:
                cells.append("🐍")
            elif n in LADDERS:
                cells.append("🪜")
            else:
                cells.append("▪️")
        lines.append("".join(cells))
    return "\n".join(lines)


def _snakes_text(game, note=None, timer_left=None):
    lines = ["🌑 🐍 مار و پله — GOTHAM BOARD 🪜", ""]
    lines.append(_snakes_board_text(game))
    lines.append("")
    for i, uid in enumerate(game["players"]):
        marker = "👉 " if game.get("started") and game["players"][game["turn"]] == uid else "   "
        lines.append(f"{marker}{SNAKE_PIECE[i]} {game['names'][uid]}: خانه {game['pos'][uid]}")
    if note:
        lines.append("")
        lines.append(note)
    if game.get("started"):
        turn_name = game["names"][game["players"][game["turn"]]]
        lines.append("")
        lines.append(f"🎯 نوبت: {turn_name}")
        if timer_left is not None:
            lines.append(f"⏳ زمان باقی‌مانده‌ی نوبت: {timer_left} ثانیه")
    return "\n".join(lines)


def _snakes_join_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ پیوستن به بازی", callback_data=f"snake:join:{gid}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"snake:cancel:{gid}")],
    ])


def _snakes_play_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 انداختن تاس", callback_data=f"snake:roll:{gid}")],
        [InlineKeyboardButton("🏳️ خروج از بازی", callback_data=f"snake:leave:{gid}")],
    ])


def _snakes_end_markup(gid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 بازی مجدد", callback_data=f"snake:rematch:{gid}"),
        InlineKeyboardButton("🏠 بازگشت", callback_data=f"snake:home:{gid}"),
    ]])


async def snakes_start(update, context):
    uid = update.effective_user.id
    gid = _gid("sn")
    game = {
        "chat_id": update.effective_chat.id, "message_id": None,
        "players": [uid], "names": {uid: _name(update.effective_user)},
        "pos": {uid: 0}, "turn": 0, "started": False, "creator": uid,
    }
    SNAKES_GAMES[gid] = game
    sent = await update.effective_message.reply_text(
        "🌑 🐍 مار و پله گاتهام — دونفره\n\n"
        f"👤 بازیکن ۱: {game['names'][uid]}\n"
        "⏳ منتظر بازیکن دوم...\n\n"
        f"روی «پیوستن» بزن (تا {SNAKE_JOIN_TIMEOUT_SEC} ثانیه).",
        reply_markup=_snakes_join_markup(gid),
    )
    game["message_id"] = sent.message_id
    if context.application.job_queue:
        context.application.job_queue.run_once(
            _snake_join_timeout_job, when=SNAKE_JOIN_TIMEOUT_SEC, data={"gid": gid}, name=_snake_join_job_name(gid)
        )


async def _snake_join_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = SNAKES_GAMES.get(gid)
    if not game or game["started"]:
        return
    del SNAKES_GAMES[gid]
    await _safe_edit(context.bot, game["chat_id"], game["message_id"],
                      "⏱️ زمان ورود بازیکن دوم تمام شد.\n🐍 بازی مار و پله لغو شد.")


def _schedule_snake_turn_timer(app, gid):
    _cancel_job(app, _snake_turn_job_name(gid))
    if app.job_queue:
        app.job_queue.run_once(
            _snake_turn_timeout_job, when=SNAKE_TURN_TIMEOUT_SEC, data={"gid": gid}, name=_snake_turn_job_name(gid)
        )


async def _snake_turn_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = SNAKES_GAMES.get(gid)
    if not game or not game["started"]:
        return
    uid = game["players"][game["turn"]]
    await _snake_do_roll(context.application, context.bot, gid, game, uid, auto=True)


async def _snake_do_roll(app, bot, gid, game, uid, auto=False):
    """منطق مشترک انداختن تاس — هم از دکمه صدا زده می‌شود، هم از Timeout خودکار."""
    idx = game["players"].index(uid)
    roll = random.randint(1, 6)
    old = game["pos"][uid]
    landing = old + roll if old + roll <= 100 else old
    prefix = "⏱️ زمان تمام شد — تاس به‌صورت خودکار انداخته شد.\n\n" if auto else ""

    # حرکت مرحله‌به‌مرحله‌ی مهره، خانه به خانه
    for step in range(1, landing - old + 1):
        game["pos"][uid] = old + step
        await _safe_edit(bot, game["chat_id"], game["message_id"],
                          _snakes_text(game, note=f"{prefix}🎲 {game['names'][uid]} عدد {roll} آورد..." if step == 1 else None))
        await asyncio.sleep(SNAKE_STEP_DELAY)

    final = LADDERS.get(landing, SNAKES.get(landing, landing))
    game["pos"][uid] = final
    if final != landing:
        slide_note = f"🪜 نردبان گرفت! {landing} → {final}" if final > landing else f"🐍 مار قورتش داد! {landing} → {final}"
        await _safe_edit(bot, game["chat_id"], game["message_id"], _snakes_text(game, note=slide_note))
        await asyncio.sleep(SNAKE_STEP_DELAY)

    if final == 100:
        _cancel_job(app, _snake_turn_job_name(gid))
        game["started"] = False
        loser = next((x for x in game["players"] if x != uid), None)
        if loser:
            _save_game_record(game["chat_id"], uid, loser)
        text = (_snakes_text(game) + f"\n\n🏆 برنده: {game['names'][uid]}!\n🐍 مار و پله تمام شد.")
        await _safe_edit(bot, game["chat_id"], game["message_id"], text, _snakes_end_markup(gid))
        return

    game["turn"] = (game["turn"] + 1) % len(game["players"])
    _schedule_snake_turn_timer(app, gid)
    await _safe_edit(bot, game["chat_id"], game["message_id"],
                      _snakes_text(game, timer_left=SNAKE_TURN_TIMEOUT_SEC), _snakes_play_markup(gid))


async def _snake_callback(update, context):
    q = update.callback_query
    p = q.data.split(":")
    action, gid = p[1], p[2]
    game = SNAKES_GAMES.get(gid)
    uid = update.effective_user.id
    try:
        if not game:
            await q.answer("این بازی دیگر در دسترس نیست.", show_alert=True)
            return

        if action == "join":
            if game["started"]:
                await q.answer("این بازی قبلاً شروع شده.", show_alert=True); return
            if uid == game["creator"]:
                await q.answer("نمی‌تونی به بازی خودت بپیوندی؛ منتظر حریف بمان.", show_alert=True); return
            if uid in game["players"]:
                await q.answer("قبلاً وارد شدی.", show_alert=True); return
            game["players"].append(uid); game["names"][uid] = _name(update.effective_user); game["pos"][uid] = 0
            game["started"] = True
            _cancel_job(context.application, _snake_join_job_name(gid))
            _schedule_snake_turn_timer(context.application, gid)
            await q.answer("وارد بازی شدی! 🎲")
            await q.edit_message_text(_snakes_text(game, timer_left=SNAKE_TURN_TIMEOUT_SEC), reply_markup=_snakes_play_markup(gid))
            return

        if action == "cancel":
            if uid != game["creator"]:
                await q.answer("فقط سازنده می‌تواند لغو کند.", show_alert=True); return
            _cancel_job(context.application, _snake_join_job_name(gid))
            _cancel_job(context.application, _snake_turn_job_name(gid))
            del SNAKES_GAMES[gid]
            await q.edit_message_text("🐍 بازی مار و پله لغو شد.")
            return

        if not game["started"]:
            await q.answer("بازی هنوز شروع نشده.", show_alert=True); return
        if uid not in game["players"]:
            await q.answer("این بازی برای تو نیست.", show_alert=True); return

        if action == "roll":
            if uid != game["players"][game["turn"]]:
                await q.answer("نوبت تو نیست.", show_alert=True); return
            _cancel_job(context.application, _snake_turn_job_name(gid))
            await q.answer("🎲")
            await _snake_do_roll(context.application, context.bot, gid, game, uid, auto=False)
            return

        if action == "leave":
            _cancel_job(context.application, _snake_turn_job_name(gid))
            opponent = next((x for x in game["players"] if x != uid), None)
            game["started"] = False
            if opponent:
                text = f"🏳️ {game['names'][uid]} از بازی خارج شد.\n🏆 برنده: {game['names'][opponent]}"
                _save_game_record(game["chat_id"], opponent, uid)
            else:
                text = "🐍 بازی مار و پله پایان یافت."
            del SNAKES_GAMES[gid]
            await q.edit_message_text(text)
            return

        if action == "rematch":
            if uid not in game["players"]:
                await q.answer("این بازی برای تو نیست.", show_alert=True); return
            new_gid = _gid("sn")
            new_game = {
                "chat_id": game["chat_id"], "message_id": game["message_id"],
                "players": list(game["players"]), "names": dict(game["names"]),
                "pos": {p: 0 for p in game["players"]}, "turn": 0,
                "started": True, "creator": game["creator"],
            }
            SNAKES_GAMES[new_gid] = new_game
            SNAKES_GAMES.pop(gid, None)
            _schedule_snake_turn_timer(context.application, new_gid)
            await q.answer("دور جدید شروع شد!")
            await q.edit_message_text(_snakes_text(new_game, timer_left=SNAKE_TURN_TIMEOUT_SEC), reply_markup=_snakes_play_markup(new_gid))
            return

        if action == "home":
            SNAKES_GAMES.pop(gid, None)
            await q.edit_message_text("🏠 از بازی مار و پله خارج شدی.")
            return

        await q.answer()
    except Exception as e:
        log.warning(f"snake_callback error: {e}")
        try:
            await q.answer("⚠️ یک مشکل موقت پیش آمد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass


# ============================== ROUTER ==============================

async def board_game_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text=(update.effective_message.text or "").strip().lower()
    if text in {"شطرنج", "بازی شطرنج", "♟️ شطرنج"}:
        if chess is None:
            await update.effective_message.reply_text("⚠️ موتور شطرنج نصب نشده. requirements.txt را به‌روزرسانی کن.")
        else:
            await chess_start(update, context)
    elif text in {"منچ", "بازی منچ", "🎲 منچ"}:
        await ludo_start(update, context)
    elif text in {"مار و پله", "ماروپله", "بازی مار و پله", "🐍 مار و پله"}:
        await snakes_start(update, context)
    elif text in {"گو", "بازی گو", "go", "بازی go", "⚫ گو"}:
        await go_start(update, context)


def register_board_games(app):
    app.add_handler(CallbackQueryHandler(_chess_callback, pattern=r"^chess:"), group=1)
    app.add_handler(CallbackQueryHandler(_ludo_callback, pattern=r"^ludo:"), group=1)
    app.add_handler(CallbackQueryHandler(_snake_callback, pattern=r"^snake:"), group=1)
    app.add_handler(CallbackQueryHandler(_go_callback, pattern=r"^go:"), group=1)
    # نکته‌ی مهم: این نباید group=1 باشه، چون keyword_router تو games.py هم یه
    # MessageHandler(filters.TEXT & ~filters.COMMAND) با group=1 داره؛ تو یه گروه،
    # فقط اولین هندلری که فیلترش match بشه اجرا می‌شه (فیلتر TEXT خام همیشه match
    # می‌شه)، پس این هندلر هیچ‌وقت اجرا نمی‌شد. باید تو گروه اختصاصی خودش باشه.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, board_game_router), group=11)
