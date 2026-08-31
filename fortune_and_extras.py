# -*- coding: utf-8 -*-
"""
fortune_and_extras.py
================
۶ امکان جدید برای بخش «🧩 امکانات جدید»:

    🔮 فال گاتهام — «فال»، یه‌بار در روز یه فال گاتهامی می‌گیری
    🎰 اسلات گاتهام — «اسلات»، دستگاه اسلات واقعیِ خودِ تلگرام رو می‌چرخونه
    🧩 پرونده روز — «پرونده روز» یه معما نشون می‌ده، با «جواب <حدس>» جواب بده
    🧠 کدوم شخصیت گاتهامی هستی؟ — «شخصیت گاتهامی»، ۳ سوال کوتاه با دکمه
    ⏳ کپسول زمان — «کپسول <روز> <متن>»، پیام برای N روز بعدِ خودت
    🏅 شهروند نمونه‌ی امروز — هر روز یه عضو تصادفی از هر گروه معرفی می‌شه

مثل بقیه‌ی ماژول‌ها، مستقل از bot.py؛ register_fortune_and_extras(app, deps).
"""

import time
import random
import logging
from datetime import date, datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

FORTUNE_RE = filters.Regex(r"(?i)^\s*فال\s*$")
SLOT_RE = filters.Regex(r"(?i)^\s*اسلات\s*$")
CASE_RE = filters.Regex(r"(?i)^\s*پرونده روز\s*$")
ANSWER_RE = filters.Regex(r"(?i)^\s*جواب\s+(.+)$")
QUIZ_RE = filters.Regex(r"(?i)^\s*شخصیت گاتهامی\s*$")
CAPSULE_RE = filters.Regex(r"(?i)^\s*کپسول\s+(\d+)\s+(.+)$")

# --- ۱) فال گاتهام ---
FORTUNES = [
    "امروز تصمیمی می‌گیری که آینده‌ت رو مثل نور بت‌سیگنال روشن می‌کنه.",
    "یکی از سایه‌ها بهت کمک می‌کنه، حتی اگه اول بهش شک داشته باشی.",
    "امروز روز خوبیه برای صبر — حتی بتمن هم همیشه فوری حمله نمی‌کنه.",
    "یه فرصت غیرمنتظره سر می‌رسه؛ مثل باتارنگ، غافلگیرکننده ولی دقیق.",
    "امروز حواست به کسی باشه که ادعای دوستی می‌کنه — گاتهام پر از توفیسه.",
    "انرژی امروزت مثل جوکره: غیرقابل‌پیش‌بینی، ولی به نفعت تموم می‌شه.",
    "یه مکالمه‌ی کوتاه امروز، مثل یه سرنخ کوچیک، به یه چیز بزرگ می‌رسه.",
    "امروز وقتشه یه چیزی که مدت‌هاست عقبش انداختی رو شروع کنی.",
    "صبرت امروز جواب می‌ده — گاتهام همیشه به قهرمان‌های صبور پاداش می‌ده.",
    "یه خبر خوب از یه‌جای غیرمنتظره میاد، مثل تماس گوردون نیمه‌شب.",
]


def _fortune_cmd_factory(deps):
    async def fortune_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        player = await deps["db_run"](deps["get_player"], chat_id, user.id, user.username or "")
        inv = deps["get_inventory"](player)
        today = date.today().isoformat()
        if inv.get("last_fortune_date") == today:
            await update.effective_message.reply_text(
                f"🔮 فال امروزت همینه:\n«{inv.get('last_fortune_text', '')}»\n(فردا یه فال جدید بگیر)"
            )
            return
        line = random.choice(FORTUNES)
        inv["last_fortune_date"] = today
        inv["last_fortune_text"] = line
        deps["set_inventory"](player, inv)
        await deps["db_run"](deps["save_player"], player)
        await update.effective_message.reply_text(f"🔮 فال گاتهام امروزت:\n«{line}»")
    return fortune_cmd


# --- ۲) اسلات گاتهام (روی دایس واقعی خودِ تلگرام سوار شده) ---
SLOT_BET = 5
SLOT_JACKPOT_VALUE = 64   # سه‌تا ۷۷۷ (بیشترین جایزه)
SLOT_TRIPLE_VALUES = (1, 22, 43, 64)  # چهار حالتی که هر سه نماد یکی می‌شن


