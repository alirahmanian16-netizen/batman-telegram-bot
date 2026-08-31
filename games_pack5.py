# -*- coding: utf-8 -*-
"""
games_pack5.py
================
۴ بازی جدید، هم‌خانواده با بقیه‌ی فایل‌های games*.py (کلمه‌محرک، بدون /،
دکمه‌های شیشه‌ای).

بازی‌ها:
    ۱. یونو (UNO)      -> کلمه: "یونو"           (۲ تا ۴ نفره، دکمه‌ی پیوستن)
    ۲. قلمرو           -> کلمه: "قلمرو"           (۲ تا ۴ نفره، دکمه‌ی پیوستن)
    ۳. بیلیارد          -> کلمه: "بیلیارد"          (دو نفره، ریپلای یا دکمه‌ی پیوستن)
    ۴. مسابقه ماشین    -> کلمه: "مسابقه ماشین"    (۲ تا ۴ نفره، دکمه‌ی پیوستن)

نکته: چون تلگرام گرافیک/فیزیک بلادرنگ نداره، بیلیارد و مسابقه ماشین نسخه‌ی
نوبتی و ساده‌شده‌ان (نه فیزیک واقعی)، ولی کاملاً قابل‌بازی و بدون باگ‌ان.
یونو الان یه تایمر ۴۵ ثانیه‌ای نوبت هم داره: اگه بازیکن/انتخاب‌کننده‌ی رنگ
دیر کنه، خودکار یه کارت می‌کشه (یا رنگ رندوم انتخاب می‌شه) و نوبت رد می‌شه —
تا بازی گروه رو قفل نکنه. چون پیام‌ها تو گروه عمومیه، دست بازیکن‌ها (مثل
بقیه‌ی بازی‌های این ربات) فقط موقع نوبتشون رو دکمه‌ها دیده می‌شه.

نحوه‌ی اتصال (کنار بقیه‌ی register_ها تو bot.py):

    from games_pack5 import register_extra_games3

    register_extra_games3(app)     # <-- این خط رو اضافه کن
"""

import random
import uuid
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)


EXTRA_GAMES_LIST_TEXT5 = (
    "یونو — ۲ تا ۴ نفره، دکمه‌ی «پیوستن» رو بزن\n"
    "قلمرو — ۲ تا ۴ نفره، استراتژیک: منابع/نیرو/دفاع/حمله/توسعه، ۱۰ دور، بیشترین امتیاز می‌بره\n"
    "بیلیارد — دو نفره، ریپلای کن یا «پیوستن» رو بزن، توپ‌ها رو بزن و آخرش ۸ سیاه\n"
    "مسابقه ماشین — ۲ تا ۴ نفره، تاس بنداز، بوست و لکه‌روغن رو مدیریت کن\n"
)


def _kw5(text: str):
    return filters.Regex(rf"(?i)^\s*{text}\s*$")


def _gid5(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def _name5(user) -> str:
    return user.first_name or user.username or "بازیکن"


def _join_markup5(prefix: str, gid: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ پیوستن", callback_data=f"{prefix}:join:{gid}")],
        [InlineKeyboardButton("🚀 شروع بازی", callback_data=f"{prefix}:beg:{gid}"),
         InlineKeyboardButton("❌ لغو", callback_data=f"{prefix}:cancel:{gid}")],
    ])


async def _noop5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# =========================================================
#  ۱. یونو (UNO) — ۲ تا ۴ نفره
# =========================================================

UNO_GAMES = {}
UNO_COLORS = {"R": "🔴", "G": "🟢", "B": "🔵", "Y": "🟡"}
UNO_LABELS = {"skip": "⏭", "reverse": "🔄", "+2": "+2"}
UNO_TURN_TIMEOUT_SEC = 45


def _uno_build_deck():
    deck = []
    for c in "RGBY":
        deck.append((c, "0"))
        for v in list("123456789") + ["skip", "reverse", "+2"]:
            deck.append((c, v))
            deck.append((c, v))
    for _ in range(4):
        deck.append(("W", "wild"))
        deck.append(("W", "+4"))
    random.shuffle(deck)
    return deck


def _uno_card_label(card):
    c, v = card
    if c == "W":
        return "🃏W" if v == "wild" else "🃏+4"
    return f"{UNO_COLORS[c]}{UNO_LABELS.get(v, v)}"


def _uno_draw(game, n=1):
    cards = []
    for _ in range(n):
        if not game["deck"]:
            top = game["discard"][-1]
            rest = game["discard"][:-1]
            if not rest:
                break
            random.shuffle(rest)
            game["deck"] = rest
            game["discard"] = [top]
        if game["deck"]:
            cards.append(game["deck"].pop())
    return cards


def _uno_advance(game, effect):
    n = len(game["players"])
    if effect == "reverse":
        if n == 2:
            return  # تو دونفره، ریورس مثل اسکیپه: نوبت همون بازیکنه
        game["direction"] *= -1
        game["turn"] = (game["turn"] + game["direction"]) % n
        return
    next_idx = (game["turn"] + game["direction"]) % n
    if effect == "skip":
        game["turn"] = (next_idx + game["direction"]) % n
    elif effect == "draw2":
        game["hands"][game["players"][next_idx]].extend(_uno_draw(game, 2))
        game["turn"] = (next_idx + game["direction"]) % n
    elif effect == "draw4":
        game["hands"][game["players"][next_idx]].extend(_uno_draw(game, 4))
        game["turn"] = (next_idx + game["direction"]) % n
    else:
        game["turn"] = next_idx


def _uno_job_name(gid):
    return f"uno_turn:{gid}"


def _uno_cancel_timer(app, gid):
    for job in app.job_queue.get_jobs_by_name(_uno_job_name(gid)):
        job.schedule_removal()


def _uno_schedule_timer(app, gid, game, chat_id, message_id):
    """تایمر ۴۵ ثانیه‌ای نوبت. با «timer_token» چک می‌کنیم که تایمر قدیمی
    (که قبل از رسیدنش، نوبت با یه اکشن دیگه عوض شده) اثری نداشته باشه، حتی
    اگه schedule_removal به هر دلیلی جا مونده باشه."""
    _uno_cancel_timer(app, gid)
    game["timer_token"] = game.get("timer_token", 0) + 1
    app.job_queue.run_once(
        _uno_turn_timeout,
        when=UNO_TURN_TIMEOUT_SEC,
        data={"gid": gid, "token": game["timer_token"], "chat_id": chat_id, "message_id": message_id},
        name=_uno_job_name(gid),
    )


async def _uno_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    gid = d["gid"]
    game = UNO_GAMES.get(gid)
    if not game or game.get("timer_token") != d["token"]:
        return  # بازی تموم شده یا این تایمر قدیمیه و نوبت عوض شده
    try:
        if game.get("awaiting"):
            uid = game["awaiting"]["uid"]
            name = game["names"][uid]
            color = random.choice("RGBY")
            card = game["awaiting"]["card"]
            game["color"] = color; game["value"] = card[1]
            effect = "draw4" if card[1] == "+4" else "normal"
            game["awaiting"] = None
            _uno_advance(game, effect)
            note = f"\n\n⏱️ {name} تو انتخاب رنگ دیر کرد — رنگ {UNO_COLORS[color]} خودکار انتخاب شد."
        else:
            uid = game["players"][game["turn"]]
            name = game["names"][uid]
            game["hands"][uid].extend(_uno_draw(game, 1))
            _uno_advance(game, "normal")
            note = f"\n\n⏱️ {name} تو نوبتش دیر کرد — یه کارت خودکار کشید و نوبت رد شد."
        _uno_schedule_timer(context.application, gid, game, d["chat_id"], d["message_id"])
        await context.bot.edit_message_text(
            chat_id=d["chat_id"], message_id=d["message_id"],
            text=_uno_text(game) + note, reply_markup=_uno_markup(gid, game),
        )
    except Exception as e:
        log.info(f"uno turn-timeout handling failed (harmless): {e}")


