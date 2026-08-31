# -*- coding: utf-8 -*-
"""
games.py
================
ماژول بازی‌ها با «کلمه‌ی محرک» به‌جای دستور اسلش.

نحوه‌ی اتصال به فایل اصلی (batbot.py):

    from games import register_games, GAMES_LIST_TEXT

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    register_games(app)          # همه‌ی هندلرهای بازی رو ثبت می‌کنه
    ...
    app.run_polling()

برای پین‌کردن لیست بازی‌ها یک دستور مدیریتی اضافه شده: کلمه‌ی «لیست بازی‌ها»
هر کسی بفرسته پیام رو می‌سازه، و اگه ادمین باشه خودکار پینش می‌کنه.
"""

import asyncio
import random
import re
import string
from collections import defaultdict

from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ChatType, ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

from ttt_gotham import gotham_ttt_start, TRIGGER_TEXT as GOTHAM_TTT_TRIGGER


# =========================================================
#  متن لیست بازی‌ها (به‌جای دستورات اسلش، حالا با کلمه کار می‌کنن)
# =========================================================

GAMES_LIST_TEXT = (
    "🎮 *لیست بازی‌ها* (کافیه کلمه رو بنویسی، نیازی به / نیست)\n\n"
    "سنگ کاغذ قیچی\n"
    "دوز — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن\n"
    "چهار در ردیف — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن\n"
    "دار — حدس کلمه حرف به حرف\n"
    "تاس\n"
    "شیر یا خط\n"
    "کوییز\n"
    "معما\n"
    "ترجیح میدی\n"
    "حدس عدد\n"
    "زنجیره کلمات — پایان: پایان زنجیره\n"
    "داستان گروهی — پایان: پایان داستان\n"
    "رولت روسی — شروع دور، بعدش «شلیک» بنویس تا نوبت بگیری\n"
    "وردل — حدس کلمه با راهنمای رنگی\n"
    "جدول کلمات — جواب رو مستقیم تو چت بنویس\n"
    "مسابقه تایپ — جمله رو دقیق تایپ کن\n"
    "مافیا بازی — عضویت / مافیا شروع / مافیا پایان\n"
    "2048 — گرید ۴×۴، دکمه‌های جهت‌دار\n"
    "چراغ‌ها — همه‌ی چراغ‌ها رو خاموش کن\n"
    "حافظه — جفت‌های مخفی رو پیدا کن\n"
    "نبرد دریایی — (روی پیام حریف ریپلای کن) ناوگان واقعی ۶×۶، با اصابت نوبت ادامه داری\n"
    "گنج پنهان — رو خونه‌ها کلیک کن، بمب نزن!\n"
    "مین روب — رو خونه‌ها بزن، ۶ بمب مخفی روی گرید ۶×۶\n"
    "نقطه بازی — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن\n"
    "تیکو — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن\n"
    "جمشید — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن\n"
    "گیر بازار — ریپلای کن یا دکمه‌ی «بپیوند» رو بزن\n"
    "گیم / لیست بازی‌ها — نمایش دوباره‌ی همین پیام\n"
    "دوز آنلاین (inline) — تو هر چتی بنویس @نام_ربات و دوز ۳×۳ تا ۸×۸ رو با دوست یا با ربات شروع کن\n"
    "دوز گاتهام — تو همین چت بنویس «دوز گاتهام»، سایز برد (۳×۳ تا ۸×۸) و حریف (با دوست/با ربات) رو با دکمه انتخاب کن\n"
    "\n"
    "🏆 *بازی‌های حرفه‌ای (تخته‌ای، تا ۴ نفره، با دکمه‌ی پیوستن):*\n"
    "شطرنج — دو نفره، حرکت مهره‌ها با زدن خانه‌ی مبدأ و مقصد رو تخته‌ی واقعی\n"
    "منچ — ۲ تا ۴ نفره، تاس و مهره، خونه‌های امن و زدن مهره‌ی حریف\n"
    "مار و پله — ۲ تا ۴ نفره، تاس، مار و نردبان تا خونه‌ی ۱۰۰\n"
    "یونو — ۲ تا ۴ نفره، دکمه‌ی «پیوستن» رو بزن\n"
    "قلمرو — ۲ تا ۴ نفره، هر خونه رو یکی بگیره، آخرش هرکی بیشتر داره برنده‌ست\n"
    "بیلیارد — دو نفره، ریپلای کن یا «پیوستن» رو بزن، توپ‌ها رو بزن و آخرش ۸ سیاه\n"
    "مسابقه ماشین — ۲ تا ۴ نفره، تاس بنداز، بوست و لکه‌روغن رو مدیریت کن\n"
)


# =========================================================
#  استیت‌های داخل‌حافظه (per chat)
# =========================================================

GUESS_STATE = {}          # chat_id -> number
HANGMAN_STATE = {}        # chat_id -> {"word": str, "guessed": set, "wrong": int}
TICTACTOE_STATE = {}      # chat_id -> board dict
WORDCHAIN_STATE = defaultdict(list)   # chat_id -> [words]
STORY_STATE = defaultdict(list)       # chat_id -> [lines]
ROULETTE_STATE = {}        # chat_id -> {"chamber": int, "bullet": int}
ROULETTE_MUTE_MINUTES = 5
CONNECT4_STATE = {}        # game_id -> board dict
WORDLE_STATE = {}          # chat_id -> {"word": str, "tries": int}
CROSSWORD_STATE = {}       # chat_id -> {"answer": str}
TYPERACE_STATE = {}        # chat_id -> {"sentence": str, "start": float, "done": bool}
MAFIA_STATE = {}           # chat_id -> {"players": {id: name}, "roles": {id: role}, "started": bool}