def _slot_cmd_factory(deps):
    async def slot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user
        player = await deps["db_run"](deps["get_player"], chat_id, user.id, user.username or "")
        if (player.get("score") or 0) < SLOT_BET:
            await update.effective_message.reply_text(f"🎰 برای شرط‌بندی حداقل {SLOT_BET} امتیاز لازم داری.")
            return
        player["score"] -= SLOT_BET
        dice_msg = await context.bot.send_dice(chat_id, emoji="🎰")
        value = dice_msg.dice.value
        if value == SLOT_JACKPOT_VALUE:
            win = SLOT_BET * 10
            text = f"🎰 جکپاات!! 🎉 سه‌تا ۷۷۷! +{win} امتیاز!"
        elif value in SLOT_TRIPLE_VALUES:
            win = SLOT_BET * 4
            text = f"🎰 سه‌تا یکی شد! +{win} امتیاز!"
        else:
            win = 0
            text = f"🎰 این‌بار نشد — {SLOT_BET} امتیاز از دست رفت. دوباره امتحان کن!"
        player["score"] = (player.get("score") or 0) + win
        await deps["db_run"](deps["save_player"], player)
        await update.effective_message.reply_text(text)
    return slot_cmd


# --- ۳) پرونده روز (معمای روزانه‌ی گروه) ---
CASES = [
    ("چیزی که هرچی ازش برداری بزرگ‌تر می‌شه چیه؟", "چاله"),
    ("یه چیزی که شب میاد بدون اینکه کسی صداش کنه، و روز می‌ره بدون اینکه کسی ببرتش چیه؟", "ستاره"),
    ("مادر و پدر ندارم ولی همه‌ی درخت‌ها منو دارن. من چیم؟", "میوه"),
    ("چه چیزی هست که همیشه جلوته ولی هیچ‌وقت نمی‌بینیش؟", "آینده"),
    ("چیزی که هرچی بشوریش کثیف‌تر می‌شه چیه؟", "آب"),
]
_CASE_STATE = {}  # chat_id -> {"date": iso, "q": ..., "a": ..., "solved": bool}


def _today_case(chat_id):
    today = date.today().isoformat()
    state = _CASE_STATE.get(chat_id)
    if not state or state["date"] != today:
        q, a = random.choice(CASES)
        state = {"date": today, "q": q, "a": a, "solved": False}
        _CASE_STATE[chat_id] = state
    return state


def _case_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔍 بررسی پرونده", callback_data="case:show"),
        InlineKeyboardButton("💡 راهنمایی", callback_data="case:hint"),
    ]])


def _case_cmd_factory(deps):
    async def case_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = _today_case(update.effective_chat.id)
        status = " (قبلاً حل شده)" if state["solved"] else ""
        await update.effective_message.reply_text(
            f"🧩 پرونده‌ی امروز گاتهام{status}:\n«{state['q']}»\n\nبرای جواب بنویس: «جواب <حدست>»",
            reply_markup=_case_keyboard(),
        )

    async def answer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        state = _today_case(chat_id)
        if state["solved"]:
            await update.effective_message.reply_text("🧩 پرونده‌ی امروز قبلاً حل شده — فردا یه پرونده‌ی جدید میاد.")
            return
        text = update.effective_message.text or ""
        guess = text[len("جواب"):].strip()
        if guess.replace("ی", "ي") != state["a"].replace("ی", "ي") and guess != state["a"]:
            await update.effective_message.reply_text("🧩 نه، این جواب درست نیست. دوباره امتحان کن!")
            return
        state["solved"] = True
        user = update.effective_user
        player = await deps["db_run"](deps["get_player"], chat_id, user.id, user.username or "")
        player["score"] = (player.get("score") or 0) + 20
        await deps["db_run"](deps["save_player"], player)
        await update.effective_message.reply_text(
            f"🧩 آفرین {user.first_name}! درست بود — جواب: «{state['a']}». +۲۰ امتیاز 🏆"
        )

    async def case_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 🕵️ GOTHAM CASE FILE (Phase 6) — از همون _CASE_STATE/_today_case
        # موجود استفاده می‌کنه؛ فقط دو دکمه‌ی «بررسی پرونده» و «راهنمایی»
        # به سیستم فعلی اضافه شد. «✅ جواب» دکمه نشد چون جواب یه حدسِ آزاده و
        # از طریق دکمه قابل گرفتن نیست — همون «جواب <حدست>» متنی موجود می‌مونه.
        q = update.callback_query
        action = q.data.split(":", 1)[1]
        chat_id = update.effective_chat.id
        state = _today_case(chat_id)
        if action == "show":
            status = " (قبلاً حل شده)" if state["solved"] else ""
            await q.answer()
            await q.edit_message_text(
                f"🧩 پرونده‌ی امروز گاتهام{status}:\n«{state['q']}»\n\nبرای جواب بنویس: «جواب <حدست>»",
                reply_markup=_case_keyboard(),
            )
            return
        if action == "hint":
            if state["solved"]:
                await q.answer("این پرونده قبلاً حل شده.", show_alert=True)
                return
            ans = state["a"]
            hint = f"جواب {len(ans)} حرفیه و با «{ans[0]}» شروع می‌شه."
            await q.answer(f"💡 {hint}", show_alert=True)
            return
        await q.answer()

    return case_cmd, answer_cmd, case_button_callback