def _uno_text(game):
    lines = ["🃏 یونو گاتهام", ""]
    top = game["discard"][-1]
    lines.append(f"روی زمین: {_uno_card_label(top)}   (رنگ جاری: {UNO_COLORS.get(game['color'], '🃏')})")
    lines.append("")
    for uid in game["players"]:
        marker = "👉 " if game["players"][game["turn"]] == uid else "   "
        lines.append(f"{marker}{game['names'][uid]}: {len(game['hands'][uid])} کارت")
    if game.get("awaiting"):
        lines.append(f"\n🎨 {game['names'][game['awaiting']['uid']]} باید رنگ انتخاب کنه...")
    else:
        lines.append(f"\n🎯 نوبت: {game['names'][game['players'][game['turn']]]}")
    return "\n".join(lines)


def _uno_markup(gid, game):
    if not game["started"]:
        return _join_markup5("uno", gid)
    if game.get("awaiting"):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(UNO_COLORS[c], callback_data=f"uno:color:{gid}:{c}") for c in "RGBY"
        ]])
    uid = game["players"][game["turn"]]
    hand = game["hands"][uid]
    rows, row = [], []
    for i, card in enumerate(hand):
        row.append(InlineKeyboardButton(_uno_card_label(card), callback_data=f"uno:play:{gid}:{i}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🃏 کشیدن کارت", callback_data=f"uno:draw:{gid}")])
    return InlineKeyboardMarkup(rows)


async def uno_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    gid = _gid5("un")
    UNO_GAMES[gid] = {
        "chat_id": update.effective_chat.id, "players": [uid],
        "names": {uid: _name5(update.effective_user)}, "started": False,
        "timer_token": 0,
    }
    await update.effective_message.reply_text(
        f"🃏 یونو گاتهام\n\n👤 {_name5(update.effective_user)}\n۲ تا ۴ نفر می‌تونن وارد بشن.",
        reply_markup=_join_markup5("uno", gid),
    )


async def uno_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    game = UNO_GAMES.get(gid)
    if not game:
        await q.answer("این بازی تمام شده.", show_alert=True)
        return
    uid = update.effective_user.id

    if action == "join":
        if uid in game["players"]:
            await q.answer("قبلاً وارد شدی.", show_alert=True); return
        if game["started"] or len(game["players"]) >= 4:
            await q.answer("ورود ممکن نیست.", show_alert=True); return
        game["players"].append(uid); game["names"][uid] = _name5(update.effective_user)
        names_txt = "\n".join(f"👤 {game['names'][p]}" for p in game["players"])
        await q.edit_message_text(f"🃏 یونو گاتهام\n\n{names_txt}\n\n۲ تا ۴ نفر.", reply_markup=_join_markup5("uno", gid))
        await q.answer(); return

    if action == "beg":
        if uid != game["players"][0] or len(game["players"]) < 2:
            await q.answer("حداقل ۲ نفر لازمه و فقط سازنده شروع می‌کنه.", show_alert=True); return
        deck = _uno_build_deck()
        hands = {p: [deck.pop() for _ in range(7)] for p in game["players"]}
        first = deck.pop()
        color = first[0] if first[0] != "W" else random.choice("RGBY")
        game.update({
            "started": True, "deck": deck, "discard": [first], "hands": hands,
            "color": color, "value": first[1], "turn": 0, "direction": 1, "awaiting": None,
        })
        await q.edit_message_text(_uno_text(game), reply_markup=_uno_markup(gid, game))
        _uno_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
        await q.answer(); return

    if action == "cancel":
        if uid == game["players"][0]:
            _uno_cancel_timer(context.application, gid)
            del UNO_GAMES[gid]
            await q.edit_message_text("🃏 یونو لغو شد.")
        await q.answer(); return

    if not game["started"]:
        await q.answer(); return

    if game.get("awaiting"):
        if action == "color":
            if uid != game["awaiting"]["uid"]:
                await q.answer("نوبت انتخاب رنگ تو نیست.", show_alert=True); return
            color = parts[3]
            card = game["awaiting"]["card"]
            game["color"] = color; game["value"] = card[1]
            effect = "draw4" if card[1] == "+4" else "normal"
            game["awaiting"] = None
            _uno_advance(game, effect)
            await q.edit_message_text(_uno_text(game), reply_markup=_uno_markup(gid, game))
            _uno_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
        await q.answer(); return

    if uid != game["players"][game["turn"]]:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    if action == "play":
        idx = int(parts[3])
        hand = game["hands"][uid]
        if idx < 0 or idx >= len(hand):
            await q.answer("این کارت وجود نداره.", show_alert=True); return
        card = hand[idx]
        valid = card[0] == "W" or card[0] == game["color"] or card[1] == game["value"]
        if not valid:
            await q.answer("این کارت با رنگ/عدد روی زمین جور نیست.", show_alert=True); return
        hand.pop(idx)
        game["discard"].append(card)
        if not hand:
            _uno_cancel_timer(context.application, gid)
            # اتصال به Score موجود — فقط وقتی بازی دقیقاً دونفره‌ست، چون
            # _record_game_result یه برنده/بازنده‌ی تکی می‌خواد و تو یونوی
            # ۳-۴ نفره معلوم نیست «بازنده» کدوم بقیه‌ست؛ حدس نزدم.
            if len(game["players"]) == 2:
                loser_id = [p for p in game["players"] if p != uid][0]
                try:
                    import bot as _bot
                    _bot._record_game_result(game["chat_id"], uid, loser_id)
                except Exception as e:
                    # قبلاً این خطا کاملاً بی‌صدا گم می‌شد — یعنی اگه ثبت امتیاز
                    # شکست می‌خورد، هیچ ردی ازش نمی‌موند و کسی متوجه نمی‌شد چرا
                    # جدول امتیازات با نتیجه‌ی واقعی بازی‌ها نمی‌خونه.
                    log.warning(f"ثبت نتیجه‌ی بازی یونو (uno) شکست خورد: {e}")
            await q.edit_message_text(f"🃏 یونو تمام شد!\n\n🏆 برنده: {game['names'][uid]}\n🎉 کارت‌هاش تموم شد!")
            del UNO_GAMES[gid]; await q.answer(); return
        if card[0] == "W":
            game["awaiting"] = {"uid": uid, "card": card}
            await q.edit_message_text(_uno_text(game), reply_markup=_uno_markup(gid, game))
            _uno_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
            await q.answer(); return
        game["color"] = card[0]; game["value"] = card[1]
        effect = {"skip": "skip", "reverse": "reverse", "+2": "draw2"}.get(card[1], "normal")
        _uno_advance(game, effect)
        await q.edit_message_text(_uno_text(game), reply_markup=_uno_markup(gid, game))
        _uno_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
        await q.answer(); return

    if action == "draw":
        game["hands"][uid].extend(_uno_draw(game, 1))
        _uno_advance(game, "normal")
        await q.edit_message_text(_uno_text(game), reply_markup=_uno_markup(gid, game))
        _uno_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
        await q.answer(); return


