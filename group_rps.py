# -*- coding: utf-8 -*-
"""
group_rps.py
================
🎮 سنگ کاغذ قیچی — نسخه‌ی گروهی و PvP واقعی (نه در برابر ربات).

روند:
    ۱. یکی «🎮 سنگ کاغذ قیچی» (از منوی بازی‌ها ← 👥 گروهی) رو می‌زنه.
    ۲. ربات پیام بازی با دکمه‌ی «⚔️ پیوستن به بازی» می‌سازه.
    ۳. نفر دوم (نه خودِ سازنده) می‌زنه، بازی شروع می‌شه.
    ۴. هر دو مخفیانه سنگ/کاغذ/قیچی انتخاب می‌کنن (انتخاب هرکس فقط با یه
       آلرت خصوصی به خودش نشون داده می‌شه، نه تو پیام گروه).
    ۵. وقتی هر دو انتخاب کردن، نتیجه‌ی نهایی با هر دو انتخاب نمایش داده می‌شه.
    ۶. «🔄 بازی دوباره» یه دور جدید بین همون دو نفر می‌سازه (بدون نیاز به
       پیوستن دوباره)، «🏠 بازگشت» بازی رو می‌بنده.

قانون ۶۰ ثانیه:
    - اگه نفر دوم تا ۶۰ ثانیه نپیونده، سازنده برنده‌ی خودکار اعلام می‌شه.
    - اگه بعد از شروع، یکی/هردو تا ۶۰ ثانیه انتخاب نکنن، هرکی انتخاب کرده
      برنده می‌شه؛ اگه هیچ‌کدوم انتخاب نکردن، بازی بدون برنده لغو می‌شه.

هر بازی یه game_id یکتا داره (uuid) و بازی‌های همزمان تو گروه‌های مختلف یا
حتی یه گروه، کاملاً مستقل از هم‌ان. Timeoutها با JobQueue مدیریت می‌شن و با
اسم اختصاصیِ هر بازی قابل لغو کردن‌ان تا تداخل ایجاد نشه. بین چک‌کردن و
ثبتِ join/choice هیچ await ای نیست، پس رِیس‌کاندیشن (دو نفر هم‌زمان جای دوم
رو بگیرن) عملاً پیش نمی‌آد.
"""

import time
import uuid
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

GRPS_GAMES: dict = {}

CHOICE_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
CHOICE_LABEL = {"rock": "سنگ", "paper": "کاغذ", "scissors": "قیچی"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

JOIN_TIMEOUT_SEC = 60
CHOICE_TIMEOUT_SEC = 60

GROUP_RPS_TRIGGER_RE = filters.Regex(
    r"(?i)^\s*(سنگ کاغذ قیچی گروهی|پی وی پی سنگ کاغذ قیچی|rps گروهی)\s*$"
)


def _name(user) -> str:
    return user.first_name or (f"@{user.username}" if user.username else "بازیکن")


def _gid() -> str:
    return uuid.uuid4().hex[:10]


def _join_text(game, remaining=JOIN_TIMEOUT_SEC) -> str:
    return (
        "🎮 GOTHAM RPS\n\n"
        f"👤 بازیکن ۱: {game['creator_name']}\n"
        "⚔️ حریف: در انتظار بازیکن...\n\n"
        f"⏳ زمان باقی‌مانده: {remaining} ثانیه"
    )


def _join_markup(gid) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⚔️ پیوستن به بازی", callback_data=f"grps:join:{gid}")]]
    )


def _battle_text(game) -> str:
    c_status = "✅ انتخاب کرد" if game["creator_id"] in game["choices"] else "⏳ در حال انتخاب..."
    o_status = "✅ انتخاب کرد" if game["opponent_id"] in game["choices"] else "⏳ در حال انتخاب..."
    return (
        "🎮 BATTLE STARTED\n\n"
        f"👤 بازیکن ۱: {game['creator_name']} — {c_status}\n"
        f"👤 بازیکن ۲: {game['opponent_name']} — {o_status}\n\n"
        "🤫 انتخابت رو بزن؛ تا حریف انتخاب نکنه کسی انتخابت رو نمی‌بینه."
    )