TRIVIA_BANK = [
    {
        "q": "پایتخت ژاپن کدومه؟",
        "options": ["اوزاکا", "توکیو", "کیوتو", "ناگویا"],
        "answer": 1,
    },
    {
        "q": "بزرگ‌ترین اقیانوس دنیا؟",
        "options": ["اطلس", "هند", "آرام (آرامش/پاسیفیک)", "منجمد شمالی"],
        "answer": 2,
    },
]

RIDDLES = [
    ("چه چیزی هرچی ازش برداری بزرگ‌تر میشه؟", "چاله/گودال"),
    ("چیزی که شب میاد و صبح میره چیه؟", "خواب"),
]

WYR_BANK = [
    "ترجیح می‌دی همیشه یک ساعت زودتر برسی یا همیشه یک ساعت دیرتر؟",
    "ترجیح می‌دی حرف دلت رو بلد نباشی بگی یا همیشه بلند فکر کنی؟",
]

HANGMAN_WORDS = ["گاتهام", "بتمن", "جوکر", "کامپیوتر", "پایتون"]

WORDLE_WORDS = ["گاتهام", "بتمن", "جوکر", "شمشیر", "پنجره"]

CROSSWORD_BANK = [
    {"clue": "نگهبان تاریک گاتهام (اسم مبدل)", "answer": "بتمن"},
    {"clue": "دشمن اصلی بتمن با رنگ‌های سبز و بنفش", "answer": "جوکر"},
    {"clue": "شهری که بتمن ازش محافظت می‌کنه", "answer": "گاتهام"},
]

TYPERACE_SENTENCES = [
    "گاتهام همیشه به یک نگهبان تاریک نیاز داره",
    "شب از هر روزی تاریک‌تره اما صبح می‌رسه",
    "قدرت واقعی از اراده میاد نه از زور بازو",
]

MAFIA_ROLES_BY_COUNT = {
    4: ["مافیا", "پزشک", "شهروند", "شهروند"],
    5: ["مافیا", "پزشک", "کارآگاه", "شهروند", "شهروند"],
    6: ["مافیا", "مافیا", "پزشک", "کارآگاه", "شهروند", "شهروند"],
}


# =========================================================
#  ابزار کمکی
# =========================================================

# نگاشت کاراکترهای عربی که کیبورد بعضی گوشی‌ها/تلگرام‌ها جای معادل فارسی‌شون
# می‌فرستن (ي عربی به‌جای ی فارسی، ك عربی به‌جای ک فارسی و...). بدون این نگاشت،
# پیامی که ظاهرش دقیقاً «گیم» به‌نظر می‌رسه ممکنه از نظر یونیکد با کلمه‌ی محرک
# ثبت‌شده تو دیکشنری یکی نباشه و بی‌صدا هیچ‌کاری نکنه.
_ARABIC_TO_PERSIAN = str.maketrans({
    "\u064a": "\u06cc",  # ي عربی -> ی فارسی
    "\u0643": "\u06a9",  # ك عربی -> ک فارسی
    "\u0629": "\u0647",  # ة -> ه
    "\u200f": "",         # RTL mark
    "\u200e": "",         # LTR mark
    "\ufeff": "",         # BOM
})


def norm(text: str) -> str:
    t = (text or "").strip()
    t = t.translate(_ARABIC_TO_PERSIAN)
    t = t.replace("\u06cc\u0670", "\u06cc")  # ی + کشیده‌ی زائد -> ی ساده
    # چند تا اسپیس/تب پشت‌سرهم رو یکی کن تا تطبیق با کلمات محرک خراب نشه
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _save_game_record(chat_id, winner_id, loser_id):
    """رکورد برد/باخت رو تو دیتابیس اصلی (bot.py) ثبت می‌کنه. ایمپورت رو داخل تابع
    نگه داشتیم تا با ایمپورت bot.py از games.py توی بالای فایل، سیکل ایجاد نشه."""
    try:
        import bot as _bot
        _bot._record_game_result(chat_id, winner_id, loser_id)
    except Exception:
        pass  # اگه ثبت رکورد شکست بخوره، نباید جلوی خود بازی رو بگیره


async def is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id, user_id) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except TelegramError:
        return False


# =========================================================
#  بازی‌های ساده (بدون نیاز به حریف)
# =========================================================

async def dice_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_dice(emoji="🎲")


async def coinflip_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = random.choice(["شیر 🪙", "خط 🪙"])
    await update.effective_message.reply_text(f"نتیجه: {result}")


