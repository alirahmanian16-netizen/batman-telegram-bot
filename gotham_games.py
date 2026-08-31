# -*- coding: utf-8 -*-
"""
gotham_games.py
================
🎟️ بازی‌های استیکری بتمن — بخش Gotham Multiplayer Games.

این فایل هیچ سیستم موازی جدیدی نمی‌سازه؛ فقط از همون الگوی Lobby/Join که
card_room.py برای «هفت‌خبیث» استفاده می‌کنه (لابی + دکمه‌ی پیوستن + شروع
خودکار) پیروی می‌کنه، با معماری هندلر یکسان (CallbackQueryHandler روی
python-telegram-bot که آپدیت‌ها رو سریال/یکی‌یکی پردازش می‌کنه — یعنی خودِ
Event Loop تضمین می‌کنه بین «چک کردن ظرفیت لابی» و «اضافه کردن بازیکن» هیچ
آپدیت دیگه‌ای وسط نپره، پس نیازی به Lock دستی نیست).

شامل:
    🎲 بازی سریع (Quick Game) — تاس / دارت / بسکتبال / فوتبال / بولینگ / اسلات
        هر ۶‌تا از send_dice واقعیِ تلگرام استفاده می‌کنن (نه ایموجی معمولی).
        Lobby حرفه‌ای: ۲ تا ۴ نفر، تایمر ۹۰ ثانیه‌ای، شروع فوری با نفر چهارم.
        Turn-based با Turn Timeout، امتیازدهی روی ۳ دور، Rank/Tie.
    🃏 نفرین ریدلر (Riddler's Curse) — بازنامِ «هفت‌خبیث»، همون مکانیک اصلی
        (جفت‌کردن و حذف کارت، یک کارت بی‌جفت = نفرین)، ولی روی ۵ مرحله.
    🎟️ سیستم کارت پیروزی/شکست بعد از پایان بازی (چون تو کل پروژه هیچ سیستم
        ارسال Sticker واقعی‌ای وجود نداره، این بخش با هماهنگیِ کاربر به‌صورت
        کارتِ متنیِ گاتهامی پیاده شده — به‌محض این‌که file_id استیکر واقعی
        در دسترس باشه، فقط _send_result_card باید عوض بشه).

هیچ فایل بازیِ قبلی (games.py / card_room.py / ...) دست نخورده؛ بازی‌های
قبلی (هفت‌خبیث اصلی، بیلیارد، دوز گاتهام و ...) دقیقاً مثل قبل کار می‌کنن.
"""

import random
import time
import uuid
import logging
import os

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

log = logging.getLogger(__name__)


def _gid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _name(user) -> str:
    return user.first_name or user.username or "بازیکن"


# 👑 OWNER_ID — همون منطق bot.py (اول از Environment Variable، بعد Fallback)
# تا با چیزی که تو Railway ست شده هماهنگ باشه. برای Owner هیچ قفلی (مثل
# «همزمان فقط یه بازی فعال») اعمال نمی‌شه.
_owner_id_env = os.getenv("OWNER_ID", "").strip()
if _owner_id_env:
    try:
        OWNER_ID = int(_owner_id_env)
    except ValueError:
        OWNER_ID = 5527941204
else:
    OWNER_ID = 5527941204


def _is_owner(uid: int) -> bool:
    return uid == OWNER_ID


# =========================================================
#  ثابت‌ها
# =========================================================

MIN_PLAYERS = 2
MAX_PLAYERS = 4
LOBBY_SECONDS = 90
TURN_SECONDS = 30
ROUNDS_PER_QUICK_GAME = 3
RC_STAGES = 2

# کلید -> (برچسب، ایموجی واقعیِ Telegram Dice)
QUICK_GAMES = {
    "dice":     ("🎲 تاس",       "🎲"),
    "dart":     ("🎯 دارت",      "🎯"),
    "bball":    ("🏀 بسکتبال",   "🏀"),
    "football": ("⚽ فوتبال",    "⚽"),
    "bowling":  ("🎳 بولینگ",    "🎳"),
    "slot":     ("🎰 اسلات",     "🎰"),
}
SLOT_JACKPOT_VALUES = {1, 22, 43, 64}  # مقادیر واقعی Telegram برای ردیف یکسان

MEDALS = ["🥇", "🥈", "🥉", "4️⃣"]

GOLD_LINES = [
    "🟢 امروز ذهن تو از تمام معماهای گاتهام جلوتر بود. 👑",
    "🟢 ریدلر شکست خورد؛ این بار تو معما را حل کردی.",
    "🟢 گاتهام امشب امن‌تر شد — به‌لطف تو. 🦇",
    "🟢 حتی بتمن هم به این بردت افتخار می‌کنه.",
]
RIDDLER_LINES = [
    "🟣 حتی یک معما هم برای شکست دادنت لازم نبود!",
    "🟣 تبریک! تو ساده‌ترین معمای گاتهام بودی.",
    "🟣 ریدلر امشب حتی زحمت کشیدن هم نکشید.",
    "🟣 نفرین ریدلر امشب دقیقاً روی تو نشست. 🃏",
]
DRAW_LINES = [
    "🟡 نه پیروزی، نه شکست — گاتهام امشب مساوی موند.",
]