def _battle_markup(gid) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪨 سنگ", callback_data=f"grps:choice:{gid}:rock"),
            InlineKeyboardButton("📄 کاغذ", callback_data=f"grps:choice:{gid}:paper"),
            InlineKeyboardButton("✂️ قیچی", callback_data=f"grps:choice:{gid}:scissors"),
        ],
        [InlineKeyboardButton("🏳 انصراف", callback_data=f"grps:forfeit:{gid}")],
    ])


def _result_markup(gid) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 بازی دوباره", callback_data=f"grps:rematch:{gid}"),
        InlineKeyboardButton("🏠 بازگشت", callback_data=f"grps:home:{gid}"),
    ]])


def _resolve(c1, c2):
    """۱ یعنی نفر اول برد، ۲ یعنی نفر دوم برد، ۰ یعنی مساوی."""
    if c1 == c2:
        return 0
    return 1 if BEATS[c1] == c2 else 2


def _cancel_job(app, name):
    for job in app.job_queue.get_jobs_by_name(name):
        job.schedule_removal()


async def _safe_edit(bot, chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup
        )
    except Exception as e:
        log.info(f"group_rps: edit failed (harmless, message probably unchanged): {e}")


def _record_result(chat_id, winner_id, loser_id):
    """برد/باخت رو تو سیستم امتیازدهیِ مشترکِ ربات ثبت می‌کنه (همون تابعی که
    card_room.py هم استفاده می‌کنه) — بدون ساختن سیستم امتیاز جدید."""
    if not chat_id or not winner_id or not loser_id:
        return
    try:
        import bot as _bot
        _bot._record_game_result(chat_id, winner_id, loser_id)
    except Exception as e:
        log.info(f"group_rps: could not save game record (harmless): {e}")


# =========================================================
#  شروع بازی
# =========================================================

async def group_rps_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    gid = _gid()
    game = {
        "chat_id": chat_id,
        "message_id": None,
        "creator_id": user.id,
        "creator_name": _name(user),
        "opponent_id": None,
        "opponent_name": None,
        "choices": {},
        "phase": "join",
        "created_ts": time.time(),
    }
    GRPS_GAMES[gid] = game

    sent = await update.effective_message.reply_text(
        _join_text(game), reply_markup=_join_markup(gid)
    )
    game["message_id"] = sent.message_id

    context.application.job_queue.run_once(
        _join_timeout_job, when=JOIN_TIMEOUT_SEC, data={"gid": gid}, name=f"grps_join:{gid}"
    )


async def _join_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = GRPS_GAMES.get(gid)
    if not game or game["phase"] != "join":
        return  # یا قبلاً پیوسته شده، یا لغو شده — دیگه کاری لازم نیست
    del GRPS_GAMES[gid]
    text = (
        "⏱️ زمان ورود بازیکن دوم تمام شد!\n"
        f"🏆 برنده: {game['creator_name']}\n"
        "بازیکن حریف در ۶۰ ثانیه وارد بازی نشد."
    )
    await _safe_edit(context.bot, game["chat_id"], game["message_id"], text)


# =========================================================
#  Timeout مرحله‌ی انتخاب
# =========================================================