async def trivia_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item = random.choice(TRIVIA_BANK)
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"trivia:{i}:{item['answer']}")]
        for i, opt in enumerate(item["options"])
    ]
    await update.effective_message.reply_text(
        f"❓ {item['q']}", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def trivia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, chosen, correct = query.data.split(":")
    await query.answer(
        "درست بود! ✅" if chosen == correct else "غلط بود ❌", show_alert=True
    )


async def riddle_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q, a = random.choice(RIDDLES)
    context.chat_data["last_riddle_answer"] = a
    await update.effective_message.reply_text(f"🧩 {q}\n(جواب رو تو چت بنویس)")


async def wyr_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"🤔 {random.choice(WYR_BANK)}")


async def rps_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [[
        InlineKeyboardButton("🪨 سنگ", callback_data="rps:rock"),
        InlineKeyboardButton("📄 کاغذ", callback_data="rps:paper"),
        InlineKeyboardButton("✂️ قیچی", callback_data="rps:scissors"),
    ]]
    await update.effective_message.reply_text(
        "یکی رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    choice = query.data.split(":")[1]
    bot_choice = random.choice(["rock", "paper", "scissors"])
    labels = {"rock": "سنگ 🪨", "paper": "کاغذ 📄", "scissors": "قیچی ✂️"}

    if choice == bot_choice:
        outcome = "مساوی شد 🤝"
    elif (choice, bot_choice) in [("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")]:
        outcome = "بردی! 🎉"
    else:
        outcome = "باختی 😅"

    await query.edit_message_text(
        f"تو: {labels[choice]}\nربات: {labels[bot_choice]}\n{outcome}"
    )
    await query.answer()


async def guess_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    GUESS_STATE[chat_id] = random.randint(1, 100)
    await update.effective_message.reply_text("عددی بین ۱ تا ۱۰۰ تو ذهنمه. حدس بزن!")


# =========================================================
#  دار (Hangman)
# =========================================================

def render_hangman(state):
    word = state["word"]
    guessed = state["guessed"]
    shown = " ".join(ch if ch in guessed else "_" for ch in word)
    return f"کلمه: {shown}\nاشتباه‌ها: {state['wrong']}/6\nحرف‌های گفته‌شده: {', '.join(sorted(guessed)) or '-'}"


async def hangman_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    word = random.choice(HANGMAN_WORDS)
    HANGMAN_STATE[chat_id] = {"word": word, "guessed": set(), "wrong": 0}
    await update.effective_message.reply_text(
        "بازی دار شروع شد! حرف حرف بنویس تا حدس بزنی.\n\n"
        + render_hangman(HANGMAN_STATE[chat_id])
    )


async def hangman_guess(update: Update, context: ContextTypes.DEFAULT_TYPE, letter: str):
    chat_id = update.effective_chat.id
    state = HANGMAN_STATE.get(chat_id)
    if not state:
        return False
    letter = letter.strip()
    if len(letter) != 1:
        return False

    if letter in state["word"]:
        state["guessed"].add(letter)
    else:
        state["wrong"] += 1
        state["guessed"].add(letter)

    if all(ch in state["guessed"] for ch in state["word"]):
        await update.effective_message.reply_text(f"🎉 درست حدس زدی! کلمه: {state['word']}")
        del HANGMAN_STATE[chat_id]
    elif state["wrong"] >= 6:
        await update.effective_message.reply_text(f"💀 باختی! کلمه: {state['word']}")
        del HANGMAN_STATE[chat_id]
    else:
        await update.effective_message.reply_text(render_hangman(state))
    return True


# =========================================================
#  رولت روسی (شبیه‌سازی متنی — بدون خشونت واقعی)
#  باخت = میوت موقت تو گروه (اگه ربات ادمین باشه)، وگرنه فقط پیام
# =========================================================

async def roulette_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text("این بازی فقط تو گروه کار می‌کنه.")
        return

    chat_id = chat.id
    if chat_id in ROULETTE_STATE:
        await update.effective_message.reply_text(
            "یه دور رولت الان تو همین گروه در جریانه! بنویس «شلیک» تا نوبت بگیری."
        )
        return

    ROULETTE_STATE[chat_id] = {
        "bullet": random.randint(1, 6),
        "chamber": 1,
    }
    await update.effective_message.reply_text(
        "🔫 اسلحه پر شد و خشابش چرخید...\n"
        "هرکی می‌خواد شانسش رو امتحان کنه بنویسه «شلیک».\n"
        "بازنده ۵ دقیقه تو گروه سکوت می‌کنه 😅"
    )


async def roulette_shoot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_id = chat.id
    state = ROULETTE_STATE.get(chat_id)
    if not state:
        await update.effective_message.reply_text(
            "الان بازی‌ای در جریان نیست. اول بنویس «رولت روسی» تا شروع بشه."
        )
        return

    user = update.effective_user
    current = state["chamber"]

    if current == state["bullet"]:
        del ROULETTE_STATE[chat_id]
        text = f"💥 بنگ! {user.first_name} باخت."
        try:
            until = int(datetime.now().timestamp()) + ROULETTE_MUTE_MINUTES * 60
            await context.bot.restrict_chat_member(
                chat_id, user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            text += f" ({ROULETTE_MUTE_MINUTES} دقیقه سکوت 🤐)"
        except TelegramError:
            text += " (نتونستم میوتش کنم، ربات باید ادمین باشه)"
        await update.effective_message.reply_text(text)
        return

    state["chamber"] += 1
    if state["chamber"] > 6:
        # کسی نبرد به گلوله، دور جدید با چرخش تازه شروع می‌شه
        del ROULETTE_STATE[chat_id]
        await update.effective_message.reply_text(
            f"🔄 {user.first_name} جون سالم به در برد و خشاب تموم شد. "
            "برای دور بعد دوباره بنویس «رولت روسی»."
        )
        return

    await update.effective_message.reply_text(
        f"😮‍💨 *کلیک*... {user.first_name} جون سالم به در برد. نفر بعدی؟"
    )


# =========================================================
# =========================================================
#  لابی مشترک برای بازی‌های دو نفره: به‌جای اجباری‌بودن ریپلای،
#  یه پیام با دکمه‌ی «بپیوند» می‌فرسته و هرکی زد می‌شه حریف.
#  (اگه پیام ریپلای به یه نفر باشه، طبق قبل مستقیم باهاش شروع می‌شه)
# =========================================================

LOBBIES = {}   # token -> {"game": "ttt"/"c4", "creator": User}


def _new_lobby_token(chat_id, creator_id):
    return f"{chat_id}_{creator_id}_{random.randint(100000, 999999)}"


def _lobby_markup(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🙋 بپیوند به بازی", callback_data=f"lobby:{token}")]])


async def lobby_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    token = query.data.split(":", 1)[1]
    lobby = LOBBIES.get(token)
    if not lobby:
        await query.answer("این دعوت منقضی شده یا یکی دیگه قبلاً پیوسته.", show_alert=True)
        return

    creator = lobby["creator"]
    joiner = query.from_user
    if joiner.id == creator.id:
        await query.answer("نمی‌تونی با خودت بازی کنی 🙂", show_alert=True)
        return

    del LOBBIES[token]
    game = lobby["game"]
    if game == "ttt":
        await _launch_tictactoe(query.message, creator, joiner, edit=True)
    elif game == "c4":
        await _launch_connect4(query.message, creator, joiner, edit=True)
    await query.answer()


# =========================================================
#  دوز (Tic-Tac-Toe) — یا روی پیام حریف ریپلای کن، یا با دکمه‌ی «بپیوند»
# =========================================================

def new_board():
    return [""] * 9


def board_markup(board, game_id):
    symbols = {"": "▫️", "X": "❌", "O": "⭕"}
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            row.append(InlineKeyboardButton(symbols[board[i]], callback_data=f"ttt:{game_id}:{i}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def check_winner(board):
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a, b, c in lines:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(board):
        return "draw"
    return None


TURN_TIMEOUT_SECONDS = 90  # اگه تو این مدت حرکت نکنی، خودکار می‌بازی


async def _tictactoe_timeout_watch(game_id, move_no, chat_id, message_id, bot):
    await asyncio.sleep(TURN_TIMEOUT_SECONDS)
    state = TICTACTOE_STATE.get(game_id)
    if not state or state.get("move_no") != move_no:
        return  # یا بازی تموم شده، یا یه حرکت دیگه زده شده
    loser_id = state["turn"]
    winner_id = [pid for pid in state["players"] if pid != loser_id][0]
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"⏰ وقت {state['names'][loser_id]} تموم شد! {state['names'][winner_id]} با نبود حریف برنده شد.",
        )
    except Exception:
        pass
    _save_game_record(chat_id, winner_id, loser_id)
    TICTACTOE_STATE.pop(game_id, None)


async def _launch_tictactoe(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}"
    TICTACTOE_STATE[game_id] = {
        "board": new_board(),
        "players": {p1.id: "X", p2.id: "O"},
        "turn": p1.id,
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
        "move_no": 0,
    }
    text = (
        f"دوز شروع شد: {p1.first_name} (❌) در برابر {p2.first_name} (⭕)\nنوبت: {p1.first_name}\n"
        f"⏰ {TURN_TIMEOUT_SECONDS} ثانیه وقت داری حرکت کنی، وگرنه می‌بازی."
    )
    markup = board_markup(TICTACTOE_STATE[game_id]["board"], game_id)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
        message_id = target_msg.message_id
    else:
        sent = await target_msg.reply_text(text, reply_markup=markup)
        message_id = sent.message_id
    asyncio.create_task(_tictactoe_timeout_watch(
        game_id, 0, target_msg.chat.id, message_id, target_msg.get_bot()
    ))


async def tictactoe_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    creator = update.effective_user

    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        p2 = msg.reply_to_message.from_user
        if p2.id == creator.id:
            await msg.reply_text("نمی‌تونی با خودت بازی کنی 🙂")
            return
        await _launch_tictactoe(msg, creator, p2)
        return

    token = _new_lobby_token(update.effective_chat.id, creator.id)
    LOBBIES[token] = {"game": "ttt", "creator": creator}
    await msg.reply_text(
        f"🎮 {creator.first_name} می‌خواد دوز بازی کنه!\nحریف، دکمه‌ی زیر رو بزن تا بازی شروع بشه.",
        reply_markup=_lobby_markup(token),
    )


async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, idx = query.data.split(":")
    idx = int(idx)
    state = TICTACTOE_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in state["players"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return
    if state["board"][idx]:
        await query.answer("این خونه پره.", show_alert=True)
        return

    state["board"][idx] = state["players"][user_id]
    winner = check_winner(state["board"])

    other_id = [pid for pid in state["players"] if pid != user_id][0]
    state["move_no"] += 1
    if winner == "draw":
        await query.edit_message_text("مساوی شد! 🤝", reply_markup=board_markup(state["board"], game_id))
        del TICTACTOE_STATE[game_id]
    elif winner:
        await query.edit_message_text(
            f"🏆 {state['names'][user_id]} برد!", reply_markup=board_markup(state["board"], game_id)
        )
        _save_game_record(query.message.chat.id, user_id, other_id)
        del TICTACTOE_STATE[game_id]
    else:
        state["turn"] = other_id
        await query.edit_message_text(
            f"نوبت: {state['names'][other_id]}\n⏰ {TURN_TIMEOUT_SECONDS} ثانیه وقت داری.",
            reply_markup=board_markup(state["board"], game_id),
        )
        asyncio.create_task(_tictactoe_timeout_watch(
            game_id, state["move_no"], query.message.chat.id, query.message.message_id, context.bot
        ))
    await query.answer()


# =========================================================
#  چهار در ردیف (Connect 4) — روی پیام حریف ریپلای کن
#  صفحه ۷ ستون × ۶ ردیف
# =========================================================

C4_COLS, C4_ROWS = 7, 6


def c4_new_board():
    return [["" for _ in range(C4_COLS)] for _ in range(C4_ROWS)]


def c4_markup(game_id):
    row = [InlineKeyboardButton(str(c + 1), callback_data=f"c4:{game_id}:{c}") for c in range(C4_COLS)]
    return InlineKeyboardMarkup([row])


def c4_render(board):
    symbols = {"": "⚪", "R": "🔴", "Y": "🟡"}
    return "\n".join("".join(symbols[cell] for cell in row) for row in board)


def c4_drop(board, col, symbol):
    for r in range(C4_ROWS - 1, -1, -1):
        if board[r][col] == "":
            board[r][col] = symbol
            return r
    return None


def c4_check_winner(board):
    for r in range(C4_ROWS):
        for c in range(C4_COLS):
            s = board[r][c]
            if not s:
                continue
            for dr, dc in [(0, 1), (1, 0), (1, 1), (1, -1)]:
                cells = [(r + dr * i, c + dc * i) for i in range(4)]
                if all(0 <= rr < C4_ROWS and 0 <= cc < C4_COLS and board[rr][cc] == s for rr, cc in cells):
                    return s
    if all(board[0][c] != "" for c in range(C4_COLS)):
        return "draw"
    return None


async def _connect4_timeout_watch(game_id, move_no, chat_id, message_id, bot):
    await asyncio.sleep(TURN_TIMEOUT_SECONDS)
    state = CONNECT4_STATE.get(game_id)
    if not state or state.get("move_no") != move_no:
        return
    loser_id = state["turn"]
    winner_id = [pid for pid in state["players"] if pid != loser_id][0]
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"⏰ وقت {state['names'][loser_id]} تموم شد! {state['names'][winner_id]} با نبود حریف برنده شد.",
        )
    except Exception:
        pass
    _save_game_record(chat_id, winner_id, loser_id)
    CONNECT4_STATE.pop(game_id, None)


async def _launch_connect4(target_msg, p1, p2, edit=False):
    game_id = f"{target_msg.chat.id}_{p1.id}_{p2.id}"
    CONNECT4_STATE[game_id] = {
        "board": c4_new_board(),
        "players": {p1.id: "R", p2.id: "Y"},
        "turn": p1.id,
        "names": {p1.id: p1.first_name, p2.id: p2.first_name},
        "move_no": 0,
    }
    text = (
        f"چهار در ردیف شروع شد: {p1.first_name} (🔴) در برابر {p2.first_name} (🟡)\n"
        f"نوبت: {p1.first_name}\n\n{c4_render(CONNECT4_STATE[game_id]['board'])}\n"
        f"⏰ {TURN_TIMEOUT_SECONDS} ثانیه وقت داری حرکت کنی، وگرنه می‌بازی."
    )
    markup = c4_markup(game_id)
    if edit:
        await target_msg.edit_text(text, reply_markup=markup)
        message_id = target_msg.message_id
    else:
        sent = await target_msg.reply_text(text, reply_markup=markup)
        message_id = sent.message_id
    asyncio.create_task(_connect4_timeout_watch(
        game_id, 0, target_msg.chat.id, message_id, target_msg.get_bot()
    ))


async def connect4_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    creator = update.effective_user

    if msg.reply_to_message and msg.reply_to_message.from_user and not msg.reply_to_message.from_user.is_bot:
        p2 = msg.reply_to_message.from_user
        if p2.id == creator.id:
            await msg.reply_text("نمی‌تونی با خودت بازی کنی 🙂")
            return
        await _launch_connect4(msg, creator, p2)
        return

    token = _new_lobby_token(update.effective_chat.id, creator.id)
    LOBBIES[token] = {"game": "c4", "creator": creator}
    await msg.reply_text(
        f"🎮 {creator.first_name} می‌خواد چهار در ردیف بازی کنه!\nحریف، دکمه‌ی زیر رو بزن تا بازی شروع بشه.",
        reply_markup=_lobby_markup(token),
    )


async def connect4_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, game_id, col = query.data.split(":")
    col = int(col)
    state = CONNECT4_STATE.get(game_id)
    if not state:
        await query.answer("این بازی تموم شده.", show_alert=True)
        return
    user_id = query.from_user.id
    if user_id not in state["players"]:
        await query.answer("تو تو این بازی نیستی.", show_alert=True)
        return
    if state["turn"] != user_id:
        await query.answer("نوبت تو نیست.", show_alert=True)
        return

    row = c4_drop(state["board"], col, state["players"][user_id])
    if row is None:
        await query.answer("این ستون پره.", show_alert=True)
        return

    winner_symbol = c4_check_winner(state["board"])
    board_text = c4_render(state["board"])
    other_id = [pid for pid in state["players"] if pid != user_id][0]
    state["move_no"] += 1

    if winner_symbol == "draw":
        await query.edit_message_text(f"مساوی شد! 🤝\n\n{board_text}", reply_markup=c4_markup(game_id))
        del CONNECT4_STATE[game_id]
    elif winner_symbol:
        await query.edit_message_text(
            f"🏆 {state['names'][user_id]} برد!\n\n{board_text}", reply_markup=c4_markup(game_id)
        )
        _save_game_record(query.message.chat.id, user_id, other_id)
        del CONNECT4_STATE[game_id]
    else:
        state["turn"] = other_id
        await query.edit_message_text(
            f"نوبت: {state['names'][other_id]}\n\n{board_text}\n⏰ {TURN_TIMEOUT_SECONDS} ثانیه وقت داری.",
            reply_markup=c4_markup(game_id),
        )
        asyncio.create_task(_connect4_timeout_watch(
            game_id, state["move_no"], query.message.chat.id, query.message.message_id, context.bot
        ))
    await query.answer()


# =========================================================
#  وردل فارسی — حدس کلمه با راهنمای رنگی
# =========================================================

def wordle_feedback(guess, target):
    result = []
    for i, ch in enumerate(guess):
        if i < len(target) and ch == target[i]:
            result.append("🟩")
        elif ch in target:
            result.append("🟨")
        else:
            result.append("⬛")
    return "".join(result)


async def wordle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    word = random.choice(WORDLE_WORDS)
    WORDLE_STATE[chat_id] = {"word": word, "tries": 0}
    await update.effective_message.reply_text(
        f"وردل شروع شد! یه کلمه‌ی {len(word)} حرفی حدس بزن (حداکثر ۶ بار)."
    )


async def wordle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE, guess: str):
    chat_id = update.effective_chat.id
    state = WORDLE_STATE.get(chat_id)
    if not state or len(guess) != len(state["word"]):
        return False

    state["tries"] += 1
    fb = wordle_feedback(guess, state["word"])
    if guess == state["word"]:
        await update.effective_message.reply_text(f"{fb}\n🎉 درست بود! کلمه: {state['word']}")
        del WORDLE_STATE[chat_id]
    elif state["tries"] >= 6:
        await update.effective_message.reply_text(f"{fb}\n💀 حدس‌ها تموم شد. کلمه: {state['word']}")
        del WORDLE_STATE[chat_id]
    else:
        await update.effective_message.reply_text(f"{fb}\nتلاش {state['tries']}/6")
    return True


# =========================================================
#  جدول کلمات (نسخه‌ی ساده: یک کلایو در هر دور)
# =========================================================

async def crossword_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    item = random.choice(CROSSWORD_BANK)
    CROSSWORD_STATE[chat_id] = {"answer": item["answer"]}
    await update.effective_message.reply_text(
        f"🧩 راهنما: {item['clue']}\nجواب رو مستقیم تو چت بنویس."
    )


async def crossword_check(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    chat_id = update.effective_chat.id
    state = CROSSWORD_STATE.get(chat_id)
    if not state:
        return False
    if text.strip() == state["answer"]:
        await update.effective_message.reply_text("✅ درست بود!")
        del CROSSWORD_STATE[chat_id]
        return True
    return False


# =========================================================
#  مسابقه‌ی سرعت تایپ
# =========================================================

async def typerace_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sentence = random.choice(TYPERACE_SENTENCES)
    TYPERACE_STATE[chat_id] = {"sentence": sentence, "start": datetime.now().timestamp(), "done": False}
    await update.effective_message.reply_text(
        f"⌨️ این جمله رو دقیقاً تایپ کن، هرکی اول بفرسته برنده‌ست:\n\n{sentence}"
    )


async def typerace_check(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    chat_id = update.effective_chat.id
    state = TYPERACE_STATE.get(chat_id)
    if not state or state["done"]:
        return False
    if text.strip() == state["sentence"]:
        elapsed = round(datetime.now().timestamp() - state["start"], 2)
        state["done"] = True
        del TYPERACE_STATE[chat_id]
        await update.effective_message.reply_text(
            f"🏆 {update.effective_user.first_name} برد! زمان: {elapsed} ثانیه"
        )
        return True
    return False


# =========================================================
#  مافیا (نسخه‌ی ساده: عضویت، شروع، افشای نقش‌ها به‌صورت خصوصی)
# =========================================================

async def mafia_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    state = MAFIA_STATE.setdefault(chat_id, {"players": {}, "roles": {}, "started": False})
    if state["started"]:
        await update.effective_message.reply_text("یه بازی مافیا الان در جریانه، صبر کن تموم بشه.")
        return
    if user.id in state["players"]:
        await update.effective_message.reply_text("قبلاً عضو شدی.")
        return
    state["players"][user.id] = user.first_name
    await update.effective_message.reply_text(
        f"✅ {user.first_name} به بازی مافیا اضافه شد. ({len(state['players'])} نفر)"
    )


async def mafia_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = MAFIA_STATE.get(chat_id)
    if not state or len(state["players"]) < 4:
        await update.effective_message.reply_text("حداقل ۴ نفر باید عضو بشن (بنویس «مافیا بازی»).")
        return

    count = min(len(state["players"]), max(MAFIA_ROLES_BY_COUNT.keys()))
    roles_template = MAFIA_ROLES_BY_COUNT.get(count, MAFIA_ROLES_BY_COUNT[4])
    player_ids = list(state["players"].keys())
    random.shuffle(player_ids)

    # اگه تعداد از roles_template بیشتره، بقیه شهروند می‌شن
    roles = list(roles_template) + ["شهروند"] * max(0, len(player_ids) - len(roles_template))
    random.shuffle(roles)

    state["roles"] = {}
    state["started"] = True
    for pid, role in zip(player_ids, roles):
        state["roles"][pid] = role
        try:
            await context.bot.send_message(pid, f"🎭 نقش تو تو بازی مافیا: {role}")
        except TelegramError:
            pass

    await update.effective_message.reply_text(
        f"🎬 بازی مافیا شروع شد با {len(player_ids)} نفر! نقش‌ها تو پی‌وی هرکس ارسال شد."
    )


async def mafia_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = MAFIA_STATE.pop(chat_id, None)
    if not state or not state.get("roles"):
        await update.effective_message.reply_text("بازی‌ای در جریان نبود.")
        return
    lines = [f"{name}: {state['roles'].get(pid, '?')}" for pid, name in state["players"].items()]
    await update.effective_message.reply_text("🎭 نقش‌ها فاش شد:\n" + "\n".join(lines))


# =========================================================
#  زنجیره کلمات / داستان گروهی
# =========================================================

async def wordchain_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    WORDCHAIN_STATE[chat_id] = []
    await update.effective_message.reply_text(
        "زنجیره کلمات شروع شد! هر کلمه باید با آخرین حرف کلمه‌ی قبلی شروع بشه. "
        "برای پایان بنویس «پایان زنجیره»."
    )


async def wordchain_word(update: Update, context: ContextTypes.DEFAULT_TYPE, word: str):
    chat_id = update.effective_chat.id
    chain = WORDCHAIN_STATE[chat_id]
    if chain and word[0] != chain[-1][-1]:
        await update.effective_message.reply_text(
            f"باید با حرف «{chain[-1][-1]}» شروع بشه!"
        )
        return
    chain.append(word)
    await update.effective_message.reply_text(f"✅ ثبت شد ({len(chain)} کلمه)")


async def wordchain_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chain = WORDCHAIN_STATE.pop(chat_id, [])
    await update.effective_message.reply_text(
        "زنجیره تموم شد:\n" + (" ← ".join(chain) if chain else "هیچی ثبت نشد.")
    )


async def story_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    STORY_STATE[chat_id] = []
    await update.effective_message.reply_text(
        "داستان‌نویسی گروهی شروع شد! هرکی یه جمله بنویسه به داستان اضافه میشه. "
        "برای پایان بنویس «پایان داستان»."
    )


async def story_line(update: Update, context: ContextTypes.DEFAULT_TYPE, line: str):
    chat_id = update.effective_chat.id
    STORY_STATE[chat_id].append(line)


async def story_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lines = STORY_STATE.pop(chat_id, [])
    await update.effective_message.reply_text(
        "📖 داستان کامل:\n\n" + ("\n".join(lines) if lines else "داستانی ثبت نشد.")
    )


# =========================================================
#  نمایش/پین لیست بازی‌ها
# =========================================================

async def games_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.effective_message.reply_text(GAMES_LIST_TEXT, parse_mode="Markdown")
    chat = update.effective_chat
    user = update.effective_user
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) and await is_admin(context, chat.id, user.id):
        try:
            await context.bot.pin_chat_message(chat.id, msg.message_id, disable_notification=True)
        except TelegramError:
            pass


# =========================================================
#  دیسپچر کلمه‌محور (به‌جای اسلش کامند)
# =========================================================

EXACT_TRIGGERS = {
    "سنگ کاغذ قیچی": rps_game,
    "تاس": dice_game,
    "شیر یا خط": coinflip_game,
    "کوییز": trivia_game,
    "معما": riddle_game,
    "ترجیح میدی": wyr_game,
    "حدس عدد": guess_start,
    "دار": hangman_start,
    "زنجیره کلمات": wordchain_start,
    "پایان زنجیره": wordchain_end,
    "داستان گروهی": story_start,
    "پایان داستان": story_end,
    "لیست بازی‌ها": games_list_cmd,
    "لیست بازی ها": games_list_cmd,
    "گیم": games_list_cmd,
    "رولت روسی": roulette_start,
    "شلیک": roulette_shoot,
    "وردل": wordle_start,
    "جدول کلمات": crossword_start,
    "مسابقه تایپ": typerace_start,
    "مافیا بازی": mafia_join,
    "مافیا شروع": mafia_start,
    "مافیا پایان": mafia_end,
}


GAME_TRIGGER_WORDS = set(EXACT_TRIGGERS.keys()) | {"دوز", "چهار در ردیف", GOTHAM_TTT_TRIGGER}


def is_game_text(chat_id, text: str) -> bool:
    """برمی‌گردونه True اگه این پیام قراره توسط سیستم بازی‌ها (کلمه‌ی شروع بازی یا
    حرکت داخل یه بازیِ فعال) مصرف بشه. bot.py قبل از فرستادن پاسخ هوش مصنوعی این رو
    چک می‌کنه تا رو کلمات/حرکت‌های بازی، یه پیام اضافه‌ی ناخواسته نفرسته -
    حتی وقتی پیام ریپلای به خود ربات باشه."""
    t = norm(text)
    if not t:
        return False
    if t in GAME_TRIGGER_WORDS:
        return True
    if chat_id in HANGMAN_STATE and len(t) == 1:
        return True
    if chat_id in WORDLE_STATE:
        return True
    if chat_id in CROSSWORD_STATE:
        return True
    if chat_id in TYPERACE_STATE:
        return True
    if chat_id in GUESS_STATE and t.isdigit():
        return True
    if chat_id in WORDCHAIN_STATE and " " not in t:
        return True
    if chat_id in STORY_STATE:
        return True
    return False


async def keyword_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = norm(update.effective_message.text)
    chat_id = update.effective_chat.id

    # 1) دوز - به کلمه‌ی "دوز" با ریپلای نیاز داره
    if text == "دوز":
        await tictactoe_start(update, context)
        return

    # 1b) دوز گاتهام - منوی انتخاب سایز برد (۳×۳ تا ۸×۸) و حریف (با دوست/با ربات)
    if text == GOTHAM_TTT_TRIGGER:
        await gotham_ttt_start(update, context)
        return

    if text == "چهار در ردیف":
        await connect4_start(update, context)
        return

    # 2) تطبیق دقیق روی کلمات ثابت
    handler = EXACT_TRIGGERS.get(text)
    if handler:
        await handler(update, context)
        return

    # 3) اگه بازی «دار» فعاله و پیام یک حرف تنهاست => حدس حرف
    if chat_id in HANGMAN_STATE and len(text) == 1:
        handled = await hangman_guess(update, context, text)
        if handled:
            return

    # 3b) اگه وردل فعاله
    if chat_id in WORDLE_STATE:
        handled = await wordle_guess(update, context, text)
        if handled:
            return

    # 3c) اگه جدول کلمات فعاله
    if chat_id in CROSSWORD_STATE:
        handled = await crossword_check(update, context, text)
        if handled:
            return

    # 3d) اگه مسابقه‌ی تایپ فعاله
    if chat_id in TYPERACE_STATE:
        handled = await typerace_check(update, context, text)
        if handled:
            return

    # 4) اگه بازی «حدس عدد» فعاله و پیام عدده => چک کن
    if chat_id in GUESS_STATE and text.isdigit():
        guess = int(text)
        target = GUESS_STATE[chat_id]
        if guess == target:
            await update.effective_message.reply_text("🎉 درست حدس زدی!")
            del GUESS_STATE[chat_id]
        elif guess < target:
            await update.effective_message.reply_text("بزرگ‌تره ⬆️")
        else:
            await update.effective_message.reply_text("کوچیک‌تره ⬇️")
        return

    # 5) اگه معما فعاله، جواب رو چک کن
    last_answer = context.chat_data.get("last_riddle_answer")
    if last_answer and text and last_answer in text:
        await update.effective_message.reply_text("✅ آفرین، درست بود!")
        context.chat_data.pop("last_riddle_answer", None)
        return

    # 6) اگه «زنجیره کلمات» فعاله و پیام یک کلمه‌ست
    if chat_id in WORDCHAIN_STATE and " " not in text and text:
        await wordchain_word(update, context, text)
        return

    # 7) اگه «داستان گروهی» فعاله، هر پیامی خط داستانه
    if chat_id in STORY_STATE and text:
        await story_line(update, context, text)
        return


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_games(app):
    # همه‌ی این‌ها باید group=1 بگیرن، وگرنه CallbackQueryHandler(button_handler) تو
    # bot.py (بدون pattern، تو group=0) جلوی اجراشون رو می‌گیره چون زودتر ثبت شده و
    # روی همه‌ی callback query ها match می‌شه.
    app.add_handler(CallbackQueryHandler(rps_callback, pattern=r"^rps:"), group=1)
    app.add_handler(CallbackQueryHandler(trivia_callback, pattern=r"^trivia:"), group=1)
    app.add_handler(CallbackQueryHandler(tictactoe_callback, pattern=r"^ttt:"), group=1)
    app.add_handler(CallbackQueryHandler(connect4_callback, pattern=r"^c4:"), group=1)
    app.add_handler(CallbackQueryHandler(lobby_join_callback, pattern=r"^lobby:"), group=1)
    # این باید بعد از هندلرهای دیگه‌ی متنیِ ربات اضافه بشه (اولویت پایین‌تر با group=1)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyword_router), group=1)