# =========================================================
#  حافظه‌ی درون‌فرآیندی (Session Registry)
# =========================================================

LOBBIES = {}        # token -> lobby dict
QUICK_STATE = {}     # gid -> quick-game dict
RC_STATE = {}        # gid -> riddler's curse dict
ACTIVE_USERS = set()  # uid هایی که الان تو یه لابی/بازیِ Gotham فعالن


def user_has_active_session(uid: int) -> bool:
    return uid in ACTIVE_USERS


def gotham_status_lines_for_user(uid: int):
    """برای پنل «بازی‌های فعال من» — لیست خط‌های آماده‌ی نمایش، یا [] اگه هیچی نبود."""
    lines = []
    for lobby in LOBBIES.values():
        if uid in lobby["players"] and not lobby["started"] and not lobby["cancelled"]:
            label = "🃏 نفرین ریدلر" if lobby["kind"] == "rc" else QUICK_GAMES[lobby["game_key"]][0]
            lines.append(f"🎟️ {label} — ⏳ منتظر بازیکن (لابی)")
    for game in QUICK_STATE.values():
        if not game["finished"] and uid in game["order"]:
            label = QUICK_GAMES[game["game_key"]][0]
            turn = "📍 نوبت توئه" if game["order"][game["turn_idx"]] == uid else ""
            lines.append(f"🎟️ {label} — دور {game['round']}/{ROUNDS_PER_QUICK_GAME}" + (f" — {turn}" if turn else ""))
    for game in RC_STATE.values():
        if not game["finished"] and uid in game["order"]:
            lines.append(f"🃏 نفرین ریدلر — مرحله {game['stage']}/{RC_STAGES}")
    return lines


def _cleanup_lobby_jobs(lobby):
    for job in lobby.get("jobs", []):
        try:
            job.schedule_removal()
        except Exception:
            pass
    lobby["jobs"] = []


def _release_players(uids):
    for uid in uids:
        ACTIVE_USERS.discard(uid)


# =========================================================
#  🎟️ منوی ریشه‌ی «بازی‌های استیکری بتمن»
# =========================================================

ROOT_TEXT = (
    "🎟️ *بازی‌های استیکری بتمن*\n\n"
    "🦇 Batman  •  🃏 Joker  •  🧩 Riddler  •  👮 Gordon\n\n"
    "یه بخش رو انتخاب کن:"
)


def _root_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 بازی سریع", callback_data="gg:quick")],
        [InlineKeyboardButton("🃏 نفرین ریدلر", callback_data="gg:rc:new")],
        [InlineKeyboardButton("🔙 گیم‌ها", callback_data="panel:games")],
    ])


async def gg_root_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.edit_message_text(ROOT_TEXT, reply_markup=_root_markup(), parse_mode="Markdown")
    await q.answer()