# --- ۴) کدوم شخصیت گاتهامی هستی؟ (کوییز کوتاه) ---
QUIZ_QUESTIONS = [
    ("تو یه بحران چیکار می‌کنی؟", [
        ("با برنامه‌ریزی دقیق وارد عمل می‌شم", "batman"),
        ("با شوخی و بی‌خیالی جو رو عوض می‌کنم", "joker"),
        ("منطقی و آروم تحلیل می‌کنم", "gordon"),
        ("خودمو می‌زنم به اون راه و بعد ضربه می‌زنم", "harley"),
    ]),
    ("تو تیم بودن یعنی چی؟", [
        ("رهبری می‌کنم، ولی تنها کار می‌کنم", "batman"),
        ("قوانین رو دوست ندارم", "joker"),
        ("قانون و نظم رو نگه می‌دارم", "gordon"),
        ("فداکارم برای کسی که دوستش دارم", "harley"),
    ]),
    ("نقطه‌ضعفت چیه؟", [
        ("تنهایی بیش‌ازحد", "batman"),
        ("پیش‌بینی‌ناپذیری خودم", "joker"),
        ("گاهی زیادی به سیستم اعتماد می‌کنم", "gordon"),
        ("زیادی احساساتی می‌شم", "harley"),
    ]),
]
QUIZ_RESULTS = {
    "batman": ("🦇 بتمن", "منظم، مصمم و همیشه یه قدم جلوتر — رهبر سایه‌های گاتهام."),
    "joker": ("🃏 جوکر", "غیرقابل‌پیش‌بینی و پرانرژی — همه‌جا رو به‌هم می‌ریزی، به روش خودت."),
    "gordon": ("👮 گوردون", "قابل‌اعتماد و منطقی — ستون فقرات هر تیمی هستی."),
    "harley": ("🎭 هارلی کویین", "پرشور، وفادار و کمی دیوونه — دلی که هرچی بگه انجامش می‌دی."),
}
_QUIZ_STATE = {}  # (chat_id, user_id) -> {"idx": int, "scores": {...}}


def _quiz_cmd_factory():
    async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        key = (update.effective_chat.id, update.effective_user.id)
        _QUIZ_STATE[key] = {"idx": 0, "scores": {}}
        await _send_quiz_question(update.effective_message, key)

    async def _send_quiz_question(message, key):
        state = _QUIZ_STATE[key]
        q, options = QUIZ_QUESTIONS[state["idx"]]
        rows = [
            [InlineKeyboardButton(label, callback_data=f"quiz:{key[0]}:{key[1]}:{state['idx']}:{res}")]
            for label, res in options
        ]
        await message.reply_text(f"🧠 سوال {state['idx'] + 1}/{len(QUIZ_QUESTIONS)}:\n{q}", reply_markup=InlineKeyboardMarkup(rows))

    async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        _, chat_id_s, user_id_s, idx_s, res = query.data.split(":")
        key = (int(chat_id_s), int(user_id_s))
        if update.effective_user.id != key[1]:
            await query.answer("🧠 این کوییز مال یکی دیگه‌ست، خودت «شخصیت گاتهامی» رو بنویس.", show_alert=True)
            return
        state = _QUIZ_STATE.get(key)
        if not state or state["idx"] != int(idx_s):
            await query.answer()
            return
        state["scores"][res] = state["scores"].get(res, 0) + 1
        state["idx"] += 1
        await query.answer()
        if state["idx"] >= len(QUIZ_QUESTIONS):
            winner = max(state["scores"], key=state["scores"].get)
            label, desc = QUIZ_RESULTS[winner]
            await query.edit_message_text(f"🧠 نتیجه: تو {label} هستی!\n{desc}")
            del _QUIZ_STATE[key]
        else:
            q, options = QUIZ_QUESTIONS[state["idx"]]
            rows = [
                [InlineKeyboardButton(lbl, callback_data=f"quiz:{key[0]}:{key[1]}:{state['idx']}:{r}")]
                for lbl, r in options
            ]
            await query.edit_message_text(
                f"🧠 سوال {state['idx'] + 1}/{len(QUIZ_QUESTIONS)}:\n{q}", reply_markup=InlineKeyboardMarkup(rows)
            )

    return quiz_cmd, quiz_callback


