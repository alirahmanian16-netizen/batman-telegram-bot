# -*- coding: utf-8 -*-
"""
card_room.py
================
🃏 Gotham Card Room — اتاق بازی‌های کارتی.

با زدن «پاسور» بازی مستقیم شروع نمی‌شه؛ اول منوی بازی‌های کارتی نمایش داده
می‌شه. بعد از انتخاب بازی، یه لابی با دکمه‌ی «پیوستن» ساخته می‌شه — دقیقاً
مثل بقیه‌ی بازی‌های دونفره‌ی ربات (بیلیارد و ...): سازنده نمی‌تونه رو دکمه‌ی
خودش بزنه، فقط یه نفر دیگه می‌تونه بپیونده و بازی شروع می‌شه.

پیاده‌سازی‌شده (کامل و قابل‌بازی — همه‌ی بازی‌های IMPLEMENTED_GAMES):
    🃏 جنگ (War)
    🃏 بیست‌ویک (21 — دو بازیکن مستقل، بدون دیلر)
    🃏 بلک‌جک (در برابر دیلر/ربات، قوانین استاندارد Blackjack)
    🃏 حکم (نسخه‌ی دونفره)
    🃏 هفت‌خبیث (۲ تا ۶ نفره)
    🃏 چهاربرگ
    🃏 پوکر (تگزاس هولدم دونفره، صرفاً امتیاز مجازی)
    🃏 رامی

این پاراگراف قبلاً می‌گفت هفت‌خبیث/چهاربرگ/پوکر/رامی «به‌زودی»ان؛ اون توضیح
قدیمی بود و با IMPLEMENTED_GAMES واقعیِ زیر همخونی نداشت — الان هر ۸ بازی
پیاده و قابل‌بازی‌ان.

معماری کارت‌های مخفی:
    چون بازی‌ها تو گروه اجرا می‌شن، دستِ هر بازیکن با دکمه‌ی «🃏 دست من»
    فقط به‌صورت یه popup خصوصی (answer(..., show_alert=True)) به همون بازیکن
    نشون داده می‌شه — این popup توسط تلگرام فقط برای کسی که زده قابل دیدنه،
    پس بازیکن دیگه نمی‌تونه دست حریف رو ببینه. این همون روشیه که group_rps.py
    برای مخفی نگه‌داشتن انتخاب سنگ/کاغذ/قیچی استفاده کرده.
"""

import random
import uuid
import logging
import itertools

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)


# =========================================================
#  ابزارهای مشترک: Deck / Shuffle / برچسب کارت
# =========================================================

SUITS = ["♠", "♥", "♦", "♣"]
RED_SUITS = {"♥", "♦"}
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}  # 2..14 (A=14) برای مقایسه‌ی ساده مثل جنگ/حکم
BJ_VALUE = {**{str(n): n for n in range(2, 11)}, "J": 10, "Q": 10, "K": 10, "A": 11}


def _new_deck():
    deck = [(r, s) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck


def _card_label(card):
    r, s = card
    return f"{r}{s}"


def _hand_label(cards):
    return " ".join(_card_label(c) for c in cards) if cards else "—"


def _gid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _name(user) -> str:
    return user.first_name or user.username or "بازیکن"


def _kw(pattern: str):
    return filters.Regex(rf"(?i)^\s*({pattern})\s*$")


# =========================================================
#  ابزار مشترک: Timeout / Forfeit / Rematch / ثبت امتیاز
# =========================================================
# این بخش برای همه‌ی بازی‌های کارتیِ دونفره (جنگ، بیست‌ویک، بلک‌جک، چهاربرگ،
# پوکر، رامی) به یه شکل استفاده می‌شه تا کد تکراری نشه.

TURN_TIMEOUT_SEC = 60  # هر بازیکن ۶۰ ثانیه برای نوبتش وقت داره


def _timeout_job_name(prefix: str, gid: str) -> str:
    return f"{prefix}_to_{gid}"


def _cancel_timeout(app, prefix: str, gid: str):
    if not getattr(app, "job_queue", None):
        return
    for job in app.job_queue.get_jobs_by_name(_timeout_job_name(prefix, gid)):
        job.schedule_removal()


def _schedule_timeout(app, prefix: str, gid: str, callback, seconds: int = TURN_TIMEOUT_SEC):
    _cancel_timeout(app, prefix, gid)
    if getattr(app, "job_queue", None):
        app.job_queue.run_once(callback, when=seconds, data={"gid": gid}, name=_timeout_job_name(prefix, gid))


def _record_result(chat_id, winner_id, loser_id):
    """برد/باخت رو تو سیستم امتیازدهیِ مشترکِ ربات ثبت می‌کنه (بدون ساختن سیستم جدید)."""
    if not chat_id or not winner_id or not loser_id:
        return
    try:
        import bot as _bot
        _bot._record_game_result(chat_id, winner_id, loser_id)
    except Exception as e:
        log.info(f"card_room: could not save game record (harmless): {e}")


REMATCH_STORE = {}  # token -> (chat_id, p1_user, p2_user, game_key)


def _store_rematch(chat_id, p1, p2, game_key) -> str:
    token = _gid("crrm")
    REMATCH_STORE[token] = (chat_id, p1, p2, game_key)
    return token


def _rematch_markup(token: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔁 بازی مجدد", callback_data=f"cr:rematch:{token}")]])


def _forfeit_row(prefix: str, gid: str):
    return [InlineKeyboardButton("🏳 انصراف", callback_data=f"{prefix}:forfeit:{gid}")]


async def _cr_rematch_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str):
    """دکمه‌ی مشترکِ «بازی مجدد» (cr:rematch:token) — بر اساس game_key ذخیره‌شده،
    بازی‌ی درست رو با همون دو بازیکن دوباره می‌سازه."""
    q = update.callback_query
    entry = REMATCH_STORE.pop(token, None)
    if not entry:
        await q.answer("این دکمه دیگه معتبر نیست.", show_alert=True); return
    chat_id, p1, p2, game_key = entry
    launcher = LAUNCHERS.get(game_key)
    if not launcher:
        await q.answer("این بازی دیگه در دسترس نیست.", show_alert=True); return
    await q.answer("🃏 بازی مجدد شروع شد!")
    await launcher(context, q.message, p1, p2)


# =========================================================
#  منوی اتاق پاسور
# =========================================================

CARD_ROOM_TEXT = (
    "🃏 *GOTHAM CARD ROOM*\n\n"
    "یه بازی رو انتخاب کن:"
)

CARD_GAMES_MENU = [
    ("hokm", "🃏 حکم"),
    ("haft", "🃏 هفت‌خبیث"),
    ("charbarg", "🃏 چهاربرگ"),
    ("war", "🃏 جنگ"),
    ("bj21", "🃏 بیست‌ویک"),
    ("poker", "🃏 پوکر"),
    ("rummy", "🃏 رامی"),
    ("blackjack", "🃏 بلک‌جک"),
]
CARD_GAME_LABELS = dict(CARD_GAMES_MENU)
IMPLEMENTED_GAMES = {"war", "bj21", "blackjack", "hokm", "haft", "charbarg", "poker", "rummy"}

# بازی‌های چندنفره (۲..۶ نفر) که لابی مخصوص خودشون رو دارن، نه لابی دونفره‌ی cr:join
MULTI_PLAYER_GAMES = {"haft": (2, 6)}

CARD_RULES_TEXT = (
    "📖 *قوانین اتاق پاسور*\n\n"
    "🃏 جنگ — هر بازیکن نصف دسته رو داره؛ هر دور بالاترین کارت رو رو می‌کنه، "
    "برنده هر دو کارت رو می‌بره. تساوی = جنگ (هرکی کارتِ جنگ نداشته باشه می‌بازه).\n\n"
    "🃏 بیست‌ویک — هر بازیکن مستقل Hit/Stand می‌زنه؛ هرکی به ۲۱ نزدیک‌تر باشه بدون "
    "رد شدن، می‌بره.\n\n"
    "🃏 بلک‌جک — در برابر دیلر (ربات)؛ دیلر تا ۱۷ می‌کشه. بلک‌جک (آس+۱۰) برنده‌ی فوریه.\n\n"
    "🃏 حکم (دونفره) — هرکی کارتِ اول بالاتر بیاد حکم (خال برتر) رو انتخاب می‌کنه؛ "
    "هر دست ۱۳ دست کوچیک بازی می‌شه، باید هم‌خال بازی کنی وگرنه می‌تونی حکم بزنی یا "
    "کارت بی‌ربط بندازی؛ هرکی ۷ دست کوچیک رو ببره برنده‌ست.\n\n"
    "🃏 هفت‌خبیث (۲ تا ۶ نفره) — یه کارتِ «خبیث» بی‌جفت تو بازیه. نوبتی از نفر بعدی یه "
    "کارت کور می‌کشی؛ هر جفتِ هم‌ارزش تو دستت رو خودکار دور می‌ندازی. هرکی دستش خالی شد "
    "برنده‌ست و از بازی بیرون میاد؛ آخرین نفری که کارتِ خبیث دستشه می‌بازه. دست هر بازیکن "
    "فقط تو چت خصوصی خودش نشون داده می‌شه.\n\n"
    "🃏 چهاربرگ — هر دور ۴ کارت رو زمینه؛ نوبتی یه کارت از دستت بازی می‌کنی: اگه "
    "هم‌ارزشِ یکی از کارت‌های زمین بود می‌تونی جفتش کنی و هر دو رو جمع کنی (امتیاز "
    "می‌گیری)، وگرنه کارتت میره رو زمین. آخرِ بازی هرکی کارت بیشتر جمع کرده برنده‌ست.\n\n"
    "🃏 پوکر (تگزاس هولدم، دونفره، صرفاً امتیاز مجازی) — هرکی دو کارت مخفی داره؛ "
    "Pre-Flop → Flop → Turn → River با Check/Fold/Call/Raise مجازی؛ آخرش با ۵ کارتِ "
    "روی زمین بهترین دستِ ۵تایی مشخص می‌شه.\n\n"
    "🃏 رامی — هر بازیکن ۱۰ کارت می‌گیره؛ هر نوبت یه کارت می‌کِشی (از دسته یا رو "
    "تخته) و یکی دور می‌ندازی؛ هرکی زودتر همه‌ی کارت‌هاش رو Set (سه‌تای هم‌ارزش) یا "
    "Run (سه‌تای پشت‌سرهمِ هم‌خال) کنه، دستش رو اعلام می‌کنه و برنده می‌شه.\n\n"
    "⚠️ پوکر، بیست‌ویک و بلک‌جک هیچ پول واقعی یا شرط‌بندی مالی ندارن؛ فقط امتیاز "
    "مجازیِ داخل بازی.\n\n"
    "⏱ همه‌ی بازی‌های دونفره ۶۰ ثانیه Timeout روی هر نوبت دارن؛ دیر کردن = حرکت "
    "خودکار (Stand/Fold/رد نوبت). دکمه‌ی «🏳 انصراف» هم همیشه هست."
)


def _card_room_markup():
    rows, row = [], []
    for key, label in CARD_GAMES_MENU:
        row.append(InlineKeyboardButton(label, callback_data=f"cr:pick:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⚡ بازی سریع", callback_data="cr:quick")])
    rows.append([InlineKeyboardButton("📖 قوانین", callback_data="cr:rules")])
    return InlineKeyboardMarkup(rows)


async def card_room_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        CARD_ROOM_TEXT, reply_markup=_card_room_markup(), parse_mode="Markdown"
    )


# =========================================================
#  لابی مشترک (پیوستن دو نفره) برای همه‌ی بازی‌های کارتی
# =========================================================

CARD_LOBBIES = {}  # token -> {"creator": user, "game": key}