# =========================================================
#  ۲. قلمرو (Kingdom) — استراتژیک، ۲ تا ۴ نفره
#     هر بازیکن: قلمرو با منابع/نیرو/دفاع/سطح/امتیاز؛ هر نوبت یکی از ۶ اقدام
#     رو انتخاب می‌کنه: حمله، دفاع، جمع‌آوری منابع، توسعه‌ی قلمرو، ارتقا،
#     یا یه اقدام استراتژیک (کمک به یه هم‌پیمان). بعد از TER_MAX_ROUNDS دور
#     کامل، بازیکنی که بیشترین امتیاز رو داره برنده‌ست.
# =========================================================

TER_GAMES = {}
TER_COLORS = ["🔴", "🟢", "🟡", "🔵"]
TER_MAX_ROUNDS = 10
TER_TURN_TIMEOUT_SEC = 45

TER_ACTIONS = {
    "gather": ("💰 جمع‌آوری منابع", 0),
    "build": ("🏰 توسعه قلمرو", None),   # هزینه‌ش به سطح فعلی بستگی داره
    "upgrade": ("🔬 ارتقا", 25),
    "defend": ("🛡 دفاع", 15),
    "attack": ("⚔️ حمله", 0),
    "alliance": ("🤝 اقدام استراتژیک", 10),
}


def _ter_new_kingdom():
    return {"resources": 40, "power": 10, "defense": 10, "level": 1, "score": 0}


def _ter_score(k):
    return k["level"] * 100 + k["resources"] + k["power"] * 5 + k["defense"] * 2


def _ter_build_cost(level):
    return 30 + level * 15


def _ter_text(game):
    lines = ["🏰 GOTHAM KINGDOM — نبرد قلمروها", ""]
    if not game["started"]:
        for uid in game["players"]:
            lines.append(f"👤 {game['names'][uid]}")
        lines.append("\n۲ تا ۴ نفر می‌تونن وارد بشن.")
        return "\n".join(lines)
    for i, uid in enumerate(game["players"]):
        k = game["kingdoms"][uid]
        marker = "👉 " if game["players"][game["turn"]] == uid else "   "
        lines.append(
            f"{marker}{TER_COLORS[i % 4]} {game['names'][uid]} — سطح {k['level']} | "
            f"💰{k['resources']} ⚔️{k['power']} 🛡{k['defense']} | امتیاز: {_ter_score(k)}"
        )
    lines.append(f"\n🗓 دور {game['round']} از {TER_MAX_ROUNDS}")
    if game.get("log"):
        lines.append(f"\n📜 {game['log']}")
    lines.append(f"\n🎯 نوبت: {game['names'][game['players'][game['turn']]]}")
    return "\n".join(lines)


def _ter_markup(gid, game):
    if not game["started"]:
        return _join_markup5("ter", gid)
    uid = game["players"][game["turn"]]
    level = game["kingdoms"][uid]["level"]
    build_label = f"🏰 توسعه قلمرو (ۀ{_ter_build_cost(level)}💰)"
    rows = [
        [InlineKeyboardButton("⚔️ حمله", callback_data=f"ter:attack:{gid}"),
         InlineKeyboardButton("🛡 دفاع (۱۵💰)", callback_data=f"ter:defend:{gid}")],
        [InlineKeyboardButton("💰 جمع‌آوری منابع", callback_data=f"ter:gather:{gid}")],
        [InlineKeyboardButton(build_label, callback_data=f"ter:build:{gid}")],
        [InlineKeyboardButton("🔬 ارتقا (۲۵💰)", callback_data=f"ter:upgrade:{gid}"),
         InlineKeyboardButton("🤝 استراتژیک (۱۰💰)", callback_data=f"ter:alliance:{gid}")],
        [InlineKeyboardButton("🏳️ خروج", callback_data=f"ter:leave:{gid}")],
    ]
    return InlineKeyboardMarkup(rows)


def _ter_target_markup(gid, game, action):
    uid = game["players"][game["turn"]]
    rows = []
    for p in game["players"]:
        if p == uid:
            continue
        rows.append([InlineKeyboardButton(f"🎯 {game['names'][p]}", callback_data=f"ter:{action}t:{gid}:{p}")])
    rows.append([InlineKeyboardButton("🔙 انصراف", callback_data=f"ter:cancelaction:{gid}")])
    return InlineKeyboardMarkup(rows)


async def territory_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    gid = _gid5("te")
    TER_GAMES[gid] = {
        "chat_id": update.effective_chat.id, "players": [uid],
        "names": {uid: _name5(update.effective_user)}, "turn": 0, "started": False,
        "round": 1, "log": "", "timer_token": 0,
    }
    await update.effective_message.reply_text(
        _ter_text(TER_GAMES[gid]), reply_markup=_join_markup5("ter", gid),
    )


def _ter_job_name(gid):
    return f"ter_turn:{gid}"


def _ter_cancel_timer(app, gid):
    for job in app.job_queue.get_jobs_by_name(_ter_job_name(gid)):
        job.schedule_removal()


def _ter_schedule_timer(app, gid, game, chat_id, message_id):
    _ter_cancel_timer(app, gid)
    game["timer_token"] = game.get("timer_token", 0) + 1
    app.job_queue.run_once(
        _ter_turn_timeout, when=TER_TURN_TIMEOUT_SEC,
        data={"gid": gid, "token": game["timer_token"], "chat_id": chat_id, "message_id": message_id},
        name=_ter_job_name(gid),
    )


async def _ter_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    gid = d["gid"]
    game = TER_GAMES.get(gid)
    if not game or game.get("timer_token") != d["token"]:
        return
    try:
        uid = game["players"][game["turn"]]
        name = game["names"][uid]
        game["log"] = f"⏱️ {name} تو نوبتش دیر کرد و نوبتش رد شد (بدون اقدام)."
        _ter_end_turn(game)
        _ter_schedule_timer(context.application, gid, game, d["chat_id"], d["message_id"])
        finished = _ter_maybe_finish(gid, game)
        if finished:
            await context.bot.edit_message_text(chat_id=d["chat_id"], message_id=d["message_id"], text=finished)
            _ter_cancel_timer(context.application, gid)
            return
        await context.bot.edit_message_text(
            chat_id=d["chat_id"], message_id=d["message_id"],
            text=_ter_text(game), reply_markup=_ter_markup(gid, game),
        )
    except Exception as e:
        log.info(f"territory turn-timeout handling failed (harmless): {e}")


def _ter_end_turn(game):
    """نوبت رو می‌بره جلو؛ اگه یه دور کامل شد، شمارنده‌ی دور رو زیاد می‌کنه."""
    game["turn"] += 1
    if game["turn"] >= len(game["players"]):
        game["turn"] = 0
        game["round"] += 1


def _ter_maybe_finish(gid, game):
    """اگه دورها تموم شده باشه، متن نتیجه‌ی نهایی رو برمی‌گردونه و بازی رو پاک می‌کنه؛
    وگرنه None."""
    if game["round"] <= TER_MAX_ROUNDS:
        return None
    scored = sorted(game["players"], key=lambda p: _ter_score(game["kingdoms"][p]), reverse=True)
    lines = ["🏰 نبرد قلمروها تمام شد!", ""]
    for i, p in enumerate(scored, 1):
        k = game["kingdoms"][p]
        medal = "🏆" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else "▫️"))
        lines.append(f"{medal} {game['names'][p]} — سطح {k['level']} | امتیاز {_ter_score(k)}")
    del TER_GAMES[gid]
    return "\n".join(lines)