# --- ۵) کپسول زمان ---
MAX_CAPSULE_DAYS = 90


def _capsule_cmd_factory():
    async def capsule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.effective_message.text or ""
        m_days, m_text = None, None
        parts = text.split(None, 2)  # ["کپسول", "7", "متن..."]
        if len(parts) >= 3 and parts[1].isdigit():
            m_days, m_text = int(parts[1]), parts[2]
        if not m_days or not m_text:
            await update.effective_message.reply_text("⚠️ فرمت: «کپسول <تعداد روز> <متن>» — مثلاً «کپسول 7 سلام به خودم»")
            return
        if m_days < 1 or m_days > MAX_CAPSULE_DAYS:
            await update.effective_message.reply_text(f"⚠️ تعداد روز باید بین ۱ تا {MAX_CAPSULE_DAYS} باشه.")
            return
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        if context.job_queue:
            context.job_queue.run_once(
                _deliver_capsule, when=timedelta(days=m_days),
                data={"chat_id": chat_id, "user_id": user_id, "text": m_text},
            )
            await update.effective_message.reply_text(
                f"⏳ کپسول ثبت شد! {m_days} روز دیگه همین پیام برات می‌فرستم."
            )
        else:
            await update.effective_message.reply_text("⚠️ سیستم زمان‌بندی ربات فعال نیست، کپسول ثبت نشد.")

    async def _deliver_capsule(context: ContextTypes.DEFAULT_TYPE):
        d = context.job.data
        try:
            await context.bot.send_message(
                d["chat_id"], f"⏳🦇 کپسول زمانِ چند روز پیشت رسید:\n\n«{d['text']}»"
            )
        except Exception:
            pass

    return capsule_cmd


# --- ۶) شهروند نمونه‌ی امروز (جاب روزانه) ---
def _citizen_job_factory(deps):
    async def citizen_job(context: ContextTypes.DEFAULT_TYPE):
        chat_ids = await deps["db_run"](deps["get_all_chat_ids"])
        for chat_id in chat_ids:
            try:
                rows = await deps["db_run"](deps["get_leaderboard"], chat_id, 30)
                candidates = [r for r in rows if r.get("username")]
                if not candidates:
                    continue
                pick = random.choice(candidates)
                await context.bot.send_message(
                    chat_id,
                    f"🏅 شهروند نمونه‌ی امروز گاتهام: @{pick['username']} 🎉\nگاتهام امروز به‌خاطر تو یه‌کم امن‌تره!",
                )
            except Exception:
                pass
    return citizen_job


def register_fortune_and_extras(app, deps):
    """
    deps = {
        "get_player": ..., "save_player": ..., "get_inventory": ..., "set_inventory": ...,
        "db_run": ..., "get_leaderboard": ..., "get_all_chat_ids": ...,
    }
    """
    fortune_cmd = _fortune_cmd_factory(deps)
    slot_cmd = _slot_cmd_factory(deps)
    case_cmd, answer_cmd, case_button_callback = _case_cmd_factory(deps)
    quiz_cmd, quiz_callback = _quiz_cmd_factory()
    capsule_cmd = _capsule_cmd_factory()
    citizen_job = _citizen_job_factory(deps)

    app.add_handler(MessageHandler(FORTUNE_RE, fortune_cmd), group=26)
    app.add_handler(MessageHandler(SLOT_RE, slot_cmd), group=26)
    app.add_handler(MessageHandler(CASE_RE, case_cmd), group=26)
    app.add_handler(CallbackQueryHandler(case_button_callback, pattern=r"^case:"), group=26)
    app.add_handler(MessageHandler(ANSWER_RE, answer_cmd), group=26)
    app.add_handler(MessageHandler(QUIZ_RE, quiz_cmd), group=26)
    app.add_handler(CallbackQueryHandler(quiz_callback, pattern=r"^quiz:"), group=26)
    app.add_handler(MessageHandler(CAPSULE_RE, capsule_cmd), group=26)

    if getattr(app, "job_queue", None):
        from datetime import time as dtime
        try:
            from zoneinfo import ZoneInfo
            tehran = ZoneInfo("Asia/Tehran")
        except Exception:
            tehran = None
        app.job_queue.run_daily(citizen_job, time=dtime(12, 0, tzinfo=tehran))