def _lobby_markup(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ پیوستن به بازی", callback_data=f"cr:join:{token}")]])


async def _open_lobby(query_or_msg, creator, game_key, edit=False):
    label = CARD_GAME_LABELS.get(game_key, game_key)
    token = _gid("crlobby")
    CARD_LOBBIES[token] = {"creator": creator, "game": game_key}
    text = (
        f"🃏 GOTHAM CARD ROOM — {label}\n\n"
        f"👤 {_name(creator)} می‌خواد {label} بازی کنه!\n"
        f"⚔️ فقط یه بازیکن دیگه (نه خودِ سازنده) می‌تونه بپیونده."
    )
    if edit:
        # query_or_msg همیشه یه Message ـه (از q.message پاس داده می‌شه)، نه خودِ
        # CallbackQuery — و کلاس Message تو python-telegram-bot متد
        # edit_message_text نداره (فقط Bot و CallbackQuery دارن)، بلکه edit_text
        # داره. این دقیقاً همون AttributeError ای بود که باعث می‌شد لابیِ حکم/
        # هفت‌خبیث/... بعد از کلیک هیچ‌وقت ساخته نشه.
        await query_or_msg.edit_text(text, reply_markup=_lobby_markup(token))
    else:
        await query_or_msg.reply_text(text, reply_markup=_lobby_markup(token))


# =========================================================
#  لابی چندنفره (۲..۶ نفر) — برای بازی‌های کارتی گروهی مثل هفت‌خبیث
# =========================================================

MULTI_LOBBIES = {}  # token -> {"creator": user, "game": key, "players": {uid: user}, "order": [uid,...]}


def _multi_lobby_markup(token, game_key, joined_count):
    min_p, max_p = MULTI_PLAYER_GAMES[game_key]
    rows = [[InlineKeyboardButton("➕ پیوستن", callback_data=f"cr:mjoin:{token}")]]
    row2 = [InlineKeyboardButton("❌ لغو", callback_data=f"cr:mcancel:{token}")]
    if joined_count >= min_p:
        row2.insert(0, InlineKeyboardButton("🚀 شروع بازی", callback_data=f"cr:mstart:{token}"))
    rows.append(row2)
    return InlineKeyboardMarkup(rows)


def _multi_lobby_text(lobby, game_key):
    label = CARD_GAME_LABELS.get(game_key, game_key)
    min_p, max_p = MULTI_PLAYER_GAMES[game_key]
    names = "\n".join(f"👤 {_name(u)}" for u in lobby["order"] and [lobby["players"][uid] for uid in lobby["order"]] or [])
    return (
        f"🃏 GOTHAM CARD ROOM — {label}\n\n"
        f"👥 {len(lobby['order'])}/{max_p} نفر (حداقل {min_p} نفر لازمه)\n\n"
        f"{names}\n\n"
        "با «➕ پیوستن» وارد بازی شو. بعد از رسیدن به حداقل نفرات، سازنده می‌تونه "
        "«🚀 شروع بازی» رو بزنه.\n\n"
        "⚠️ توجه: کارت‌های دستت فقط تو چت خصوصی ربات نشونت داده می‌شه، پس مطمئن شو "
        "قبلاً /start رو تو چت خصوصی ربات زدی."
    )


async def _open_multi_lobby(query_or_msg, creator, game_key, edit=False):
    token = _gid("crmlobby")
    MULTI_LOBBIES[token] = {
        "creator": creator, "game": game_key,
        "players": {creator.id: creator}, "order": [creator.id],
    }
    lobby = MULTI_LOBBIES[token]
    text = _multi_lobby_text(lobby, game_key)
    markup = _multi_lobby_markup(token, game_key, len(lobby["order"]))
    if edit:
        # همون دلیل _open_lobby: query_or_msg یه Message ـه، edit_text درسته نه
        # edit_message_text.
        await query_or_msg.edit_text(text, reply_markup=markup)
    else:
        await query_or_msg.reply_text(text, reply_markup=markup)


async def _multi_lobby_callback(q, update, context, action, parts):
    token = parts[2]
    lobby = MULTI_LOBBIES.get(token)
    if not lobby:
        await q.answer("این لابی منقضی شده یا بازی شروع شده.", show_alert=True); return
    game_key = lobby["game"]
    min_p, max_p = MULTI_PLAYER_GAMES[game_key]
    user = update.effective_user

    if action == "mcancel":
        if user.id != lobby["creator"].id:
            await q.answer("فقط سازنده‌ی لابی می‌تونه لغوش کنه.", show_alert=True); return
        del MULTI_LOBBIES[token]
        await q.edit_message_text("❌ لابی لغو شد.")
        await q.answer(); return

    if action == "mjoin":
        if user.id in lobby["players"]:
            await q.answer("قبلاً پیوستی.", show_alert=True); return
        if len(lobby["order"]) >= max_p:
            await q.answer("لابی پره.", show_alert=True); return
        lobby["players"][user.id] = user
        lobby["order"].append(user.id)
        await q.edit_message_text(
            _multi_lobby_text(lobby, game_key),
            reply_markup=_multi_lobby_markup(token, game_key, len(lobby["order"])),
        )
        await q.answer("پیوستی! 🃏"); return

    if action == "mstart":
        if user.id != lobby["creator"].id:
            await q.answer("فقط سازنده‌ی لابی می‌تونه بازی رو شروع کنه.", show_alert=True); return
        if len(lobby["order"]) < min_p:
            await q.answer(f"حداقل {min_p} بازیکن لازمه.", show_alert=True); return
        del MULTI_LOBBIES[token]
        players = [lobby["players"][uid] for uid in lobby["order"]]
        await q.answer()
        await MULTI_LAUNCHERS[game_key](context, q.message, players)
        return

    await q.answer()


# =========================================================
#  چت خصوصی برای کارت‌های مخفی — معماری مشترک بازی‌های کارتی چندنفره
# =========================================================
#
# چون تلگرام هیچ راهی برای «نشون دادن یه چیز فقط به یه نفر تو گروه» نداره،
# دستِ هر بازیکن باید تو چت خصوصی (Private Chat) با خودِ ربات فرستاده بشه.
# این بخش سه تا مسئولیت داره:
#   1) فرستادن/آپدیت‌کردن پیامِ دستِ هر بازیکن تو PV خودش (send_or_update_hand)
#   2) اگه بازیکن هنوز /start رو تو PV نزده، به‌جای Crash کردن، پیام گروه رو
#      با راهنما و دکمه‌ی دیپ‌لینک به PV ربات آپدیت می‌کنه و بازی رو مکث می‌کنه
#      (PRIVATE_PENDING) تا وقتی بازیکن /start بزنه.
#   3) وقتی بازیکن /start می‌زنه (با دیپ‌لینک cardhand)، بوت.پی این ماژول رو
#      صدا می‌زنه (try_resume_after_start) تا دستش رو بفرسته و بازی ادامه پیدا کنه.

PRIVATE_PENDING = {}  # user_id -> set of gid هایی که منتظر Start کردنِ این کاربرن


async def _deliver_hand(context, gid, uid, text, markup=None):
    """پیام دستِ بازیکن رو تو PV می‌فرسته یا آپدیت می‌کنه. اگه بازیکن هنوز با ربات
    PV نزده (Forbidden)، uid رو تو PRIVATE_PENDING علامت می‌زنه و False برمی‌گردونه."""
    try:
        await context.bot.send_message(chat_id=uid, text=text, reply_markup=markup)
        PRIVATE_PENDING.setdefault(uid, set()).discard(gid)
        return True
    except Exception as e:
        log.info(f"card_room: could not DM {uid} (probably hasn't started bot): {e}")
        PRIVATE_PENDING.setdefault(uid, set()).add(gid)
        return False


async def _private_hint_markup(context):
    try:
        me = await context.bot.get_me()
        url = f"https://t.me/{me.username}?start=cardhand"
        return InlineKeyboardMarkup([[InlineKeyboardButton("👉 استارت زدن به ربات", url=url)]])
    except Exception:
        return None


async def try_resume_after_start(update, context):
    """از bot.py صدا زده می‌شه وقتی کاربر /start رو با دیپ‌لینک cardhand زده.
    اگه این کاربر تو یه بازی کارتی منتظر Start بوده، الان دستش رو می‌فرستیم و
    اگه همه منتظر بودن، بازی رو ادامه می‌دیم."""
    uid = update.effective_user.id
    pending_gids = list(PRIVATE_PENDING.get(uid, set()))
    if not pending_gids:
        return
    for gid in pending_gids:
        if gid in HAFT_GAMES:
            await _haft_resume_player(context, gid, uid)
        elif gid in RUMMY_GAMES:
            ok = await _rummy_send_hand(context, gid, uid,
                                         phase="discard" if RUMMY_GAMES[gid]["order"][RUMMY_GAMES[gid]["turn"]] == uid and RUMMY_GAMES[gid]["phase"] == "discard" else "draw")
            if ok:
                PRIVATE_PENDING.get(uid, set()).discard(gid)
        elif gid in POKER_GAMES:
            await _poker_send_holecards(context, gid, uid)
            PRIVATE_PENDING.get(uid, set()).discard(gid)
        else:
            PRIVATE_PENDING.get(uid, set()).discard(gid)


def active_card_games_for_user(user_id):
    """🎮 بازی‌های فعال من (Phase 5) — بازی‌های کارتیِ فعلاً بازِ این کاربر رو
    برمی‌گردونه: [(label, chat_id, opponent_name, my_turn_bool), ...].
    دورهم‌جمع (ttt_gotham.GTTT_GAMES) عمداً اینجا نیست چون اصلاً chat_id تو
    state‌ش ذخیره نمی‌کنه — امن نیست حدس زده بشه؛ تو گزارش نهایی هم گفته شده."""
    results = []
    for game_key, games_dict in (
        ("war", WAR_GAMES), ("bj21", BJ21_GAMES), ("blackjack", BLACKJACK_GAMES),
        ("charbarg", CHARBARG_GAMES), ("rummy", RUMMY_GAMES), ("poker", POKER_GAMES),
        ("hokm", HOKM_GAMES),
    ):
        for gid, state in games_dict.items():
            if user_id not in state.get("players", {}):
                continue
            order = state.get("order", [])
            opp_id = next((u for u in order if u != user_id), None)
            opp_name = state.get("names", {}).get(opp_id, "؟") if opp_id else "؟"
            my_turn = bool(order) and "turn" in state and 0 <= state["turn"] < len(order) and order[state["turn"]] == user_id
            results.append((CARD_GAME_LABELS.get(game_key, game_key), state.get("chat_id"), opp_name, my_turn))

    for gid, state in HAFT_GAMES.items():
        if user_id not in state.get("order", []):
            continue
        others = [state["names"][u] for u in state.get("order", []) if u != user_id]
        results.append((CARD_GAME_LABELS.get("haft", "haft"), state.get("chat_id"), "، ".join(others) or "؟", None))

    return results


def _safe_game_callback(fn):
    """دور هر Callback بازیِ کارتی رو می‌گیره تا:
    ۱) اگه Exception ای رخ داد، هیچ‌وقت بی‌صدا گم نشه — لاگ کامل با traceback ثبت می‌شه.
    ۲) کاربر همیشه یه پاسخ ببینه (نه یه Spinner که فقط خودش تایم‌اوت می‌شه) —
       یه Alert کوتاه «❌ بازی با خطا مواجه شد» نشونش می‌دیم.
    ۳) بعدش Exception رو دوباره raise می‌کنیم تا global_error_handler (تو bot.py،
       که همین الان به OWNER_ID پیام می‌ده) هم طبق روال عادی خودش خبردار بشه.
    این یعنی دفعه‌ی بعد که یه بازی «هیچ واکنشی نشون نده»، یا خطا لاگ/به Owner
    گزارش می‌شه، یا اگه واقعاً هیچ Exception ای رخ نده، معلوم می‌شه مشکل اصلاً
    اینجا (تو خودِ این Handler) نیست، جای دیگه‌ست.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        try:
            return await fn(update, context)
        except Exception as e:
            log.exception(f"card_room: خطای پیش‌بینی‌نشده تو {fn.__name__} (data={getattr(q, 'data', '?')})")
            try:
                await q.answer("❌ بازی با خطا مواجه شد. دوباره امتحان کن.", show_alert=True)
            except Exception:
                pass
            raise
    wrapper.__name__ = fn.__name__
    return wrapper


@_safe_game_callback
async def card_room_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]

    if action == "rules":
        await q.edit_message_text(CARD_RULES_TEXT, reply_markup=_card_room_markup(), parse_mode="Markdown")
        await q.answer(); return

    if action == "quick":
        # ⚡ بازی سریع (Phase 5): طبق اولویت مشخصات — ۱) دونفره ۲) سریع ۳) کارتی.
        # قبلاً بین هر ۸ بازی (even رامی/پوکر/حکم که کند و پرمرحله‌ن) رندوم
        # انتخاب می‌شد؛ الان فقط از بین بازی‌های واقعاً دونفره‌ی سریع انتخاب می‌شه.
        quick_pool = [g for g in ("war", "bj21") if g in IMPLEMENTED_GAMES] or list(IMPLEMENTED_GAMES)
        game_key = random.choice(quick_pool)
        await _open_lobby(q.message, update.effective_user, game_key, edit=True)
        await q.answer(f"⚡ {CARD_GAME_LABELS[game_key]} انتخاب شد!"); return

    if action == "pick":
        game_key = parts[2]
        if game_key not in IMPLEMENTED_GAMES:
            await q.answer("این بازی به‌زودی اضافه می‌شه 🚧", show_alert=True); return
        if game_key in MULTI_PLAYER_GAMES:
            await _open_multi_lobby(q.message, update.effective_user, game_key, edit=True)
            await q.answer(); return
        await _open_lobby(q.message, update.effective_user, game_key, edit=True)
        await q.answer(); return

    if action == "join":
        token = parts[2]
        lobby = CARD_LOBBIES.get(token)
        if not lobby:
            await q.answer("این دعوت منقضی شده یا یکی دیگه قبلاً پیوسته.", show_alert=True); return
        creator = lobby["creator"]; joiner = update.effective_user
        if joiner.id == creator.id:
            await q.answer("نمی‌تونی با خودت بازی کنی 🙂", show_alert=True); return
        del CARD_LOBBIES[token]
        game_key = lobby["game"]
        await LAUNCHERS[game_key](context, q.message, creator, joiner)
        await q.answer(); return

    if action in ("mjoin", "mstart", "mcancel"):
        await _multi_lobby_callback(q, update, context, action, parts)
        return

    if action == "rematch":
        token = parts[2]
        await _cr_rematch_dispatch(update, context, token)
        return

    await q.answer()


# =========================================================
#  🃏 جنگ (War)
# =========================================================

WAR_GAMES = {}


def _war_text(state, note=""):
    p1, p2 = state["order"]
    n1, n2 = state["names"][p1], state["names"][p2]
    lines = [
        "🃏 GOTHAM WAR",
        f"👤 {n1}: {len(state['piles'][p1])} کارت   |   👤 {n2}: {len(state['piles'][p2])} کارت",
    ]
    if state.get("last"):
        c1, c2 = state["last"]
        lines.append(f"\n{n1}: {_card_label(c1)}      {n2}: {_card_label(c2)}")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def _war_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 دور بعد", callback_data=f"war:go:{gid}")],
        _forfeit_row("war", gid),
    ])


async def _launch_war(context, target_msg, p1, p2):
    gid = _gid("war")
    deck = _new_deck()
    half = len(deck) // 2
    WAR_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id],
        "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "piles": {p1.id: deck[:half], p2.id: deck[half:]},
        "last": None,
    }
    msg = await target_msg.edit_text(_war_text(WAR_GAMES[gid]), reply_markup=_war_markup(gid))
    WAR_GAMES[gid]["message_id"] = msg.message_id
    _schedule_timeout(context.application, "war", gid, _war_timeout)


def _war_finish(state, gid, champ, loser, extra_note=""):
    _record_result(state["chat_id"], champ, loser)
    token = _store_rematch(state["chat_id"], state["players"][state["order"][0]],
                            state["players"][state["order"][1]], "war")
    text = _war_text(state, extra_note + f"\n\n🏆🏆 {state['names'][champ]} کل بازی رو برد!")
    del WAR_GAMES[gid]
    return text, _rematch_markup(token)


async def _war_play_round(context, gid, q=None, auto=False):
    """یه دورِ جنگ رو اجرا می‌کنه. یا از callback (q) یا از Timeout (auto=True) صدا زده می‌شه."""
    state = WAR_GAMES.get(gid)
    if not state:
        return
    p1, p2 = state["order"]
    pot = []

    def _flip_pair():
        c1 = state["piles"][p1].pop(0)
        c2 = state["piles"][p2].pop(0)
        pot.extend([c1, c2])
        return c1, c2

    note = "⏱️ کسی دکمه رو نزد — دور خودکار اجرا شد.\n" if auto else ""

    async def _render(text, markup=None):
        try:
            if q is not None:
                await q.edit_message_text(text, reply_markup=markup)
            else:
                await context.bot.edit_message_text(
                    chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup,
                )
        except Exception as e:
            log.info(f"war: render failed (harmless): {e}")

    if not state["piles"][p1] or not state["piles"][p2]:
        loser = p1 if not state["piles"][p1] else p2
        champ = p2 if loser == p1 else p1
        text, markup = _war_finish(state, gid, champ, loser, note)
        await _render(text, markup)
        return

    c1, c2 = _flip_pair()
    while RANK_VALUE[c1[0]] == RANK_VALUE[c2[0]]:
        note += f"⚔️ تساوی ({_card_label(c1)} = {_card_label(c2)}) — جنگ!\n"
        if len(state["piles"][p1]) < 2 or len(state["piles"][p2]) < 2:
            loser = p1 if len(state["piles"][p1]) < 2 else p2
            champ = p2 if loser == p1 else p1
            state["piles"][champ].extend(pot)
            text, markup = _war_finish(state, gid, champ, loser, note)
            await _render(text, markup)
            return
        burn1 = state["piles"][p1].pop(0); burn2 = state["piles"][p2].pop(0)
        pot.extend([burn1, burn2])
        c1, c2 = _flip_pair()

    winner = p1 if RANK_VALUE[c1[0]] > RANK_VALUE[c2[0]] else p2
    state["piles"][winner].extend(pot)
    state["last"] = (c1, c2)
    note += f"🏆 {state['names'][winner]} این دور رو برد و {len(pot)} کارت گرفت."

    if not state["piles"][p1] or not state["piles"][p2]:
        loser = p1 if not state["piles"][p1] else p2
        champ = p2 if loser == p1 else p1
        text, markup = _war_finish(state, gid, champ, loser, note)
        await _render(text, markup)
        return

    await _render(_war_text(state, note), _war_markup(gid))
    _schedule_timeout(context.application, "war", gid, _war_timeout)


async def _war_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    if gid in WAR_GAMES:
        await _war_play_round(context, gid, q=None, auto=True)


@_safe_game_callback
async def war_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]

    state = WAR_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    p1, p2 = state["order"]
    if uid not in (p1, p2):
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "forfeit":
        _cancel_timeout(context.application, "war", gid)
        winner = p2 if uid == p1 else p1
        text, markup = _war_finish(state, gid, winner, uid, f"🏳 {state['names'][uid]} از بازی انصراف داد.")
        await q.edit_message_text(text, reply_markup=markup)
        await q.answer(); return

    if action == "go":
        await q.answer()
        await _war_play_round(context, gid, q=q, auto=False)
        return

    await q.answer()


# =========================================================
#  🃏 بیست‌ویک (بدون دیلر، دو بازیکن مستقل)
# =========================================================

BJ21_GAMES = {}


def _hand_value_21(cards):
    total = sum(BJ_VALUE[r] for r, _ in cards)
    aces = sum(1 for r, _ in cards if r == "A")
    while total > 21 and aces:
        total -= 10; aces -= 1
    return total


def _bj_text(state, header="🃏 بیست‌ویک گاتهام"):
    lines = [header, ""]
    for uid in state["order"]:
        st = state["status"][uid]
        val = _hand_value_21(state["hands"][uid])
        tag = {"playing": "🎯", "stand": "✋", "bust": "💥"}[st]
        lines.append(f"{tag} {state['names'][uid]}: {_hand_label(state['hands'][uid])}  (مجموع {val})")
    if state["order"] and state["status"][state["order"][state["turn"]]] == "playing":
        lines.append(f"\n🎲 نوبت: {state['names'][state['order'][state['turn']]]}")
    return "\n".join(lines)


def _bj_markup(gid, state, dealer=False):
    if not dealer:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🃏 Hit", callback_data=f"bj21:hit:{gid}"),
             InlineKeyboardButton("✋ Stand", callback_data=f"bj21:stand:{gid}")],
            _forfeit_row("bj21", gid),
        ])
    return None


def _bj_advance_turn(state):
    n = len(state["order"])
    for _ in range(n):
        state["turn"] = (state["turn"] + 1) % n
        if state["status"][state["order"][state["turn"]]] == "playing":
            return True
    return False


def _bj_finish(state):
    lines = ["🏁 پایان بیست‌ویک", ""]
    best_uid, best_val = None, -1
    tie = False
    for uid in state["order"]:
        val = _hand_value_21(state["hands"][uid])
        bust = state["status"][uid] == "bust"
        lines.append(f"👤 {state['names'][uid]}: {_hand_label(state['hands'][uid])} = {val}{' (رد شد 💥)' if bust else ''}")
        if not bust:
            if val > best_val:
                best_val, best_uid, tie = val, uid, False
            elif val == best_val:
                tie = True
    if best_uid and not tie:
        lines.append(f"\n🏆 برنده: {state['names'][best_uid]}")
        loser = [u for u in state["order"] if u != best_uid][0]
        _record_result(state["chat_id"], best_uid, loser)
    else:
        lines.append("\n🤝 هر دو رد شدن یا مساوی — بدون برنده.")
    p1, p2 = state["order"]
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "bj21")
    return "\n".join(lines), _rematch_markup(token)


async def _launch_bj21(context, target_msg, p1, p2):
    gid = _gid("bj21")
    deck = _new_deck()
    hands = {p1.id: [deck.pop(), deck.pop()], p2.id: [deck.pop(), deck.pop()]}
    BJ21_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id], "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "deck": deck, "hands": hands,
        "status": {p1.id: "playing", p2.id: "playing"}, "turn": 0,
    }
    state = BJ21_GAMES[gid]
    msg = await target_msg.edit_text(_bj_text(state), reply_markup=_bj_markup(gid, state))
    state["message_id"] = msg.message_id
    _schedule_timeout(context.application, "bj21", gid, _bj21_timeout)


async def _bj21_finish_and_render(context, gid, state, q=None):
    text, markup = _bj_finish(state)
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=markup)
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
    except Exception as e:
        log.info(f"bj21: finish render failed (harmless): {e}")
    del BJ21_GAMES[gid]


async def _bj21_render_turn(context, gid, state, note="", q=None):
    text = _bj_text(state)
    if note:
        text += f"\n\n{note}"
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=_bj_markup(gid, state))
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=_bj_markup(gid, state))
    except Exception as e:
        log.info(f"bj21: turn render failed (harmless): {e}")
    _schedule_timeout(context.application, "bj21", gid, _bj21_timeout)


async def _bj21_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = BJ21_GAMES.get(gid)
    if not state:
        return
    uid = state["order"][state["turn"]]
    state["status"][uid] = "stand"  # عدم اقدام = Stand خودکار
    has_more = _bj_advance_turn(state)
    if not has_more:
        await _bj21_finish_and_render(context, gid, state)
        return
    await _bj21_render_turn(context, gid, state, note=f"⏱️ {state['names'][uid]} دیر کرد — خودکار Stand شد.")


@_safe_game_callback
async def bj21_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, gid = q.data.split(":")[1], q.data.split(":")[2]
    state = BJ21_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    if uid not in state["order"]:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "forfeit":
        _cancel_timeout(context.application, "bj21", gid)
        winner = [u for u in state["order"] if u != uid][0]
        _record_result(state["chat_id"], winner, uid)
        p1, p2 = state["order"]
        token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "bj21")
        await q.edit_message_text(
            f"🏳 {state['names'][uid]} از بیست‌ویک انصراف داد.\n🏆 برنده: {state['names'][winner]}",
            reply_markup=_rematch_markup(token),
        )
        del BJ21_GAMES[gid]; await q.answer(); return

    if state["order"][state["turn"]] != uid:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    if action == "hit":
        _cancel_timeout(context.application, "bj21", gid)
        state["hands"][uid].append(state["deck"].pop())
        if _hand_value_21(state["hands"][uid]) > 21:
            state["status"][uid] = "bust"
            has_more = _bj_advance_turn(state)
            if not has_more:
                await _bj21_finish_and_render(context, gid, state, q=q); await q.answer(); return
            await _bj21_render_turn(context, gid, state, q=q); await q.answer(); return
        _schedule_timeout(context.application, "bj21", gid, _bj21_timeout)
        await q.edit_message_text(_bj_text(state), reply_markup=_bj_markup(gid, state)); await q.answer(); return

    if action == "stand":
        _cancel_timeout(context.application, "bj21", gid)
        state["status"][uid] = "stand"
        has_more = _bj_advance_turn(state)
        if not has_more:
            await _bj21_finish_and_render(context, gid, state, q=q); await q.answer(); return
        await _bj21_render_turn(context, gid, state, q=q); await q.answer(); return


# =========================================================
#  🃏 بلک‌جک (در برابر دیلر/ربات)
# =========================================================

BLACKJACK_GAMES = {}
DEALER_UID = -1


def _bj2_text(state):
    lines = ["🂡 GOTHAM BLACKJACK", ""]
    for uid in state["order"]:
        st = state["status"][uid]
        val = _hand_value_21(state["hands"][uid])
        tag = {"playing": "🎯", "stand": "✋", "bust": "💥", "blackjack": "🂡"}[st]
        lines.append(f"{tag} {state['names'][uid]}: {_hand_label(state['hands'][uid])}  (مجموع {val})")
    dealer_hand = state["dealer"]
    if state["dealer_hidden"]:
        lines.append(f"\n🤖 دیلر: {_card_label(dealer_hand[0])} + 🂠")
    else:
        lines.append(f"\n🤖 دیلر: {_hand_label(dealer_hand)}  (مجموع {_hand_value_21(dealer_hand)})")
    active = [u for u in state["order"] if state["status"][u] == "playing"]
    if active:
        lines.append(f"\n🎲 نوبت: {state['names'][active[0]]}")
    return "\n".join(lines)


def _bj2_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 Hit", callback_data=f"bjd:hit:{gid}"),
         InlineKeyboardButton("✋ Stand", callback_data=f"bjd:stand:{gid}"),
         InlineKeyboardButton("💰 Double", callback_data=f"bjd:double:{gid}")],
        _forfeit_row("bjd", gid),
    ])


def _bj2_active_uid(state):
    for u in state["order"]:
        if state["status"][u] == "playing":
            return u
    return None


def _bj2_result_text(state):
    state["dealer_hidden"] = False
    dealer = state["dealer"]
    while _hand_value_21(dealer) < 17:
        dealer.append(state["deck"].pop())
    dealer_val = _hand_value_21(dealer)
    dealer_bust = dealer_val > 21
    lines = ["🏁 پایان بلک‌جک", "", f"🤖 دیلر: {_hand_label(dealer)} = {dealer_val}{' (رد شد 💥)' if dealer_bust else ''}", ""]
    winners, losers = [], []
    for uid in state["order"]:
        pval = _hand_value_21(state["hands"][uid])
        st = state["status"][uid]
        if st == "bust":
            result = "باخت 💥"; losers.append(uid)
        elif st == "blackjack" and dealer_val == 21 and len(dealer) == 2:
            result = "مساوی (هر دو بلک‌جک) 🤝"
        elif st == "blackjack":
            result = "بلک‌جک! برد 🏆"; winners.append(uid)
        elif dealer_bust:
            result = "برد (دیلر رد شد) 🏆"; winners.append(uid)
        elif pval > dealer_val:
            result = "برد 🏆"; winners.append(uid)
        elif pval == dealer_val:
            result = "مساوی 🤝"
        else:
            result = "باخت 💥"; losers.append(uid)
        lines.append(f"👤 {state['names'][uid]}: {_hand_label(state['hands'][uid])} = {pval} → {result}")
    # هر دو بازیکن جدا از دیلر بازی می‌کنن، پس امتیاز رو نسبت به دیلر (نه رقیب) ثبت می‌کنیم:
    # اگه یکی برد و اون یکی باخت، برد/باخت رودررو هم ثبت می‌شه؛ در غیر این صورت رکوردی ثبت نمی‌شه.
    if len(winners) == 1 and len(losers) == 1:
        _record_result(state["chat_id"], winners[0], losers[0])
    p1, p2 = state["order"]
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "blackjack")
    return "\n".join(lines), _rematch_markup(token)


async def _launch_blackjack(context, target_msg, p1, p2):
    gid = _gid("bjd")
    deck = _new_deck()
    hands = {p1.id: [deck.pop(), deck.pop()], p2.id: [deck.pop(), deck.pop()]}
    dealer = [deck.pop(), deck.pop()]
    status = {}
    for uid, u in ((p1.id, p1), (p2.id, p2)):
        status[uid] = "blackjack" if _hand_value_21(hands[uid]) == 21 else "playing"
    BLACKJACK_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id], "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "deck": deck, "hands": hands, "status": status,
        "dealer": dealer, "dealer_hidden": True, "doubled": set(),
    }
    state = BLACKJACK_GAMES[gid]
    if _bj2_active_uid(state) is None:
        # هر دو همون اول بلک‌جک زدن؛ مستقیم دیلر بازی می‌کنه، نوبتی برای هیچ‌کس نیست
        text, markup = _bj2_result_text(state)
        await target_msg.edit_text(text, reply_markup=markup)
        del BLACKJACK_GAMES[gid]
        return
    msg = await target_msg.edit_text(_bj2_text(state), reply_markup=_bj2_markup(gid))
    state["message_id"] = msg.message_id
    _schedule_timeout(context.application, "bjd", gid, _blackjack_timeout)


async def _blackjack_finish_and_render(context, gid, state, q=None):
    text, markup = _bj2_result_text(state)
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=markup)
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
    except Exception as e:
        log.info(f"blackjack: finish render failed (harmless): {e}")
    del BLACKJACK_GAMES[gid]


async def _blackjack_render_turn(context, gid, state, note="", q=None):
    text = _bj2_text(state)
    if note:
        text += f"\n\n{note}"
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=_bj2_markup(gid))
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=_bj2_markup(gid))
    except Exception as e:
        log.info(f"blackjack: turn render failed (harmless): {e}")
    _schedule_timeout(context.application, "bjd", gid, _blackjack_timeout)


async def _blackjack_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = BLACKJACK_GAMES.get(gid)
    if not state:
        return
    uid = _bj2_active_uid(state)
    if uid is None:
        return
    state["status"][uid] = "stand"  # عدم اقدام = Stand خودکار
    nxt = _bj2_active_uid(state)
    if nxt is None:
        await _blackjack_finish_and_render(context, gid, state)
        return
    await _blackjack_render_turn(context, gid, state, note=f"⏱️ {state['names'][uid]} دیر کرد — خودکار Stand شد.")


@_safe_game_callback
async def blackjack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    action, gid = q.data.split(":")[1], q.data.split(":")[2]
    state = BLACKJACK_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    if uid not in state["order"]:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "forfeit":
        _cancel_timeout(context.application, "bjd", gid)
        winner = [u for u in state["order"] if u != uid][0]
        _record_result(state["chat_id"], winner, uid)
        p1, p2 = state["order"]
        token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "blackjack")
        await q.edit_message_text(
            f"🏳 {state['names'][uid]} از بلک‌جک انصراف داد.\n🏆 برنده: {state['names'][winner]}",
            reply_markup=_rematch_markup(token),
        )
        del BLACKJACK_GAMES[gid]; await q.answer(); return

    active = _bj2_active_uid(state)
    if active != uid:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    if action == "hit":
        _cancel_timeout(context.application, "bjd", gid)
        state["hands"][uid].append(state["deck"].pop())
        if _hand_value_21(state["hands"][uid]) > 21:
            state["status"][uid] = "bust"
        nxt = _bj2_active_uid(state)
        if nxt is None:
            await _blackjack_finish_and_render(context, gid, state, q=q); await q.answer(); return
        await _blackjack_render_turn(context, gid, state, q=q); await q.answer(); return

    if action == "stand":
        _cancel_timeout(context.application, "bjd", gid)
        state["status"][uid] = "stand"
        nxt = _bj2_active_uid(state)
        if nxt is None:
            await _blackjack_finish_and_render(context, gid, state, q=q); await q.answer(); return
        await _blackjack_render_turn(context, gid, state, q=q); await q.answer(); return

    if action == "double":
        if len(state["hands"][uid]) != 2 or uid in state["doubled"]:
            await q.answer("Double فقط با دو کارت اول ممکنه.", show_alert=True); return
        _cancel_timeout(context.application, "bjd", gid)
        state["doubled"].add(uid)
        state["hands"][uid].append(state["deck"].pop())
        if _hand_value_21(state["hands"][uid]) > 21:
            state["status"][uid] = "bust"
        else:
            state["status"][uid] = "stand"
        nxt = _bj2_active_uid(state)
        if nxt is None:
            await _blackjack_finish_and_render(context, gid, state, q=q); await q.answer(); return
        await _blackjack_render_turn(context, gid, state, q=q); await q.answer(); return


# =========================================================
#  🃏 حکم — نسخه‌ی دونفره
# =========================================================

HOKM_GAMES = {}
HOKM_TRICKS_TO_WIN = 7


def _hokm_deal_full(deck):
    return deck[:13], deck[13:26]


def _hokm_text(state):
    p1, p2 = state["order"]
    lines = ["🃏 GOTHAM HOKM (دونفره)", ""]
    if state["phase"] == "choose_trump":
        lines.append(f"🂠 حکم‌بند: {state['names'][state['hakem']]}")
        lines.append("منتظر انتخاب خالِ حکم...")
        return "\n".join(lines)
    lines.append(f"👑 خالِ حکم: {state['trump']}")
    lines.append(f"🏆 دست‌ها: {state['names'][p1]} {state['tricks'][p1]} — {state['tricks'][p2]} {state['names'][p2]}")
    if state["table"]:
        who, card = state["table"][0]
        lines.append(f"\n🎯 روی زمین: {state['names'][who]} → {_card_label(card)}")
    else:
        lines.append("\n🎯 زمین خالیه.")
    turn_uid = state["order"][state["turn"]]
    lines.append(f"🎲 نوبت: {state['names'][turn_uid]}")
    return "\n".join(lines)


def _hokm_hand_markup(gid, hand):
    rows, row = [], []
    for i, c in enumerate(hand):
        row.append(InlineKeyboardButton(_card_label(c), callback_data=f"hokm:play:{gid}:{i}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None


def _hokm_trump_markup(gid):
    return InlineKeyboardMarkup([[InlineKeyboardButton(s, callback_data=f"hokm:trump:{gid}:{s}") for s in SUITS]])


def _hokm_control_markup(gid, state):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🃏 دست من (خصوصی)", callback_data=f"hokm:hand:{gid}")],
        _forfeit_row("hokm", gid),
    ])


async def _launch_hokm(context, target_msg, p1, p2):
    gid = _gid("hokm")
    deck = _new_deck()
    # یه کارت به هر بازیکن برای تعیین حکم‌بند
    c1, c2 = deck.pop(), deck.pop()
    while RANK_VALUE[c1[0]] == RANK_VALUE[c2[0]]:
        deck.append(c1); deck.append(c2); random.shuffle(deck)
        c1, c2 = deck.pop(), deck.pop()
    hakem = p1.id if RANK_VALUE[c1[0]] > RANK_VALUE[c2[0]] else p2.id
    deck = _new_deck()
    h1, h2 = _hokm_deal_full(deck)
    h1.sort(key=lambda c: (c[1], RANK_VALUE[c[0]]))
    h2.sort(key=lambda c: (c[1], RANK_VALUE[c[0]]))
    HOKM_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id], "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "hands": {p1.id: h1, p2.id: h2},
        "hakem": hakem, "trump": None, "phase": "choose_trump",
        "tricks": {p1.id: 0, p2.id: 0}, "table": [], "turn": 0,
    }
    state = HOKM_GAMES[gid]
    if hakem == p2.id:
        state["order"] = [p2.id, p1.id]
    msg = await target_msg.edit_text(_hokm_text(state), reply_markup=_hokm_trump_markup(gid))
    # 🐞 قبلاً message_id ذخیره نمی‌شد (برخلاف همه‌ی بازی‌های کارتیِ دیگه‌ی این
    # فایل)، یعنی هیچ Timeout ای اصلاً نمی‌تونست پیام رو پیدا کنه و ادیت کنه.
    state["message_id"] = msg.message_id
    # ⏰ مهلت انتخاب خالِ حکم — اگه حکم‌بند تو مهلت انتخاب نکنه، بازی به نفع
    # حریف تموم می‌شه (قبلاً هیچ Timeout ای برای حکم ثبت نمی‌شد).
    _schedule_timeout(context.application, "hokm", gid, _hokm_timeout)


async def _hokm_timeout(context: ContextTypes.DEFAULT_TYPE):
    """اگه بازیکن تو مهلتش (چه انتخاب خالِ حکم، چه بازیِ کارت) اقدام نکنه،
    بازی به نفع حریف تموم می‌شه و سشن (HOKM_GAMES[gid]) صحیح پاک می‌شه —
    دقیقاً هم‌الگو با جنگ/بیست‌ویک/بلک‌جک تو همین فایل."""
    gid = context.job.data["gid"]
    state = HOKM_GAMES.get(gid)
    if not state:
        return  # بازی از قبل (مثلاً با انصراف دستی) تموم شده
    timeout_uid = state["hakem"] if state["phase"] == "choose_trump" else state["order"][state["turn"]]
    p1, p2 = state["order"]
    winner = p2 if timeout_uid == p1 else p1
    _record_result(state["chat_id"], winner, timeout_uid)
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "hokm")
    text = (
        f"{_hokm_text(state)}\n\n"
        f"⏰ {state['names'][timeout_uid]} تو مهلت خودش اقدام نکرد.\n\n"
        f"🏆 برنده: {state['names'][winner]}"
    )
    del HOKM_GAMES[gid]
    if state.get("message_id"):
        try:
            await context.bot.edit_message_text(
                chat_id=state["chat_id"], message_id=state["message_id"],
                text=text, reply_markup=_rematch_markup(token),
            )
        except Exception as e:
            log.info(f"hokm_timeout: could not edit message (harmless): {e}")


def _hokm_start_play(state):
    state["phase"] = "play"
    hakem_idx = state["order"].index(state["hakem"])
    state["turn"] = hakem_idx


def _hokm_valid_indices(hand, table, trump):
    if not table:
        return list(range(len(hand)))
    lead_suit = table[0][1][1]
    same = [i for i, c in enumerate(hand) if c[1] == lead_suit]
    return same if same else list(range(len(hand)))


def _hokm_resolve_trick(state):
    (u1, c1), (u2, c2) = state["table"]
    trump = state["trump"]
    lead_suit = c1[1]
    if c1[1] == c2[1]:
        winner = u1 if RANK_VALUE[c1[0]] > RANK_VALUE[c2[0]] else u2
    elif c1[1] == trump:
        winner = u1
    elif c2[1] == trump:
        winner = u2
    else:
        winner = u1  # هیچ‌کدوم هم‌خالِ لید یا حکم نیست جز اولی؛ اولی می‌بره چون رنگ رو داشته
    state["tricks"][winner] += 1
    state["table"] = []
    state["turn"] = state["order"].index(winner)
    return winner


@_safe_game_callback
async def hokm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    state = HOKM_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    if uid not in state["order"]:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "forfeit":
        _cancel_timeout(context.application, "hokm", gid)
        p1, p2 = state["order"]
        winner = p2 if uid == p1 else p1
        _record_result(state["chat_id"], winner, uid)
        token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "hokm")
        await q.edit_message_text(
            f"🏳 {state['names'][uid]} از بازی انصراف داد.\n\n🏆 برنده: {state['names'][winner]}",
            reply_markup=_rematch_markup(token),
        )
        del HOKM_GAMES[gid]; await q.answer(); return

    if action == "trump":
        if uid != state["hakem"]:
            await q.answer("فقط حکم‌بند خالِ حکم رو انتخاب می‌کنه.", show_alert=True); return
        state["trump"] = parts[3]
        _hokm_start_play(state)
        await q.edit_message_text(_hokm_text(state), reply_markup=_hokm_control_markup(gid, state))
        _schedule_timeout(context.application, "hokm", gid, _hokm_timeout)
        await q.answer(f"حکم: {state['trump']}"); return

    if action == "hand":
        hand = state["hands"][uid]
        if state["phase"] != "play" or state["order"][state["turn"]] != uid:
            await q.answer(f"دست تو: {_hand_label(hand)}", show_alert=True); return
        valid = _hokm_valid_indices(hand, state["table"], state["trump"])
        text = "🃏 دست تو (بزن تا بازی کنی):\n" + "\n".join(
            f"{i+1}. {_card_label(c)}{'  ✅' if i in valid else ''}" for i, c in enumerate(hand)
        )
        await q.answer(text[:200], show_alert=True)
        # کیبورد واقعی برای بازی‌کردن، زیر همون پیام گروه (فقط دکمه‌ها، بدون افشای کارت رقیب)
        try:
            await q.edit_message_reply_markup(reply_markup=_hokm_hand_markup(gid, hand))
        except Exception:
            pass
        return

    if action == "play":
        if state["phase"] != "play":
            await q.answer("هنوز حکم انتخاب نشده.", show_alert=True); return
        if state["order"][state["turn"]] != uid:
            await q.answer("نوبت تو نیست.", show_alert=True); return
        idx = int(parts[3])
        hand = state["hands"][uid]
        if idx >= len(hand):
            await q.answer("این کارت دیگه دستت نیست.", show_alert=True); return
        valid = _hokm_valid_indices(hand, state["table"], state["trump"])
        if idx not in valid:
            await q.answer("باید هم‌خالِ زمین بازی کنی (اگه داری).", show_alert=True); return
        card = hand.pop(idx)
        state["table"].append((uid, card))

        if len(state["table"]) == 1:
            other = [p for p in state["order"] if p != uid][0]
            state["turn"] = state["order"].index(other)
            await q.edit_message_text(_hokm_text(state), reply_markup=_hokm_control_markup(gid, state))
            _schedule_timeout(context.application, "hokm", gid, _hokm_timeout)
            await q.answer(f"کارتِ {_card_label(card)} بازی شد."); return

        winner = _hokm_resolve_trick(state)
        note = f"🎯 دست کوچیک رو {state['names'][winner]} برد."
        if state["tricks"][winner] >= HOKM_TRICKS_TO_WIN:
            _cancel_timeout(context.application, "hokm", gid)
            loser = [p for p in state["order"] if p != winner][0]
            _record_result(state["chat_id"], winner, loser)
            token = _store_rematch(state["chat_id"], state["players"][state["order"][0]],
                                    state["players"][state["order"][1]], "hokm")
            await q.edit_message_text(
                f"{_hokm_text(state)}\n\n{note}\n\n🏆🏆 {state['names'][winner]} با {state['tricks'][winner]} دست برنده‌ی بازی شد!",
                reply_markup=_rematch_markup(token),
            )
            del HOKM_GAMES[gid]; await q.answer(); return

        if not state["hands"][state["order"][0]] and not state["hands"][state["order"][1]]:
            _cancel_timeout(context.application, "hokm", gid)
            p1, p2 = state["order"]
            champ = p1 if state["tricks"][p1] > state["tricks"][p2] else p2
            loser = p2 if champ == p1 else p1
            _record_result(state["chat_id"], champ, loser)
            token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "hokm")
            await q.edit_message_text(
                f"{_hokm_text(state)}\n\n{note}\n\n🏁 کارت‌ها تموم شد. 🏆 برنده: {state['names'][champ]}",
                reply_markup=_rematch_markup(token),
            )
            del HOKM_GAMES[gid]; await q.answer(); return

        await q.edit_message_text(_hokm_text(state) + f"\n\n{note}", reply_markup=_hokm_control_markup(gid, state))
        _schedule_timeout(context.application, "hokm", gid, _hokm_timeout)
        await q.answer(); return


# =========================================================
#  🃏 هفت‌خبیث (Old-Maid style, ۲..۶ نفره) — با چت خصوصی
# =========================================================

HAFT_GAMES = {}          # gid -> state
HAFT_REMATCH = {}        # token -> [PseudoUser, ...]
HAFT_VILLAIN = ("Q", "♠")
HAFT_TURN_TIMEOUT_SEC = 90


class _PseudoUser:
    """برای دکمه‌ی «بازی مجدد» — بعد از تموم‌شدن بازی، آبجکت User واقعی تلگرام رو
    نداریم، پس یه نسخه‌ی سبک نگه می‌داریم که _name() باهاش کار کنه."""
    def __init__(self, uid, first_name, username=""):
        self.id = uid
        self.first_name = first_name
        self.username = username


def _haft_new_deck():
    deck = [(r, s) for s in SUITS for r in RANKS]
    # فقط بی‌بی‌پیک رو نگه می‌داریم؛ سه‌تای دیگه حذف می‌شن تا این کارت تو کل بازی جفت نداشته باشه
    deck = [c for c in deck if not (c[0] == "Q" and c[1] != "♠")]
    return deck  # ۴۹ کارت؛ (Q,♠) تنها کارتِ «خبیث»ه


def _haft_discard_pairs(hand):
    """جفت‌های هم‌ارزش (صرف‌نظر از خال) رو از دست حذف می‌کنه؛ کارتِ خبیث هیچ‌وقت جفت نداره
    چون تو کل بازی فقط یه دونه‌ست. لیستِ کارت‌های حذف‌شده رو برمی‌گردونه."""
    removed = []
    ranks = {}
    for c in hand:
        ranks.setdefault(c[0], []).append(c)
    for rank, cards in ranks.items():
        pairs = len(cards) // 2
        for c in cards[: pairs * 2]:
            hand.remove(c)
            removed.append(c)
    return removed


def _haft_job_name(gid):
    return f"haft_timeout_{gid}"


def _haft_cancel_job(app, gid):
    if not getattr(app, "job_queue", None):
        return
    for job in app.job_queue.get_jobs_by_name(_haft_job_name(gid)):
        job.schedule_removal()


def _haft_schedule_timeout(app, gid):
    _haft_cancel_job(app, gid)
    if getattr(app, "job_queue", None):
        app.job_queue.run_once(_haft_turn_timeout, when=HAFT_TURN_TIMEOUT_SEC,
                                data={"gid": gid}, name=_haft_job_name(gid))


def _haft_text(state, note=""):
    lines = ["🃏 GOTHAM — هفت‌خبیث", ""]
    for uid in state["order"]:
        mark = "👉 " if state["order"][state["turn_idx"]] == uid else "• "
        lines.append(f"{mark}{state['names'][uid]}: {len(state['hands'][uid])} کارت")
    if state["out_order"]:
        winners = "، ".join(state["names"][u] for u in state["out_order"])
        lines.append(f"\n✅ دستشون خالی شده (تو بازی نیستن): {winners}")
    if note:
        lines.append(f"\n{note}")
    lines.append(f"\n⏳ نوبتِ {state['names'][state['order'][state['turn_idx']]]}")
    return "\n".join(lines)


def _haft_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 کشیدن کارت", callback_data=f"haft:draw:{gid}")],
        [InlineKeyboardButton("🃏 دست من", callback_data=f"haft:hand:{gid}"),
         InlineKeyboardButton("🏳 خروج از بازی", callback_data=f"haft:leave:{gid}")],
    ])


def _haft_end_markup(token):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔁 بازی مجدد", callback_data=f"haft:rematch:{token}")]])


async def _haft_send_hand(context, gid, uid):
    state = HAFT_GAMES.get(gid)
    if not state or uid not in state["hands"]:
        return
    hand = state["hands"][uid]
    text = (
        f"🃏 دستِ تو تو بازیِ هفت‌خبیث:\n\n{_hand_label(hand)}\n\n"
        f"({len(hand)} کارت) — برای دیدن وضعیتِ کلی به گروه برگرد."
    )
    ok = await _deliver_hand(context, gid, uid, text)
    if not ok:
        hint = await _private_hint_markup(context)
        try:
            await context.bot.send_message(
                chat_id=state["chat_id"],
                text=(f"⚠️ {state['names'][uid]} هنوز چت خصوصی با ربات رو Start نکرده؛ "
                      "برای دیدن کارت‌هاش باید اول تو PV ربات /start بزنه، بعد بازی ادامه پیدا می‌کنه."),
                reply_markup=hint,
            )
        except Exception:
            pass
    return ok


async def _haft_resume_player(context, gid, uid):
    """وقتی کاربر تو PV ربات /start می‌زنه، صدا زده می‌شه: دستِ فعلیش رو می‌فرسته."""
    ok = await _haft_send_hand(context, gid, uid)
    state = HAFT_GAMES.get(gid)
    if ok and state:
        PRIVATE_PENDING.get(uid, set()).discard(gid)
        try:
            await context.bot.send_message(
                chat_id=state["chat_id"],
                text=f"✅ {state['names'][uid]} وارد چت خصوصی ربات شد؛ بازی ادامه پیدا می‌کنه.",
            )
        except Exception:
            pass


async def _launch_haft(context, target_msg, players):
    gid = _gid("haft")
    deck = _haft_new_deck()
    random.shuffle(deck)
    hands = {u.id: [] for u in players}
    for i, c in enumerate(deck):
        hands[players[i % len(players)].id].append(c)
    for uid in hands:
        _haft_discard_pairs(hands[uid])

    order = [u.id for u in players if len(hands[u.id]) > 0]
    names = {u.id: _name(u) for u in players}
    state = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": order, "names": names, "hands": hands,
        "out_order": [u.id for u in players if len(hands[u.id]) == 0],
        "turn_idx": 0, "processing": False,
        "player_objs": players,
    }
    HAFT_GAMES[gid] = state

    if len(order) <= 1:
        # اتفاق نادر: بعد از تقسیم اولیه، بقیه دستشون کاملاً جفت شده و خالی شده
        await _haft_finish(context, gid, target_msg, note="🍀 شانسی! همون تقسیمِ اول همه‌چی رو جفت کرد.")
        return

    msg = await target_msg.edit_text(_haft_text(state), reply_markup=_haft_markup(gid))
    state["message_id"] = msg.message_id
    _haft_schedule_timeout(context.application, gid)
    for uid in order:
        await _haft_send_hand(context, gid, uid)


async def _haft_finish(context, gid, message=None, note=""):
    state = HAFT_GAMES.get(gid)
    if not state:
        return
    _haft_cancel_job(context.application, gid)
    loser = state["order"][0] if state["order"] else None
    winner = state["out_order"][0] if state["out_order"] else None
    if loser and winner:
        try:
            import bot as _bot
            _bot._record_game_result(state["chat_id"], winner, loser)
        except Exception as e:
            log.info(f"haft: could not save game record (harmless): {e}")
    text = "🃏 بازی هفت‌خبیث تموم شد."
    if note:
        text = f"{note}\n\n{text}"
    if loser:
        text += f"\n\n💀 {state['names'][loser]} کارتِ خبیث دستش موند و باخت!"
        if winner:
            text += f"\n🏆 برنده: {state['names'][winner]}"
    token = _gid("haftrm")
    HAFT_REMATCH[token] = state["player_objs"]
    markup = _haft_end_markup(token)
    try:
        if message is not None:
            await message.edit_text(text, reply_markup=markup)
        elif state.get("message_id"):
            await context.bot.edit_message_text(
                chat_id=state["chat_id"], message_id=state["message_id"],
                text=text, reply_markup=markup,
            )
        else:
            await context.bot.send_message(chat_id=state["chat_id"], text=text, reply_markup=markup)
    except Exception as e:
        log.info(f"haft: finish edit failed (harmless): {e}")
    del HAFT_GAMES[gid]


async def _haft_execute_turn(context, gid, acting_uid, auto=False):
    state = HAFT_GAMES.get(gid)
    if not state or state.get("processing"):
        return
    state["processing"] = True
    try:
        order = state["order"]
        if acting_uid not in order:
            return
        cur_idx = order.index(acting_uid)
        nxt_idx = (cur_idx + 1) % len(order)
        nxt_uid = order[nxt_idx]
        cur_hand = state["hands"][acting_uid]
        nxt_hand = state["hands"][nxt_uid]
        if not nxt_hand:
            return
        draw_idx = random.randrange(len(nxt_hand))
        card = nxt_hand.pop(draw_idx)
        cur_hand.append(card)
        removed_pairs = _haft_discard_pairs(cur_hand)

        note = f"🎴 {state['names'][acting_uid]} از {state['names'][nxt_uid]} یه کارت کور کشید."
        if auto:
            note = f"⏱️ زمان تموم شد — {note}"
        if removed_pairs:
            note += f"\n🃏 {state['names'][acting_uid]} یه جفت دور انداخت."

        finishing = []
        if not nxt_hand:
            finishing.append(nxt_uid)
        if not cur_hand:
            finishing.append(acting_uid)
        for uid in finishing:
            if uid in state["order"]:
                state["order"].remove(uid)
                state["out_order"].append(uid)

        try:
            bot_app = context.application
        except Exception:
            bot_app = None

        if len(state["order"]) <= 1:
            await _haft_finish(context, gid, message=None, note=note)
            return

        # نوبت رو به نفر بعدیِ فعال بده
        if acting_uid in state["order"]:
            state["turn_idx"] = state["order"].index(acting_uid)
            state["turn_idx"] = (state["turn_idx"] + 1) % len(state["order"])
        else:
            state["turn_idx"] = state["turn_idx"] % len(state["order"])

        try:
            await context.bot.edit_message_text(
                chat_id=state["chat_id"], message_id=state["message_id"],
                text=_haft_text(state, note=note), reply_markup=_haft_markup(gid),
            )
        except Exception as e:
            log.info(f"haft: edit failed (harmless): {e}")

        if bot_app:
            _haft_schedule_timeout(bot_app, gid)

        for uid in {acting_uid, nxt_uid} & set(state["order"]):
            await _haft_send_hand(context, gid, uid)
    finally:
        state["processing"] = False


async def _haft_turn_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = HAFT_GAMES.get(gid)
    if not state:
        return
    uid = state["order"][state["turn_idx"]]
    await _haft_execute_turn(context, gid, uid, auto=True)


@_safe_game_callback
async def haft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action = parts[1]
    token_or_gid = parts[2]

    if action == "rematch":
        players = HAFT_REMATCH.pop(token_or_gid, None)
        if not players:
            await q.answer("این دکمه دیگه معتبر نیست.", show_alert=True); return
        await q.answer("🃏 بازی مجدد شروع شد!")
        await _launch_haft(context, q.message, players)
        return

    gid = token_or_gid
    state = HAFT_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده یا پیدا نشد.", show_alert=True); return
    uid = update.effective_user.id

    if action == "hand":
        if uid not in state["hands"] or uid not in state["order"]:
            await q.answer("تو تو این بازی نیستی یا کارت‌هات تموم شده.", show_alert=True); return
        await q.answer("🃏 دستت رو تو PV فرستادم.")
        await _haft_send_hand(context, gid, uid)
        return

    if action == "leave":
        if uid not in state["order"]:
            await q.answer("تو تو این بازی نیستی.", show_alert=True); return
        _haft_cancel_job(context.application, gid)
        state["order"].remove(uid)
        remaining = state["order"]
        if remaining:
            winner = min(remaining, key=lambda x: len(state["hands"][x]))
            try:
                import bot as _bot
                _bot._record_game_result(state["chat_id"], winner, uid)
            except Exception as e:
                log.info(f"haft: could not save forfeit record (harmless): {e}")
            text = (f"🏳 {state['names'][uid]} از هفت‌خبیث انصراف داد؛ بازی همین‌جا تموم شد.\n"
                    f"🏆 برنده: {state['names'][winner]}")
        else:
            text = f"🏳 {state['names'][uid]} از هفت‌خبیث انصراف داد. بازی تموم شد."
        token = _gid("haftrm")
        HAFT_REMATCH[token] = state["player_objs"]
        try:
            await q.edit_message_text(text, reply_markup=_haft_end_markup(token))
        except Exception:
            pass
        del HAFT_GAMES[gid]
        await q.answer(); return

    if action == "draw":
        if state.get("processing"):
            await q.answer("یه لحظه صبر کن، دور قبلی داره پردازش می‌شه...", show_alert=True); return
        if uid not in state["order"]:
            await q.answer("تو تو این بازی نیستی یا بردی/از بازی خارج شدی.", show_alert=True); return
        if state["order"][state["turn_idx"]] != uid:
            await q.answer("الان نوبتِ تو نیست.", show_alert=True); return
        await q.answer()
        await _haft_execute_turn(context, gid, uid)
        return

    await q.answer()


# =========================================================
#  🃏 چهاربرگ (Casino-style capturing game)
# =========================================================
# هر بازیکن نوبتی یه کارت از دستش بازی می‌کنه: اگه هم‌ارزشِ یکی (یا چندتا) از
# کارت‌های روی زمین بود، اونا رو جمع می‌کنه (برای امتیاز آخر بازی)، وگرنه کارتش
# میره رو زمین. هر بار دستِ بازیکنی خالی شد و نوبتش رسید، اگه دسته کارت باقی
# مونده باشه ۴ کارتِ تازه می‌گیره؛ وقتی دسته و هر دو دست خالی شد، بازی تموم
# می‌شه و هرکی کارتِ بیشتری جمع کرده برنده‌ست.

CHARBARG_GAMES = {}


def _charbarg_deal_n(deck, n):
    n = min(n, len(deck))
    cards = deck[:n]
    del deck[:n]
    return cards


def _charbarg_text(state, note=""):
    lines = ["🃏 GOTHAM چهاربرگ", ""]
    for uid in state["order"]:
        mark = "👉 " if state["order"][state["turn"]] == uid else "• "
        lines.append(f"{mark}{state['names'][uid]}: {len(state['hands'][uid])} کارت دست | "
                      f"{len(state['captured'][uid])} کارت جمع‌شده")
    lines.append(f"\n🎴 روی زمین: {_hand_label(state['table'])}")
    lines.append(f"📦 باقیِ دسته: {len(state['deck'])} کارت")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def _charbarg_markup(gid, state):
    uid = state["order"][state["turn"]]
    hand = state["hands"][uid]
    rows, row = [], []
    for i, c in enumerate(hand):
        row.append(InlineKeyboardButton(_card_label(c), callback_data=f"charbarg:play:{gid}:{i}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append(_forfeit_row("charbarg", gid))
    return InlineKeyboardMarkup(rows)


def _charbarg_refill_if_needed(state):
    """اگه نوبتِ کسیه که دستش خالیه، سعی می‌کنه ۴ کارت تازه بهش بده. اگه نه اون
    نه رقیبش دیگه نمی‌تونن حرکت کنن (دستِ هردو خالی و دسته هم خالی)، True برمی‌گردونه
    یعنی بازی تموم شده."""
    n = len(state["order"])
    for _ in range(n + 1):
        uid = state["order"][state["turn"]]
        if state["hands"][uid]:
            return False  # می‌تونه بازی کنه
        if state["deck"]:
            state["hands"][uid] = _charbarg_deal_n(state["deck"], 4)
            return False
        # این بازیکن نه کارت دستشه نه دسته کارتی مونده؛ نوبت رو به نفر بعد بده
        state["turn"] = (state["turn"] + 1) % n
    return True  # هیچ‌کس نمی‌تونه حرکت کنه -> پایان بازی


def _charbarg_finish(state):
    p1, p2 = state["order"]
    c1, c2 = len(state["captured"][p1]), len(state["captured"][p2])
    lines = [
        "🏁 پایان چهاربرگ", "",
        f"👤 {state['names'][p1]}: {c1} کارت جمع‌شده",
        f"👤 {state['names'][p2]}: {c2} کارت جمع‌شده",
    ]
    if c1 != c2:
        winner, loser = (p1, p2) if c1 > c2 else (p2, p1)
        lines.append(f"\n🏆 برنده: {state['names'][winner]}")
        _record_result(state["chat_id"], winner, loser)
    else:
        lines.append("\n🤝 مساوی — بدون برنده.")
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "charbarg")
    return "\n".join(lines), _rematch_markup(token)


async def _launch_charbarg(context, target_msg, p1, p2):
    gid = _gid("charbarg")
    deck = _new_deck()
    table = _charbarg_deal_n(deck, 4)
    hands = {p1.id: _charbarg_deal_n(deck, 4), p2.id: _charbarg_deal_n(deck, 4)}
    CHARBARG_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id], "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "deck": deck, "hands": hands, "table": table,
        "captured": {p1.id: [], p2.id: []}, "turn": 0,
    }
    state = CHARBARG_GAMES[gid]
    msg = await target_msg.edit_text(_charbarg_text(state), reply_markup=_charbarg_markup(gid, state))
    state["message_id"] = msg.message_id
    _schedule_timeout(context.application, "charbarg", gid, _charbarg_timeout)


async def _charbarg_render_or_finish(context, gid, state, note="", q=None):
    ended = _charbarg_refill_if_needed(state)
    if ended:
        text, markup = _charbarg_finish(state)
        del CHARBARG_GAMES[gid]
    else:
        text = _charbarg_text(state, note)
        markup = _charbarg_markup(gid, state)
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=markup)
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
    except Exception as e:
        log.info(f"charbarg: render failed (harmless): {e}")
    if not ended:
        _schedule_timeout(context.application, "charbarg", gid, _charbarg_timeout)


def _charbarg_play_card(state, uid, idx):
    hand = state["hands"][uid]
    card = hand.pop(idx)
    matches = [c for c in state["table"] if c[0] == card[0]]
    if matches:
        for c in matches:
            state["table"].remove(c)
        state["captured"][uid].extend(matches)
        state["captured"][uid].append(card)
        note = f"🃏 {state['names'][uid]} با {_card_label(card)} جفت گرفت و {len(matches) + 1} کارت جمع کرد."
    else:
        state["table"].append(card)
        note = f"🃏 {state['names'][uid]} کارتِ {_card_label(card)} رو گذاشت رو زمین."
    n = len(state["order"])
    state["turn"] = (state["turn"] + 1) % n
    return note


async def _charbarg_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = CHARBARG_GAMES.get(gid)
    if not state:
        return
    uid = state["order"][state["turn"]]
    if not state["hands"][uid]:
        return
    note = _charbarg_play_card(state, uid, 0)
    await _charbarg_render_or_finish(context, gid, state, note=f"⏱️ {state['names'][uid]} دیر کرد — {note}")


@_safe_game_callback
async def charbarg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    state = CHARBARG_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    if uid not in state["order"]:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "forfeit":
        _cancel_timeout(context.application, "charbarg", gid)
        winner = [u for u in state["order"] if u != uid][0]
        _record_result(state["chat_id"], winner, uid)
        p1, p2 = state["order"]
        token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "charbarg")
        await q.edit_message_text(
            f"🏳 {state['names'][uid]} از چهاربرگ انصراف داد.\n🏆 برنده: {state['names'][winner]}",
            reply_markup=_rematch_markup(token),
        )
        del CHARBARG_GAMES[gid]; await q.answer(); return

    if state["order"][state["turn"]] != uid:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    if action == "play":
        idx = int(parts[3])
        if idx >= len(state["hands"][uid]):
            await q.answer("این کارت دیگه دستت نیست.", show_alert=True); return
        _cancel_timeout(context.application, "charbarg", gid)
        note = _charbarg_play_card(state, uid, idx)
        await _charbarg_render_or_finish(context, gid, state, note=note, q=q)
        await q.answer(); return

    await q.answer()


# =========================================================
#  🃏 رامی (Rummy) — دونفره
# =========================================================
# ۱۰ کارت به هر بازیکن؛ هر نوبت یا از بالای دسته می‌کِشی یا از رو تخته‌ی دورانداخته‌ها،
# بعد یه کارت دور می‌ندازی. هرکی دستش رو با Setهای سه‌تاییِ هم‌ارزش یا Runهای
# سه‌تاییِ پشت‌سرهمِ هم‌خال کامل کنه، «رامی» می‌گه و برنده می‌شه.

RUMMY_GAMES = {}
RUMMY_ORDER_VALUE = {r: i for i, r in enumerate(RANKS)}  # ترتیب برای Run: 2..A


def _rummy_find_melds(hand):
    """بهترین ترکیبِ Setها و Runهایی که با کارت‌های دست می‌شه ساخت رو پیدا می‌کنه
    (حریصانه: اول Runهای بلندتر، بعد Setها) و لیستِ ایندکس‌های استفاده‌نشده رو
    (deadwood) برمی‌گردونه."""
    remaining = list(hand)
    melds = []

    # Runs: برای هر خال، دنباله‌های پشت‌سرهمِ حداقل ۳تایی رو پیدا کن
    by_suit = {}
    for i, c in enumerate(remaining):
        by_suit.setdefault(c[1], []).append(i)
    used = set()
    for suit, idxs in by_suit.items():
        idxs_sorted = sorted(idxs, key=lambda i: RUMMY_ORDER_VALUE[remaining[i][0]])
        run = [idxs_sorted[0]]
        for i in idxs_sorted[1:]:
            prev_val = RUMMY_ORDER_VALUE[remaining[run[-1]][0]]
            cur_val = RUMMY_ORDER_VALUE[remaining[i][0]]
            if cur_val == prev_val + 1:
                run.append(i)
            elif cur_val == prev_val:
                continue  # کارتِ تکراری همون رتبه تو همون خال (عملاً نمی‌شه تو یه دسته استاندارد)
            else:
                if len(run) >= 3:
                    melds.append(("run", [remaining[j] for j in run]))
                    used.update(run)
                run = [i]
        if len(run) >= 3:
            melds.append(("run", [remaining[j] for j in run]))
            used.update(run)

    # Sets: کارت‌های باقی‌مونده (که تو Run استفاده نشدن) با رتبه‌ی یکسان
    by_rank = {}
    for i, c in enumerate(remaining):
        if i in used:
            continue
        by_rank.setdefault(c[0], []).append(i)
    for rank, idxs in by_rank.items():
        if len(idxs) >= 3:
            melds.append(("set", [remaining[j] for j in idxs]))
            used.update(idxs)

    deadwood = [remaining[i] for i in range(len(remaining)) if i not in used]
    return melds, deadwood


def _rummy_deadwood_value(cards):
    return sum(min(BJ_VALUE[r], 10) for r, _ in cards)


def _rummy_text(state, note=""):
    lines = ["🃏 GOTHAM رامی", ""]
    for uid in state["order"]:
        mark = "👉 " if state["order"][state["turn"]] == uid else "• "
        lines.append(f"{mark}{state['names'][uid]}: {len(state['hands'][uid])} کارت")
    top_discard = state["discard"][-1] if state["discard"] else None
    lines.append(f"\n🗑 رو دورانداخته‌ها: {_card_label(top_discard) if top_discard else '—'}")
    lines.append(f"📦 باقیِ دسته: {len(state['deck'])} کارت")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def _rummy_draw_markup(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎴 کشیدن از دسته", callback_data=f"rummy:drawdeck:{gid}"),
         InlineKeyboardButton("🗑 کشیدن از دورانداخته", callback_data=f"rummy:drawdisc:{gid}")],
        [InlineKeyboardButton("🃏 دست من", callback_data=f"rummy:hand:{gid}")],
        _forfeit_row("rummy", gid),
    ])


def _rummy_discard_markup(gid, hand, drawn_from_discard=False):
    rows, row = [], []
    for i, c in enumerate(hand):
        # اگه از دورانداخته کشیده، نمی‌تونه فوراً همون کارت رو دوباره دور بندازه
        row.append(InlineKeyboardButton(_card_label(c), callback_data=f"rummy:discard:{gid}:{i}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    melds, deadwood = _rummy_find_melds(hand)
    if not deadwood:
        rows.append([InlineKeyboardButton("🏆 رامی! (اعلام برد)", callback_data=f"rummy:declare:{gid}")])
    rows.append(_forfeit_row("rummy", gid))
    return InlineKeyboardMarkup(rows)


async def _rummy_send_hand(context, gid, uid, phase="draw"):
    state = RUMMY_GAMES.get(gid)
    if not state:
        return
    hand = sorted(state["hands"][uid], key=lambda c: (c[1], RANK_VALUE[c[0]]))
    melds, deadwood = _rummy_find_melds(hand)
    text = f"🃏 دستِ تو تو رامی:\n\n{_hand_label(hand)}\n\n"
    if melds:
        meld_lines = "، ".join(f"{'Run' if k=='run' else 'Set'}: {_hand_label(m)}" for k, m in melds)
        text += f"✅ ترکیب‌های آماده: {meld_lines}\n"
    text += f"باقی‌مونده (Deadwood): {_hand_label(deadwood)} — امتیاز {_rummy_deadwood_value(deadwood)}"
    if phase == "discard":
        markup = _rummy_discard_markup(gid, hand)
    else:
        markup = None
    ok = await _deliver_hand(context, gid, uid, text, markup)
    if not ok:
        hint = await _private_hint_markup(context)
        try:
            await context.bot.send_message(
                chat_id=state["chat_id"],
                text=(f"⚠️ {state['names'][uid]} هنوز چت خصوصی با ربات رو Start نکرده؛ "
                      "برای دیدن دستش باید اول تو PV ربات /start بزنه."),
                reply_markup=hint,
            )
        except Exception:
            pass
    return ok


def _rummy_finish(state, winner_uid, note=""):
    p1, p2 = state["order"]
    loser_uid = p1 if winner_uid == p2 else p2
    _, dead_loser = _rummy_find_melds(state["hands"][loser_uid])
    score = _rummy_deadwood_value(dead_loser)
    lines = ["🏁 پایان رامی", ""]
    if note:
        lines.append(note)
    lines.append(f"🏆 برنده: {state['names'][winner_uid]}")
    lines.append(f"👤 {state['names'][loser_uid]} با {score} امتیاز Deadwood باخت.")
    _record_result(state["chat_id"], winner_uid, loser_uid)
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "rummy")
    return "\n".join(lines), _rematch_markup(token)


async def _launch_rummy(context, target_msg, p1, p2):
    gid = _gid("rummy")
    deck = _new_deck()
    hands = {p1.id: _charbarg_deal_n(deck, 10), p2.id: _charbarg_deal_n(deck, 10)}
    discard = _charbarg_deal_n(deck, 1)
    RUMMY_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id], "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "deck": deck, "hands": hands, "discard": discard,
        "turn": 0, "phase": "draw",  # draw -> discard
    }
    state = RUMMY_GAMES[gid]
    msg = await target_msg.edit_text(_rummy_text(state), reply_markup=_rummy_draw_markup(gid))
    state["message_id"] = msg.message_id
    for uid in state["order"]:
        await _rummy_send_hand(context, gid, uid, phase="draw")
    _schedule_timeout(context.application, "rummy", gid, _rummy_timeout)


async def _rummy_render(context, gid, state, note="", q=None):
    uid = state["order"][state["turn"]]
    markup = _rummy_draw_markup(gid) if state["phase"] == "draw" else None
    text = _rummy_text(state, note)
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=markup)
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
    except Exception as e:
        log.info(f"rummy: render failed (harmless): {e}")
    if state["phase"] == "discard":
        await _rummy_send_hand(context, gid, uid, phase="discard")


async def _rummy_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = RUMMY_GAMES.get(gid)
    if not state:
        return
    uid = state["order"][state["turn"]]
    if state["phase"] == "draw":
        if not state["deck"]:
            return
        card = state["deck"].pop()
        state["hands"][uid].append(card)
        state["phase"] = "discard"
        await _rummy_render(context, gid, state, note=f"⏱️ {state['names'][uid]} دیر کرد — خودکار از دسته کشید.")
        _schedule_timeout(context.application, "rummy", gid, _rummy_timeout)
    else:
        hand = state["hands"][uid]
        if not hand:
            return
        card = hand.pop(0)
        state["discard"].append(card)
        note = f"⏱️ {state['names'][uid]} دیر کرد — خودکار {_card_label(card)} رو دور انداخت."
        if not hand:
            text, markup = _rummy_finish(state, uid, note=note)
            del RUMMY_GAMES[gid]
            try:
                await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
            except Exception:
                pass
            return
        state["turn"] = (state["turn"] + 1) % 2
        state["phase"] = "draw"
        await _rummy_render(context, gid, state, note=note)
        _schedule_timeout(context.application, "rummy", gid, _rummy_timeout)


@_safe_game_callback
async def rummy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    state = RUMMY_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    if uid not in state["order"]:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "hand":
        await q.answer("🃏 دستت رو تو PV فرستادم.")
        await _rummy_send_hand(context, gid, uid, phase=state["phase"] if state["order"][state["turn"]] == uid else "draw")
        return

    if action == "forfeit":
        _cancel_timeout(context.application, "rummy", gid)
        winner = [u for u in state["order"] if u != uid][0]
        text, markup = _rummy_finish(state, winner, note=f"🏳 {state['names'][uid]} از رامی انصراف داد.")
        del RUMMY_GAMES[gid]
        await q.edit_message_text(text, reply_markup=markup)
        await q.answer(); return

    if state["order"][state["turn"]] != uid:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    if action == "drawdeck":
        if state["phase"] != "draw":
            await q.answer("اول باید یه کارت دور بندازی.", show_alert=True); return
        if not state["deck"]:
            await q.answer("دسته کارت خالی شده.", show_alert=True); return
        _cancel_timeout(context.application, "rummy", gid)
        state["hands"][uid].append(state["deck"].pop())
        state["phase"] = "discard"
        await q.answer("🎴 از دسته کشیدی.")
        await _rummy_render(context, gid, state, q=q)
        _schedule_timeout(context.application, "rummy", gid, _rummy_timeout)
        return

    if action == "drawdisc":
        if state["phase"] != "draw":
            await q.answer("اول باید یه کارت دور بندازی.", show_alert=True); return
        if not state["discard"]:
            await q.answer("چیزی رو تخته نیست.", show_alert=True); return
        _cancel_timeout(context.application, "rummy", gid)
        card = state["discard"].pop()
        state["hands"][uid].append(card)
        state["phase"] = "discard"
        await q.answer(f"🗑 {_card_label(card)} رو از دورانداخته کشیدی.")
        await _rummy_render(context, gid, state, q=q)
        _schedule_timeout(context.application, "rummy", gid, _rummy_timeout)
        return

    if action == "discard":
        if state["phase"] != "discard":
            await q.answer("اول باید یه کارت بکِشی.", show_alert=True); return
        idx = int(parts[3])
        hand = state["hands"][uid]
        if idx >= len(hand):
            await q.answer("این کارت دیگه دستت نیست.", show_alert=True); return
        _cancel_timeout(context.application, "rummy", gid)
        card = hand.pop(idx)
        state["discard"].append(card)
        if not hand:
            text, markup = _rummy_finish(state, uid)
            del RUMMY_GAMES[gid]
            await q.edit_message_text(text, reply_markup=markup)
            await q.answer("🏆 دستت خالی شد — بردی!"); return
        state["turn"] = (state["turn"] + 1) % 2
        state["phase"] = "draw"
        await q.answer(f"🗑 {_card_label(card)} رو دور انداختی.")
        await _rummy_render(context, gid, state, q=q)
        _schedule_timeout(context.application, "rummy", gid, _rummy_timeout)
        return

    if action == "declare":
        _, deadwood = _rummy_find_melds(state["hands"][uid])
        if deadwood:
            await q.answer("هنوز دستت کامل Set/Run نشده.", show_alert=True); return
        _cancel_timeout(context.application, "rummy", gid)
        text, markup = _rummy_finish(state, uid, note=f"🃏 {state['names'][uid]} رامی گفت!")
        del RUMMY_GAMES[gid]
        await q.edit_message_text(text, reply_markup=markup)
        await q.answer("🏆 رامی! بردی."); return

    await q.answer()


# =========================================================
#  🃏 پوکر (Texas Hold'em heads-up، امتیاز مجازی — بدون پول واقعی)
# =========================================================

POKER_GAMES = {}
POKER_HAND_RANK = ["High Card", "Pair", "Two Pair", "Three of a Kind", "Straight",
                    "Flush", "Full House", "Four of a Kind", "Straight Flush"]


def _poker_eval5(cards):
    """بهترین امتیازِ ۵ کارتِ داده‌شده رو برمی‌گردونه: (رتبه (۰-۸ بزرگ‌تر بهتره), tiebreakers...)."""
    values = sorted((RANK_VALUE[r] for r, _ in cards), reverse=True)
    suits = [s for _, s in cards]
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    is_flush = len(set(suits)) == 1
    uniq_vals = sorted(set(values), reverse=True)
    is_straight = False
    straight_high = None
    if len(uniq_vals) == 5:
        if uniq_vals[0] - uniq_vals[4] == 4:
            is_straight = True; straight_high = uniq_vals[0]
        elif uniq_vals == [14, 5, 4, 3, 2]:  # A-2-3-4-5
            is_straight = True; straight_high = 5
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    group_sizes = [g[1] for g in groups]
    group_vals = [g[0] for g in groups]

    if is_straight and is_flush:
        return (8, straight_high)
    if group_sizes[0] == 4:
        return (7, group_vals[0], group_vals[1])
    if group_sizes[0] == 3 and group_sizes[1] == 2:
        return (6, group_vals[0], group_vals[1])
    if is_flush:
        return (5, *values)
    if is_straight:
        return (4, straight_high)
    if group_sizes[0] == 3:
        return (3, group_vals[0], *[v for v in values if v != group_vals[0]])
    if group_sizes[0] == 2 and group_sizes[1] == 2:
        pair_vals = sorted([group_vals[0], group_vals[1]], reverse=True)
        kicker = [v for v in values if v not in pair_vals][0]
        return (2, *pair_vals, kicker)
    if group_sizes[0] == 2:
        return (1, group_vals[0], *[v for v in values if v != group_vals[0]])
    return (0, *values)


def _poker_best_hand(seven_cards):
    best = None
    for combo in itertools.combinations(seven_cards, 5):
        score = _poker_eval5(list(combo))
        if best is None or score > best:
            best = score
    return best


def _poker_hand_name(score):
    return POKER_HAND_RANK[score[0]]


def _poker_text(state, note=""):
    lines = ["🂡 GOTHAM POKER (Texas Hold'em — امتیاز مجازی)", ""]
    p1, p2 = state["order"]
    for uid in (p1, p2):
        lines.append(f"👤 {state['names'][uid]} — امتیاز: {state['chips'][uid]} | این دست: {state['bets'][uid]}")
    stage_names = {"preflop": "Pre-Flop", "flop": "Flop", "turn": "Turn", "river": "River", "showdown": "Showdown"}
    lines.append(f"\n🃏 مرحله: {stage_names.get(state['stage'], state['stage'])}")
    lines.append(f"🎴 کارت‌های زمین: {_hand_label(state['board'])}")
    lines.append(f"💰 پات: {state['pot']}")
    turn_uid = state["order"][state["turn"]]
    if state["stage"] != "showdown":
        lines.append(f"🎲 نوبت: {state['names'][turn_uid]}")
    if note:
        lines.append(f"\n{note}")
    return "\n".join(lines)


def _poker_markup(gid, state):
    uid = state["order"][state["turn"]]
    to_call = state["current_bet"] - state["bets"][uid]
    rows = []
    row = []
    if to_call <= 0:
        row.append(InlineKeyboardButton("✅ Check", callback_data=f"poker:check:{gid}"))
    else:
        row.append(InlineKeyboardButton(f"📞 Call ({to_call})", callback_data=f"poker:call:{gid}"))
    row.append(InlineKeyboardButton("❌ Fold", callback_data=f"poker:fold:{gid}"))
    rows.append(row)
    if state["chips"][uid] > to_call:
        rows.append([InlineKeyboardButton("⬆️ Raise (+10)", callback_data=f"poker:raise:{gid}")])
    rows.append(_forfeit_row("poker", gid))
    return InlineKeyboardMarkup(rows)


POKER_RAISE_STEP = 10
POKER_START_CHIPS = 200


async def _poker_send_holecards(context, gid, uid):
    state = POKER_GAMES.get(gid)
    if not state:
        return
    text = f"🂠 کارت‌های مخفیِ تو تو پوکر:\n\n{_hand_label(state['hole'][uid])}"
    ok = await _deliver_hand(context, gid, uid, text)
    if not ok:
        hint = await _private_hint_markup(context)
        try:
            await context.bot.send_message(
                chat_id=state["chat_id"],
                text=(f"⚠️ {state['names'][uid]} هنوز چت خصوصی با ربات رو Start نکرده؛ "
                      "برای دیدن کارت‌هاش باید اول تو PV ربات /start بزنه."),
                reply_markup=hint,
            )
        except Exception:
            pass


async def _launch_poker(context, target_msg, p1, p2):
    gid = _gid("poker")
    deck = _new_deck()
    hole = {p1.id: [deck.pop(), deck.pop()], p2.id: [deck.pop(), deck.pop()]}
    POKER_GAMES[gid] = {
        "chat_id": target_msg.chat_id, "message_id": None,
        "order": [p1.id, p2.id], "names": {p1.id: _name(p1), p2.id: _name(p2)},
        "players": {p1.id: p1, p2.id: p2},
        "deck": deck, "hole": hole, "board": [],
        "chips": {p1.id: POKER_START_CHIPS, p2.id: POKER_START_CHIPS},
        "bets": {p1.id: 0, p2.id: 0}, "current_bet": 0, "pot": 0,
        "folded": None, "stage": "preflop", "turn": 0, "acted": set(),
    }
    state = POKER_GAMES[gid]
    msg = await target_msg.edit_text(_poker_text(state), reply_markup=_poker_markup(gid, state))
    state["message_id"] = msg.message_id
    for uid in state["order"]:
        await _poker_send_holecards(context, gid, uid)
    _schedule_timeout(context.application, "poker", gid, _poker_timeout)


def _poker_other(state, uid):
    return [u for u in state["order"] if u != uid][0]


def _poker_collect_bets(state):
    for uid in state["order"]:
        state["pot"] += state["bets"][uid]
        state["chips"][uid] -= 0  # چیپ‌ها موقعِ شرط‌بندی کم شدن؛ اینجا فقط پات رو جمع می‌کنیم
        state["bets"][uid] = 0
    state["current_bet"] = 0


def _poker_advance_stage(state):
    """پاتِ این دور رو جمع می‌کنه و مرحله‌ی بعد رو باز می‌کنه (Flop/Turn/River/Showdown)."""
    _poker_collect_bets(state)
    state["acted"] = set()
    if state["stage"] == "preflop":
        state["board"].extend([state["deck"].pop() for _ in range(3)])
        state["stage"] = "flop"
    elif state["stage"] == "flop":
        state["board"].append(state["deck"].pop())
        state["stage"] = "turn"
    elif state["stage"] == "turn":
        state["board"].append(state["deck"].pop())
        state["stage"] = "river"
    elif state["stage"] == "river":
        state["stage"] = "showdown"
    state["turn"] = 0


def _poker_showdown_text(state):
    p1, p2 = state["order"]
    s1 = _poker_best_hand(state["hole"][p1] + state["board"])
    s2 = _poker_best_hand(state["hole"][p2] + state["board"])
    lines = ["🏁 Showdown!", "", f"🎴 زمین: {_hand_label(state['board'])}", ""]
    lines.append(f"👤 {state['names'][p1]}: {_hand_label(state['hole'][p1])} → {_poker_hand_name(s1)}")
    lines.append(f"👤 {state['names'][p2]}: {_hand_label(state['hole'][p2])} → {_poker_hand_name(s2)}")
    if s1 > s2:
        winner, loser = p1, p2
    elif s2 > s1:
        winner, loser = p2, p1
    else:
        winner, loser = None, None
    if winner:
        state["chips"][winner] += state["pot"]
        lines.append(f"\n🏆 {state['names'][winner]} پاتِ {state['pot']} امتیازی رو برد!")
        _record_result(state["chat_id"], winner, loser)
    else:
        half = state["pot"] // 2
        state["chips"][p1] += half; state["chips"][p2] += state["pot"] - half
        lines.append("\n🤝 مساوی — پات مساوی تقسیم شد.")
    lines.append(f"\n💰 امتیازِ فعلی: {state['names'][p1]}={state['chips'][p1]}  {state['names'][p2]}={state['chips'][p2]}")
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "poker")
    return "\n".join(lines), _rematch_markup(token)


def _poker_fold_finish(state, folder_uid):
    winner = _poker_other(state, folder_uid)
    _poker_collect_bets(state)
    state["chips"][winner] += state["pot"]
    p1, p2 = state["order"]
    lines = [
        "🏁 پایان دست پوکر", "",
        f"❌ {state['names'][folder_uid]} Fold کرد.",
        f"🏆 {state['names'][winner]} پاتِ {state['pot']} امتیازی رو گرفت.",
        f"\n💰 امتیازِ فعلی: {state['names'][p1]}={state['chips'][p1]}  {state['names'][p2]}={state['chips'][p2]}",
    ]
    _record_result(state["chat_id"], winner, folder_uid)
    token = _store_rematch(state["chat_id"], state["players"][p1], state["players"][p2], "poker")
    return "\n".join(lines), _rematch_markup(token)


async def _poker_render(context, gid, state, note="", q=None):
    text = _poker_text(state, note)
    markup = _poker_markup(gid, state) if state["stage"] != "showdown" else None
    try:
        if q is not None:
            await q.edit_message_text(text, reply_markup=markup)
        else:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
    except Exception as e:
        log.info(f"poker: render failed (harmless): {e}")


async def _poker_maybe_advance(context, gid, state, q=None, note=""):
    """اگه هر دو نفر تو این مرحله Act کرده باشن و شرط‌ها برابر باشه، مرحله رو جلو می‌بره؛
    وگرنه نوبت رو به نفر بعدی می‌ده."""
    p1, p2 = state["order"]
    bets_equal = state["bets"][p1] == state["bets"][p2]
    both_acted = state["acted"] >= set(state["order"])
    if both_acted and bets_equal:
        if state["stage"] == "river":
            _poker_collect_bets(state)
            state["stage"] = "showdown"
            text, markup = _poker_showdown_text(state)
            del POKER_GAMES[gid]
            try:
                if q is not None:
                    await q.edit_message_text(text, reply_markup=markup)
                else:
                    await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, reply_markup=markup)
            except Exception as e:
                log.info(f"poker: showdown render failed (harmless): {e}")
            return
        _poker_advance_stage(state)
        await _poker_render(context, gid, state, note=note, q=q)
        _schedule_timeout(context.application, "poker", gid, _poker_timeout)
        return
    state["turn"] = (state["turn"] + 1) % 2
    await _poker_render(context, gid, state, note=note, q=q)
    _schedule_timeout(context.application, "poker", gid, _poker_timeout)


async def _poker_timeout(context: ContextTypes.DEFAULT_TYPE):
    gid = context.job.data["gid"]
    state = POKER_GAMES.get(gid)
    if not state or state["stage"] == "showdown":
        return
    uid = state["order"][state["turn"]]
    to_call = state["current_bet"] - state["bets"][uid]
    if to_call <= 0:
        state["acted"].add(uid)
        await _poker_maybe_advance(context, gid, state, note=f"⏱️ {state['names'][uid]} دیر کرد — خودکار Check شد.")
    else:
        text, markup = _poker_fold_finish(state, uid)
        del POKER_GAMES[gid]
        try:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"],
                                                  text=f"⏱️ {state['names'][uid]} دیر کرد — خودکار Fold شد.\n\n{text}",
                                                  reply_markup=markup)
        except Exception:
            pass


@_safe_game_callback
async def poker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    action, gid = parts[1], parts[2]
    state = POKER_GAMES.get(gid)
    if not state:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    uid = update.effective_user.id
    if uid not in state["order"]:
        await q.answer("تو تو این بازی نیستی.", show_alert=True); return

    if action == "forfeit":
        _cancel_timeout(context.application, "poker", gid)
        text, markup = _poker_fold_finish(state, uid)
        del POKER_GAMES[gid]
        await q.edit_message_text(f"🏳 {state['names'][uid]} از پوکر انصراف داد.\n\n{text}", reply_markup=markup)
        await q.answer(); return

    if state["order"][state["turn"]] != uid:
        await q.answer("نوبت تو نیست.", show_alert=True); return

    to_call = state["current_bet"] - state["bets"][uid]

    if action == "check":
        if to_call > 0:
            await q.answer("نمی‌تونی Check کنی، باید Call یا Fold کنی.", show_alert=True); return
        _cancel_timeout(context.application, "poker", gid)
        state["acted"].add(uid)
        await q.answer("✅ Check")
        await _poker_maybe_advance(context, gid, state, q=q, note=f"✅ {state['names'][uid]} Check کرد.")
        return

    if action == "call":
        if to_call <= 0:
            await q.answer("چیزی برای Call نیست.", show_alert=True); return
        _cancel_timeout(context.application, "poker", gid)
        amount = min(to_call, state["chips"][uid])
        state["chips"][uid] -= amount
        state["bets"][uid] += amount
        state["acted"].add(uid)
        await q.answer("📞 Call")
        await _poker_maybe_advance(context, gid, state, q=q, note=f"📞 {state['names'][uid]} Call کرد ({amount}).")
        return

    if action == "raise":
        raise_amt = min(POKER_RAISE_STEP, state["chips"][uid] - to_call)
        if raise_amt <= 0:
            await q.answer("چیپِ کافی برای Raise نداری.", show_alert=True); return
        _cancel_timeout(context.application, "poker", gid)
        total = to_call + raise_amt
        state["chips"][uid] -= total
        state["bets"][uid] += total
        state["current_bet"] = state["bets"][uid]
        state["acted"] = {uid}
        await q.answer(f"⬆️ Raise (+{raise_amt})")
        await _poker_maybe_advance(context, gid, state, q=q, note=f"⬆️ {state['names'][uid]} Raise کرد (+{raise_amt}).")
        return

    if action == "fold":
        _cancel_timeout(context.application, "poker", gid)
        text, markup = _poker_fold_finish(state, uid)
        del POKER_GAMES[gid]
        await q.edit_message_text(text, reply_markup=markup)
        await q.answer(); return

    await q.answer()


MULTI_LAUNCHERS = {
    "haft": _launch_haft,
}


# =========================================================
#  راه‌انداز هر بازی (از لابی مشترک صدا زده می‌شه)
# =========================================================

LAUNCHERS = {
    "war": _launch_war,
    "bj21": _launch_bj21,
    "blackjack": _launch_blackjack,
    "hokm": _launch_hokm,
    "charbarg": _launch_charbarg,
    "poker": _launch_poker,
    "rummy": _launch_rummy,
}


# =========================================================
#  ثبت هندلرها
# =========================================================

def register_card_room(app):
    app.add_handler(MessageHandler(_kw("پاسور|کارت|بازی پاسور|بازی کارت"), card_room_start), group=12)
    app.add_handler(CallbackQueryHandler(card_room_callback, pattern=r"^cr:"), group=5)
    app.add_handler(CallbackQueryHandler(war_callback, pattern=r"^war:"), group=5)
    app.add_handler(CallbackQueryHandler(bj21_callback, pattern=r"^bj21:"), group=5)
    app.add_handler(CallbackQueryHandler(blackjack_callback, pattern=r"^bjd:"), group=5)
    app.add_handler(CallbackQueryHandler(hokm_callback, pattern=r"^hokm:"), group=5)
    app.add_handler(CallbackQueryHandler(haft_callback, pattern=r"^haft:"), group=5)
    app.add_handler(CallbackQueryHandler(charbarg_callback, pattern=r"^charbarg:"), group=5)
    app.add_handler(CallbackQueryHandler(rummy_callback, pattern=r"^rummy:"), group=5)
    app.add_handler(CallbackQueryHandler(poker_callback, pattern=r"^poker:"), group=5)