async def territory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    game = TER_GAMES.get(gid)
    if not game:
        await q.answer("این بازی تمام شده.", show_alert=True); return
    uid = update.effective_user.id

    if action == "join":
        if uid in game["players"]:
            await q.answer("قبلاً وارد شدی.", show_alert=True); return
        if game["started"] or len(game["players"]) >= 4:
            await q.answer("ورود ممکن نیست.", show_alert=True); return
        game["players"].append(uid); game["names"][uid] = _name5(update.effective_user)
        await q.edit_message_text(_ter_text(game), reply_markup=_join_markup5("ter", gid))
        await q.answer(); return

    if action == "beg":
        if uid != game["players"][0] or len(game["players"]) < 2:
            await q.answer("حداقل ۲ نفر لازمه و فقط سازنده شروع می‌کنه.", show_alert=True); return
        game["started"] = True
        game["kingdoms"] = {p: _ter_new_kingdom() for p in game["players"]}
        await q.edit_message_text(_ter_text(game), reply_markup=_ter_markup(gid, game))
        _ter_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
        await q.answer(); return

    if action == "cancel":
        if uid == game["players"][0]:
            del TER_GAMES[gid]
            await q.edit_message_text("🏰 قلمرو لغو شد.")
        await q.answer(); return

    if not game["started"]:
        await q.answer(); return

    if action == "leave":
        if len(game["players"]) <= 2:
            _ter_cancel_timer(context.application, gid)
            del TER_GAMES[gid]
            await q.edit_message_text("🏰 بازی قلمرو پایان یافت.")
            await q.answer(); return
        if uid in game["players"]:
            game["players"].remove(uid); game["names"].pop(uid, None); game["kingdoms"].pop(uid, None)
            game["turn"] %= len(game["players"])
            await q.edit_message_text(_ter_text(game), reply_markup=_ter_markup(gid, game))
        await q.answer(); return

    if action == "cancelaction":
        if uid != game["players"][game["turn"]]:
            await q.answer(); return
        await q.edit_message_text(_ter_text(game), reply_markup=_ter_markup(gid, game))
        await q.answer(); return

    # اقدام‌های هدف‌دار (حمله / کمک استراتژیک) دو مرحله‌این: اول نفر رو انتخاب کن
    if action in ("attack", "alliance") and uid == game["players"][game["turn"]] and len(parts) == 3:
        if len(game["players"]) == 2:
            target = next(p for p in game["players"] if p != uid)
            action = action + "t"
            parts = [parts[0], action, gid, str(target)]
        else:
            await q.edit_message_text(
                f"{_ter_text(game)}\n\n🎯 هدفت رو انتخاب کن:", reply_markup=_ter_target_markup(gid, game, action)
            )
            await q.answer(); return

    if uid != game["players"][game["turn"]]:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    k = game["kingdoms"][uid]

    if action == "gather":
        gained = random.randint(8, 15) + k["level"] * 2
        k["resources"] += gained
        game["log"] = f"💰 {game['names'][uid]} {gained} منبع جمع کرد."
        _ter_end_turn(game)

    elif action == "build":
        cost = _ter_build_cost(k["level"])
        if k["resources"] < cost:
            await q.answer(f"منابع کافی نیست (نیاز: {cost}💰).", show_alert=True); return
        k["resources"] -= cost
        k["level"] += 1
        k["defense"] += 5
        game["log"] = f"🏰 {game['names'][uid]} قلمروش رو به سطح {k['level']} رسوند."
        _ter_end_turn(game)

    elif action == "upgrade":
        if k["resources"] < 25:
            await q.answer("منابع کافی نیست (نیاز: ۲۵💰).", show_alert=True); return
        k["resources"] -= 25
        k["power"] += 8
        game["log"] = f"🔬 {game['names'][uid]} نیروی نظامیش رو ارتقا داد."
        _ter_end_turn(game)

    elif action == "defend":
        if k["resources"] < 15:
            await q.answer("منابع کافی نیست (نیاز: ۱۵💰).", show_alert=True); return
        k["resources"] -= 15
        k["defense"] += 10
        game["log"] = f"🛡 {game['names'][uid]} دفاع قلمروش رو تقویت کرد."
        _ter_end_turn(game)

    elif action == "attackt":
        target = int(parts[3])
        tk = game["kingdoms"][target]
        if k["power"] > tk["defense"]:
            loot = max(5, int(tk["resources"] * random.uniform(0.2, 0.4)))
            tk["resources"] = max(0, tk["resources"] - loot)
            tk["defense"] = max(0, tk["defense"] - 5)
            k["resources"] += loot
            game["log"] = f"⚔️ {game['names'][uid]} به {game['names'][target]} حمله کرد و برد! (+{loot}💰 غنیمت)"
        else:
            k["power"] = max(0, k["power"] - 5)
            game["log"] = f"⚔️ حمله‌ی {game['names'][uid]} به {game['names'][target]} شکست خورد (دفاع حریف قوی‌تر بود)."
        _ter_end_turn(game)

    elif action == "alliancet":
        target = int(parts[3])
        if k["resources"] < 10:
            await q.answer("منابع کافی نیست (نیاز: ۱۰💰).", show_alert=True); return
        k["resources"] -= 10
        game["kingdoms"][target]["resources"] += 15
        game["log"] = f"🤝 {game['names'][uid]} به {game['names'][target]} کمک استراتژیک کرد (+۱۵💰 براش)."
        _ter_end_turn(game)

    else:
        await q.answer(); return

    _ter_schedule_timer(context.application, gid, game, game["chat_id"], q.message.message_id)
    finished = _ter_maybe_finish(gid, game)
    if finished:
        _ter_cancel_timer(context.application, gid)
        await q.edit_message_text(finished)
        await q.answer(); return
    await q.edit_message_text(_ter_text(game), reply_markup=_ter_markup(gid, game))
    await q.answer()


# =========================================================
#  ۳. بیلیارد — Turn-Based Billiards حرفه‌ای، دونفره
# =========================================================
# چون تلگرام فیزیک واقعی نداره، هر توپ یه «جهت + قدرت درست» مخفی و seed-دار
# داره (ثابت در طول همون بازی). بازیکن جهت (۸ جهته) و قدرت (۴ درجه) ضربه رو
# انتخاب می‌کنه؛ هرچه به ترکیب درست نزدیک‌تر باشه، احتمال ورود توپ بیشتره —
# پس نتیجه منطقیه، نه کاملاً شانسی. بعد از هر توپ درست، نوبت همون بازیکن
# ادامه پیدا می‌کنه (مثل بیلیارد واقعی)؛ خطا نوبت رو به حریف می‌ده.

BIL_GAMES = {}
BIL_LOBBIES = {}
BIL_TURN_TIMEOUT_SEC = 30