def _quick_menu_markup():
    rows, row = [], []
    for key, (label, _emoji) in QUICK_GAMES.items():
        row.append(InlineKeyboardButton(label, callback_data=f"gg:pick:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="gg:root")])
    return InlineKeyboardMarkup(rows)


QUICK_MENU_TEXT = (
    "🎲 *بازی سریع*\n\n"
    "۶ بازی، همه برای همه‌ی کاربرا آزاده — بدون قفل، بدون VIP.\n"
    "یکی رو انتخاب کن تا Lobby ساخته بشه:"
)


# =========================================================
#  ساخت / نمایش Lobby (مشترک بین Quick و RC)
# =========================================================

def _lobby_title(lobby):
    if lobby["kind"] == "quick":
        label = QUICK_GAMES[lobby["game_key"]][0]
        return f"🦇 ═══ GOTHAM {label.split(' ',1)[1].upper()} ═══\n\n🎮 بازی: {label}"
    return "🦇 ═══ نفرین ریدلر ═══\n\n🃏 بازی: 🃏 نفرین ریدلر"


def _lobby_text(lobby, remaining=LOBBY_SECONDS):
    n = len(lobby["players"])
    lines = [_lobby_title(lobby), "", f"👥 بازیکنان: {n}/{MAX_PLAYERS}", f"⏱️ زمان باقی‌مانده: {remaining}s"]
    if n:
        lines.append("")
        lines.append("👤 بازیکنان:")
        for i, uid in enumerate(lobby["players"], 1):
            lines.append(f"{i}. {lobby['names'][uid]}")
    lines.append("")
    lines.append("منتظر بازیکنان دیگر..." if n < MAX_PLAYERS else "🔥 لابی پر شد!")
    return "\n".join(lines)


def _lobby_markup(lobby):
    token = lobby["token"]
    rows = [[InlineKeyboardButton("🟢 پیوستن", callback_data=f"gg:join:{token}")]]
    if len(lobby["players"]) >= MIN_PLAYERS:
        rows.append([InlineKeyboardButton("🔥 شروع بازی (سازنده)", callback_data=f"gg:start:{token}")])
    rows.append([InlineKeyboardButton("❌ لغو گیم", callback_data=f"gg:cancel:{token}")])
    return InlineKeyboardMarkup(rows)


async def _open_lobby(context, query, creator, kind, game_key=None):
    uid = creator.id
    if user_has_active_session(uid) and not _is_owner(uid):
        await query.answer("⚠️ تو همین الان تو یه گیم/لابیِ دیگه‌ای، اول اونو تموم کن.", show_alert=True)
        return None

    token = _gid("gglobby")
    lobby = {
        "token": token,
        "kind": kind,               # "quick" | "rc"
        "game_key": game_key,
        "chat_id": query.message.chat_id,
        "creator_id": uid,
        "players": [uid],
        "names": {uid: _name(creator)},
        "created_ts": time.time(),
        "message_id": None,
        "started": False,
        "cancelled": False,
        "jobs": [],
    }
    ACTIVE_USERS.add(uid)
    LOBBIES[token] = lobby

    msg = await query.edit_message_text(_lobby_text(lobby), reply_markup=_lobby_markup(lobby))
    lobby["message_id"] = msg.message_id

    # تایمر ۹۰ ثانیه‌ای از همین لحظه‌ی ساخت لابی شروع می‌شه (نه از Join دوم)
    _schedule_lobby_jobs(context, lobby)

    await query.answer("لابی ساخته شد ⬇️")
    return lobby


def _schedule_lobby_jobs(context, lobby):
    token = lobby["token"]
    jq = context.job_queue
    j1 = jq.run_once(_lobby_tick, when=30, data={"token": token, "remaining": 60}, chat_id=lobby["chat_id"])
    j2 = jq.run_once(_lobby_tick, when=60, data={"token": token, "remaining": 30}, chat_id=lobby["chat_id"])
    j3 = jq.run_once(_lobby_expire, when=LOBBY_SECONDS, data={"token": token}, chat_id=lobby["chat_id"])
    lobby["jobs"] = [j1, j2, j3]


async def _lobby_tick(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    lobby = LOBBIES.get(data["token"])
    if not lobby or lobby["started"] or lobby["cancelled"]:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=lobby["chat_id"], message_id=lobby["message_id"],
            text=_lobby_text(lobby, remaining=data["remaining"]),
            reply_markup=_lobby_markup(lobby),
        )
    except Exception:
        pass


async def _lobby_expire(context: ContextTypes.DEFAULT_TYPE):
    token = context.job.data["token"]
    lobby = LOBBIES.get(token)
    if not lobby or lobby["started"] or lobby["cancelled"]:
        return
    if len(lobby["players"]) < MIN_PLAYERS:
        lobby["cancelled"] = True
        _release_players(lobby["players"])
        text = _lobby_title(lobby) + "\n\n👥 بازیکنان: 1/4\n⏱️ زمان: 00:00\n\n❌ به‌اندازه‌ی کافی بازیکن جمع نشد؛ لابی لغو شد."
        try:
            await context.bot.edit_message_text(chat_id=lobby["chat_id"], message_id=lobby["message_id"], text=text)
        except Exception:
            pass
        LOBBIES.pop(token, None)
        return
    await _launch_lobby(context, lobby)


async def _launch_lobby(context, lobby):
    lobby["started"] = True
    _cleanup_lobby_jobs(lobby)
    n = len(lobby["players"])
    header = _lobby_title(lobby) + f"\n\n👥 بازیکنان: {n}/4\n⏱️ زمان: 00:00\n\n🔥 بازی شروع شد!"
    try:
        await context.bot.edit_message_text(chat_id=lobby["chat_id"], message_id=lobby["message_id"], text=header)
    except Exception:
        pass
    if lobby["kind"] == "quick":
        await _start_quick_game(context, lobby)
    else:
        await _start_rc_game(context, lobby)
    LOBBIES.pop(lobby["token"], None)


async def gg_join(update, context, token):
    q = update.callback_query
    lobby = LOBBIES.get(token)
    if not lobby or lobby["cancelled"]:
        await q.answer("این لابی دیگه وجود نداره.", show_alert=True)
        return
    if lobby["started"]:
        await q.answer("⛔ بازی شروع شده؛ دیگه نمی‌شه Join کرد.", show_alert=True)
        return
    uid = q.from_user.id
    if uid in lobby["players"]:
        await q.answer("تو همین الان تو این لابی هستی.", show_alert=True)
        return
    if len(lobby["players"]) >= MAX_PLAYERS:
        await q.answer("⛔ لابی پره (۴ نفر کامله).", show_alert=True)
        return
    if user_has_active_session(uid) and not _is_owner(uid):
        await q.answer("⚠️ تو همین الان تو یه گیم/لابیِ دیگه‌ای، اول اونو تموم کن.", show_alert=True)
        return

    lobby["players"].append(uid)
    lobby["names"][uid] = _name(q.from_user)
    ACTIVE_USERS.add(uid)

    n = len(lobby["players"])
    if n >= MAX_PLAYERS:
        # نفر چهارم: لابی فوراً بسته می‌شه، فقط یه‌بار شروع می‌شه
        await q.answer("پیوستی! لابی پر شد ⚡")
        await _launch_lobby(context, lobby)
        return

    try:
        await q.edit_message_text(_lobby_text(lobby), reply_markup=_lobby_markup(lobby))
    except Exception:
        pass
    await q.answer("پیوستی ✅")


async def gg_cancel(update, context, token):
    q = update.callback_query
    lobby = LOBBIES.get(token)
    if not lobby or lobby["cancelled"] or lobby["started"]:
        await q.answer("این لابی دیگه فعال نیست.", show_alert=True)
        return
    if q.from_user.id != lobby["creator_id"]:
        await q.answer("❌ فقط سازنده گیم می‌تواند آن را لغو کند.", show_alert=True)
        return

    lobby["cancelled"] = True
    _cleanup_lobby_jobs(lobby)
    _release_players(lobby["players"])
    try:
        await q.edit_message_text(_lobby_title(lobby) + "\n\n❌ این گیم توسط سازنده لغو شد.")
    except Exception:
        pass
    LOBBIES.pop(token, None)
    await q.answer("لغو شد.")


async def gg_start(update, context, token):
    """دکمه‌ی «🔥 شروع بازی» — فقط سازنده، فقط وقتی حداقل ۲ نفر جمع شدن؛
    برای شروع زودتر از تایمر ۹۰ ثانیه‌ای یا تکمیل ۴ نفر."""
    q = update.callback_query
    lobby = LOBBIES.get(token)
    if not lobby or lobby["cancelled"] or lobby["started"]:
        await q.answer("این لابی دیگه فعال نیست.", show_alert=True)
        return
    if q.from_user.id != lobby["creator_id"]:
        await q.answer("❌ فقط سازنده گیم می‌تواند بازی رو شروع کنه.", show_alert=True)
        return
    if len(lobby["players"]) < MIN_PLAYERS:
        await q.answer(f"⛔ حداقل {MIN_PLAYERS} نفر لازمه.", show_alert=True)
        return
    await q.answer("🔥 بازی شروع شد!")
    await _launch_lobby(context, lobby)


# =========================================================
#  🎲 بازی سریع — Turn Engine (برد/باخت هر دور، نه جمع امتیاز)
# =========================================================

def _round_points(game_key, value):
    if game_key == "slot":
        return 6 if value in SLOT_JACKPOT_VALUES else 1
    return value


def _history_lines(game):
    lines = ["📊 روند:"]
    for uid in game["order"]:
        marks = "".join(game["history"][uid]) or "—"
        lines.append(f"👤 {game['names'][uid]}: {marks}")
    return lines


def _quick_turn_text(game):
    label = QUICK_GAMES[game["game_key"]][0]
    uid = game["order"][game["turn_idx"]]
    lines = [
        f"🦇 ═══ GOTHAM {label.split(' ',1)[1].upper()} ═══",
        "",
        f"دور {game['round']}/{ROUNDS_PER_QUICK_GAME}",
        "",
    ]
    lines.extend(_history_lines(game))
    lines.append("")
    lines.append(f"🎲 نوبت {game['names'][uid]} است.")
    return "\n".join(lines)


def _quick_turn_markup(game):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎲 پرتاب", callback_data=f"gg:roll:{game['gid']}")]])


async def _start_quick_game(context, lobby):
    gid = _gid("gggame")
    game = {
        "gid": gid,
        "chat_id": lobby["chat_id"],
        "game_key": lobby["game_key"],
        "order": list(lobby["players"]),
        "names": dict(lobby["names"]),
        "round": 1,
        "turn_idx": 0,
        "round_values": {},          # uid -> points، فقط برای دور جاری
        "history": {uid: [] for uid in lobby["players"]},   # uid -> ["🟢","🔴",...]
        "wins": {uid: 0 for uid in lobby["players"]},        # تعداد دورهایی که برده
        "message_id": None,
        "turn_job": None,
        "finished": False,
    }
    QUICK_STATE[gid] = game
    msg = await context.bot.send_message(
        chat_id=lobby["chat_id"], text=_quick_turn_text(game), reply_markup=_quick_turn_markup(game)
    )
    game["message_id"] = msg.message_id
    game["turn_job"] = context.job_queue.run_once(
        _quick_turn_timeout, when=TURN_SECONDS, data={"gid": gid}, chat_id=lobby["chat_id"]
    )


def _finalize_round(game):
    """بعد از این‌که همه تو این دور پرتاب کردن: بیشترین امتیاز = 🟢 (برنده‌ی دور)، بقیه 🔴."""
    values = game["round_values"]
    if not values:
        return
    top = max(values.values())
    for uid in game["order"]:
        v = values.get(uid)
        if v is None:
            continue  # کسی که این دور Timeout شد و اصلاً پرتاب نکرد، دور رو نمی‌بره و نمی‌بازه
        if v == top:
            game["history"][uid].append("🟢")
            game["wins"][uid] += 1
        else:
            game["history"][uid].append("🔴")
    game["round_values"] = {}


async def _advance_quick_turn(context, game):
    game["turn_idx"] += 1
    if game["turn_idx"] >= len(game["order"]):
        _finalize_round(game)
        game["turn_idx"] = 0
        game["round"] += 1
    if game["round"] > ROUNDS_PER_QUICK_GAME:
        await _finish_quick_game(context, game)
        return
    try:
        await context.bot.edit_message_text(
            chat_id=game["chat_id"], message_id=game["message_id"],
            text=_quick_turn_text(game), reply_markup=_quick_turn_markup(game),
        )
    except Exception:
        pass
    game["turn_job"] = context.job_queue.run_once(
        _quick_turn_timeout, when=TURN_SECONDS, data={"gid": game["gid"]}, chat_id=game["chat_id"]
    )


async def gg_roll(update, context, gid):
    q = update.callback_query
    game = QUICK_STATE.get(gid)
    if not game or game["finished"]:
        await q.answer("این بازی دیگه فعال نیست.", show_alert=True)
        return
    uid = q.from_user.id
    current_uid = game["order"][game["turn_idx"]]
    if uid != current_uid:
        await q.answer("⛔ نوبت تو نیست.", show_alert=True)
        return

    if game.get("turn_job"):
        try:
            game["turn_job"].schedule_removal()
        except Exception:
            pass

    emoji = QUICK_GAMES[game["game_key"]][1]
    dice_msg = await context.bot.send_dice(chat_id=game["chat_id"], emoji=emoji)
    value = dice_msg.dice.value
    points = _round_points(game["game_key"], value)
    game["round_values"][uid] = points

    await q.answer(f"🎲 نتیجه: {value}")
    await _advance_quick_turn(context, game)


async def _quick_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = QUICK_STATE.get(gid)
    if not game or game["finished"]:
        return
    uid = game["order"][game["turn_idx"]]
    try:
        await context.bot.send_message(
            chat_id=game["chat_id"], text=f"⏭ {game['names'][uid]} به‌موقع پرتاب نکرد — نوبت رد شد."
        )
    except Exception:
        pass
    await _advance_quick_turn(context, game)


def _rank_scores(scores: dict, names: dict):
    """[(rank, uid, name, score), ...] با پشتیبانی از هم‌امتیازی (Tie)."""
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    result, rank, prev_score = [], 0, None
    for i, (uid, score) in enumerate(ordered):
        if score != prev_score:
            rank = i + 1
            prev_score = score
        result.append((rank, uid, names[uid], score))
    return result


def _result_text(title, ranked, unit="امتیاز"):
    lines = [f"🏆 ═══ {title} ═══", ""]
    top_score = ranked[0][3]
    winners = [r for r in ranked if r[3] == top_score]
    for rank, uid, name, score in ranked:
        medal = MEDALS[rank - 1] if rank - 1 < len(MEDALS) else f"{rank}."
        lines.append(f"{medal} {name} — {score} {unit}")
    lines.append("")
    if len(winners) > 1:
        lines.append("🤝 بازی مساوی شد!")
    else:
        lines.append("🔥 برنده:")
        lines.append(f"👑 {winners[0][2]}")
    return "\n".join(lines), {uid for _, uid, _, _ in winners}


def _post_game_markup(gid, order, names, is_rc):
    rows = [[InlineKeyboardButton(f"🎟️ استیکر {names[uid]}", callback_data=f"gg:card:{gid}:{uid}")]
            for uid in order]
    rows.append([
        InlineKeyboardButton("🏆 نتیجه بازی", callback_data=f"gg:result:{gid}"),
        InlineKeyboardButton("🎮 بازی دوباره", callback_data=f"gg:replay:{gid}"),
    ])
    rows.append([InlineKeyboardButton("🔙 گیم‌ها", callback_data="gg:root")])
    return InlineKeyboardMarkup(rows)


async def _finish_quick_game(context, game):
    game["finished"] = True
    _release_players(game["order"])
    ranked = _rank_scores(game["wins"], game["names"])
    text, winners = _result_text("نتیجه گاتهام", ranked, unit="برد")
    text = "\n".join(_history_lines(game)) + "\n\n" + text
    game["winners"] = winners
    game["result_text"] = text
    try:
        await context.bot.edit_message_text(
            chat_id=game["chat_id"], message_id=game["message_id"], text=text,
            reply_markup=_post_game_markup(game["gid"], game["order"], game["names"], is_rc=False),
        )
    except Exception:
        await context.bot.send_message(
            chat_id=game["chat_id"], text=text,
            reply_markup=_post_game_markup(game["gid"], game["order"], game["names"], is_rc=False),
        )


# =========================================================
#  🃏 نفرین ریدلر — بازنامِ «هفت‌خبیث»، ۵ مرحله
#  (مکانیک: یک کارتِ بی‌جفت = نفرین؛ بازیکن‌ها به‌نوبت از نفر بعدی یه کارت
#  کور می‌کشن، جفت‌های تازه حذف می‌شن، تا فقط یه نفر با کارت نفرین‌شده بمونه)
# =========================================================

RANKS_RC = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS_RC = ["♠", "♥", "♦", "♣"]


def _rc_deck_for(n_players):
    deck = [(r, s) for s in SUITS_RC for r in RANKS_RC]
    random.shuffle(deck)
    deck.pop()  # یه کارت کم می‌شه تا جفتش بی‌جفت بمونه = کارت نفرین‌شده
    return deck


def _rc_remove_pairs(hand):
    counts = {}
    for c in hand:
        counts[c[0]] = counts.get(c[0], 0) + 1
    keep = []
    seen_once = {}
    for c in hand:
        r = c[0]
        if counts[r] % 2 == 0:
            continue  # کل این رنک جفت‌جفته، حذف می‌شه (ساده‌سازی: زوج=حذف کامل)
        keep.append(c)
    return keep


async def _start_rc_game(context, lobby):
    gid = _gid("rcgame")
    game = {
        "gid": gid,
        "chat_id": lobby["chat_id"],
        "order": list(lobby["players"]),
        "names": dict(lobby["names"]),
        "stage": 1,
        "total_scores": {uid: 0 for uid in lobby["players"]},
        "message_id": None,
        "finished": False,
        "turn_job": None,
    }
    RC_STATE[gid] = game
    await _rc_start_stage(context, game)


def _rc_deal_stage(game):
    order = list(game["order"])
    deck = _rc_deck_for(len(order))
    hands = {uid: [] for uid in order}
    for i, card in enumerate(deck):
        hands[order[i % len(order)]].append(card)
    for uid in hands:
        hands[uid] = _rc_remove_pairs(hands[uid])
    game["hands"] = hands
    game["active"] = [uid for uid in order if hands[uid]]  # کسایی که هنوز کارت دارن
    # کسی که از همون اول خالی شد (خیلی نادر ولی ممکنه) اول از همه امن می‌شه
    game["emptied_order"] = [uid for uid in order if not hands[uid]]
    game["turn_idx"] = 0


def _rc_stage_text(game, header="🃏 نفرین ریدلر"):
    n_active = len(game["active"])
    lines = [f"🦇 ═══ {header} ═══", "", f"{['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣'][game['stage']-1]} مرحله {['اول','دوم','سوم','چهارم','پنجم'][game['stage']-1]}", ""]
    for uid in game["order"]:
        n = len(game["hands"].get(uid, []))
        state = f"{n} کارت" if uid in game["active"] else "✅ ایمن شد"
        lines.append(f"👤 {game['names'][uid]}: {state}")
    if n_active > 1:
        cur = game["active"][game["turn_idx"] % n_active]
        lines.append("")
        lines.append(f"🎴 نوبت {game['names'][cur]} برای کشیدن کارت.")
    return "\n".join(lines)


def _rc_stage_markup(game):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎴 کشیدن کارت", callback_data=f"gg:rcdraw:{game['gid']}")]])


async def _rc_start_stage(context, game):
    _rc_deal_stage(game)
    if len(game["active"]) <= 1:
        # همه به‌جز یکی (یا صفر نفر) از اول جفت شدن — این مرحله رو مستقیم ببند
        await _rc_finish_stage(context, game)
        return
    text = _rc_stage_text(game)
    if game["message_id"]:
        try:
            await context.bot.edit_message_text(chat_id=game["chat_id"], message_id=game["message_id"],
                                                  text=text, reply_markup=_rc_stage_markup(game))
        except Exception:
            game["message_id"] = None
    if not game["message_id"]:
        msg = await context.bot.send_message(chat_id=game["chat_id"], text=text, reply_markup=_rc_stage_markup(game))
        game["message_id"] = msg.message_id
    game["turn_job"] = context.job_queue.run_once(
        _rc_turn_timeout, when=TURN_SECONDS, data={"gid": game["gid"]}, chat_id=game["chat_id"]
    )


async def _rc_do_draw(context, game, drawer_uid):
    active = game["active"]
    idx = active.index(drawer_uid)
    target_uid = active[(idx + 1) % len(active)]
    if target_uid == drawer_uid or not game["hands"][target_uid]:
        # فقط یه نفر مونده یا نمی‌شه کشید؛ رد کن
        pass
    else:
        card = game["hands"][target_uid].pop(random.randrange(len(game["hands"][target_uid])))
        game["hands"][drawer_uid].append(card)
        game["hands"][drawer_uid] = _rc_remove_pairs(game["hands"][drawer_uid])
        if not game["hands"][target_uid] and target_uid not in game["emptied_order"]:
            game["emptied_order"].append(target_uid)
            game["active"].remove(target_uid)

    if not game["hands"][drawer_uid] and drawer_uid not in game["emptied_order"]:
        game["emptied_order"].append(drawer_uid)
        if drawer_uid in game["active"]:
            game["active"].remove(drawer_uid)
    else:
        # نوبت به نفر بعدی می‌رسه (اگه خودش خالی نشده باشه، همچنان تو چرخه‌ست)
        if drawer_uid in game["active"]:
            game["turn_idx"] = (game["active"].index(drawer_uid) + 1) % max(len(game["active"]), 1)
        else:
            game["turn_idx"] = 0

    if len(game["active"]) <= 1:
        await _rc_finish_stage(context, game)
        return

    try:
        await context.bot.edit_message_text(
            chat_id=game["chat_id"], message_id=game["message_id"],
            text=_rc_stage_text(game), reply_markup=_rc_stage_markup(game),
        )
    except Exception:
        pass
    game["turn_job"] = context.job_queue.run_once(
        _rc_turn_timeout, when=TURN_SECONDS, data={"gid": game["gid"]}, chat_id=game["chat_id"]
    )


async def gg_rc_draw(update, context, gid):
    q = update.callback_query
    game = RC_STATE.get(gid)
    if not game or game["finished"]:
        await q.answer("این بازی دیگه فعال نیست.", show_alert=True)
        return
    if not game["active"]:
        await q.answer("این مرحله تموم شده.", show_alert=True)
        return
    uid = q.from_user.id
    cur = game["active"][game["turn_idx"] % len(game["active"])]
    if uid != cur:
        await q.answer("⛔ نوبت تو نیست.", show_alert=True)
        return
    if game.get("turn_job"):
        try:
            game["turn_job"].schedule_removal()
        except Exception:
            pass
    await q.answer("🎴 کشیدی!")
    await _rc_do_draw(context, game, uid)


async def _rc_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = RC_STATE.get(gid)
    if not game or game["finished"] or not game["active"]:
        return
    cur = game["active"][game["turn_idx"] % len(game["active"])]
    try:
        await context.bot.send_message(chat_id=game["chat_id"], text=f"⏭ {game['names'][cur]} کارت نکشید — به‌صورت خودکار کشیده شد.")
    except Exception:
        pass
    await _rc_do_draw(context, game, cur)


async def _rc_finish_stage(context, game):
    cursed = game["active"][0] if game["active"] else None
    n = len(game["order"])
    for i, uid in enumerate(game["emptied_order"]):
        game["total_scores"][uid] += (n - 1 - i)
    stage_lines = [f"🦇 نتیجه‌ی مرحله {['اول','دوم','سوم','چهارم','پنجم'][game['stage']-1]}:", ""]
    for i, uid in enumerate(game["emptied_order"], 1):
        stage_lines.append(f"{i}. {game['names'][uid]} ✅")
    if cursed:
        stage_lines.append(f"🃏 {game['names'][cursed]} — نفرین‌شده‌ی این مرحله")

    if game["stage"] >= RC_STAGES:
        await _finish_rc_game(context, game, "\n".join(stage_lines))
        return

    try:
        await context.bot.edit_message_text(
            chat_id=game["chat_id"], message_id=game["message_id"], text="\n".join(stage_lines),
        )
    except Exception:
        pass
    game["stage"] += 1
    game["message_id"] = None
    context.job_queue.run_once(_rc_next_stage_job, when=4, data={"gid": game["gid"]}, chat_id=game["chat_id"])


async def _rc_next_stage_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = RC_STATE.get(gid)
    if not game or game["finished"]:
        return
    await _rc_start_stage(context, game)


async def _finish_rc_game(context, game, last_stage_text):
    game["finished"] = True
    _release_players(game["order"])
    ranked = _rank_scores(game["total_scores"], game["names"])
    result_text, winners = _result_text("نتیجه‌ی نهایی نفرین ریدلر", ranked)
    full_text = last_stage_text + "\n\n" + result_text
    game["winners"] = winners
    game["result_text"] = full_text
    game["order_for_cards"] = game["order"]
    try:
        await context.bot.send_message(
            chat_id=game["chat_id"], text=full_text,
            reply_markup=_post_game_markup(game["gid"], game["order"], game["names"], is_rc=True),
        )
    except Exception:
        pass


# =========================================================
#  🎟️ کارت پیروزی/شکست + دکمه‌های بعد از بازی (مشترک quick/rc)
# =========================================================

def _get_finished_game(gid):
    return QUICK_STATE.get(gid) or RC_STATE.get(gid)


def _card_text(name, outcome):
    if outcome == "win":
        return f"🟢 VICTORY\n👑 {name} برنده شد!\n\n{random.choice(GOLD_LINES)}"
    if outcome == "lose":
        return f"🔴 DEFEAT\n💀 {name} این بار طعمه‌ی نفرین شد.\n\n{random.choice(RIDDLER_LINES)}"
    return f"🟡 DRAW\n🤝 {name} — بازی مساوی شد.\n\n{random.choice(DRAW_LINES)}"


async def gg_card(update, context, gid, uid_str):
    q = update.callback_query
    game = _get_finished_game(gid)
    if not game or not game.get("finished"):
        await q.answer("این بازی هنوز تموم نشده.", show_alert=True)
        return
    target_uid = int(uid_str)
    if q.from_user.id != target_uid:
        await q.answer(f"این دکمه فقط برای {game['names'].get(target_uid,'اون بازیکن')} است.", show_alert=True)
        return
    winners = game.get("winners", set())
    if len(winners) == len(game["order"]):
        outcome = "draw"
    elif target_uid in winners:
        outcome = "win"
    else:
        outcome = "lose"
    text = _card_text(game["names"][target_uid], outcome)
    await context.bot.send_message(chat_id=game["chat_id"], text=text)
    await q.answer("🎟️ کارت ارسال شد!")


async def gg_result(update, context, gid):
    q = update.callback_query
    game = _get_finished_game(gid)
    if not game or not game.get("finished"):
        await q.answer("این بازی هنوز تموم نشده.", show_alert=True)
        return
    await q.answer(game["result_text"][:190], show_alert=True)


async def gg_replay(update, context, gid):
    q = update.callback_query
    game = _get_finished_game(gid)
    if not game or not game.get("finished"):
        await q.answer("این بازی هنوز تموم نشده.", show_alert=True)
        return
    if q.from_user.id not in game["order"]:
        await q.answer("فقط بازیکن‌های همین بازی می‌تونن دور جدید بسازن.", show_alert=True)
        return
    is_rc = gid in RC_STATE
    game_key = game.get("game_key")
    if is_rc:
        RC_STATE.pop(gid, None)
    else:
        QUICK_STATE.pop(gid, None)
    if is_rc:
        await _open_lobby(context, q, q.from_user, kind="rc")
    else:
        await _open_lobby(context, q, q.from_user, kind="quick", game_key=game_key)


# =========================================================
#  روتر Callback واحد: gg:*
# =========================================================

async def gg_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data  # gg:root | gg:quick | gg:pick:<key> | gg:join:<token> | gg:cancel:<token> |
                    # gg:roll:<gid> | gg:rc:new | gg:rcdraw:<gid> | gg:card:<gid>:<uid> |
                    # gg:result:<gid> | gg:replay:<gid>
    parts = data.split(":")
    action = parts[1]

    if action == "root":
        await gg_root_entry(update, context)
        return
    if action == "quick":
        await q.edit_message_text(QUICK_MENU_TEXT, reply_markup=_quick_menu_markup(), parse_mode="Markdown")
        await q.answer()
        return
    if action == "pick":
        key = parts[2]
        if key not in QUICK_GAMES:
            await q.answer("این بازی پیدا نشد.", show_alert=True)
            return
        await _open_lobby(context, q, q.from_user, kind="quick", game_key=key)
        return
    if action == "rc" and len(parts) > 2 and parts[2] == "new":
        await _open_lobby(context, q, q.from_user, kind="rc")
        return
    if action == "join":
        await gg_join(update, context, parts[2])
        return
    if action == "cancel":
        await gg_cancel(update, context, parts[2])
        return
    if action == "start":
        await gg_start(update, context, parts[2])
        return
    if action == "roll":
        await gg_roll(update, context, parts[2])
        return
    if action == "rcdraw":
        await gg_rc_draw(update, context, parts[2])
        return
    if action == "card":
        await gg_card(update, context, parts[2], parts[3])
        return
    if action == "result":
        await gg_result(update, context, parts[2])
        return
    if action == "replay":
        await gg_replay(update, context, parts[2])
        return

    await q.answer()


def register_gotham_games(app):
    app.add_handler(CallbackQueryHandler(gg_router, pattern=r"^gg:"), group=5)