async def _choice_timeout_job(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    game = GRPS_GAMES.get(gid)
    if not game or game["phase"] != "choose":
        return
    del GRPS_GAMES[gid]
    chosen = list(game["choices"].keys())
    if len(chosen) == 1:
        winner_id = chosen[0]
        loser_id = game["opponent_id"] if winner_id == game["creator_id"] else game["creator_id"]
        winner_name = game["creator_name"] if winner_id == game["creator_id"] else game["opponent_name"]
        _record_result(game["chat_id"], winner_id, loser_id)
        text = (
            "⏱️ زمان انتخاب تموم شد!\n"
            f"🏆 برنده: {winner_name}\n"
            "حریف انتخابش رو به‌موقع انجام نداد."
        )
    else:
        text = "⏱️ زمان انتخاب تموم شد و هیچ‌کدوم انتخاب نکردید!\n🤷 بازی بدون برنده لغو شد."
    await _safe_edit(context.bot, game["chat_id"], game["message_id"], text, reply_markup=_result_markup(gid) if len(chosen) == 1 else None)
    # نکته: بعد از timeout، gid از GRPS_GAMES حذف شده، پس دکمه‌ی «بازی دوباره»
    # (اگه نشون داده بشه) یه بازی جدید مستقل می‌سازه، نه ادامه‌ی همین رکورد.


def _start_battle_phase(app, gid, game):
    game["phase"] = "choose"
    game["choices"] = {}
    app.job_queue.run_once(
        _choice_timeout_job, when=CHOICE_TIMEOUT_SEC, data={"gid": gid}, name=f"grps_choice:{gid}"
    )


# =========================================================
#  کال‌بک اصلی
# =========================================================

async def group_rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    gid = parts[2]
    user = update.effective_user

    try:
        if action == "join":
            game = GRPS_GAMES.get(gid)
            if not game or game["phase"] != "join":
                await q.answer("این بازی دیگه در دسترس نیست.", show_alert=True)
                return
            if user.id == game["creator_id"]:
                await q.answer("❌ نمی‌تونی خودت با خودت بازی کنی!", show_alert=True)
                return
            # هیچ awaitـی بین چک بالا و ثبت پایین نیست -> رِیس‌کاندیشن رخ نمی‌ده
            game["opponent_id"] = user.id
            game["opponent_name"] = _name(user)
            _cancel_job(context.application, f"grps_join:{gid}")
            _start_battle_phase(context.application, gid, game)
            await q.edit_message_text(_battle_text(game), reply_markup=_battle_markup(gid))
            await q.answer("وارد بازی شدی! انتخابت رو بزن ⚔️")
            return

        if action == "choice":
            choice = parts[3]
            game = GRPS_GAMES.get(gid)
            if not game or game["phase"] != "choose":
                await q.answer("این بازی تموم شده یا هنوز شروع نشده.", show_alert=True)
                return
            if user.id not in (game["creator_id"], game["opponent_id"]):
                await q.answer("این بازی برای تو نیست.", show_alert=True)
                return
            if user.id in game["choices"]:
                await q.answer("قبلاً انتخابت رو ثبت کردی، صبر کن حریف هم انتخاب کنه.", show_alert=True)
                return
            game["choices"][user.id] = choice
            await q.answer(f"انتخابت ({CHOICE_LABEL[choice]}) ثبت شد ✅ — مخفی می‌مونه تا حریف هم انتخاب کنه.")

            if len(game["choices"]) < 2:
                await _safe_edit(context.bot, game["chat_id"], game["message_id"], _battle_text(game), _battle_markup(gid))
                return

            _cancel_job(context.application, f"grps_choice:{gid}")
            c1 = game["choices"][game["creator_id"]]
            c2 = game["choices"][game["opponent_id"]]
            outcome = _resolve(c1, c2)
            n1, n2 = game["creator_name"], game["opponent_name"]
            e1, e2 = CHOICE_EMOJI[c1], CHOICE_EMOJI[c2]
            if outcome == 0:
                header = "🤝 مساوی شد!"
            else:
                winner_id = game["creator_id"] if outcome == 1 else game["opponent_id"]
                loser_id = game["opponent_id"] if outcome == 1 else game["creator_id"]
                winner = n1 if outcome == 1 else n2
                header = f"🏆 برنده: {winner}\n💀 بازنده: {n2 if outcome == 1 else n1}"
                _record_result(game["chat_id"], winner_id, loser_id)
            text = (
                "🎮 نتیجه‌ی نبرد گاتهام\n\n"
                f"👤 {n1}: {e1} {CHOICE_LABEL[c1]}\n"
                f"👤 {n2}: {e2} {CHOICE_LABEL[c2]}\n\n"
                f"{header}"
            )
            game["phase"] = "done"
            await _safe_edit(context.bot, game["chat_id"], game["message_id"], text, _result_markup(gid))
            return

        if action == "rematch":
            old = GRPS_GAMES.get(gid)
            if not old or user.id not in (old["creator_id"], old["opponent_id"]):
                await q.answer("این بازی دیگه در دسترس نیست.", show_alert=True)
                return
            new_gid = _gid()
            new_game = {
                "chat_id": old["chat_id"],
                "message_id": old["message_id"],
                "creator_id": old["creator_id"],
                "creator_name": old["creator_name"],
                "opponent_id": old["opponent_id"],
                "opponent_name": old["opponent_name"],
                "choices": {},
                "phase": "choose",
                "created_ts": time.time(),
            }
            GRPS_GAMES[new_gid] = new_game
            GRPS_GAMES.pop(gid, None)
            context.application.job_queue.run_once(
                _choice_timeout_job, when=CHOICE_TIMEOUT_SEC, data={"gid": new_gid}, name=f"grps_choice:{new_gid}"
            )
            await q.edit_message_text(_battle_text(new_game), reply_markup=_battle_markup(new_gid))
            await q.answer("دور جدید شروع شد! ⚔️")
            return

        if action == "home":
            GRPS_GAMES.pop(gid, None)
            await q.edit_message_text("🏠 از بازی سنگ کاغذ قیچی گاتهام خارج شدی. هر وقت خواستی، از «👥 گروهی» دوباره بساز.")
            await q.answer()
            return

        if action == "forfeit":
            game = GRPS_GAMES.get(gid)
            if not game or game["phase"] not in ("choose",):
                await q.answer("این بازی دیگه در دسترس نیست.", show_alert=True)
                return
            if user.id not in (game["creator_id"], game["opponent_id"]):
                await q.answer("این بازی برای تو نیست.", show_alert=True)
                return
            _cancel_job(context.application, f"grps_choice:{gid}")
            GRPS_GAMES.pop(gid, None)
            loser_id = user.id
            winner_id = game["opponent_id"] if user.id == game["creator_id"] else game["creator_id"]
            winner_name = game["opponent_name"] if user.id == game["creator_id"] else game["creator_name"]
            loser_name = game["creator_name"] if user.id == game["creator_id"] else game["opponent_name"]
            _record_result(game["chat_id"], winner_id, loser_id)
            text = (
                "🏳 انصراف داده شد!\n\n"
                f"👤 {loser_name} انصراف داد.\n"
                f"🏆 برنده: {winner_name}"
            )
            await _safe_edit(context.bot, game["chat_id"], game["message_id"], text, _result_markup(gid))
            await q.answer("انصراف ثبت شد.")
            return

        await q.answer()
    except Exception as e:
        # طبق قانون کلی ربات: هیچ خطایی نباید کل ربات رو کرش بده.
        log.warning(f"group_rps_callback error: {e}")
        try:
            await q.answer("⚠️ یه مشکل موقت پیش اومد، دوباره امتحان کن.", show_alert=True)
        except Exception:
            pass


async def _group_rps_keyword_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await group_rps_start(update, context)


def register_group_rps(app):
    # کال‌بک‌های grps: با pattern اختصاصی، تو گروه ۱ (مثل بقیه‌ی بازی‌ها) —
    # چون pattern اختصاصیه، با CallbackQueryHandlerهای دیگه‌ی هم‌گروه تداخل نداره.
    app.add_handler(CallbackQueryHandler(group_rps_callback, pattern=r"^grps:"), group=1)
    # تریگر متنیِ اختیاری (علاوه بر ورود از منوی بازی‌ها ← 👥 گروهی) — گروه ۳۰
    # چون هندلرهای catch-all متنی تو گروه‌های پایین‌تر (مثل ۱، ۵) هر متنی رو
    # می‌قاپن و می‌تونن جلوی این تریگر رو بگیرن.
    app.add_handler(MessageHandler(GROUP_RPS_TRIGGER_RE, _group_rps_keyword_router), group=30)