BIL_DIRS = ["nw", "n", "ne", "w", "e", "sw", "s", "se"]
BIL_DIR_LABEL = {"nw": "↖️", "n": "⬆️", "ne": "↗️", "w": "⬅️", "e": "➡️", "sw": "↙️", "s": "⬇️", "se": "↘️"}
BIL_DIR_ADJ = {  # جهت‌های همسایه (۴۵ درجه فاصله) برای محاسبه‌ی احتمال
    "nw": {"n", "w"}, "n": {"nw", "ne"}, "ne": {"n", "e"},
    "w": {"nw", "sw"}, "e": {"ne", "se"}, "sw": {"w", "s"},
    "s": {"sw", "se"}, "se": {"s", "e"},
}
BIL_POWER_LABEL = {1: "▂", 2: "▄", 3: "▆", 4: "█"}


def _bil_job_name(gid):
    return f"bil_turn:{gid}"


def _bil_gen_solutions(seed):
    rnd = random.Random(seed)
    return {ball: {"dir": rnd.choice(BIL_DIRS), "power": rnd.randint(1, 4)} for ball in range(1, 9)}


def _bil_lobby_markup(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🙋 بپیوند به بازی", callback_data=f"lobby5:{token}")]])


def _bil_text(state, note=None, timer_left=None):
    balls_left = "، ".join(str(b) for b in sorted(state["remaining"])) if state["remaining"] else "فقط ⚫ ۸"
    lines = [
        "🎱 GOTHAM BILLIARDS", "",
        f"👤 {state['names'][state['players'][0]]}: {state['score'][state['players'][0]]} امتیاز | خطا: {state['fouls'][state['players'][0]]}",
        f"👤 {state['names'][state['players'][1]]}: {state['score'][state['players'][1]]} امتیاز | خطا: {state['fouls'][state['players'][1]]}",
        "", f"🎯 توپ‌های باقی‌مانده: {balls_left}",
    ]
    if note:
        lines += ["", note]
    lines += ["", f"🔔 نوبت: {state['names'][state['turn']]}"]
    if state.get("target") is not None:
        label = "⚫ ۸" if state["target"] == 8 else f"🎱 {state['target']}"
        lines.append(f"🎯 هدف انتخاب‌شده: {label}")
        lines.append(f"↔️ جهت: {BIL_DIR_LABEL[state['direction']]}   💪 قدرت: {BIL_POWER_LABEL[state['power']]}")
    if timer_left is not None:
        lines.append(f"⏳ زمان باقی‌مانده: {timer_left} ثانیه")
    return "\n".join(lines)


def _bil_ball_markup(gid, state):
    balls = sorted(state["remaining"]) if state["remaining"] else [8]
    rows, row = [], []
    for n in balls:
        label = "⚫ ۸" if n == 8 else f"🎱 {n}"
        row.append(InlineKeyboardButton(label, callback_data=f"bil:target:{gid}:{n}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _bil_aim_markup(gid, state):
    power_row = [InlineKeyboardButton(BIL_POWER_LABEL[p] + (" ✓" if p == state["power"] else ""), callback_data=f"bil:power:{gid}:{p}") for p in (1, 2, 3, 4)]
    d = state["direction"]
    grid = [
        ["nw", "n", "ne"],
        ["w", None, "e"],
        ["sw", "s", "se"],
    ]
    dir_rows = []
    for r in grid:
        row = []
        for cell in r:
            if cell is None:
                row.append(InlineKeyboardButton("🎱", callback_data=f"noop5"))
            else:
                label = BIL_DIR_LABEL[cell] + (" ✓" if cell == d else "")
                row.append(InlineKeyboardButton(label, callback_data=f"bil:dir:{gid}:{cell}"))
        dir_rows.append(row)
    return InlineKeyboardMarkup([power_row] + dir_rows + [
        [InlineKeyboardButton("💥 ضربه", callback_data=f"bil:strike:{gid}")],
        [InlineKeyboardButton("🔙 تغییر هدف", callback_data=f"bil:retarget:{gid}"), InlineKeyboardButton("🏳️ خروج", callback_data=f"bil:leave:{gid}")],
    ])


def _bil_end_markup(gid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 بازی مجدد", callback_data=f"bil:rematch:{gid}"),
        InlineKeyboardButton("🏠 بازگشت", callback_data=f"bil:home:{gid}"),
    ]])


async def _launch_billiards(target_msg, p1, p2, edit=False):
    gid = f"{target_msg.chat.id}_{p1.id}_{p2.id}_{random.randint(1000,9999)}"
    state = {
        "chat_id": target_msg.chat.id, "message_id": None,
        "players": [p1.id, p2.id], "names": {p1.id: p1.first_name, p2.id: p2.first_name},
        "score": {p1.id: 0, p2.id: 0}, "fouls": {p1.id: 0, p2.id: 0},
        "remaining": set(range(1, 8)), "turn": p1.id,
        "target": None, "direction": "n", "power": 2,
        "solutions": _bil_gen_solutions(gid),
    }
    BIL_GAMES[gid] = state
    if edit:
        sent = await target_msg.edit_text(_bil_text(state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_ball_markup(gid, state))
        state["message_id"] = target_msg.message_id
    else:
        sent = await target_msg.reply_text(_bil_text(state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_ball_markup(gid, state))
        state["message_id"] = sent.message_id
    return gid, state


async def billiards_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    creator = update.effective_user
    if msg.reply_to_message and msg.reply_to_message.from_user and \
       not msg.reply_to_message.from_user.is_bot and msg.reply_to_message.from_user.id != creator.id:
        gid, state = await _launch_billiards(msg, creator, msg.reply_to_message.from_user)
        _bil_schedule_timer(context.application, gid)
        return
    token = f"{update.effective_chat.id}_{creator.id}_{random.randint(100000, 999999)}"
    BIL_LOBBIES[token] = {"creator": creator, "kind": "bil"}
    await msg.reply_text(
        f"🎮 {creator.first_name} می‌خواد بیلیارد بازی کنه!\nحریف، دکمه‌ی زیر رو بزن تا بازی شروع بشه.",
        reply_markup=_bil_lobby_markup(token),
    )


async def bil_lobby_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    token = query.data.split(":", 1)[1]
    lobby = BIL_LOBBIES.get(token)
    if not lobby:
        await query.answer("این دعوت منقضی شده یا یکی دیگه قبلاً پیوسته.", show_alert=True); return
    creator = lobby["creator"]; joiner = query.from_user
    if joiner.id == creator.id:
        await query.answer("نمی‌تونی با خودت بازی کنی 🙂", show_alert=True); return
    del BIL_LOBBIES[token]
    gid, state = await _launch_billiards(query.message, creator, joiner, edit=True)
    _bil_schedule_timer(context.application, gid)
    await query.answer()


def _bil_schedule_timer(app, gid):
    _cancel_job(app, _bil_job_name(gid))
    if getattr(app, "job_queue", None):
        app.job_queue.run_once(_bil_turn_timeout, when=BIL_TURN_TIMEOUT_SEC, data={"gid": gid}, name=_bil_job_name(gid))


async def _bil_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = BIL_GAMES.get(gid)
    if not state:
        return
    uid = state["turn"]
    if state["target"] is None:
        state["target"] = min(state["remaining"]) if state["remaining"] else 8
    await _bil_do_strike(context.application, context.bot, gid, state, uid, auto=True)


def _bil_save_result(chat_id, winner_id, loser_id):
    try:
        import bot as _bot
        _bot._record_game_result(chat_id, winner_id, loser_id)
    except Exception as e:
        log.info(f"billiards: could not save game record (harmless): {e}")


async def _bil_do_strike(app, bot, gid, state, uid, auto=False):
    other = next(p for p in state["players"] if p != uid)
    ball = state["target"]
    sol = state["solutions"][ball]
    d, pw = state["direction"], state["power"]
    prefix = "⏱️ زمان تمام شد — ربات به‌جای شما ضربه زد.\n\n" if auto else ""

    dir_score = 1.0 if d == sol["dir"] else (0.5 if d in BIL_DIR_ADJ[sol["dir"]] else 0.15)
    pow_diff = abs(pw - sol["power"])
    pow_score = 1.0 if pow_diff == 0 else (0.7 if pow_diff == 1 else 0.35)
    prob = dir_score * pow_score
    hit = random.random() < prob
    scratch = (not hit) and pw == 4 and dir_score < 0.5 and random.random() < 0.3

    state["target"] = None
    label = "⚫ ۸" if ball == 8 else f"🎱 {ball}"

    if ball == 8:
        if hit:
            state["score"][uid] += 3
            _cancel_job(app, _bil_job_name(gid))
            _bil_save_result(state["chat_id"], uid, other)
            text = (_bil_text(state) + f"\n\n{prefix}⚫🎉 {state['names'][uid]} توپ ۸ رو زد و برد!\n\n"
                    f"🏆 برنده: {state['names'][uid]}")
            await _safe_edit(bot, state["chat_id"], state["message_id"], text, _bil_end_markup(gid))
            return
        else:
            note = prefix + f"😅 {state['names'][uid]} به {label} نخورد."
            state["turn"] = other
            _bil_schedule_timer(app, gid)
            await _safe_edit(bot, state["chat_id"], state["message_id"], _bil_text(state, note=note, timer_left=BIL_TURN_TIMEOUT_SEC), _bil_ball_markup(gid, state))
            return

    if scratch:
        state["fouls"][uid] += 1
        note = prefix + f"❌ خطا! {state['names'][uid]} کیوی زد و توپ سفید هم رفت تو."
        state["turn"] = other
        _bil_schedule_timer(app, gid)
        await _safe_edit(bot, state["chat_id"], state["message_id"], _bil_text(state, note=note, timer_left=BIL_TURN_TIMEOUT_SEC), _bil_ball_markup(gid, state))
        return

    if hit:
        state["remaining"].discard(ball)
        state["score"][uid] += 1
        note = prefix + f"✅ {state['names'][uid]} {label} رو زد! ({state['score'][uid]} امتیاز) — نوبتش ادامه داره."
        _bil_schedule_timer(app, gid)
        await _safe_edit(bot, state["chat_id"], state["message_id"], _bil_text(state, note=note, timer_left=BIL_TURN_TIMEOUT_SEC), _bil_ball_markup(gid, state))
        return
    else:
        note = prefix + f"❌ {state['names'][uid]} به {label} نخورد."
        state["turn"] = other
        _bil_schedule_timer(app, gid)
        await _safe_edit(bot, state["chat_id"], state["message_id"], _bil_text(state, note=note, timer_left=BIL_TURN_TIMEOUT_SEC), _bil_ball_markup(gid, state))
        return


async def billiards_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    state = BIL_GAMES.get(gid)
    uid = update.effective_user.id
    try:
        if not state:
            await q.answer("این بازی تموم شده.", show_alert=True); return
        if uid not in state["players"]:
            await q.answer("تو تو این بازی نیستی.", show_alert=True); return

        if action == "leave":
            _cancel_job(context.application, _bil_job_name(gid))
            other = next(p for p in state["players"] if p != uid)
            _bil_save_result(state["chat_id"], other, uid)
            del BIL_GAMES[gid]
            await q.edit_message_text(f"🏳️ {state['names'][uid]} از بازی خارج شد.\n🏆 برنده: {state['names'][other]}")
            await q.answer(); return

        if action == "rematch":
            p1, p2 = state["players"]
            new_gid = f"{state['chat_id']}_{p1}_{p2}_{random.randint(1000,9999)}"
            new_state = {
                "chat_id": state["chat_id"], "message_id": state["message_id"],
                "players": [p1, p2], "names": dict(state["names"]),
                "score": {p1: 0, p2: 0}, "fouls": {p1: 0, p2: 0},
                "remaining": set(range(1, 8)), "turn": p1,
                "target": None, "direction": "n", "power": 2,
                "solutions": _bil_gen_solutions(new_gid),
            }
            BIL_GAMES[new_gid] = new_state
            BIL_GAMES.pop(gid, None)
            _bil_schedule_timer(context.application, new_gid)
            await q.edit_message_text(_bil_text(new_state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_ball_markup(new_gid, new_state))
            await q.answer("بازی جدید شروع شد!"); return

        if action == "home":
            BIL_GAMES.pop(gid, None)
            await q.edit_message_text("🏠 از بازی بیلیارد خارج شدی.")
            await q.answer(); return

        if state["turn"] != uid:
            await q.answer("نوبت تو نیست.", show_alert=True); return

        _cancel_job(context.application, _bil_job_name(gid))

        if action == "target":
            n = int(parts[3])
            if n not in state["remaining"] and not (n == 8 and not state["remaining"]):
                await q.answer("این توپ در دسترس نیست.", show_alert=True)
                _bil_schedule_timer(context.application, gid); return
            state["target"] = n
            _bil_schedule_timer(context.application, gid)
            await q.edit_message_text(_bil_text(state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_aim_markup(gid, state))
            await q.answer(); return

        if action == "retarget":
            state["target"] = None
            _bil_schedule_timer(context.application, gid)
            await q.edit_message_text(_bil_text(state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_ball_markup(gid, state))
            await q.answer(); return

        if state["target"] is None:
            await q.answer("اول یه توپ رو به‌عنوان هدف انتخاب کن.", show_alert=True)
            _bil_schedule_timer(context.application, gid); return

        if action == "power":
            state["power"] = int(parts[3])
            _bil_schedule_timer(context.application, gid)
            await q.edit_message_text(_bil_text(state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_aim_markup(gid, state))
            await q.answer(); return

        if action == "dir":
            state["direction"] = parts[3]
            _bil_schedule_timer(context.application, gid)
            await q.edit_message_text(_bil_text(state, timer_left=BIL_TURN_TIMEOUT_SEC), reply_markup=_bil_aim_markup(gid, state))
            await q.answer(); return

        if action == "strike":
            await q.answer("💥")
            await _bil_do_strike(context.application, context.bot, gid, state, uid, auto=False)
            return

        _bil_schedule_timer(context.application, gid)
        await q.answer()
    except Exception as e:
        log.warning(f"billiards_callback error: {e}")
        try:
            await q.answer("⚠️ یک مشکل موقت پیش آمد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass


# =========================================================
#  ۴. مسابقه ماشین — GOTHAM RACING، دونفره‌ی حرفه‌ای
# =========================================================
# هر بازیکن یه ماشین با ۵ آمار داره: سرعت/شتاب/نیترو/دفاع/سلامت.
# هر نوبت یکی از ۴ اکشن رو انتخاب می‌کنه: حرکت / نیترو / تغییر مسیر / دفاع.
# مسیر دو لاینه (A/B) با موانع، پیچ، مسیر سریع، منطقه‌ی خطرناک و بوست که از
# قبل (seed مخصوص هر بازی) تولید می‌شه؛ نتیجه‌ی هر اکشن منطقی و قابل پیش‌بینیه
# (نه کاملاً شانسی)، چون سرعت/دفاع/نیترو واقعاً روی نتیجه اثر می‌ذارن.

RACE_GAMES = {}
RACE_LEN = 40          # طول مسیر (٪ موقعیت = pos / RACE_LEN * 100)
RACE_TURN_TIMEOUT_SEC = 30
RACE_MAX_NITRO = 3
RACE_ICONS = ["🏎️", "🚗"]

# نوع خونه‌های هر لاین: "obstacle", "danger", "fast", "boost", "turn", None
RACE_CELL_TYPES = ["obstacle", "danger", "fast", "boost", "turn"]


def _race_job_name(gid):
    return f"race_turn:{gid}"


def _race_bar(pct, width=10):
    filled = max(0, min(width, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def _race_gen_track(seed_gid):
    """مسیر دو لاینه‌ی مسابقه رو با یه seed مخصوص همین بازی می‌سازه، تا هر بازی
    مسیر خودش رو داشته باشه ولی در طول یک بازی همیشه ثابت و قابل پیش‌بینی بمونه."""
    rnd = random.Random(seed_gid)
    track = {0: [None, None], 1: [None, None]}
    for lane in (0, 1):
        used = set()
        for _ in range(9):
            cell = rnd.randint(4, RACE_LEN - 2)
            if cell in used:
                continue
            used.add(cell)
            track.setdefault(cell, [None, None])[lane] = rnd.choice(RACE_CELL_TYPES)
    return track


def _race_new_car(name):
    return {
        "name": name, "pos": 0, "lane": 0, "health": 100, "nitro": RACE_MAX_NITRO,
        "speed": 50, "accel": 0, "defense": 20, "shield": False,
    }


def _race_text(game, note=None, timer_left=None):
    lines = ["🏁 GOTHAM RACING", ""]
    for i, uid in enumerate(game["players"]):
        car = game["cars"][uid]
        pct = round(min(car["pos"], RACE_LEN) / RACE_LEN * 100)
        marker = "👉 " if game["started"] and game["players"][game["turn"]] == uid else "   "
        lane_label = "A" if car["lane"] == 0 else "B"
        lines.append(f"{marker}👤 {game['names'][uid]}: {RACE_ICONS[i]}")
        lines.append(f"   🏎 ماشین: {_race_bar(pct)}")
        lines.append(f"   ⚡ نیترو: {'●' * car['nitro']}{'○' * (RACE_MAX_NITRO - car['nitro'])}")
        lines.append(f"   ❤️ سلامت: {car['health']}٪   🛡 دفاع: {car['defense']}٪   🛣 لاین: {lane_label}")
        lines.append(f"   📍 موقعیت: {pct}٪ ({min(car['pos'], RACE_LEN)}/{RACE_LEN})")
        if car["shield"]:
            lines.append("   🛡 سپر فعال")
    if note:
        lines.append("")
        lines.append(note)
    if game["started"]:
        turn_name = game["names"][game["players"][game["turn"]]]
        lines.append("")
        lines.append(f"🎯 نوبت: {turn_name}")
        if timer_left is not None:
            lines.append(f"⏳ زمان باقی‌مانده: {timer_left} ثانیه")
    return "\n".join(lines)


def _race_join_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ پیوستن به مسابقه", callback_data=f"race:join:{gid}")],
        [InlineKeyboardButton("❌ لغو", callback_data=f"race:cancel:{gid}")],
    ])


def _race_play_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏎 حرکت", callback_data=f"race:move:{gid}"),
         InlineKeyboardButton("⚡ نیترو", callback_data=f"race:nitro:{gid}")],
        [InlineKeyboardButton("↔️ تغییر مسیر", callback_data=f"race:lane:{gid}"),
         InlineKeyboardButton("🛡 دفاع", callback_data=f"race:defend:{gid}")],
        [InlineKeyboardButton("🏳️ خروج", callback_data=f"race:leave:{gid}")],
    ])


def _race_end_markup(gid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 مسابقه‌ی مجدد", callback_data=f"race:rematch:{gid}"),
        InlineKeyboardButton("🏠 بازگشت", callback_data=f"race:home:{gid}"),
    ]])


async def racing_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    gid = _gid5("rc")
    game = {
        "chat_id": update.effective_chat.id, "message_id": None,
        "players": [uid], "names": {uid: _name5(update.effective_user)},
        "cars": {uid: _race_new_car(_name5(update.effective_user))},
        "turn": 0, "started": False, "creator": uid,
        "track": _race_gen_track(gid),
    }
    RACE_GAMES[gid] = game
    sent = await update.effective_message.reply_text(
        "🏁 GOTHAM RACING — دونفره\n\n"
        f"👤 راننده ۱: {game['names'][uid]}\n"
        "⏳ منتظر حریف دوم...\n\n"
        "روی «پیوستن» بزن.",
        reply_markup=_race_join_markup(gid),
    )
    game["message_id"] = sent.message_id


def _race_schedule_timer(app, gid):
    _cancel_job(app, _race_job_name(gid))
    if getattr(app, "job_queue", None):
        app.job_queue.run_once(_race_turn_timeout, when=RACE_TURN_TIMEOUT_SEC, data={"gid": gid}, name=_race_job_name(gid))


def _cancel_job(app, name):
    if not getattr(app, "job_queue", None):
        return
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()


async def _race_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = RACE_GAMES.get(gid)
    if not game or not game["started"]:
        return
    uid = game["players"][game["turn"]]
    await _race_resolve_action(context.application, context.bot, gid, game, uid, "move",
                                note_prefix="⏱️ زمان تمام شد — ربات به‌جای شما گاز داد.\n\n")


def _race_save_result(chat_id, winner_id, loser_id):
    try:
        import bot as _bot
        _bot._record_game_result(chat_id, winner_id, loser_id)
    except Exception as e:
        log.info(f"race: could not save game record (harmless): {e}")


async def _race_resolve_action(app, bot, gid, game, uid, action, note_prefix=""):
    car = game["cars"][uid]
    opp_uid = next(p for p in game["players"] if p != uid)
    note = note_prefix

    if action == "lane":
        car["lane"] = 1 - car["lane"]
        note += f"↔️ {game['names'][uid]} به لاین {'B' if car['lane'] else 'A'} تغییر مسیر داد."
    elif action == "defend":
        car["shield"] = True
        note += f"🛡 {game['names'][uid]} حالت دفاعی گرفت (ضربه‌ی بعدی کم‌اثرتره)."
    else:  # move / nitro
        using_nitro = action == "nitro" and car["nitro"] > 0
        base = random.randint(2, 4) + car["speed"] // 25 + car["accel"]
        if using_nitro:
            car["nitro"] -= 1
            base += random.randint(5, 8)
            note += f"⚡ {game['names'][uid]} نیترو زد! "
        else:
            note += f"🏎 {game['names'][uid]} گاز داد. "
        old = car["pos"]
        new = min(old + base, RACE_LEN)
        crossed = range(old + 1, new + 1)
        crash_damage = 0
        events = []
        for cell in crossed:
            ctype = game["track"].get(cell, [None, None])[car["lane"]]
            if ctype == "fast":
                new = min(new + 2, RACE_LEN); events.append("🟢 مسیر سریع (+2)")
            elif ctype == "boost":
                car["nitro"] = min(RACE_MAX_NITRO, car["nitro"] + 1); events.append("⛽ بوست نیترو گرفتی")
            elif ctype == "turn":
                new = max(old, new - 1); events.append("↩️ پیچ تند (-1)")
            elif ctype == "obstacle":
                crash_damage += 15; events.append("💥 برخورد با مانع")
            elif ctype == "danger":
                crash_damage += 25; events.append("⚠️ منطقه‌ی خطرناک")
        if crash_damage:
            reduced = crash_damage * (1 - car["defense"] / 100)
            if car["shield"]:
                reduced *= 0.4
                car["shield"] = False
            car["health"] = max(0, round(car["health"] - reduced))
            car["accel"] = 0
        else:
            car["accel"] = min(3, car["accel"] + 1)
        car["pos"] = new
        if events:
            note += " | ".join(events)

    # --- بررسی پایان مسابقه ---
    if car["health"] <= 0:
        _cancel_job(app, _race_job_name(gid))
        game["started"] = False
        _race_save_result(game["chat_id"], opp_uid, uid)
        text = (_race_text(game) + f"\n\n🏆 RACE FINISHED\n🥇 {game['names'][opp_uid]}\n🥈 {game['names'][uid]}\n\n"
                f"💥 ماشین {game['names'][uid]} از حرکت افتاد!")
        await _safe_edit(bot, game["chat_id"], game["message_id"], text, _race_end_markup(gid))
        return
    if car["pos"] >= RACE_LEN:
        _cancel_job(app, _race_job_name(gid))
        game["started"] = False
        _race_save_result(game["chat_id"], uid, opp_uid)
        text = (_race_text(game) + f"\n\n🏆 RACE FINISHED\n🥇 {game['names'][uid]}\n🥈 {game['names'][opp_uid]}")
        await _safe_edit(bot, game["chat_id"], game["message_id"], text, _race_end_markup(gid))
        return

    game["turn"] = (game["turn"] + 1) % len(game["players"])
    _race_schedule_timer(app, gid)
    await _safe_edit(bot, game["chat_id"], game["message_id"],
                      _race_text(game, note=note, timer_left=RACE_TURN_TIMEOUT_SEC), _race_play_markup(gid))


async def _safe_edit(bot, chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        log.info(f"race: edit failed (harmless): {e}")


async def racing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    game = RACE_GAMES.get(gid)
    uid = update.effective_user.id
    try:
        if not game:
            await q.answer("این مسابقه دیگر در دسترس نیست.", show_alert=True); return

        if action == "join":
            if game["started"]:
                await q.answer("این مسابقه قبلاً شروع شده.", show_alert=True); return
            if uid == game["creator"]:
                await q.answer("نمی‌تونی به مسابقه‌ی خودت بپیوندی؛ منتظر حریف بمان.", show_alert=True); return
            if uid in game["players"]:
                await q.answer("قبلاً وارد شدی.", show_alert=True); return
            game["players"].append(uid); game["names"][uid] = _name5(update.effective_user)
            game["cars"][uid] = _race_new_car(_name5(update.effective_user))
            game["started"] = True
            _race_schedule_timer(context.application, gid)
            await q.answer("وارد مسابقه شدی! 🏁")
            await q.edit_message_text(_race_text(game, timer_left=RACE_TURN_TIMEOUT_SEC), reply_markup=_race_play_markup(gid))
            return

        if action == "cancel":
            if uid != game["creator"]:
                await q.answer("فقط سازنده می‌تواند لغو کند.", show_alert=True); return
            _cancel_job(context.application, _race_job_name(gid))
            del RACE_GAMES[gid]
            await q.edit_message_text("🏁 مسابقه لغو شد.")
            return

        if not game["started"]:
            await q.answer("مسابقه هنوز شروع نشده.", show_alert=True); return
        if uid not in game["players"]:
            await q.answer("این مسابقه برای تو نیست.", show_alert=True); return

        if action == "leave":
            _cancel_job(context.application, _race_job_name(gid))
            opponent = next((x for x in game["players"] if x != uid), None)
            game["started"] = False
            if opponent:
                _race_save_result(game["chat_id"], opponent, uid)
                text = f"🏳️ {game['names'][uid]} از مسابقه انصراف داد.\n🏆 برنده: {game['names'][opponent]}"
            else:
                text = "🏁 مسابقه پایان یافت."
            del RACE_GAMES[gid]
            await q.edit_message_text(text)
            return

        if action == "rematch":
            if uid not in game["players"]:
                await q.answer("این مسابقه برای تو نیست.", show_alert=True); return
            new_gid = _gid5("rc")
            new_game = {
                "chat_id": game["chat_id"], "message_id": game["message_id"],
                "players": list(game["players"]), "names": dict(game["names"]),
                "cars": {p: _race_new_car(game["names"][p]) for p in game["players"]},
                "turn": 0, "started": True, "creator": game["creator"],
                "track": _race_gen_track(new_gid),
            }
            RACE_GAMES[new_gid] = new_game
            RACE_GAMES.pop(gid, None)
            _race_schedule_timer(context.application, new_gid)
            await q.answer("مسابقه‌ی جدید شروع شد!")
            await q.edit_message_text(_race_text(new_game, timer_left=RACE_TURN_TIMEOUT_SEC), reply_markup=_race_play_markup(new_gid))
            return

        if action == "home":
            RACE_GAMES.pop(gid, None)
            await q.edit_message_text("🏠 از مسابقه‌ی ماشین خارج شدی.")
            return

        if uid != game["players"][game["turn"]]:
            await q.answer("نوبت تو نیست.", show_alert=True); return

        if action not in ("move", "nitro", "lane", "defend"):
            await q.answer(); return
        if action == "nitro" and game["cars"][uid]["nitro"] <= 0:
            await q.answer("نیترو نداری!", show_alert=True); return

        await q.answer()
        await _race_resolve_action(context.application, context.bot, gid, game, uid, action)
    except Exception as e:
        log.warning(f"racing_callback error: {e}")
        try:
            await q.answer("⚠️ یک مشکل موقت پیش آمد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_extra_games3(app):
    # نکته: MessageHandler های زیر نباید group=5 بگیرن، چون count_message تو
    # games_pack3.py یه MessageHandler(filters.TEXT & ~filters.COMMAND) با
    # group=5 داره که رو هر متنی match می‌شه و اگه زودتر ثبت بشه، جلوی این‌ها رو
    # می‌گیره. برای همین گروه اختصاصی ۱۲ استفاده شده.
    app.add_handler(MessageHandler(_kw5("یونو|بازی یونو|uno"), uno_start), group=12)
    app.add_handler(CallbackQueryHandler(uno_callback, pattern=r"^uno:"), group=5)

    app.add_handler(MessageHandler(_kw5("قلمرو|بازی قلمرو"), territory_start), group=12)
    app.add_handler(CallbackQueryHandler(territory_callback, pattern=r"^ter:"), group=5)

    app.add_handler(MessageHandler(_kw5("بیلیارد|بازی بیلیارد"), billiards_start), group=12)
    app.add_handler(CallbackQueryHandler(billiards_callback, pattern=r"^bil:"), group=5)
    app.add_handler(CallbackQueryHandler(bil_lobby_join_callback, pattern=r"^lobby5:"), group=5)

    app.add_handler(MessageHandler(_kw5("مسابقه ماشین|بازی مسابقه ماشین|مسابقه"), racing_start), group=12)
    app.add_handler(CallbackQueryHandler(racing_callback, pattern=r"^race:"), group=5)

    app.add_handler(CallbackQueryHandler(_noop5, pattern=r"^noop5$"), group=5)
