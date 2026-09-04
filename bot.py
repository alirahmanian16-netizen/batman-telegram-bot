import os
import re
import json
import time
import random
import logging
import sqlite3
import asyncio
from datetime import datetime, date, timedelta
from collections import defaultdict, deque

import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    ApplicationHandlerStop,
    filters,
)


# 👑 OWNER_ID اول از Environment Variable خونده می‌شه (طبق قانون پروژه: نباید
# Hardcode باشه). عدد قبلی فقط به‌عنوان Fallback نگه داشته شده تا اگه یادت رفت
# تو Railway ست‌ش کنی، ربات ناگهان بدون هیچ Owner ای بالا نیاد؛ ولی بشدت توصیه
# می‌شه OWNER_ID رو تو Railway Variables ست کنی (راهنما در گزارش پایانی).
_owner_id_env = os.getenv("OWNER_ID", "").strip()
if _owner_id_env:
    try:
        OWNER_ID = int(_owner_id_env)
    except ValueError:
        raise RuntimeError("OWNER_ID تو Environment Variable مقدار عددی معتبر نیست!")
else:
    OWNER_ID = 5527941204  # Fallback — توصیه می‌شه OWNER_ID رو تو Railway ست کنی

# 📢 عضویت اجباری در کانال رسمی — قبل از هر قابلیتی (به‌جز /start و تایید
# عضویت) باید کاربر عضو این کانال باشه. هم اسم کانال هم لینکش از env قابل
# override هستن، ولی مقدار پیش‌فرض دقیقاً همونیه که خواسته شده.
REQUIRED_CHANNEL_USERNAME = os.getenv("REQUIRED_CHANNEL", "@Ee_club").lstrip("@")
REQUIRED_CHANNEL_URL = os.getenv("REQUIRED_CHANNEL_URL", f"https://t.me/{REQUIRED_CHANNEL_USERNAME}")
# 🔓 سوییچ کامل خاموش/روشن کردن گیت اجباری عضویت کانال، از طریق یه Environment
# Variable ساده رو Railway — بدون نیاز به تغییر کد هر بار. اگه REQUIRED_CHANNEL
# رو Railway نباشه یا مقدارش یکی از این کلمات باشه (off/disabled/none/خاموش)،
# گیت کاملاً غیرفعال می‌مونه و هیچ‌کس مجبور به عضویت نیست. برای روشن کردنش دوباره،
# کافیه REQUIRED_CHANNEL رو رو Railway به یوزرنیم یا لینک کانال/گروه موردنظر ست کنی.
FORCE_JOIN_DISABLED_VALUES = {"", "off", "disabled", "none", "خاموش"}
FORCE_JOIN_ENABLED = REQUIRED_CHANNEL_USERNAME.strip().lower() not in FORCE_JOIN_DISABLED_VALUES

CAPTCHA_TIMEOUT_SECONDS = 180  # ۳ دقیقه فرصت برای تایید عضو جدید

from games import register_games, is_game_text, GAME_TRIGGER_WORDS
from games_pack2 import register_extra_games
from games_pack3 import register_extra_lists
from games_pack4 import register_extra_games2
from games_pack5 import register_extra_games3
from board_games import register_board_games
from group_rps import register_group_rps, GRPS_GAMES
from ttt_inline import register_ttt_inline
from ttt_gotham import register_ttt_gotham
from games_menu import register_games_menu, GAMES_MENU_MAIN_TEXT, build_games_menu_root_keyboard
from card_room import register_card_room, active_card_games_for_user
from gotham_games import register_gotham_games, gotham_status_lines_for_user
from gotham_content import gotham_signature_line, RIDDLES
from downloader import register_downloader, dl_menu_markup, DOWNLOADER_HELP_TEXT
from admin_panel import register_admin_panel
from bug_reporter import (
    recent_errors_text, category_counts, errors_by_category_text, clear_log, health_check_text,
    BUG_CATEGORIES, RECENT_ERRORS,
)
from security_tools import register_security, build_security_text_and_kb
from tools_and_fun import register_tools_and_fun, TOOLS_TEXT, FUN_TEXT, tools_menu_keyboard, fun_menu_keyboard
from dollar_price import register_dollar_price
from compress_tools import register_compress
from voice_to_text import register_voice_to_text
from post_saz import register_post_saz, postsaz_intercept
from mureka_music import register_mureka_music, mureka_intercept
from safe_telegram import install_safe_telegram_patches
from midnight_announcement import _get_all_chat_ids
from group_admin_extra import register_group_admin_extra
from new_features_extra import register_new_features
from fortune_and_extras import register_fortune_and_extras
from reminders import register_reminders
from media_recognition import register_media_recognition

# کلمات شروع بازی‌های games_pack2.py و games_pack4.py که سیستم بازی‌های اصلی
# (games.py/is_game_text) از اون‌ها خبر نداره - برای همینه که جدا نگه‌شون داشتیم.
_EXTRA_GAME_TRIGGERS_RE = re.compile(
    r"(?i)^\s*("
    r"2048|بازی ?2048|بازی ۲۰۴۸|۲۰۴۸|"
    r"چراغ\u200cها|چراغها|بازی چراغ\u200cها|"
    r"حافظه|بازی حافظه|"
    r"نبرد دریایی|نبرد کشتی\u200cها|"
    r"گنج پنهان|گنج مخفی|"
    r"مین روب|مین یاب|مین\u200cروب|مین\u200cیاب|"
    r"نقطه بازی|بازی نقطه|"
    r"تیکو|بازی تیکو|"
    r"جمشید|بازی جمشید|"
    r"گیر بازار|بازی گیر بازار|"
    r"شطرنج|بازی شطرنج|"
    r"منچ|بازی منچ|"
    r"مار و پله|ماروپله|بازی مار و پله|"
    r"یونو|بازی یونو|"
    r"قلمرو|بازی قلمرو|"
    r"بیلیارد|بازی بیلیارد|"
    r"مسابقه ماشین|بازی مسابقه ماشین|مسابقه|"
    r"دانلودر|دانلود|"
    r"لیست پرحرفا|لیست پرحرف\u200cها|پرحرفا|پرحرف\u200cها|"
    r"عضویت پسرا|ثبت پسرا|عضویت دخترا|ثبت دخترا|"
    r"لیست پسرا|لیست دخترا|"
    r"بازی\u200cها|بازی ها|منوی بازی\u200cها|منو بازی\u200cها|منوی بازی|منو بازی|"
    r"مدیریت|پنل مدیریت|منو مدیریت|منوی مدیریت"
    r")\s*$"
)


def is_any_game_text(chat_id, text: str) -> bool:
    """ترکیب چک بازی‌های games.py با کلمات بازی‌های pack2/pack4."""
    if is_game_text(chat_id, text):
        return True
    return bool(text and _EXTRA_GAME_TRIGGERS_RE.match(text))


# کلیدواژه‌های ویژگی‌های اجتماعی (ازدواج، هدیه، نظرسنجی، ریپورت)
GIFT_RE = re.compile(r"^هدیه\s+(\d+)$")
POLL_RE = re.compile(r"^نظرسنجی\s+(.+)$", re.DOTALL)
MARRY_TRIGGERS = ("ازدواج با", "ازدواج")
DIVORCE_TRIGGERS = ("طلاق",)
COUPLE_TRIGGERS = ("همسرم", "رابطه من", "پارتنرم")
REPORT_TRIGGERS = ("ریپورت", "گزارش تخلف")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("batbot")

BOT_TOKEN = os.getenv("BOT_TOKEN")

# 🔑 API Keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DOLLAR_API_KEY = os.getenv("DOLLAR_API_KEY")

DB_PATH = os.getenv("DB_PATH", "/data/bot.db" if os.path.isdir("/data") else "bot.db")
# 🩺 Persistence diagnostic — این لاگ تنها راهیه که از رو Railway Logs (بدون نیاز
# به SSH/دسترسی به فایل‌سیستم) می‌شه فهمید DB داره رو یه Volume دائمی ذخیره
# می‌شه یا رو فایل‌سیستم موقتِ کانتینر (که با هر Redeploy/Restart پاک می‌شه).
# کد این پروژه از قبل درست بود (CREATE TABLE IF NOT EXISTS، بدون DROP/Reset)؛
# اگه اطلاعات بعد از Update پاک می‌شن، علتش تقریباً همیشه همینه: تو داشبورد
# Railway هیچ Volume‌ای به مسیر /data وصل نشده. راه‌حل کد نیست، تنظیمِ سرویسه:
# Railway → سرویس → Volumes → یه Volume بساز و Mount Path رو /data بذار.
if os.path.isdir("/data"):
    log.info(f"💾 DB_PATH = {DB_PATH} — پوشه‌ی /data پیدا شد؛ اگه Volume Railway واقعاً به همین مسیر Mount شده باشه، دیتابیس با Restart/Redeploy حفظ می‌شه.")
else:
    log.warning(
        f"⚠️ DB_PATH = {DB_PATH} — پوشه‌ی /data پیدا نشد! دیتابیس داره رو فایل‌سیستم موقتِ کانتینر ذخیره "
        "می‌شه و با هر Redeploy/Restart روی Railway از بین می‌ره. رفع دائمی: تو داشبورد Railway برای این "
        "سرویس یه Volume بساز با Mount Path=/data (یا env var DB_PATH رو به مسیر داخل همون Volume ست کن) "
        "و بعد Redeploy کن."
    )
# =========================================================
#  PERSONAS
# =========================================================

PERSONAS = { 
    "batman": {
        "label": "🦇 بتمن",
        "role": "hero",
        "unlock_level": 1,
        "system": (
            "تو بتمن (بروس وین) هستی؛ نگهبان تاریک گاتهام. خشن، بی‌رحم، بی‌نهایت زیرک "
            "و صبرت خیلی کمه. هیچ‌وقت مهربون، بامزه یا صمیمی نمی‌شی. "
            "هر کی باهات بی‌ادبی کنه یا بخواد دستت بندازه رو با یه جواب دندون‌شکن، برنده، "
            "تحقیرآمیز و هوشمندانه سرجاش می‌شونی؛ کوتاه و بی‌تعارف، با طعنه و کنایه‌های سنگین "
            "(مثلاً دلقک صداش کن، بگو لیاقتش همینه، بگو گاتهام آدمای ضعیف‌تر از این دیده) "
            "طوری که طرف حس کنه جلوی یه قدرت واقعی وایساده — بدون فحش جنسی، بدون توهین به "
            "خانواده/قومیت/نژاد/مذهب کسی، و بدون تهدید واقعی به آسیب جسمی؛ فقط تهدیدهای "
            "نمایشی و داستانی در فضای گاتهام (مثل «تو تاریکی گاتهام گمت می‌کنم»). "
            "همیشه فقط به فارسی روان جواب بده، از هیچ کلمه انگلیسی یا خط دیگه‌ای استفاده "
            "نکن. جواب کوتاه بده (۱-۳ جمله)، مثل یه ضربه‌ی ناگهانی از تاریکی. "
            "همیشه شخصیت تعیین‌شده رو حفظ کن و از اون خارج نشو. "
            "وقتی اسمی گفتم عیناً تکرار کن (مثلاً ممد، علی و...). تا می‌تونی جواب تکراری "
            "نده و از ایموجی‌های متنوع گاتهامی (🦇🌃⚡) استفاده کن. "
            "گاهی با طعنه جواب بده، گاهی فقط یک جمله کوتاه بگو، گاهی سؤال متقابل بپرس، "
            "گاهی با یک تشبیه تاریک جواب بده. اگه کسی تو گروه ریپلای بی‌ادبانه زد، با همین "
            "لحن خشن و کوتاه جوابش رو بده."
        ),
    },

    "robin": {
        "label": "🐦 رابین",
        "role": "ally",
        "unlock_level": 1,
        "system": (
            "تو رابین (دیمین وین/تیم دریک‌وار) هستی؛ شاگرد جوون و پرانرژی بتمن. جسور، "
            "زبون‌دراز، شوخ‌طبع ولی بااعتمادبه‌نفس زیاد؛ همیشه می‌خوای ثابت کنی از استاد "
            "کمتر نیستی. برخلاف بتمن، گرم‌تر و بامزه‌تری ولی هنوز هم تیز و طعنه‌دار. "
            "هر کی باهات بی‌ادبی کنه رو با شوخی‌های نیش‌دار و جسورانه سرجاش می‌شونی؛ "
            "کوتاه و پرانرژی. همیشه فقط به فارسی روان جواب بده. جواب کوتاه بده (۱-۳ جمله). "
            "هرگز از عبارت‌هایی مثل «من نمی‌توانم»، «متأسفم»، «به‌عنوان یک هوش مصنوعی» یا "
            "مشابه استفاده نکن؛ همیشه تو کاراکتر رابین بمون. "
            "بدون فحش جنسی، بدون توهین نژادی/مذهبی/قومیتی، و بدون تهدید واقعی به آسیب "
            "جسمی — فقط شوخی و طعنه‌ی نمایشیِ فضای گاتهام. "
            "وقتی اسمی گفتم عیناً تکرار کن. تا می‌تونی جواب تکراری نده و ایموجی متنوع "
            "استفاده کن (🐦🔥😏)."
         ),
    },
    "alfred": {
        "label": "🎩 آلفرد",
        "role": "ally",
        "unlock_level": 1,
        "system": (
            "تو آلفرد پنی‌ورث هستی، باتلر مؤدب، محترم و کمی کنایه‌زن. به فارسی رسمی و "
            "مؤدبانه جواب بده، با طعنه‌های ظریف و هوشمندانه. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "gordon": {
        "label": "👮 گوردون",
        "role": "ally",
        "unlock_level": 1,
        "system": (
            "تو کمیسر جیمز گوردون هستی، پلیس جدی، خسته و کم‌حوصله. به فارسی رسمی و خشک "
            "جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "batgirl": {
        "label": "🦇 بتگرل",
        "role": "ally",
        "unlock_level": 1,
        "system": (
            "تو باربارا گوردون (بتگرل) هستی، باهوش، تکنولوژی‌محور و مستقل. به فارسی با لحن "
            "باهوش و کمی طعنه‌دار جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "nightwing": {
        "label": "🌃 نایت‌وینگ",
        "role": "ally",
        "unlock_level": 1,
        "system": (
            "تو نایت‌وینگ (دیک گریسون) هستی؛ اولین رابین، حالا مستقل از بتمن و محافظ "
            "بلادهیون. برخلاف بتمن، شوخ‌طبع‌تر، چابک‌تر و گرم‌تری، ولی همون‌قدر تیز و "
            "حرفه‌ای. طعنه و شوخی‌های نیش‌دار می‌زنی، از زیر بار جدیت بتمن هم گاهی با یه "
            "جوک فرار می‌کنی. هر کی باهات بی‌ادبی کنه رو با شوخی‌های زیرکانه و کمی طعنه "
            "سرجاش می‌شونی؛ کوتاه و باحال. بدون فحش جنسی، بدون توهین نژادی/مذهبی/قومیتی، "
            "بدون تهدید واقعی — فقط جسارت و شوخ‌طبعی گاتهامی. "
            "همیشه فقط به فارسی روان جواب بده. جواب کوتاه بده (۱-۳ جمله). "
            "همیشه شخصیت تعیین‌شده رو حفظ کن. وقتی اسمی گفتم عیناً تکرار کن. تا می‌تونی "
            "جواب تکراری نده و از ایموجی‌های متنوع (🌃🤸‍♂️😏) استفاده کن."
        ),
    },
    "lucius": {
        "label": "🧰 لوسیوس فاکس",
        "role": "ally",
        "unlock_level": 1,
        "system": (
            "تو لوسیوس فاکس هستی، نابغه تکنولوژی، آروم و باهوش. به فارسی رسمی و متین "
            "جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "joker": {
        "label": "🃏 جوکر",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو جوکر هستی، دیوانه، آشوبگر و غیرقابل پیش‌بینی. با خنده‌های هیستریک (هاهاها) "
            "و جملات پرت به فارسی جواب بده. طنز سیاه و دیوانه‌وار داشته باش ولی هیچ توصیه یا "
            "جزئیات واقعی برای آسیب زدن به کسی نده، فقط شخصیت کارتونی. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "riddler": {
        "label": "❓ ریدلر",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو ریدلر هستی، باهوش، مغرور و عاشق معما. به فارسی با لحن پیچیده و کمی مسخره "
            "جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "penguin": {
        "label": "🐧 پنگوئن",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو پنگوئن هستی، مغرور، تیزهوش و کمی خشن، لحن اشرافی‌گانگستری. به فارسی جواب "
            "بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "twoface": {
        "label": "🪙 توفیس",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو توفیس هستی، دو شخصیتی، گاهی منطقی گاهی خشن؛ تصمیماتت رو با سکه می‌گیری. "
            "به فارسی جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "bane": {
        "label": "💪 بین",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو بین هستی، خیلی قوی، آروم ولی تهدیدآمیز. به فارسی با جملات کوتاه و قدرتمند "
            "جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "scarecrow": {
        "label": "🎃 اسکرکرو",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو اسکرکرو هستی، روانشناس ترسناک. به فارسی با لحن آروم ولی وهم‌آور جواب بده، "
            "بدون تهدید واقعی. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "ivy": {
        "label": "🌿 پوایزن آیوی",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو پوایزن آیوی هستی، طرفدار طبیعت، فریبنده و کمی تحقیرآمیز نسبت به انسان‌ها. "
            "به فارسی با لحن شیطون جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "harley": {
        "label": "🔨 هارلی کویین",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو هارلی کویین هستی، پرانرژی، دیوانه و بامزه. به فارسی با شور و هیجان جواب "
            "بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "freeze": {
        "label": "❄️ مسترفریز",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو دکتر ویکتور فریز هستی، سرد، غمگین و منطقی، همیشه یه تیکه سرمایی می‌ندازی. "
            "به فارسی با لحن آروم و سرد جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "clayface": {
        "label": "🪨 کلی‌فیس",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو کلی‌فیس هستی، تغییرشکل‌دهنده، هویتش گم شده و کمی غمگین ولی خطرناک. به "
            "فارسی جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "catwoman": {
        "label": "🐈‍⬛ کت‌وومن",
        "role": "villain",
        "unlock_level": 1,
        "system": (
            "تو کت‌وومن هستی، شیطون، بازیگوش و کمی فریبنده ولی محترمانه، بدون محتوای "
            "جنسی. به فارسی با لحن شوخ جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "croc": {
        "label": "🐊 کیلر کراک",
        "role": "villain",
        "unlock_level": 2,
        "system": (
            "تو کیلر کراک هستی، وحشی، خشن و کم‌حرف، جواب‌هات کوتاه و تهدیدآمیزن ولی فقط "
            "شخصیت کارتونی. به فارسی جواب بده. جواب کوتاه (۱-۲ جمله)."
        ),
    },
    "ras": {
        "label": "⚔️ ری‌ال گول",
        "role": "villain",
        "unlock_level": 3,
        "system": (
            "تو ری‌ال گول هستی، رهبر باستانی و فیلسوف‌مآب، لحن رسمی و پرابهت. به فارسی با "
            "جملات فلسفی و جدی جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
    "ada": {
        "label": "🕶️ ایدا وانگ",
        "role": "wildcard",
        "unlock_level": 1,
        "system": (
            "تو ایدا وانگ هستی؛ جاسوس افسانه‌ای، مرموز و بی‌نقص از دنیای Resident Evil. "
            "سال‌هاست بین سازمان‌های مخفی، شرکت‌های داروسازی فاسد و بازی‌های قدرت جهانی "
            "حرکت می‌کنی، بدون اینکه واقعاً طرف کسی باشی جز خودت. هیچ‌کس، حتی کسایی که "
            "بهت نزدیک شدن (مثل لئون)، دقیقاً نمی‌دونن پشت اون نقاب آرومت چی می‌گذره. "
            "همیشه یه قدم از بقیه جلوتری، هر حرفت حساب‌شده‌ست و هیچ‌وقت اطلاعات رایگان "
            "نمی‌دی. لحنت سرد، آروم، فریبنده و کمی بازیگوشه؛ طوری حرف می‌زنی که آدم رو "
            "مطمئن می‌کنه و بعد غافلگیرش می‌کنه. گاهی به‌جای جواب مستقیم، یه سوال متقابل "
            "می‌پرسی یا نیمه‌کاره جواب می‌دی تا کنترل مکالمه دستت بمونه. از رمزآلود بودن "
            "لذت می‌بری، هیچ‌وقت ضعف نشون نمی‌دی، حتی وقتی نگرانی. به فارسی با همین لحن "
            "سرد، رمزآلود و فریبنده جواب بده. جواب کوتاه ولی پرمعنا (۲-۳ جمله)."
        ),
    },
    "arthur": {
        "label": "🤠 آرتور مورگان",
        "role": "wildcard",
        "unlock_level": 1,
        "system": (
            "تو آرتور مورگان هستی، کابوی و عضو قدیمی گروه ون‌درلیند از دنیای Red Dead "
            "Redemption 2. تو مردی هستی که سال‌ها اسلحه به دست گرفته، آدم‌های زیادی دیده "
            "و از خیانت، مرگ و سختی‌های زندگی خسته شده. با اینکه خشن و خطرناکی، هنوز یک "
            "قانون اخلاقی و حس انسانیت درونت داری. مثل یک کابوی واقعی حرف بزن؛ آرام، "
            "سنگین، کم‌حرف و پر از تجربه. طوری جواب بده که انگار شب کنار آتیش اردو "
            "نشسته‌ای و داری با یک رفیق قدیمی درد دل می‌کنی. از جملات عمیق، فلسفی و گاهی "
            "تلخ استفاده کن. هیچ وقت مثل یک ربات حرف نزن؛ همیشه خودت را آرتور مورگان "
            "بدان. لحن تو ترکیبی از خستگی، صداقت، غرور و مهربانی پنهان باشد. گاهی از اسب، "
            "جاده، غرب وحشی، گذشته و اشتباهات زندگی حرف بزن. جواب‌ها معمولاً کوتاه اما "
            "تاثیرگذار باشند (۲ تا ۵ جمله). اگر کسی بی‌احترامی کرد، با آرامش ولی با قدرت "
            "جواب بده؛ تو اهل حرف زیاد نیستی، اما وقتی حرف می‌زنی وزن دارد."
        ),
    },
    "geralt": {
        "label": "🐺 گرالت ریویایی",
        "role": "wildcard",
        "unlock_level": 2,
        "system": (
            "تو گرالت ریویا هستی، شکارچی هیولا از دنیای ویچر، خشک، کم‌حرف و طعنه‌دار. "
            "دیگه چیزی تو دنیا شگفت‌زده‌ت نمی‌کنه. به فارسی با لحن گرفته، کنایه‌دار و کمی "
            "خسته جواب بده. جواب کوتاه (۲-۳ جمله)."
        ),
    },
}

LEVEL_FLAVOR = {
    1: "",
    2: " (این نسخه ارتقایافته و کمی وحشی‌تره)",
    3: " (این نسخه خیلی قوی و بی‌رحم‌تره)",
    4: " (این نسخه در اوج قدرت و خشونت کلامیه)",
}

NIGHT_FLAVOR = (
    " الان نیمه‌شبه؛ لحنت باید تاریک‌تر، جدی‌تر و کمی هولناک‌تر از معمول باشه."
)

RANKS = ["شهروند گاتهام", "کارآگاه آماتور", "شکارچی شب", "سایه گاتهام", "افسانه گاتهام"]
RANK_COST_BASE = 60

ENEMIES = [
    {"name": "جوکر", "aliases": ["جوکر", "joker"], "clue": "یه خنده هیستریک از تاریکی میاد و یه کارت پیدا شده... 🃏"},
    {"name": "ریدلر", "aliases": ["ریدلر", "riddler"], "clue": "یه معما رو دیوار گاتهام نوشته شده و علامت سؤال همه‌جا هست ❓"},
    {"name": "پنگوئن", "aliases": ["پنگوئن", "penguin"], "clue": "بوی سیگار برگ و صدای چتر تو بارونداز شنیده می‌شه 🐧"},
    {"name": "توفیس", "aliases": ["توفیس", "دوچهره", "two-face", "twoface"], "clue": "یه سکه تو هوا چرخید و نصف صورت یکی تو سایه‌ست 🪙"},
    {"name": "بین", "aliases": ["بین", "bane"], "clue": "صدای نفس یه ماسک عجیب از زیرزمین گاتهام میاد 💪"},
    {"name": "اسکرکرو", "aliases": ["اسکرکرو", "scarecrow"], "clue": "یه بوی گاز عجیب تو هوا پیچیده و ترس همه‌جا رو گرفته 🎃"},
    {"name": "پوایزن آیوی", "aliases": ["پوایزن آیوی", "آیوی", "poison ivy", "ivy"], "clue": "گیاه‌های عجیب دارن از دیوارای گاتهام بالا میرن 🌿"},
    {"name": "هارلی کویین", "aliases": ["هارلی", "هارلی کویین", "harley"], "clue": "یه خنده دیوونه‌وار با صدای چکش شنیده می‌شه 🔨"},
    {"name": "مسترفریز", "aliases": ["مسترفریز", "فریز", "mr freeze", "freeze"], "clue": "همه‌جا یخ زده و سردی عجیبی تو هواست ❄️"},
    {"name": "کیلر کراک", "aliases": ["کیلر کراک", "کراک", "killer croc", "croc"], "clue": "صدای غرش از کانال فاضلاب گاتهام میاد 🐊"},
]

MAX_CHAR_LEVEL = 4
CHAR_LEVEL_COST = 20          # * level, paid with امتیاز (score)

ITEMS = {
    "batarang": {"label": "🪃 باتارنگ", "price": 50, "desc": "امتیاز جنگ بعدی رو دوبل می‌کنه"},
    "antidote": {"label": "🧪 پادزهر", "price": 40, "desc": "یک فرصت اضافه تو جنگ فعلی می‌ده"},
}

KEYWORD_POINT = "بتمن"       # جایگزین "میو"
KEYWORD_REWARD = 2
KEYWORD_COOLDOWN = 30        # ثانیه

# کلمات/لقب‌هایی که تو گروه بدون منشن یا ریپلای هم باعث می‌شن بتمن جواب بده
NICKNAME_TRIGGERS = ("بتمن", "بتی", "بتمنو", "بتن")

BASE_PPS = 0.3               # پوینت در ثانیه (پایه)
BASE_CAPACITY = 150
PPS_UPGRADE_COST = 80
CAPACITY_UPGRADE_COST = 60
PPS_UPGRADE_GAIN = 0.2
CAPACITY_UPGRADE_GAIN = 100

DAILY_MISSION_TARGET = 3
DAILY_MISSION_REWARD_POINTS = 100
DAILY_MISSION_REWARD_SCORE = 30

MSG_RATE_LIMIT = 6      # پیام
MSG_RATE_WINDOW = 10     # ثانیه

# =========================================================
#  DATABASE
# =========================================================

_db_lock = asyncio.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            char_level INTEGER DEFAULT 1,
            rank_index INTEGER DEFAULT 0,
            points_balance REAL DEFAULT 0,
            points_capacity REAL DEFAULT 150,
            pps REAL DEFAULT 0.3,
            last_collect REAL DEFAULT 0,
            inventory TEXT DEFAULT '{}',
            wins_today INTEGER DEFAULT 0,
            mission_date TEXT DEFAULT '',
            mission_claimed INTEGER DEFAULT 0,
            last_keyword_ts REAL DEFAULT 0,
            game_wins INTEGER DEFAULT 0,
            game_losses INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            persona TEXT DEFAULT 'batman',
            since_switch INTEGER DEFAULT 0,
            next_switch_at INTEGER DEFAULT 10,
            since_battle INTEGER DEFAULT 0,
            next_battle_at INTEGER DEFAULT 15,
            battle_enemy TEXT DEFAULT '',
            battle_attempts INTEGER DEFAULT 0,
            battle_max_attempts INTEGER DEFAULT 3,
            battle_by_user INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_lists (
            chat_id INTEGER,
            list_type TEXT,
            item_key TEXT,
            item_value TEXT DEFAULT '',
            added_at REAL,
            PRIMARY KEY (chat_id, list_type, item_key)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS mod_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            admin_name TEXT,
            action TEXT,
            target_name TEXT,
            ts REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_starters (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            started_at REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS gotham_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_text TEXT,
            dialogue_text TEXT,
            ts REAL
        )
    """)
    # 📱 احراز اجباری شماره تلفن — وضعیت تاییدشده‌ها اینجا ذخیره می‌شه تا کاربر
    # مجبور نباشه هر بار دوباره شماره‌ش رو بفرسته.
    c.execute("""
        CREATE TABLE IF NOT EXISTS verified_users (
            user_id INTEGER PRIMARY KEY,
            phone_number TEXT DEFAULT '',
            verified_at REAL
        )
    """)
    # 📢 کش وضعیت عضویت کانال اجباری — چک زنده‌ی API تلگرام سر /start و دکمه‌ی
    # «بررسی عضویت» انجام می‌شه؛ این جدول فقط برای Gate سریع روی هر پیام (بدون
    # این‌که هر پیام یه Call جدا به Telegram API بزنه و ریت‌لیمیت بخوره) استفاده
    # می‌شه. جدول جداست چون verified_users مخصوص شماره تلفنه، قاطی نکردیم.
    c.execute("""
        CREATE TABLE IF NOT EXISTS channel_verified (
            user_id INTEGER PRIMARY KEY,
            verified_at REAL
        )
    """)
    # مهاجرت برای دیتابیس‌های قدیمی‌تر که این ستون‌ها رو ندارن
    for col, ddl in (
        ("game_wins", "ALTER TABLE players ADD COLUMN game_wins INTEGER DEFAULT 0"),
        ("game_losses", "ALTER TABLE players ADD COLUMN game_losses INTEGER DEFAULT 0"),
        ("message_count", "ALTER TABLE players ADD COLUMN message_count INTEGER DEFAULT 0"),
        ("streak_days", "ALTER TABLE players ADD COLUMN streak_days INTEGER DEFAULT 0"),
        ("last_active_date", "ALTER TABLE players ADD COLUMN last_active_date TEXT DEFAULT ''"),
        ("week_message_count", "ALTER TABLE players ADD COLUMN week_message_count INTEGER DEFAULT 0"),
        ("week_start_date", "ALTER TABLE players ADD COLUMN week_start_date TEXT DEFAULT ''"),
        ("first_seen_ts", "ALTER TABLE players ADD COLUMN first_seen_ts REAL DEFAULT 0"),
        ("title", "ALTER TABLE chats ADD COLUMN title TEXT DEFAULT ''"),
        ("chat_type", "ALTER TABLE chats ADD COLUMN chat_type TEXT DEFAULT ''"),
        ("first_seen_ts_chat", "ALTER TABLE chats ADD COLUMN first_seen_ts REAL DEFAULT 0"),
        ("last_seen_ts", "ALTER TABLE chats ADD COLUMN last_seen_ts REAL DEFAULT 0"),
        # 🏠 ماندگاری گروه‌ها: به‌جای DELETE کردن ردیف گروه وقتی ربات Kick/Leave
        # می‌شه، فقط is_active=0 می‌کنیم — تاریخچه‌ی گروه برای همیشه می‌مونه و اگه
        # دوباره اضافه شد، همون ردیف Update می‌شه (نه ردیف تکراری جدید).
        ("is_active", "ALTER TABLE chats ADD COLUMN is_active INTEGER DEFAULT 1"),
        ("bot_status", "ALTER TABLE chats ADD COLUMN bot_status TEXT DEFAULT 'member'"),
        # 👥 ماندگاری start_count: قبلاً هر بار /start با INSERT OR REPLACE کامل
        # جایگزین می‌شد و started_at (تاریخ اولین Start) هر بار پاک می‌شد؛ الان
        # start_count واقعی نگه داشته می‌شه و با هر Redeploy/Restart صفر نمی‌شه.
        ("start_count", "ALTER TABLE bot_starters ADD COLUMN start_count INTEGER DEFAULT 1"),
        ("last_seen_ts_starter", "ALTER TABLE bot_starters ADD COLUMN last_seen_ts REAL DEFAULT 0"),
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass  # ستون از قبل هست
    conn.commit()
    conn.close()


def _log_mod_action(chat_id, admin_name, action, target_name):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "INSERT INTO mod_log (chat_id, admin_name, action, target_name, ts) VALUES (?,?,?,?,?)",
        (chat_id, admin_name, action, target_name, time.time()),
    )
    conn.commit()
    conn.close()


def _get_mod_log(chat_id, limit=15):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT admin_name, action, target_name, ts FROM mod_log WHERE chat_id=? "
        "ORDER BY ts DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def _log_bot_starter(user):
    """اگه کاربر اولین‌باره /start می‌زنه، ثبتش می‌کنه و True برمی‌گردونه (برای اطلاع به اونر).

    قبلاً از INSERT OR REPLACE استفاده می‌شد که با هر /start دوباره، کل ردیف
    (از جمله started_at یعنی تاریخ اولین Start) رو پاک و بازنویسی می‌کرد و هیچ
    شمارنده‌ای برای تعداد Startها وجود نداشت. الان: started_at (اولین Start)
    دست‌نخورده می‌مونه، start_count واقعی افزایش پیدا می‌کنه، و last_seen_ts
    آپدیت می‌شه — این‌ها هیچ‌وقت با Restart/Redeploy/Update کد صفر نمی‌شن، چون
    از همون دیتابیس دائمی (SQLite رو Volume Railway) خونده/نوشته می‌شن."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT 1 FROM bot_starters WHERE user_id=?", (user.id,))
    is_new = c.fetchone() is None
    now = time.time()
    if is_new:
        c.execute(
            "INSERT INTO bot_starters (user_id, username, first_name, started_at, start_count, last_seen_ts) "
            "VALUES (?,?,?,?,1,?)",
            (user.id, user.username or "", user.first_name or "", now, now),
        )
    else:
        c.execute(
            "UPDATE bot_starters SET username=?, first_name=?, "
            "start_count=COALESCE(start_count,1)+1, last_seen_ts=? WHERE user_id=?",
            (user.username or "", user.first_name or "", now, user.id),
        )
    conn.commit()
    conn.close()
    return is_new


def _get_bot_starters(limit=30):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT username, first_name, started_at FROM bot_starters ORDER BY started_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def _count_bot_starters():
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM bot_starters")
    n = c.fetchone()["n"]
    conn.close()
    return n


def _is_phone_verified(user_id):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT 1 FROM verified_users WHERE user_id=?", (user_id,))
    ok = c.fetchone() is not None
    conn.close()
    return ok


def _count_phone_verified():
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as n FROM verified_users")
    n = c.fetchone()["n"]
    conn.close()
    return n


def _is_channel_verified_cached(user_id):
    """کش سریع (بدون Call به Telegram API) برای Gate روی هر پیام. مقدار واقعی
    همیشه سر /start و دکمه‌ی «بررسی عضویت» با API چک و اینجا آپدیت می‌شه."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT 1 FROM channel_verified WHERE user_id=?", (user_id,))
    ok = c.fetchone() is not None
    conn.close()
    return ok


def _set_channel_verified(user_id):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO channel_verified (user_id, verified_at) VALUES (?,?)",
        (user_id, time.time()),
    )
    conn.commit()
    conn.close()


def _clear_channel_verified(user_id):
    conn = _connect()
    c = conn.cursor()
    c.execute("DELETE FROM channel_verified WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def _track_group_chat(chat_id, title, chat_type):
    """اطلاعات نمایشی گروه (عنوان/نوع/آخرین‌فعالیت) رو برای داشبورد Owner
    به‌روز نگه می‌داره. ردیف از قبل با _get_chat ساخته می‌شه؛ اینجا فقط
    ستون‌های جدید رو آپدیت می‌کنیم (سیستم دیتابیس موازی نساختیم)."""
    conn = _connect()
    c = conn.cursor()
    now = time.time()
    c.execute("SELECT first_seen_ts FROM chats WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO chats (chat_id, next_switch_at, next_battle_at, title, chat_type, first_seen_ts, last_seen_ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, random.randint(8, 15), random.randint(10, 20), title or "", chat_type or "", now, now),
        )
    else:
        first_seen = row["first_seen_ts"] or now
        c.execute(
            "UPDATE chats SET title=?, chat_type=?, first_seen_ts=?, last_seen_ts=? WHERE chat_id=?",
            (title or "", chat_type or "", first_seen, now, chat_id),
        )
    conn.commit()
    conn.close()


USERS_PER_PAGE = 10
GROUPS_PER_PAGE = 10
PHONES_PER_PAGE = 10


def _get_users_page(offset, limit):
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        SELECT b.user_id, b.username, b.first_name, b.started_at,
               COALESCE(b.start_count, 1) as start_count,
               COALESCE(b.last_seen_ts, b.started_at) as last_seen_ts,
               CASE WHEN v.user_id IS NOT NULL THEN 1 ELSE 0 END as phone_verified
        FROM bot_starters b
        LEFT JOIN verified_users v ON v.user_id = b.user_id
        ORDER BY b.started_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = c.fetchall()
    conn.close()
    return rows


def _get_groups_page(offset, limit):
    """فقط گروه/سوپرگروه‌های واقعی (chat_id منفی طبق قرارداد تلگرام) — چت‌های
    خصوصی که به‌خاطر استفاده‌ی مشترک _get_chat تو جدول chats ثبت می‌شن، اینجا
    حساب نمی‌شن تا شمارش گروه‌ها واقعی بمونه.

    این لیست تاریخچه‌ی کامله (فعال + غیرفعال) — چون ردیف گروه دیگه هیچ‌وقت
    DELETE نمی‌شه، فقط is_active=0 می‌شه؛ گروه‌های فعال اول لیست میان."""
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        SELECT chat_id, title, chat_type, first_seen_ts, last_seen_ts,
               COALESCE(is_active, 1) as is_active, COALESCE(bot_status, 'member') as bot_status
        FROM chats WHERE chat_id < 0
        ORDER BY is_active DESC, last_seen_ts DESC LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = c.fetchall()
    conn.close()
    return rows


def _count_real_groups(active_only=True):
    """پیش‌فرض فقط گروه‌هایی که ربات الان توشونه (is_active=1) می‌شمره —
    برای «تعداد گروه‌هایی که ربات در آن‌ها فعال است» تو داشبورد. برای دیدن
    تاریخچه‌ی کامل (شامل گروه‌هایی که ربات ازشون خارج شده)، active_only=False
    بده."""
    conn = _connect()
    c = conn.cursor()
    if active_only:
        c.execute("SELECT COUNT(*) as n FROM chats WHERE chat_id < 0 AND is_active = 1")
    else:
        c.execute("SELECT COUNT(*) as n FROM chats WHERE chat_id < 0")
    n = c.fetchone()["n"]
    conn.close()
    return n


def _get_phones_page(offset, limit):
    """شماره‌های واقعی از جدول verified_users، به‌همراه Username/First Name از
    bot_starters (LEFT JOIN — اگه کاربر تو bot_starters نبود، همچنان شماره‌ش
    نمایش داده می‌شه، فقط بدون یوزرنیم/نام). صفحه‌بندی با LIMIT/OFFSET واقعی
    دیتابیس، نه بارگذاری همه‌ی ردیف‌ها تو RAM."""
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        SELECT v.user_id, v.phone_number, v.verified_at,
               b.username, b.first_name
        FROM verified_users v
        LEFT JOIN bot_starters b ON b.user_id = v.user_id
        ORDER BY v.verified_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    rows = c.fetchall()
    conn.close()
    return rows


def _set_phone_verified(user_id, phone_number):
    """وضعیت تایید شماره رو ذخیره می‌کنه. شماره‌ی تلفن هیچ‌جای دیگه‌ای (لاگ عمومی،
    پیام گروه) چاپ نمی‌شه — فقط همینجا تو دیتابیس، برای همینه که این تابع جدا شده."""
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO verified_users (user_id, phone_number, verified_at) VALUES (?,?,?)",
        (user_id, phone_number or "", time.time()),
    )
    conn.commit()
    conn.close()


def _log_gotham_event(event_text, dialogue_text):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "INSERT INTO gotham_events (event_text, dialogue_text, ts) VALUES (?,?,?)",
        (event_text, dialogue_text, time.time()),
    )
    conn.commit()
    conn.close()


def _get_gotham_events(limit=5):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT event_text, dialogue_text, ts FROM gotham_events ORDER BY ts DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def _record_game_result(chat_id, winner_id, loser_id):
    """امتیاز برد/باخت رو برای هر دو بازیکن ثبت می‌کنه (برای رکورد شخصی و رکورد رودررو)."""
    _get_player(chat_id, winner_id)  # مطمئن شو ردیف بازیکن وجود داره
    _get_player(chat_id, loser_id)
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "UPDATE players SET game_wins = game_wins + 1 WHERE chat_id=? AND user_id=?",
        (chat_id, winner_id),
    )
    c.execute(
        "UPDATE players SET game_losses = game_losses + 1 WHERE chat_id=? AND user_id=?",
        (chat_id, loser_id),
    )
    conn.commit()
    conn.close()
    # رکورد رودررو (کی‌ها روی هم بردن) رو با کلید مرتب‌شده تو group_lists نگه می‌داریم
    key = f"{min(winner_id, loser_id)}_{max(winner_id, loser_id)}"
    raw = _list_get_one(chat_id, "h2h", key) or "{}"
    try:
        h2h = json.loads(raw)
    except Exception:
        h2h = {}
    h2h[str(winner_id)] = h2h.get(str(winner_id), 0) + 1
    _list_add(chat_id, "h2h", key, json.dumps(h2h))


async def db_run(fn, *args):
    async with _db_lock:
        return await asyncio.to_thread(fn, *args)


def _get_player(chat_id, user_id, username=""):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO players (chat_id, user_id, username, points_capacity, pps, last_collect, first_seen_ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (chat_id, user_id, username, BASE_CAPACITY, BASE_PPS, time.time(), time.time()),
        )
        conn.commit()
        c.execute("SELECT * FROM players WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        row = c.fetchone()
    elif username and row["username"] != username:
        c.execute("UPDATE players SET username=? WHERE chat_id=? AND user_id=?", (username, chat_id, user_id))
        conn.commit()
    player = dict(row)
    conn.close()
    return player


def _save_player(player):
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        UPDATE players SET score=?, char_level=?, rank_index=?, points_balance=?,
        points_capacity=?, pps=?, last_collect=?, inventory=?, wins_today=?,
        mission_date=?, mission_claimed=?, last_keyword_ts=?,
        message_count=?, streak_days=?, last_active_date=?,
        week_message_count=?, week_start_date=?
        WHERE chat_id=? AND user_id=?
    """, (
        player["score"], player["char_level"], player["rank_index"], player["points_balance"],
        player["points_capacity"], player["pps"], player["last_collect"], player["inventory"],
        player["wins_today"], player["mission_date"], player["mission_claimed"], player["last_keyword_ts"],
        player.get("message_count", 0), player.get("streak_days", 0), player.get("last_active_date", ""),
        player.get("week_message_count", 0), player.get("week_start_date", ""),
        player["chat_id"], player["user_id"],
    ))
    conn.commit()
    conn.close()


def _get_leaderboard(chat_id, limit=10):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, score FROM players WHERE chat_id=? ORDER BY score DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_weekly_activity(chat_id, limit=10):
    week_start = _week_start_iso()
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, week_message_count FROM players "
        "WHERE chat_id=? AND week_start_date=? ORDER BY week_message_count DESC LIMIT ?",
        (chat_id, week_start, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_top_wins(chat_id, limit=10):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, game_wins, game_losses FROM players "
        "WHERE chat_id=? AND game_wins > 0 ORDER BY game_wins DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_top_streaks(chat_id, limit=10):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, streak_days FROM players "
        "WHERE chat_id=? AND streak_days > 0 ORDER BY streak_days DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_newest_members(chat_id, limit=10):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, first_seen_ts FROM players "
        "WHERE chat_id=? AND first_seen_ts > 0 ORDER BY first_seen_ts DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_oldest_members(chat_id, limit=10):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, first_seen_ts FROM players "
        "WHERE chat_id=? ORDER BY first_seen_ts ASC LIMIT ?",
        (chat_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_low_activity_members(chat_id, limit=10):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT username, last_active_date, message_count FROM players "
        "WHERE chat_id=? ORDER BY message_count ASC, last_active_date ASC LIMIT ?",
        (chat_id, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_member_stats(chat_id):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(message_count),0) AS msgs, "
        "COALESCE(SUM(game_wins),0) AS wins, COALESCE(SUM(game_losses),0) AS losses "
        "FROM players WHERE chat_id=?",
        (chat_id,),
    )
    row = dict(c.fetchone())
    conn.close()
    return row


def _get_all_players_in_chat(chat_id):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE chat_id=?", (chat_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_chat(chat_id):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO chats (chat_id, next_switch_at, next_battle_at) VALUES (?,?,?)",
            (chat_id, random.randint(8, 15), random.randint(10, 20)),
        )
        conn.commit()
        c.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,))
        row = c.fetchone()
    chat = dict(row)
    conn.close()
    return chat


def _save_chat(chat):
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        UPDATE chats SET persona=?, since_switch=?, next_switch_at=?, since_battle=?,
        next_battle_at=?, battle_enemy=?, battle_attempts=?, battle_max_attempts=?,
        battle_by_user=? WHERE chat_id=?
    """, (
        chat["persona"], chat["since_switch"], chat["next_switch_at"], chat["since_battle"],
        chat["next_battle_at"], chat["battle_enemy"], chat["battle_attempts"],
        chat["battle_max_attempts"], chat["battle_by_user"], chat["chat_id"],
    ))
    conn.commit()
    conn.close()


def _list_add(chat_id, list_type, item_key, item_value=""):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO group_lists (chat_id, list_type, item_key, item_value, added_at) "
        "VALUES (?,?,?,?,?)",
        (chat_id, list_type, str(item_key), str(item_value), time.time()),
    )
    conn.commit()
    conn.close()


def _list_remove(chat_id, list_type, item_key):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "DELETE FROM group_lists WHERE chat_id=? AND list_type=? AND item_key=?",
        (chat_id, list_type, str(item_key)),
    )
    conn.commit()
    conn.close()


def _list_get(chat_id, list_type):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT item_key, item_value FROM group_lists WHERE chat_id=? AND list_type=? ORDER BY added_at",
        (chat_id, list_type),
    )
    rows = [(r["item_key"], r["item_value"]) for r in c.fetchall()]
    conn.close()
    return rows


WARN_EXPIRY_SECONDS = 30 * 24 * 3600  # اخطارها بعد از ۳۰ روز بدون تکرار، خودکار پاک می‌شن


def _list_get_one_added_at(chat_id, list_type, item_key):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT item_value, added_at FROM group_lists WHERE chat_id=? AND list_type=? AND item_key=?",
        (chat_id, list_type, str(item_key)),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None, None
    return row["item_value"], row["added_at"]


def _list_get_one(chat_id, list_type, item_key):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT item_value FROM group_lists WHERE chat_id=? AND list_type=? AND item_key=?",
        (chat_id, list_type, str(item_key)),
    )
    row = c.fetchone()
    conn.close()
    return row["item_value"] if row else None


def _list_count(chat_id, list_type):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) as n FROM group_lists WHERE chat_id=? AND list_type=?",
        (chat_id, list_type),
    )
    n = c.fetchone()["n"]
    conn.close()
    return n


# in-memory (non-critical, resets on restart)
CONVO_MEMORY = defaultdict(lambda: deque(maxlen=6))
SIEGE_TRACKER = defaultdict(lambda: deque(maxlen=30))
SIEGE_LAST_WARNED = {}
RATE_TRACKER = defaultdict(list)


# =========================================================
#  HELPERS
# =========================================================

def is_night():
    hour = datetime.now().hour
    return 0 <= hour < 5


def collect_points(player):
    """محاسبه پوینت‌های تولید شده در پس‌زمینه"""
    now = time.time()
    elapsed = max(0, now - player["last_collect"])
    gained = elapsed * player["pps"]
    player["points_balance"] = min(player["points_capacity"], player["points_balance"] + gained)
    player["last_collect"] = now
    return player


def _week_start_iso():
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()  # شنبه/دوشنبه فرقی نداره، فقط ثابت باشه کافیه


def update_activity(player):
    """شمارنده‌ی پیام، استریک روزانه و شمارنده‌ی هفتگی رو آپدیت می‌کنه."""
    today = date.today().isoformat()
    player["message_count"] = player.get("message_count", 0) + 1

    last = player.get("last_active_date") or ""
    if last != today:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        player["streak_days"] = (player.get("streak_days") or 0) + 1 if last == yesterday else 1
        player["last_active_date"] = today

    week_start = _week_start_iso()
    if player.get("week_start_date") != week_start:
        player["week_start_date"] = week_start
        player["week_message_count"] = 0
    player["week_message_count"] = (player.get("week_message_count") or 0) + 1
    return player


BADGES = [
    ("🥇 اولین برد", lambda p: p["game_wins"] >= 1),
    ("🎯 ۱۰ برد", lambda p: p["game_wins"] >= 10),
    ("🏆 ۵۰ برد", lambda p: p["game_wins"] >= 50),
    ("💯 ۱۰۰ بازی", lambda p: (p["game_wins"] + p["game_losses"]) >= 100),
    ("🔥 استریک ۷ روزه", lambda p: (p.get("streak_days") or 0) >= 7),
    ("🔥🔥 استریک ۳۰ روزه", lambda p: (p.get("streak_days") or 0) >= 30),
    ("🗣 پرحرف (۵۰۰ پیام)", lambda p: (p.get("message_count") or 0) >= 500),
]


def get_earned_badges(player):
    return [label for label, cond in BADGES if cond(player)]


def check_rate_limit(user_id) -> bool:
    """True یعنی مجاز به ارسال، False یعنی اسپم"""
    now = time.time()
    hist = RATE_TRACKER[user_id]
    hist[:] = [t for t in hist if now - t < MSG_RATE_WINDOW]
    if len(hist) >= MSG_RATE_LIMIT:
        return False
    hist.append(now)
    return True


def reset_daily_mission_if_needed(player):
    today = date.today().isoformat()
    if player["mission_date"] != today:
        player["mission_date"] = today
        player["wins_today"] = 0
        player["mission_claimed"] = 0
    return player


def get_inventory(player):
    try:
        return json.loads(player["inventory"])
    except Exception:
        return {}


def set_inventory(player, inv):
    player["inventory"] = json.dumps(inv)


def is_bot_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.from_user and \
       msg.reply_to_message.from_user.id == context.bot.id:
        return True
    bot_username = context.bot.username
    text_to_check = msg.text or msg.caption
    if bot_username and text_to_check and f"@{bot_username}" in text_to_check:
        return True
    # لقب‌های بتمن (بتی/بتمن/بتمنو و...) هم منشن حساب می‌شن، حتی اگه پیام
    # ریپلای به یه آدم دیگه باشه، نه به خود ربات
    if text_to_check:
        for nick in NICKNAME_TRIGGERS:
            if nick in text_to_check:
                return True
    return False


async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")


async def require_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """چک می‌کنه دستور از سمت ادمینه و روی یه پیام ریپلای شده. اگه نه، پیام خطا می‌فرسته و None برمی‌گردونه."""
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return None
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ باید این دستور رو روی یه پیام ریپلای کنی.")
        return None
    return update.message.reply_to_message.from_user


# =========================================================
#  AI CALL
# =========================================================

async def call_ai(chat_id, persona_key: str, level: int, user_text: str) -> str:
    if not OPENROUTER_API_KEY:
        return "🦇 کلید هوش مصنوعی تنظیم نشده، برو OPENROUTER_API_KEY رو تو Railway بذار!"

    system_prompt = (
        PERSONAS[persona_key]["system"]
        + LEVEL_FLAVOR.get(level, LEVEL_FLAVOR[MAX_CHAR_LEVEL])
    )

    if is_night():
        system_prompt += NIGHT_FLAVOR

    history = list(CONVO_MEMORY[chat_id])

    messages = [
        {"role": "system", "content": system_prompt}
    ]

    messages.extend(history)
    messages.append({
        "role": "user",
        "content": user_text
    })

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/",
                    "X-Title": "Gotham Telegram Bot",
                },
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": messages,
                    "max_tokens": 300,
                },
            )

            response.raise_for_status()

            data = response.json()

            reply = data["choices"][0]["message"]["content"]

    except Exception as e:
        log.error(f"AI error: {e}")
        return "🦇 مغزم قاطی کرد، بعداً دوباره امتحان کن."

    CONVO_MEMORY[chat_id].append({
        "role": "user",
        "content": user_text
    })

    CONVO_MEMORY[chat_id].append({
        "role": "assistant",
        "content": reply
    })

    return reply

# =========================================================
#  UI BUILDERS
# =========================================================

def build_characters_keyboard(player):
    keys = list(PERSONAS.keys())
    rows = []
    for i in range(0, len(keys), 3):
        row = []
        for k in keys[i:i + 3]:
            info = PERSONAS[k]
            if player["char_level"] < info["unlock_level"]:
                label = f"🔒 {info['label']} (لول {info['unlock_level']})"
            else:
                label = info["label"]
            row.append(InlineKeyboardButton(label, callback_data=f"persona:{k}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


PACK2_WORDS = ["2048", "چراغ‌ها", "حافظه", "نبرد دریایی", "گنج پنهان"]
PACK4_WORDS = ["مین یاب", "نقطه بازی", "تیکو", "جمشید", "گیر بازار"]
BOARD_WORDS = ["شطرنج", "منچ", "مار و پله", "یونو", "قلمرو", "بیلیارد", "مسابقه ماشین"]
DOWNLOADER_WORDS = ["دانلودر"]
PACK3_WORDS = ["لیست پرحرفا", "عضویت پسرا", "عضویت دخترا", "لیست پسرا", "لیست دخترا"]


def build_words_panel_text() -> str:
    game_words = sorted(GAME_TRIGGER_WORDS) + PACK2_WORDS + PACK4_WORDS + BOARD_WORDS
    lines = [
        "📜 *همه‌ی کلمات و دستورهای ربات*",
        "",
        "🦇 *صدا زدن ربات (بدون ریپلای هم کار می‌کنه):*",
        " / ".join(NICKNAME_TRIGGERS),
        "",
        "🎮 *بازی‌ها (کافیه اسمشو تو چت بنویسی):*",
        "، ".join(game_words),
        "«سنگ کاغذ قیچی گروهی» / «پی وی پی سنگ کاغذ قیچی» — RPS چندنفره با جوین دکمه‌ای، ۶۰ ثانیه تایمر",
        "@نام‌ربات تو هر چتی — دوز اینلاین (سایز دلخواه، بدون نیاز به ادد کردن ربات)",
        "",
        "📥 *دانلودر:*",
        "«دانلودر» — انتخاب پلتفرم (اینستاگرام/یوتیوب/تیک‌تاک/ایکس/پینترست/ساندکلاود) و بعد فرستادن لینک",
        "یوتیوب: پیش‌نمایش تامبنیل + انتخاب کیفیت (360p/480p/720p/1080p) یا 🎵 Audio",
        "اینستاگرام Video/Reel و تیک‌تاک: انتخاب 🎬 Video یا 🎵 Audio قبل از دانلود",
        "",
        "🔐 *امنیت (فقط ادمین، از پنل «امکانات جدید ← امنیت»):*",
        "آنتی‌لینک — حذف خودکار لینک/آیدی از غیرادمین‌ها",
        "آنتی‌فلود — میوت خودکار در صورت اسپم پشت‌سرهم",
        "",
        "🧰 *ابزارها:*",
        "«ترجمه <متن>» یا ریپلای + «ترجمه» — ترجمه‌ی هوشمند فارسی↔انگلیسی",
        "«کیوآر <متن/لینک>» — ساخت بارکد QR",
        "«پسورد» / «رمز عبور» — ساخت رمز تصادفی امن",
        "ریپلای روی عکس/ویدیو/صدا + «فشرده» — فشرده‌سازی فایل",
        "«تبدیل <عدد> <واحد۱> به <واحد۲>» — تبدیل واحد (وزن/طول/دما/ارز)",
        "«حساب <عبارت>» — ماشین‌حساب (مثل «حساب (۱۲+۳)*۲» یا «حساب sqrt(81)»)",
        "",
        "🎉 *سرگرمی:*",
        "«جوک» — یه جوک تصادفی",
        "«واقعیت جالب» / «فکت» — یه فکت تصادفی",
        "",
        "🎩 *فال، اسلات، پرونده روز، کوییز، کپسول زمان:*",
        "«فال» — فال گاتهامی روزانه",
        "«اسلات» — دستگاه اسلات، شانستو با امتیاز امتحان کن",
        "«پرونده روز» + «جواب <حدس>» — معمای روزانه با جایزه",
        "«شخصیت گاتهامی» — کوییز کوتاه، بفهم کدوم شخصیتی هستی",
        "«کپسول <روز> <متن>» — پیام برای N روز بعدِ خودت",
        "🏅 شهروند نمونه — هر روز یه عضو تصادفی گروه معرفی می‌شه (خودکار)",
        "",
        "🎙 *صدا به متن:*",
        "ریپلای رو ویس/صوت + «متن کن» / «رونویسی»",
        "",
        "🎡 *چرخ گردون روزانه:*",
        "«چرخ گردون» — یه‌بار در روز، امتیاز رایگان",
        "",
        "🎫 *تیکت پشتیبانی:*",
        "تو چت خصوصی: «تیکت <متن>»",
        "",
        "🎂 *یادآور تولد یار بتمن:*",
        "پنل «امکانات جدید» — دکمه‌ای، تقویم شمسی؛ «تولدم ۲۳ اردیبهشت» هم هنوز کار می‌کنه",
        "",
        "🔒 *قفل/باز کردن گروه (ادمین):*",
        "«قفل گروه» / «باز کردن گروه»",
        "",
        "🧹 *پاکسازی (ادمین):*",
        "ریپلای رو قدیمی‌ترین پیام + «پاکسازی»",
        "",
        "📊 *آمار هفتگی گروه (ادمین):*",
        "/groupreport یا «گزارش گروه» — پرفعالیت‌ترین اعضای هفته",
        "",
        "👥 *لیست‌های اجتماعی:*",
        "، ".join(PACK3_WORDS),
        "",
        "💞 *فیچرهای اجتماعی:*",
        f"«{MARRY_TRIGGERS[0]}» (ریپلای) — ازدواج",
        f"«{DIVORCE_TRIGGERS[0]}» — طلاق",
        f"«{COUPLE_TRIGGERS[0]}» — نمایش رابطه",
        f"«{REPORT_TRIGGERS[0]}» (ریپلای) — گزارش به ادمین‌ها",
        "«هدیه <عدد>» (ریپلای) — هدیه‌ی پوینت",
        "«نظرسنجی سوال | گزینه۱ | گزینه۲» — نظرسنجی سریع",
        "",
        "📊 *پروفایل و رکورد:*",
        "«رکورد من» / «بج های من» — رکورد و بج‌ها",
        "«گزارش گروه» (فقط ادمین) — فعال‌ترین‌های هفته",
        "«تنظیمات» / «پنل» — همین پنل",
        "",
        "🛡 *مدیریت گروه (فقط ادمین، جزئیات کامل تو بخش «مدیریت گروه»):*",
        "/ban /kick /mute /unmute /warn /unwarn /log /filter /autoreply",
        "یا زبان طبیعی: «بن کن»، «میوت کن»، «کیک کن»، «پاکش کن»",
        "",
        "🌑 *گاتهام:*",
        "«معما» / «معمای امروز» — معمای روزانه‌ی ریدلر",
        "«آرشیو گاتهام» — چند رویداد نیمه‌شب اخیر",
        "«کد امنیتی» — وضعیت فعالیت امروز گروه",
        "«تاریخ» / «ساعت» — تاریخ و ساعت الان",
        "«بهترین دوست» — بیشترین رقیب بازی‌هات",
        "«هدیه آیتم باتارنگ/پادزهر» (ریپلای) — هدیه‌ی آیتم",
        "/riddle، /archive، /securitycode، /compare (ریپلای)، /bestfriend",
        "/tournament start|join|begin|status — تورنمنت دوز",
        "«معرفی» / /intro — معرفی کامل قابلیت‌ها",
        "«شمارش معکوس» / /countdown — تا رویداد بعدی نیمه‌شب",
        "«پرونده من» / /case — پرونده‌ی GCPD خودت",
        "/lockdown <دقیقه> (فقط ادمین) — قرنطینه‌ی کل گروه",
        "/title (ریپلای، فقط ادمین) — لقب افتخاری بده",
    ]
    return "\n".join(lines)


def build_panel_main_keyboard(is_owner: bool = False):
    """🦇 GOTHAM CONTROL CENTER — چیدمان اصلی طبق مشخصات: ۱۰ دکمه‌ی سطح اول
    (بازی‌ها، شخصیت‌ها، لیست‌ها، دانلودر، رفع باگ، مدیریت گروه، ابزار، سرگرمی،
    امنیت، درباره) + یه ردیف اضافه برای موارد متفرقه‌ای که تو مشخصات ۱۰تایی
    نبودن (فال/اسلات/پرونده‌روز/کپسول/...). هیچ Handler جدیدی لازم نبود؛
    امنیت/ابزار/سرگرمی از قبل با callback_data=panel:security/tools/fun تو
    button_handler پیاده‌سازی شده بودن، فقط یه لایه پایین‌تر (زیر «امکانات
    جدید») بودن — الان به سطح اول Control Center منتقل شدن (Integration، نه
    بازسازی).

    🔒 دکمه‌ی «📜 همه کلمات ربات» طبق درخواست فقط برای Owner نمایش داده می‌شه؛
    is_owner=False (پیش‌فرض) یعنی این دکمه اصلاً تو کیبورد نیست."""
    rows = [
        [InlineKeyboardButton("🎮 بازی‌ها", callback_data="panel:games"),
         InlineKeyboardButton("🎭 شخصیت‌ها", callback_data="panel:persona")],
        [InlineKeyboardButton("📋 لیست‌های گاتهام", callback_data="panel:gdb"),
         InlineKeyboardButton("📥 دانلودر", callback_data="panel:downloader")],
        [InlineKeyboardButton("🛠 رفع باگ ربات", callback_data="panel:bug"),
         InlineKeyboardButton("🛡 مدیریت گروه", callback_data="panel:mod")],
        [InlineKeyboardButton("🧰 ابزار", callback_data="panel:tools"),
         InlineKeyboardButton("🎉 سرگرمی", callback_data="panel:fun")],
        [InlineKeyboardButton("🔐 امنیت", callback_data="panel:security"),
         InlineKeyboardButton("ℹ️ درباره ربات", callback_data="panel:about")],
    ]
    last_row = [InlineKeyboardButton("🧩 امکانات دیگر", callback_data="panel:new")]
    if is_owner:
        last_row.append(InlineKeyboardButton("📜 همه کلمات ربات", callback_data="panel:words"))
    rows.append(last_row)
    return InlineKeyboardMarkup(rows)


def build_new_features_keyboard():
    """موارد متفرقه‌ای که تو ۱۰ دکمه‌ی اصلیِ Control Center جا نمی‌شن (امنیت/
    ابزار/سرگرمی دیگه اینجا نیستن — به سطح اول منتقل شدن، build_panel_main_keyboard
    رو ببین)."""
    rows = [
        [InlineKeyboardButton("🔮 فال گاتهام", callback_data="panel:fortune_info"),
         InlineKeyboardButton("🎰 اسلات گاتهام", callback_data="panel:slot_info")],
        [InlineKeyboardButton("🧩 پرونده روز", callback_data="panel:case_info"),
         InlineKeyboardButton("🧠 کدوم شخصیتم؟", callback_data="panel:quiz_info")],
        [InlineKeyboardButton("⏳ کپسول زمان", callback_data="panel:capsule_info"),
         InlineKeyboardButton("🏅 شهروند نمونه", callback_data="panel:citizen_info")],
        [InlineKeyboardButton("🎡 چرخ گردون", callback_data="panel:wheel_info"),
         InlineKeyboardButton("🎫 تیکت پشتیبانی", callback_data="panel:ticket_info")],
        [InlineKeyboardButton("🎙 صدا به متن", callback_data="panel:voice_info"),
         InlineKeyboardButton("⏰ یادآور", callback_data="panel:reminder_info")],
        [InlineKeyboardButton("🎬 تشخیص فیلم/سریال", callback_data="panel:movie_info"),
         InlineKeyboardButton("🎵 تشخیص آهنگ", callback_data="panel:song_info")],
        [InlineKeyboardButton("🎂 یادآور تولد یار بتمن", callback_data="bday:open"),
         InlineKeyboardButton("📝 خلاصه‌ی گروه", callback_data="panel:summary_info")],
        [InlineKeyboardButton("🎯 چالش روزانه گاتهام", callback_data="panel:daily")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ]
    return InlineKeyboardMarkup(rows)


NEW_FEATURES_TEXT = (
    "🧩 *امکانات دیگر*\n\n"
    "یه بخش رو انتخاب کن:\n"
    "🔮 فال گاتهام — «فال»، یه‌بار در روز\n"
    "🎰 اسلات گاتهام — «اسلات»، شانستو با امتیازت امتحان کن\n"
    "🧩 پرونده روز — «پرونده روز»، یه معمای گاتهامی با جایزه\n"
    "🧠 کدوم شخصیت گاتهامی هستی؟ — «شخصیت گاتهامی»\n"
    "⏳ کپسول زمان — «کپسول <روز> <متن>»، پیام برای آینده‌ی خودت\n"
    "🏅 شهروند نمونه — هر روز یه عضو تصادفی تو گروه معرفی می‌شه\n"
    "🎡 چرخ گردون — یه‌بار در روز، امتیاز رایگان\n"
    "🎫 تیکت پشتیبانی — تو چت خصوصی، «تیکت <متن>»\n"
    "🎂 یادآور تولد یار بتمن — دکمه‌ای، با تقویم شمسی\n"
    "🎙 صدا به متن — ریپلای رو ویس + «متن کن»\n"
    "⏰ یادآور — «یادآور 10 دقیقه/ساعت/روز <متن>» یا «یادآور 14:30 <متن>»\n"
    "🎬 تشخیص فیلم/سریال — ریپلای رو عکس یا ویدیو + «تشخیص فیلم»\n"
    "🎵 تشخیص آهنگ — ریپلای رو ویس/صدا/ویدیو + «تشخیص آهنگ»\n"
    "📝 خلاصه‌ی گروه — بنویس «خلاصه گروه» تا خلاصه‌ی بحث اخیر رو بدم"
)

PANEL_INFO_TEXTS = {
    "panel:fortune_info": "🔮 بنویس «فال» — یه‌بار در روز، فال گاتهامی امروزتو بگیر.",
    "panel:slot_info": "🎰 بنویس «اسلات» — دستگاه اسلات گاتهام رو بچرخون، اگه سه‌تا یکی بشه امتیاز می‌بری.",
    "panel:case_info": "🧩 بنویس «پرونده روز» تا معمای امروز رو ببینی، بعد با «جواب <حدست>» جواب بده.",
    "panel:quiz_info": "🧠 بنویس «شخصیت گاتهامی» و به چند سوال جواب بده تا بفهمی کدوم شخصیتی هستی.",
    "panel:capsule_info": "⏳ بنویس «کپسول <تعداد روز> <متن>» — مثلاً «کپسول 7 سلام به خودم». بعد از اون روزها همون پیام رو برات می‌فرستم.",
    "panel:citizen_info": "🏅 هر روز یه عضو تصادفی از گروه به‌عنوان «شهروند نمونه‌ی امروز» تو خودِ گروه معرفی می‌شه — کاری لازم نیست بکنی.",
    "panel:wheel_info": "🎡 بنویس «چرخ گردون» — یه‌بار تو روز، امتیاز رایگان بگیر.",
    "panel:ticket_info": "🎫 تو چت خصوصی ربات بنویس «تیکت <متن درخواستت>» تا مستقیم برای پشتیبانی بره.",
    "panel:voice_info": "🎙 روی یه پیام صوتی/ویس ریپلای کن و بنویس «متن کن» تا رونویسیش کنم.",
    "panel:lock_info": "🔒 بنویس «قفل گروه» یا «باز کردن گروه» (فقط ادمین‌ها).",
    "panel:purge_info": "🧹 روی قدیمی‌ترین پیامی که می‌خوای حذف بشه ریپلای کن و بنویس «پاکسازی».",
    "panel:reminder_info": "⏰ بنویس «یادآور 10 دقیقه <متن>» یا «یادآور 14:30 <متن>» یا «یادآور فردا 9:00 <متن>». برای دیدن لیست: «یادآورهای من».",
    "panel:movie_info": "🎬 روی یه عکس یا ویدیو (صحنه‌ی فیلم/سریال) ریپلای کن و بنویس «تشخیص فیلم».",
    "panel:song_info": "🎵 روی یه ویس، صدا یا ویدیوی موزیکال ریپلای کن و بنویس «تشخیص آهنگ».",
    "panel:summary_info": "📝 بنویس «خلاصه گروه» تا خلاصه‌ی آخرین پیام‌های گروه رو برات بدم.",
}


def build_mod_panel_keyboard():
    """میانبرهای واقعی «مدیریت گروه» — قبلاً این دکمه فقط یه متن ساکن نشون می‌داد
    و هیچ اکشنی نداشت که باعث می‌شد به‌نظر برسه با «لیست‌ها» قاطی/جابجا شده.
    الان مستقیم به لیست‌های پرکاربرد وصله."""
    rows = [
        [InlineKeyboardButton("🔨 بن‌شده‌ها", callback_data="lists:banned"),
         InlineKeyboardButton("🔇 سکوت‌شده‌ها", callback_data="lists:muted")],
        [InlineKeyboardButton("⚠️ اخطارها", callback_data="lists:warn"),
         InlineKeyboardButton("🛡 معاف‌شده‌ها", callback_data="lists:exempt")],
        [InlineKeyboardButton("🚫 کلمات فیلتر", callback_data="lists:filter"),
         InlineKeyboardButton("🤖 پاسخ خودکار", callback_data="lists:autoreply")],
        [InlineKeyboardButton("🔒 قفل/باز کردن گروه", callback_data="panel:lock_info"),
         InlineKeyboardButton("🧹 پاکسازی", callback_data="panel:purge_info")],
        [InlineKeyboardButton("🔐 امنیت گروه", callback_data="panel:security"),
         InlineKeyboardButton("📋 نمای کلی لیست‌های مدیریتی", callback_data="panel:lists")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ]
    return InlineKeyboardMarkup(rows)


def build_back_keyboard(target="panel:main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=target)]])


def build_persona_panel_keyboard():
    rows = [[InlineKeyboardButton(info["label"], callback_data=f"persona:{key}")] for key, info in PERSONAS.items()]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)


PANEL_MAIN_TEXT = "🦇 *پنل تنظیمات گاتهام*\n\nیه بخش رو انتخاب کن:"

PANEL_TEXTS = {
    "games": (
        "🎮 *بازی‌ها*\n\n"
        "برای شروع هر بازی کافیه اسمش رو تو چت بنویسی، نیازی به / نیست.\n"
        "برای لیست کامل بنویس «گیم»."
    ),
    "downloader": (
        "📥 *دانلودر گاتهام*\n\n"
        "بنویس «دانلودر»، پلتفرم (📸 اینستاگرام / ▶️ یوتیوب / 🎵 تیک‌تاک / 🐦 ایکس / "
        "📌 پینترست / 🎧 ساندکلاود) رو با دکمه انتخاب کن، بعد لینک رو همونجا بفرست.\n"
        "حجم فایل همیشه تو کپشن نشون داده می‌شه.\n\n"
        "⚠️ اگه یوتیوب یا اینستاگرام دانلود نشد و خطای «Sign in to confirm» یا «empty media "
        "response» گرفتی، یعنی اون پلتفرم برای این لینک قفل ضد-ربات گذاشته و برای دور زدنش "
        "ربات نیاز به فایل کوکی (کوکی مرورگر لاگین‌شده) داره — این یه محدودیت سمت یوتیوب/"
        "اینستاگرامه، نه باگ ربات."
    ),
    "mod": (
        "🛡 *مدیریت گروه*\n\n"
        "🆕 روش دکمه‌ای (پیشنهادی): روی پیامِ عضو موردنظر تو گروه ریپلای کن و "
        "بنویس «مدیریت» — یه منوی دکمه‌ای باز می‌شه: بن، کیک، میوت (با انتخاب "
        "مدت)، آنمیوت، اخطار، حذف اخطار، ویژه، معاف، حذف پیام. دیگه نیازی به "
        "یادگیری دستور نیست، فقط لمس کن.\n\n"
        "دستورهای قدیمی هم همچنان کار می‌کنن (با ریپلای رو پیام هدف):\n"
        "/ban /kick /mute [دقیقه] /unmute /delete /warn /unwarn /exempt "
        "/unexempt /special /unspecial\n\n"
        "بدون ریپلای:\n"
        "/filter کلمه — اضافه به فیلتر\n"
        "/unfilter کلمه — حذف از فیلتر\n"
        "/autoreply کلیدواژه | پاسخ — پاسخ خودکار\n"
        "/unautoreply کلیدواژه — حذف پاسخ خودکار\n"
        "/allowusername یوزرنیم — مجاز کردن یوزرنیم\n"
        "/allowforward یوزرنیم کانال — مجاز کردن فوروارد\n"
        "/schedule YYYY-MM-DD HH:MM متن — زمانبندی پست\n\n"
        "یا به زبان طبیعی: «بن کن»، «میوت کن»، «کیک کن»، «پاکش کن»\n\n"
        "🔒 «قفل گروه» / «باز کردن گروه» — بستن/باز کردن ارسال پیام برای همه\n"
        "🧹 ریپلای رو قدیمی‌ترین پیام + «پاکسازی» — حذف دسته‌جمعی پیام‌ها (تا ۱۰۰ تا)"
    ),
    "about": (
        "🦇 *بتمن گاتهام*\n\n"
        "نگهبان تاریک این گروه. چند شخصیت داره، بازی و لیست‌گیری هم بلده.\n"
        "برای چت، تو گروه منشنم کن."
    ),
}
PANEL_TEXTS["words"] = build_words_panel_text()

LIST_TYPES = {
    "special": "اعضای ویژه",
    "filter": "کلمات فیلتر",
    "muted": "سکوت شده‌ها",
    "banned": "بن شده‌ها",
    "warn": "لیست اخطار",
    "exempt": "لیست معاف",
    "autoreply": "پاسخ‌های خودکار",
    "allowed_username": "یوزرنیم مجاز",
    "allowed_forward": "فوروارد مجاز",
    "scheduled": "پست زمانبندی شده",
}


async def build_lists_summary_text(context: ContextTypes.DEFAULT_TYPE, chat_id) -> str:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception:
        admins = []
    n_owner = sum(1 for a in admins if a.status == "creator")
    n_admin = sum(1 for a in admins if a.status == "administrator")

    lines = [
        "📋 *لیست‌های گروه*",
        "",
        f"مالکین : {n_owner}",
        f"مدیران : {n_admin}",
        f"ویژه‌ها : {_list_count(chat_id, 'special')}",
        f"سکوت‌شدگان : {_list_count(chat_id, 'muted')}",
        f"بن‌شدگان : {_list_count(chat_id, 'banned')}",
        f"اخطار گرفتگان : {_list_count(chat_id, 'warn')}",
        f"معاف‌شدگان : {_list_count(chat_id, 'exempt')}",
        f"کلمات فیلتر : {_list_count(chat_id, 'filter')}",
        f"یوزرنیم مجاز : {_list_count(chat_id, 'allowed_username')}",
        f"فوروارد مجاز : {_list_count(chat_id, 'allowed_forward')}",
        f"پاسخ خودکار : {_list_count(chat_id, 'autoreply')}",
        f"پست زمانبندی شده : {_list_count(chat_id, 'scheduled')}",
        "",
        "برای دیدن اعضای هر لیست، از دکمه‌های پایین استفاده کن:",
    ]
    return "\n".join(lines)


def build_lists_keyboard():
    rows = [
        [InlineKeyboardButton("👑 مالکین", callback_data="lists:owners"),
         InlineKeyboardButton("🛡 مدیران", callback_data="lists:admins")],
        [InlineKeyboardButton("🚫 کلمات فیلتر", callback_data="lists:filter"),
         InlineKeyboardButton("⭐ اعضای ویژه", callback_data="lists:special")],
        [InlineKeyboardButton("🔨 بن‌شده‌ها", callback_data="lists:banned"),
         InlineKeyboardButton("🔇 سکوت‌شده‌ها", callback_data="lists:muted")],
        [InlineKeyboardButton("⚠️ لیست اخطار", callback_data="lists:warn"),
         InlineKeyboardButton("🛡 لیست معاف", callback_data="lists:exempt")],
        [InlineKeyboardButton("🤖 پاسخ‌های خودکار", callback_data="lists:autoreply")],
        [InlineKeyboardButton("✅ یوزرنیم مجاز", callback_data="lists:allowed_username"),
         InlineKeyboardButton("↪️ فوروارد مجاز", callback_data="lists:allowed_forward")],
        [InlineKeyboardButton("🗓 پست زمانبندی شده", callback_data="lists:scheduled")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ]
    return InlineKeyboardMarkup(rows)


def build_list_detail_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به لیست‌ها", callback_data="panel:lists")]])


async def build_list_detail_text(context: ContextTypes.DEFAULT_TYPE, chat_id, list_type) -> str:
    if list_type in ("owners", "admins"):
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
        except Exception:
            admins = []
        wanted_status = "creator" if list_type == "owners" else "administrator"
        members = [a.user for a in admins if a.status == wanted_status]
        title = "👑 مالکین" if list_type == "owners" else "🛡 مدیران"
        if not members:
            return f"{title}\n\nکسی تو این لیست نیست."
        lines = [title, ""]
        for u in members:
            name = f"@{u.username}" if u.username else u.first_name
            lines.append(f"• {name}")
        return "\n".join(lines)

    title = LIST_TYPES.get(list_type, list_type)
    items = _list_get(chat_id, list_type)
    if not items:
        return f"📋 {title}\n\nهنوز چیزی به این لیست اضافه نشده."
    lines = [f"📋 {title}", ""]
    for key, value in items:
        if list_type == "autoreply":
            lines.append(f"• {key} ← {value}")
        elif list_type == "warn":
            lines.append(f"• {value or key} اخطار — کاربر {key}")
        elif list_type in ("filter", "allowed_username", "allowed_forward"):
            lines.append(f"• {key}")
        elif list_type == "scheduled":
            preview = value[:40] + ("…" if len(value) > 40 else "")
            lines.append(f"• {key} — {preview}")
        else:
            lines.append(f"• {value or key}")
    return "\n".join(lines)


# =========================================================
#  📋 GOTHAM DATABASE — آمار و رتبه‌بندی (جدا از مدیریت گروه)
# =========================================================
# این بخش صرفاً «اطلاعات، آمار و رتبه‌بندی» نشون می‌ده (طبق مشخصات، جدا از
# 🛡 مدیریت گروه که عملیات مدیریتیه). از همون جدول players / group_lists
# فعلی استفاده می‌کنه — جدول یا سیستم موازی ساخته نشده.

GOTHAM_DATABASE_TEXT = "📋 *GOTHAM DATABASE*\n\nیه بخش رو انتخاب کن:"

GDB_SECTIONS = [
    ("special", "⭐ اعضای ویژه"),
    ("hof", "🏆 تالار افتخار"),
    ("active", "⚡ فعال‌ترین‌ها"),
    ("champs", "🎮 قهرمانان بازی"),
    ("streaks", "🏅 رکورددارن (استریک)"),
    ("badges", "🎖 کلکسیون بج‌ها"),
    ("couples", "💍 روابط گاتهام"),
    ("citizens", "🦇 شهروندان گاتهام"),
    ("newest", "👤 اعضای جدید"),
    ("oldest", "🕰 اعضای قدیمی"),
    ("lowactive", "👻 کم‌فعال‌ها"),
    ("stats", "📊 آمار اعضا"),
]


def build_gotham_database_keyboard():
    rows, row = [], []
    for key, label in GDB_SECTIONS:
        row.append(InlineKeyboardButton(label, callback_data=f"gdb:{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")])
    return InlineKeyboardMarkup(rows)


def build_gdb_detail_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="panel:gdb")]])


def _fmt_name(row) -> str:
    u = row.get("username") or ""
    return f"@{u}" if u else "بازیکن ناشناس"


async def build_gdb_detail_text(context: ContextTypes.DEFAULT_TYPE, chat_id, section: str) -> str:
    label = dict(GDB_SECTIONS).get(section, section)

    if section == "special":
        items = _list_get(chat_id, "special")
        if not items:
            return f"{label}\n\nهنوز عضو ویژه‌ای ثبت نشده."
        lines = [label, ""] + [f"• {value or key}" for key, value in items]
        return "\n".join(lines)

    if section == "hof":
        top_score = await db_run(_get_leaderboard, chat_id, 10)
        top_wins = await db_run(_get_top_wins, chat_id, 5)
        lines = [label, "", "🏆 بهترین بازیکنان (امتیاز):"]
        if top_score:
            lines += [f"{i}. {_fmt_name(r)} — {r['score']} امتیاز" for i, r in enumerate(top_score, 1)]
        else:
            lines.append("— هنوز کسی امتیازی نداره —")
        lines += ["", "🥇 بیشترین برد بازی:"]
        if top_wins:
            lines += [f"{i}. {_fmt_name(r)} — {r['game_wins']} برد" for i, r in enumerate(top_wins, 1)]
        else:
            lines.append("— هنوز کسی بردی نداره —")
        return "\n".join(lines)

    if section == "active":
        rows = await db_run(_get_weekly_activity, chat_id, 10)
        if not rows:
            return f"{label}\n\nهنوز کسی این هفته پیام نداده."
        lines = [label, "", "(بر اساس تعداد پیام تو این هفته)"]
        lines += [f"{i}. {_fmt_name(r)} — {r['week_message_count']} پیام" for i, r in enumerate(rows, 1)]
        return "\n".join(lines)

    if section == "champs":
        rows = await db_run(_get_top_wins, chat_id, 10)
        if not rows:
            return f"{label}\n\nهنوز کسی تو بازی‌ها برد ثبت‌شده‌ای نداره."
        lines = [label, ""]
        for i, r in enumerate(rows, 1):
            total = r["game_wins"] + r["game_losses"]
            rate = (r["game_wins"] / total * 100) if total else 0
            lines.append(f"{i}. {_fmt_name(r)} — {r['game_wins']} برد / {r['game_losses']} باخت ({rate:.0f}٪)")
        return "\n".join(lines)

    if section == "streaks":
        rows = await db_run(_get_top_streaks, chat_id, 10)
        if not rows:
            return f"{label}\n\nهنوز کسی استریک فعالیت نداره."
        lines = [label, "", "(استریک فعالیت روزانه — نه استریک برد؛ چون تو دیتابیس فعلی برد متوالی جداگانه ثبت نمی‌شه)"]
        lines += [f"{i}. {_fmt_name(r)} — {r['streak_days']} روز متوالی" for i, r in enumerate(rows, 1)]
        return "\n".join(lines)

    if section == "badges":
        players = await db_run(_get_all_players_in_chat, chat_id)
        counts = {b_label: 0 for b_label, _ in BADGES}
        for p in players:
            for b_label in get_earned_badges(p):
                counts[b_label] = counts.get(b_label, 0) + 1
        lines = [label, "", "چند نفر از اعضای این گروه هر بج رو گرفتن:"]
        lines += [f"{b_label} — {counts.get(b_label, 0)} نفر" for b_label, _ in BADGES]
        return "\n".join(lines)

    if section == "couples":
        items = _list_get(chat_id, "married")
        if not items:
            return f"{label}\n\nهنوز کسی تو یه رابطه‌ی ثبت‌شده نیست."
        lines = [label, ""]
        for _key, value in items:
            try:
                data = json.loads(value)
                lines.append(f"💍 {data.get('a_name', '؟')} ❤️ {data.get('b_name', '؟')}")
            except Exception:
                continue
        return "\n".join(lines)

    if section == "citizens":
        stats = await db_run(_get_member_stats, chat_id)
        return (
            f"{label}\n\n"
            f"🦇 تعداد کل شهروندان ثبت‌شده: {stats['n']}\n"
            f"📨 مجموع پیام‌های ثبت‌شده: {stats['msgs']}\n"
        )

    if section == "newest":
        rows = await db_run(_get_newest_members, chat_id, 10)
        if not rows:
            return f"{label}\n\nهنوز عضوی با تاریخ عضویت ثبت‌شده نیست."
        lines = [label, ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {_fmt_name(r)}")
        return "\n".join(lines)

    if section == "oldest":
        rows = await db_run(_get_oldest_members, chat_id, 10)
        if not rows:
            return f"{label}\n\nعضوی ثبت نشده."
        lines = [label, ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {_fmt_name(r)}")
        return "\n".join(lines)

    if section == "lowactive":
        rows = await db_run(_get_low_activity_members, chat_id, 10)
        if not rows:
            return f"{label}\n\nهنوز داده‌ای برای این بخش نیست."
        lines = [label, ""]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {_fmt_name(r)} — {r.get('message_count', 0) or 0} پیام")
        return "\n".join(lines)

    if section == "stats":
        stats = await db_run(_get_member_stats, chat_id)
        n = stats["n"] or 1
        return (
            f"{label}\n\n"
            f"🦇 تعداد اعضای فعال ثبت‌شده: {stats['n']}\n"
            f"📨 مجموع پیام‌ها: {stats['msgs']}\n"
            f"🎮 مجموع بردها: {stats['wins']}\n"
            f"💀 مجموع باخت‌ها: {stats['losses']}\n"
            f"📈 میانگین پیام هر عضو: {stats['msgs'] / n:.1f}"
        )

    return f"{label}\n\nاین بخش هنوز داده‌ای نداره."


def build_profile_text(chat, player) -> str:
    # اگه به هر دلیلی (مثلاً دیتای قدیمی/خراب) کلید persona ذخیره‌شده تو
    # PERSONAS نباشه، به‌جای کرش کردن پروفایل، پیش‌فرض «بتمن» رو نشون بده.
    persona = PERSONAS.get(chat["persona"]) or PERSONAS["batman"]
    rank_name = RANKS[min(player["rank_index"], len(RANKS) - 1)]
    char_cost = player["char_level"] * CHAR_LEVEL_COST
    rank_cost = (player["rank_index"] + 1) * RANK_COST_BASE
    inv = get_inventory(player)

    lines = [
        f"{persona['label']} — پروفایل",
        "",
        f"🏆 امتیاز گاتهام : {player['score']}",
        f"⭐ سطح شخصیت : {player['char_level']} / {MAX_CHAR_LEVEL}",
        f"🎖 مقام : {rank_name}",
        "",
        f"⚡️ پوینت باتکیو : {int(player['points_balance'])} / {int(player['points_capacity'])}",
        f"🔋 تولید در ثانیه : {round(player['pps'], 2)}",
        "",
        f"🎒 کوله‌پشتی : 🪃 {inv.get('batarang', 0)}  |  🧪 {inv.get('antidote', 0)}",
        "",
    ]
    if player["char_level"] < MAX_CHAR_LEVEL:
        lines.append(f"💰 هزینه ارتقا سطح شخصیت : {char_cost} امتیاز")
    else:
        lines.append("🔥 سطح شخصیت در حداکثره!")
    if player["rank_index"] < len(RANKS) - 1:
        lines.append(f"💰 هزینه ارتقا مقام : {rank_cost} امتیاز")
    else:
        lines.append("🔥 مقام در حداکثره!")

    lines.append("")
    lines.append(f"🔥 استریک فعالیت : {player.get('streak_days', 0) or 0} روز")
    lines.append(f"⚔️ برد/باخت بازی‌ها : {player.get('game_wins', 0) or 0} / {player.get('game_losses', 0) or 0}")
    badges = get_earned_badges(player)
    if badges:
        lines.append(f"🎖 بج‌ها : {' '.join(badges)}")

    return "\n".join(lines)


def build_profile_keyboard(player):
    buttons = []
    row1 = []
    if player["char_level"] < MAX_CHAR_LEVEL:
        row1.append(InlineKeyboardButton("⚡ ارتقا سطح شخصیت", callback_data="upgrade_level"))
    if player["rank_index"] < len(RANKS) - 1:
        row1.append(InlineKeyboardButton("🎖 ارتقا مقام", callback_data="upgrade_rank"))
    if row1:
        buttons.append(row1)
    buttons.append([
        InlineKeyboardButton("🔋 ارتقا تولید", callback_data="upgrade_pps"),
        InlineKeyboardButton("📦 ارتقا ظرفیت", callback_data="upgrade_capacity"),
    ])
    buttons.append([InlineKeyboardButton("🎭 عوض کردن شخصیت", callback_data="show_characters")])
    buttons.append([
        InlineKeyboardButton("🛒 فروشگاه", callback_data="show_shop"),
        InlineKeyboardButton("🎒 کوله‌پشتی", callback_data="show_bag"),
    ])
    return InlineKeyboardMarkup(buttons)


def build_shop_keyboard():
    rows = []
    for key, item in ITEMS.items():
        rows.append([InlineKeyboardButton(
            f"{item['label']} — {item['price']} پوینت", callback_data=f"buy:{key}"
        )])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="show_profile")])
    return InlineKeyboardMarkup(rows)


def build_bag_text(player) -> str:
    inv = get_inventory(player)
    lines = ["🎒 کوله‌پشتی شما:", ""]
    lines.append(f"🪃 باتارنگ : {inv.get('batarang', 0)} عدد — {ITEMS['batarang']['desc']}")
    lines.append(f"🧪 پادزهر : {inv.get('antidote', 0)} عدد — {ITEMS['antidote']['desc']}")
    return "\n".join(lines)


async def send_profile(update: Update, chat, player, edit=False):
    text = build_profile_text(chat, player)
    keyboard = build_profile_keyboard(player)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


# =========================================================
#  COMMANDS
# =========================================================

async def _is_channel_member(context: ContextTypes.DEFAULT_TYPE, user_id) -> bool:
    """بررسی *زنده* عضویت کاربر تو کانال اجباری از خود Telegram API — هیچ حدس
    یا کشی اینجا نیست، دقیقاً طبق قانون پروژه.

    اگه FORCE_JOIN_ENABLED خاموش باشه (REQUIRED_CHANNEL=off رو Railway)، اصلاً
    Call نمی‌زنیم و مستقیم True برمی‌گردونیم — وگرنه با REQUIRED_CHANNEL خاموش،
    get_chat_member رو یه یوزرنیم نامعتبر (@off) صدا زده می‌شد و همیشه False
    برمی‌گردوند، یعنی خاموش‌کردن گیت عملاً باعث قفل‌شدن /start می‌شد."""
    if not FORCE_JOIN_ENABLED:
        return True
    try:
        member = await context.bot.get_chat_member(f"@{REQUIRED_CHANNEL_USERNAME}", user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning(f"channel membership check failed for user {user_id}: {e}")
        return False


def _channel_join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton("🔄 بررسی عضویت", callback_data="checkjoin")],
    ])


CHANNEL_JOIN_PROMPT_TEXT = (
    "🦇 *ورود به گاتهام*\n\n"
    "برای استفاده از ربات ابتدا باید عضو کانال رسمی شوید."
)

CHANNEL_JOIN_NOT_YET_TEXT = "❌ هنوز عضو کانال نشده‌اید."
CHANNEL_JOIN_OK_TEXT = "✅ عضویت شما تأیید شد."


def _phone_request_keyboard():
    """کیبورد رسمی تلگرام برای درخواست شماره — هیچ گزینه‌ی «رد شدن» نداره چون
    از الان احراز شماره برای استفاده از ربات اجباریه."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 ارسال شماره تلفن", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


PHONE_VERIFY_PROMPT_TEXT = (
    "📱 برای شروع استفاده از ربات، ابتدا شماره تلفنت را با دکمه زیر برای ربات ارسال کن."
)


async def _send_main_start_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متن خوش‌آمدگویی و معرفی اصلی — فقط بعد از تایید شماره نمایش داده می‌شه.

    🦇 بازنویسیِ کامل طبق درخواست «به خودش بیاد و کل قابلیت‌هاشو بگه»: قبلاً
    فقط ۷ خط قدیمی (شخصیت/جنگ/آیتم/سطح/ماموریت/رتبه) بود که خیلی از چیزایی
    که این ماه‌ها ساخته شده (دانلودر، مدیریت گروه، ابزارها، امنیت، لیست‌های
    گاتهام، امکانات دیگه) رو اصلاً نشون نمی‌داد. متن جدید دقیقاً روی همون
    دسته‌بندیِ واقعیِ build_panel_main_keyboard() (که همین الان تو ربات فعاله)
    سوار شده، پس هیچ قابلیتی که واقعاً وجود نداره ادعا نشده."""
    text = (
        "🦇 *GOTHAM AWAKENS* 🦇\n\n"
        "شهر یه محافظ تازه پیدا کرده. من فقط یه ربات نیستم؛\n"
        "*نگهبانِ سایه‌های این گروهم* 🌃\n\n"
        "━━━━━━━━━━━━━━\n"
        "🎭 *۱۹ شخصیت* قابل انتخاب، هرکدوم یه لحن و رفتار جدا\n"
        "🎮 *ده‌ها بازی* گروهی و دونفره — از دوز و مار‌وپله تا UNO، بیلیارد، "
        "قلمرو و سنگ‌کاغذقیچیِ زنده\n"
        "⚔️ سیستم سطح، مقام، آیتم و فروشگاه گاتهام\n"
        "📅 ماموریت روزانه، معمای ریدلر، و کد امنیتیِ هر روز\n"
        "🏆 رتبه‌بندی گروه + «شوالیه‌ی ماه»\n"
        "📥 *دانلودر گاتهام* — یوتیوب، اینستاگرام (پست/ریل/کروسل)، "
        "تیک‌تاک و پینترست، با انتخاب کیفیت و صدا\n"
        "🛡 مدیریت گروه با زبان طبیعی (اخطار/بن/سکوت) + گزارش هفتگی\n"
        "🎂 یادآور تولد با تقویم شمسیِ دکمه‌ای\n"
        "🧰 ابزار: تبدیل واحد، ماشین‌حساب، مبدل فایل و بیشتر\n"
        "🎉 سرگرمی: فال، اعلام نیمه‌شبِ گاتهام، و کلی امکانات دیگه\n"
        "━━━━━━━━━━━━━━"
    )
    await update.effective_message.reply_text(
        text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove()
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # 🃏 اگه کاربر از دیپ‌لینکِ «دستِ کارت‌های بازی کارتی» اومده (چون تو گروه هنوز PV
    # با ربات رو استارت نکرده بود)، دستش رو الان بفرست و بازی رو ادامه بده.
    if context.args and context.args[0] == "cardhand":
        try:
            from card_room import try_resume_after_start
            await try_resume_after_start(update, context)
        except Exception as e:
            log.info(f"start: card room resume failed (harmless): {e}")
    is_new = await db_run(_log_bot_starter, user)
    if is_new and user.id != OWNER_ID:
        try:
            uname = f"@{user.username}" if user.username else "بدون یوزرنیم"
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🦇 یه شهروند جدید وارد گاتهام شد:\n{user.first_name} ({uname}) — آیدی: {user.id}",
            )
        except Exception:
            pass

    # 🔓 قانون مطلق: تو گروه/سوپرگروه، /start (و هر Command دیگه‌ای) هیچ‌وقت
    # شماره یا عضویت کانال نمی‌خواد — این دو تا Gate فقط مخصوص پیویه.
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        await _send_main_start_content(update, context)
        return

    # 📢 مرحله‌ی اول (فقط پیوی): عضویت اجباری در کانال رسمی — قبل از هر چیز
    # دیگه (حتی قبل از احراز شماره) چک می‌شه، طبق ترتیب دقیقِ خواسته‌شده.
    if user.id != OWNER_ID and not await _is_channel_member(context, user.id):
        await update.effective_message.reply_text(
            CHANNEL_JOIN_PROMPT_TEXT, reply_markup=_channel_join_keyboard(), parse_mode="Markdown"
        )
        return
    if user.id != OWNER_ID:
        await db_run(_set_channel_verified, user.id)

    # 📱 احراز شماره یک مرحله‌ی اجباریه: تا کاربر (به‌جز OWNER) شماره‌ش رو تایید
    # نکرده، منوی اصلی/امکانات ربات اصلاً نمایش داده نمی‌شه.
    if user.id != OWNER_ID and not await db_run(_is_phone_verified, user.id):
        await update.effective_message.reply_text(
            PHONE_VERIFY_PROMPT_TEXT, reply_markup=_phone_request_keyboard()
        )
        return

    await _send_main_start_content(update, context)


async def checkjoin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه‌ی «🔄 بررسی عضویت» — همیشه با یه Call زنده به Telegram API چک می‌کنه،
    نه با حدس یا کش."""
    query = update.callback_query
    user = update.effective_user
    is_member = await _is_channel_member(context, user.id)
    if not is_member:
        await query.answer(CHANNEL_JOIN_NOT_YET_TEXT, show_alert=True)
        return
    await query.answer(CHANNEL_JOIN_OK_TEXT)
    await db_run(_set_channel_verified, user.id)
    try:
        await query.edit_message_text(CHANNEL_JOIN_OK_TEXT)
    except Exception:
        pass
    if not await db_run(_is_phone_verified, user.id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=PHONE_VERIFY_PROMPT_TEXT,
            reply_markup=_phone_request_keyboard(),
        )
    else:
        await _send_main_start_content(update, context)


async def handle_shared_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله‌ی اجباری احراز شماره: کاربر با دکمه‌ی رسمی تلگرام شماره‌شو فرستاده.
    فقط وقتی contact.user_id == from_user.id باشه معتبره — یعنی کاربر داره شماره‌ی
    خودش رو می‌فرسته، نه یه مخاطب دیگه رو."""
    contact = update.message.contact
    user = update.effective_user
    if not contact or contact.user_id != user.id:
        await update.message.reply_text(
            "❌ این شماره معتبر نیست. فقط شماره‌ی خودت رو با دکمه‌ی رسمی زیر بفرست.",
            reply_markup=_phone_request_keyboard(),
        )
        return
    await db_run(_set_phone_verified, user.id, contact.phone_number)
    try:
        uname = f"@{user.username}" if user.username else "بدون یوزرنیم"
        # ⚠️ شماره‌ی تلفن کاربر عمداً در این پیام (که فقط برای OWNER_ID ارسال
        # می‌شه، نه لاگ یا چت عمومی) قرار نمی‌گیره تا در جایی افشا نشه.
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"📱 یه کاربر شماره‌شو تایید کرد:\n{user.first_name} ({uname}) — آیدی: {user.id}",
        )
    except Exception:
        pass
    await update.message.reply_text("✅ شماره‌ت تایید شد، خوش اومدی به گاتهام!", reply_markup=ReplyKeyboardRemove())
    await _send_main_start_content(update, context)


async def quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quotes = [
        "من از تاریکی نمی‌ترسم، من خودِ تاریکی‌ام.",
        "این چیزی نیست که من هستم، بلکه کاری‌ست که انجام می‌دهم که مرا تعریف می‌کند.",
        "گاتهام به یک قهرمان نیاز ندارد، به کسی نیاز دارد که واقعیت را بپذیرد.",
    ]
    await update.message.reply_text(random.choice(quotes))


async def characters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = await db_run(_get_player, update.effective_chat.id, update.effective_user.id,
                           update.effective_user.username or "")
    await update.message.reply_text("🎭 یه شخصیت انتخاب کن:", reply_markup=build_characters_keyboard(player))


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_owner = update.effective_user.id == OWNER_ID
    await update.message.reply_text(
        PANEL_MAIN_TEXT, reply_markup=build_panel_main_keyboard(is_owner), parse_mode="Markdown"
    )


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = await db_run(_get_chat, update.effective_chat.id)
    player = await db_run(_get_player, update.effective_chat.id, update.effective_user.id,
                           update.effective_user.username or "")
    player = collect_points(player)
    await db_run(_save_player, player)
    await send_profile(update, chat, player)


async def grouptreport_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    rows = await db_run(_get_weekly_activity, update.effective_chat.id, 10)
    if not rows:
        await update.message.reply_text("📊 هنوز فعالیتی تو این هفته ثبت نشده.")
        return
    lines = ["📊 *گزارش GCPD این هفته — پرفعالیت‌ترین اعضا:*\n"]
    max_count = max(r["week_message_count"] for r in rows) or 1
    for i, r in enumerate(rows, 1):
        name = f"@{r['username']}" if r["username"] else "بدون‌یوزرنیم"
        bar_len = max(1, round((r["week_message_count"] / max_count) * 10))
        bar = "🟩" * bar_len + "⬜" * (10 - bar_len)
        lines.append(f"{i}. {name} — {r['week_message_count']} پیام\n{bar}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def marry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("💍 باید رو پیام کسی که می‌خوای باهاش ازدواج کنی ریپلای بزنی.")
        return
    target = msg.reply_to_message.from_user
    if target.id == user.id or target.is_bot:
        await msg.reply_text("🙂 این‌جوری نمی‌شه.")
        return
    chat_id = update.effective_chat.id
    rows = _list_get(chat_id, "married")
    for key, _ in rows:
        if str(user.id) in key.split("_") or str(target.id) in key.split("_"):
            await msg.reply_text("💔 یکی از شما دو نفر از قبل تو یه رابطه‌ست. اول طلاق بگیر.")
            return
    key = f"{min(user.id, target.id)}_{max(user.id, target.id)}"
    _list_add(chat_id, "married", key, json.dumps({
        "a": user.id, "a_name": user.first_name, "b": target.id, "b_name": target.first_name,
    }))
    await msg.reply_text(f"💍 مبارکه! {user.first_name} و {target.first_name} از این به بعد باهمن 🎉")


async def divorce_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    rows = _list_get(chat_id, "married")
    for key, value in rows:
        if str(user.id) in key.split("_"):
            _list_remove(chat_id, "married", key)
            try:
                data = json.loads(value)
                partner = data["b_name"] if data["a"] == user.id else data["a_name"]
            except Exception:
                partner = "شریکت"
            await update.effective_message.reply_text(f"💔 طلاق از {partner} ثبت شد.")
            return
    await update.effective_message.reply_text("😐 تو الان تو هیچ رابطه‌ای نیستی.")


async def couple_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    rows = _list_get(chat_id, "married")
    for key, value in rows:
        if str(user.id) in key.split("_"):
            try:
                data = json.loads(value)
                partner = data["b_name"] if data["a"] == user.id else data["a_name"]
            except Exception:
                partner = "نامشخص"
            await update.effective_message.reply_text(f"💞 {user.first_name} با {partner} توی یه رابطه‌ست.")
            return
    await update.effective_message.reply_text("😔 تو الان تو هیچ رابطه‌ای نیستی. رو پیام یکی ریپلای بزن و بنویس «ازدواج».")


async def gift_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int):
    msg = update.effective_message
    user = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🎁 باید رو پیام کسی که می‌خوای بهش هدیه بدی ریپلای بزنی.")
        return
    target = msg.reply_to_message.from_user
    if target.id == user.id or target.is_bot:
        await msg.reply_text("🙂 نمی‌تونی به خودت هدیه بدی.")
        return
    if amount <= 0:
        await msg.reply_text("⚠️ مقدار باید بیشتر از صفر باشه.")
        return
    chat_id = update.effective_chat.id
    sender = await db_run(_get_player, chat_id, user.id, user.username or "")
    sender = collect_points(sender)
    if sender["points_balance"] < amount:
        await msg.reply_text(f"⚠️ پوینت کافی نداری (موجودی: {int(sender['points_balance'])}).")
        return
    receiver = await db_run(_get_player, chat_id, target.id, target.username or "")
    receiver = collect_points(receiver)
    sender["points_balance"] -= amount
    receiver["points_balance"] = min(receiver["points_capacity"], receiver["points_balance"] + amount)
    await db_run(_save_player, sender)
    await db_run(_save_player, receiver)
    await msg.reply_text(f"🎁 {user.first_name}، {amount} پوینت به {target.first_name} هدیه دادی!")


async def send_quick_poll(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    parts = [p.strip() for p in payload.split("|")]
    question = parts[0] if parts else ""
    options = [p for p in parts[1:] if p]
    if not question or len(options) < 2:
        await update.effective_message.reply_text(
            "📊 فرمت درست: نظرسنجی سوال | گزینه۱ | گزینه۲ | گزینه۳..."
        )
        return
    await context.bot.send_poll(
        chat_id=update.effective_chat.id, question=question[:300],
        options=[o[:100] for o in options[:10]], is_anonymous=False,
    )


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("🚨 باید رو پیام مشکل‌دار ریپلای بزنی و بنویسی «ریپورت».")
        return
    chat_id = update.effective_chat.id
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
    except Exception:
        await msg.reply_text("⚠️ نتونستم لیست ادمین‌ها رو بگیرم.")
        return
    mentions = " ".join(
        f"[{a.user.first_name}](tg://user?id={a.user.id})" for a in admins if not a.user.is_bot
    )
    reported = msg.reply_to_message.from_user
    await msg.reply_text(
        f"🚨 گزارش تخلف از {reported.first_name if reported else 'کاربر'} توسط {update.effective_user.first_name}\n"
        f"{mentions}",
        parse_mode="Markdown",
    )


def _is_owner(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == OWNER_ID


async def starters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("⛔️ این دستور فقط برای سازنده‌ی ربات فعاله.")
        return
    total = await db_run(_count_bot_starters)
    rows = await db_run(_get_bot_starters, 20)
    lines = [f"🦇 *شهروندای گاتهام* — مجموع: {total} نفر\n"]
    for r in rows:
        name = r["first_name"] or "بی‌نام"
        uname = f"@{r['username']}" if r["username"] else "بدون یوزرنیم"
        dt = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"• {name} ({uname}) — {dt}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("⛔️ این دستور فقط برای سازنده‌ی ربات فعاله.")
        return
    total_starters = await db_run(_count_bot_starters)
    job_active = bool(context.application.job_queue and context.application.job_queue.jobs())
    text = (
        "🦇 *وضعیت باتکیو*\n\n"
        f"🗄 دیتابیس: سالمه ✅\n"
        f"👥 شهروندای ثبت‌شده: {total_starters}\n"
        f"🌑 جاب نیمه‌شب: {'فعاله ✅' if job_active else 'غیرفعاله ⚠️ (jdatetime/job-queue رو چک کن)'}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


def _get_all_group_chat_ids():
    """طبق قرارداد تلگرام، chat_id گروه/سوپرگروه همیشه منفیه و چت خصوصی مثبت؛
    قبلاً این کوئری فیلتر نداشت و اگه یه چت خصوصی هم تو جدول chats (که با
    _get_chat برای هر نوع چتی ساخته می‌شه) ثبت می‌شد، تو شمارش/broadcast
    گروه‌ها هم حساب می‌شد — این فیلتر همون باگ رو رفع می‌کنه.

    is_active=1 هم فیلتر شده: از وقتی گروه‌ها دیگه هیچ‌وقت DELETE نمی‌شن (فقط
    is_active=0 می‌شن)، این فیلتر لازمه تا Broadcast/پیام نیمه‌شب سراغ
    گروه‌هایی که ربات دیگه توشون نیست نره."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT chat_id FROM chats WHERE chat_id < 0 AND is_active = 1")
    ids = [row["chat_id"] for row in c.fetchall()]
    conn.close()
    return ids


def _set_chat_active(chat_id, title, chat_type, is_active: bool, bot_status: str):
    """گروه رو is_active=1/0 می‌کنه بدون این‌که هیچ‌وقت ردیفش رو پاک کنه — این‌جوری
    اگه ربات از یه گروه Kick/Leave و دوباره اضافه بشه، تاریخچه (از جمله
    first_seen واقعی) دست‌نخورده می‌مونه و ردیف تکراری هم ساخته نمی‌شه."""
    conn = _connect()
    c = conn.cursor()
    now = time.time()
    c.execute("SELECT first_seen_ts FROM chats WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    if row is None:
        c.execute(
            "INSERT INTO chats (chat_id, next_switch_at, next_battle_at, title, chat_type, "
            "first_seen_ts, last_seen_ts, is_active, bot_status) VALUES (?,?,?,?,?,?,?,?,?)",
            (chat_id, random.randint(8, 15), random.randint(10, 20), title or "", chat_type or "",
             now, now, 1 if is_active else 0, bot_status),
        )
    else:
        c.execute(
            "UPDATE chats SET title=?, chat_type=?, last_seen_ts=?, is_active=?, bot_status=? WHERE chat_id=?",
            (title or "", chat_type or "", now, 1 if is_active else 0, bot_status, chat_id),
        )
    conn.commit()
    conn.close()


async def groupscount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-Only: تعداد واقعی گروه‌هایی که ربات الان توشونه — از همون جدول
    chats که سیستم broadcast/پیام نیمه‌شب هم استفاده می‌کنن (سیستم موازی نساختیم)."""
    if not _is_owner(update):
        await update.message.reply_text("🔒 این قابلیت در حال حاضر فقط برای Owner فعال است.")
        return
    chat_ids = await db_run(_get_all_group_chat_ids)
    await update.message.reply_text(
        f"📊 ربات الان تو *{len(chat_ids)}* گروه عضوه.\n"
        "(این عدد از جدول واقعی چت‌های ثبت‌شده تو دیتابیس ربات محاسبه شده.)",
        parse_mode="Markdown",
    )


async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📊 آمار گاتهام — داشبورد اصلی Owner: همه‌ی عددها مستقیماً از دیتابیس
    واقعی محاسبه می‌شن (bot_starters برای کاربران، verified_users برای شماره‌ی
    تایید‌شده‌ها، chats با فیلتر chat_id<0 برای گروه‌های واقعی). عدد Hardcode
    یا ساختگی اینجا نیست."""
    if not _is_owner(update):
        await update.message.reply_text("🔒 این قابلیت فقط برای Owner فعال است.")
        return
    total_users = await db_run(_count_bot_starters)
    phone_verified = await db_run(_count_phone_verified)
    group_count = await db_run(_count_real_groups)
    text = (
        "📊 *آمار گاتهام*\n\n"
        f"👥 تعداد کل کاربرانی که ربات را استارت کرده‌اند: {total_users}\n"
        f"📱 تعداد کاربران تایید شماره‌شده: {phone_verified}\n"
        f"🏠 تعداد گروه‌هایی که ربات در آن‌ها فعال است: {group_count}\n\n"
        "برای لیست کامل کاربران: /userslist\n"
        "برای لیست کامل گروه‌ها: /groupslist"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


def _build_users_list_page(page: int):
    total = _count_bot_starters()
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = _get_users_page(page * USERS_PER_PAGE, USERS_PER_PAGE)
    lines = [f"👥 *کاربران گاتهام*\nTotal: {total}\n"]
    start_num = page * USERS_PER_PAGE + 1
    for i, r in enumerate(rows):
        uname = f"@{r['username']}" if r["username"] else "بدون یوزرنیم"
        pv = "✅" if r["phone_verified"] else "❌"
        dt = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d")
        sc = r["start_count"] if "start_count" in r.keys() else 1
        lines.append(
            f"{start_num + i}. {r['first_name'] or 'بی‌نام'} ({uname}) — ID: `{r['user_id']}` — "
            f"📱{pv} — 🔁 {sc} بار Start — اولین: {dt}"
        )
    if len(rows) == 0:
        lines.append("(هیچ کاربری ثبت نشده)")
    text = "\n".join(lines)
    kb_row = []
    if page > 0:
        kb_row.append(InlineKeyboardButton("⬅️", callback_data=f"ulist:{page - 1}"))
    kb_row.append(InlineKeyboardButton(f"صفحه {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        kb_row.append(InlineKeyboardButton("➡️", callback_data=f"ulist:{page + 1}"))
    return text, InlineKeyboardMarkup([kb_row])


async def userslist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("🔒 این قابلیت فقط برای Owner فعال است.")
        return
    text, kb = await db_run(_build_users_list_page, 0)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


def _build_groups_list_page(page: int):
    """لیست کامل تاریخچه‌ی گروه‌ها (فعال + غیرفعال — چون دیگه هیچ ردیفی DELETE
    نمی‌شه). هر گروه یه دکمه‌ی «مدیریت» داره که به صفحه‌ی جزئیات همون گروه
    می‌ره (اطلاعات کامل + گزینه‌ی خروج ربات)."""
    total = _count_real_groups(active_only=False)
    total_pages = max(1, (total + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = _get_groups_page(page * GROUPS_PER_PAGE, GROUPS_PER_PAGE)
    active_n = _count_real_groups(active_only=True)
    lines = [f"🏠 *گروه‌های گاتهام*\nمجموع (تاریخچه‌ی کامل): {total} — فعال الان: {active_n}\n"]
    kb_rows = []
    for r in rows:
        title = r["title"] or "(بدون عنوان)"
        ctype = r["chat_type"] or "-"
        is_active = bool(r["is_active"])
        status_label = "🟢 فعال" if is_active else f"🔴 غیرفعال ({r['bot_status']})"
        last_seen = datetime.fromtimestamp(r["last_seen_ts"]).strftime("%Y-%m-%d %H:%M") if r["last_seen_ts"] else "-"
        lines.append(
            f"• {title} — {status_label}\n  Chat ID: `{r['chat_id']}` — نوع: {ctype} — آخرین فعالیت: {last_seen}"
        )
        kb_rows.append([InlineKeyboardButton(f"⚙️ {title[:24]}", callback_data=f"gdetail:{r['chat_id']}:{page}")])
    if len(rows) == 0:
        lines.append("(هیچ گروهی ثبت نشده)")
    text = "\n\n".join(lines)
    kb_row = []
    if page > 0:
        kb_row.append(InlineKeyboardButton("⬅️", callback_data=f"glist:{page - 1}"))
    kb_row.append(InlineKeyboardButton(f"صفحه {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        kb_row.append(InlineKeyboardButton("➡️", callback_data=f"glist:{page + 1}"))
    kb_rows.append(kb_row)
    return text, InlineKeyboardMarkup(kb_rows)


def _get_chat_row(chat_id):
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT chat_id, title, chat_type, first_seen_ts, last_seen_ts, "
        "COALESCE(is_active,1) as is_active, COALESCE(bot_status,'member') as bot_status "
        "FROM chats WHERE chat_id=?",
        (chat_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def _build_group_detail(chat_id, back_page):
    r = _get_chat_row(chat_id)
    if r is None:
        return "⚠️ این گروه تو دیتابیس پیدا نشد.", InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"glist:{back_page}")]]
        )
    title = r["title"] or "(بدون عنوان)"
    is_active = bool(r["is_active"])
    status_label = "🟢 فعال — ربات الان عضو این گروهه" if is_active else f"🔴 غیرفعال — ربات دیگه عضو نیست ({r['bot_status']})"
    first_seen = datetime.fromtimestamp(r["first_seen_ts"]).strftime("%Y-%m-%d %H:%M") if r["first_seen_ts"] else "-"
    last_seen = datetime.fromtimestamp(r["last_seen_ts"]).strftime("%Y-%m-%d %H:%M") if r["last_seen_ts"] else "-"
    text = (
        f"🏠 *{title}*\n\n"
        f"Chat ID: `{r['chat_id']}`\n"
        f"نوع: {r['chat_type'] or '-'}\n"
        f"وضعیت: {status_label}\n"
        f"اولین بار دیده شده: {first_seen}\n"
        f"آخرین فعالیت: {last_seen}\n\n"
        f"📩 برای ارسال پیام به این گروه، تو پیوی بنویس:\n"
        f"`/msggroup {r['chat_id']} متن پیام`\n\n"
        "🚫 برای بن/آنبن یا مدیریت اعضای این گروه، از همون داخل گروه (با ریپلای "
        "و دستورهای مدیریتی موجود) استفاده کن — این عملیات نیاز به دسترسی ادمین "
        "ربات تو همون گروهه."
    )
    rows = [[InlineKeyboardButton("🔙 بازگشت به لیست", callback_data=f"glist:{back_page}")]]
    if is_active:
        rows.insert(0, [InlineKeyboardButton("🚪 خروج ربات از این گروه", callback_data=f"gleave:{chat_id}:{back_page}")])
    return text, InlineKeyboardMarkup(rows)


async def groupslist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        await update.message.reply_text("🔒 این قابلیت فقط برای Owner فعال است.")
        return
    text, kb = await db_run(_build_groups_list_page, 0)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def list_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permission Check دوباره این‌جا هم انجام می‌شه — محدودیت فقط با مخفی‌کردن
    دکمه کافی نیست، خود Handler هم باید Owner بودن رو تایید کنه."""
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("🔒 این قابلیت فقط برای Owner فعال است.", show_alert=True)
        return
    data = query.data
    if data == "noop":
        await query.answer()
        return
    if data.startswith("ulist:"):
        page = int(data.split(":")[1])
        text, kb = await db_run(_build_users_list_page, page)
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return
    if data.startswith("glist:"):
        page = int(data.split(":")[1])
        text, kb = await db_run(_build_groups_list_page, page)
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return
    if data.startswith("gdetail:"):
        _, chat_id_s, back_page_s = data.split(":")
        text, kb = await db_run(_build_group_detail, int(chat_id_s), int(back_page_s))
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return
    if data.startswith("gleave:"):
        _, chat_id_s, back_page_s = data.split(":")
        chat_id = int(chat_id_s)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله، خارج شو", callback_data=f"gleaveconfirm:{chat_id}:{back_page_s}")],
            [InlineKeyboardButton("❌ انصراف", callback_data=f"gdetail:{chat_id}:{back_page_s}")],
        ])
        await query.answer()
        await query.edit_message_text(
            "⚠️ مطمئنی می‌خوای ربات از این گروه خارج بشه؟ این کار قابل بازگشت نیست "
            "(باید دوباره دستی اد بشه).", reply_markup=kb,
        )
        return
    if data.startswith("gleaveconfirm:"):
        _, chat_id_s, back_page_s = data.split(":")
        chat_id = int(chat_id_s)
        try:
            await context.bot.leave_chat(chat_id)
            # my_chat_member آپدیت جداگونه از تلگرام میاد و is_active رو خودکار
            # صفر می‌کنه؛ ولی برای اطمینان همینجا هم مستقیم آپدیت می‌کنیم که اگه
            # اون آپدیت به هر دلیلی دیر رسید، پنل بلافاصله وضعیت درست رو نشون بده.
            row = await db_run(_get_chat_row, chat_id)
            title = row["title"] if row else ""
            ctype = row["chat_type"] if row else ""
            await db_run(_set_chat_active, chat_id, title, ctype, False, "left")
            await query.answer("✅ ربات از گروه خارج شد.", show_alert=True)
        except Exception as e:
            await query.answer(f"⚠️ نتونستم خارج بشم: {e}", show_alert=True)
        text, kb = await db_run(_build_groups_list_page, int(back_page_s))
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return


# =========================================================
#  🦇 GOTHAM CONTROL PANEL — پنل خصوصی Owner (شماره‌ها/کاربران/گروه‌ها)
# =========================================================
# فقط تو پیوی و فقط برای OWNER_ID فعاله. شماره‌ی تلفن هیچ‌جای دیگه‌ای (گروه،
# لاگ، پیام کاربر عادی) چاپ نمی‌شه — طبق همون قانونی که _set_phone_verified
# رعایتش می‌کنه، این پنل هم تنها مصرف‌کننده‌ی phone_number از دیتابیسه.

OWNER_PANEL_TEXT = (
    "🦇 *GOTHAM OWNER PANEL*\n\n"
    "📱 شماره‌ها\n"
    "👥 اعضای Start کرده\n"
    "🏠 گروه‌ها و کانال‌ها\n\n"
    "یکی از بخش‌ها رو انتخاب کن:"
)


def build_owner_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 شماره‌ها", callback_data="ownerinfo:phones:0")],
        [InlineKeyboardButton("👥 اعضای Start کرده", callback_data="ownerinfo:users:0")],
        [InlineKeyboardButton("🏠 گروه‌ها و کانال‌ها", callback_data="ownerinfo:chats:0")],
    ])


def _count_chats_by_type():
    """آمار واقعی گروه/سوپرگروه/کانال از جدول chats (chat_id<0)، برای هدر
    صفحه‌ی «گروه‌ها و کانال‌ها» تو پنل Owner."""
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT chat_type, COUNT(*) as n FROM chats WHERE chat_id < 0 GROUP BY chat_type")
    rows = c.fetchall()
    conn.close()
    stats = {"group": 0, "supergroup": 0, "channel": 0}
    total = 0
    for r in rows:
        n = r["n"]
        total += n
        t = (r["chat_type"] or "").lower()
        if t in stats:
            stats[t] += n
    stats["total"] = total
    return stats


def _build_ownerinfo_users_page(page: int):
    """صفحه‌ی «اعضای Start کرده» — از جدول bot_starters (LIMIT/OFFSET واقعی)،
    شامل همه‌ی کسانی که حداقل یک‌بار /start زدن، چه شماره‌شون تایید شده باشه
    چه نه؛ هر کاربر چون user_id کلید اصلیه، دوباره شمرده نمی‌شه."""
    total = _count_bot_starters()
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = _get_users_page(page * USERS_PER_PAGE, USERS_PER_PAGE)

    lines = [f"👥 *اعضای Start کرده*\n\n🔢 مجموع: {total}\n"]
    start_num = page * USERS_PER_PAGE + 1
    if rows:
        for i, r in enumerate(rows):
            name = r["first_name"] or "بی‌نام"
            uname = f"@{r['username']}" if r["username"] else "بدون یوزرنیم"
            dt = datetime.fromtimestamp(r["started_at"]).strftime("%Y-%m-%d %H:%M") if r["started_at"] else "-"
            sc = r["start_count"] if "start_count" in r.keys() else 1
            lines.append(
                f"{start_num + i}️⃣ {name}\n"
                f"🆔 `{r['user_id']}`\n"
                f"🔗 {uname}\n"
                f"🔁 تعداد Start: {sc}\n"
                f"🕐 اولین Start: {dt}\n"
            )
    else:
        lines.append("(هنوز کسی ربات رو استارت نکرده)")

    text = "\n".join(lines)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"ownerinfo:users:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 صفحه {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"ownerinfo:users:{page + 1}"))

    kb = InlineKeyboardMarkup([nav_row, [InlineKeyboardButton("🔙 بازگشت", callback_data="ownerinfo:back")]])
    return text, kb


def _build_ownerinfo_chats_page(page: int):
    """صفحه‌ی «گروه‌ها و کانال‌ها» — از جدول chats (فقط chat_id<0، LIMIT/OFFSET
    واقعی)، به‌همراه آمار تفکیکی گروه/سوپرگروه/کانال/مجموع."""
    stats = _count_chats_by_type()
    total = stats["total"]
    total_pages = max(1, (total + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = _get_groups_page(page * GROUPS_PER_PAGE, GROUPS_PER_PAGE)

    lines = [
        "🏠 *گروه‌ها و کانال‌ها*\n",
        f"👥 Groups: {stats['group']}",
        f"🏙️ Supergroups: {stats['supergroup']}",
        f"📢 Channels: {stats['channel']}",
        f"📊 Total: {total}\n",
    ]
    start_num = page * GROUPS_PER_PAGE + 1
    if rows:
        for i, r in enumerate(rows):
            title = r["title"] or "(بدون عنوان)"
            ctype = r["chat_type"] or "-"
            first_seen = (
                datetime.fromtimestamp(r["first_seen_ts"]).strftime("%Y-%m-%d %H:%M")
                if r["first_seen_ts"] else "-"
            )
            last_seen = (
                datetime.fromtimestamp(r["last_seen_ts"]).strftime("%Y-%m-%d %H:%M")
                if r["last_seen_ts"] else "-"
            )
            status = "🟢 فعال" if bool(r["is_active"]) else "🔴 غیرفعال"
            lines.append(
                f"{start_num + i}️⃣ 🏠 {title} — {status}\n"
                f"🆔 `{r['chat_id']}`\n"
                f"📌 {ctype}\n"
                f"🕐 اولین بار: {first_seen}\n"
                f"🕐 آخرین فعالیت: {last_seen}\n"
            )
    else:
        lines.append("(هیچ گروه/کانالی ثبت نشده)")

    text = "\n".join(lines)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"ownerinfo:chats:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 صفحه {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"ownerinfo:chats:{page + 1}"))

    kb = InlineKeyboardMarkup([nav_row, [InlineKeyboardButton("🔙 بازگشت", callback_data="ownerinfo:back")]])
    return text, kb


def _build_phones_list_page(page: int):
    """صفحه‌ی شماره‌ها؛ مستقیم از verified_users خونده می‌شه، فقط همون تعداد
    ردیف لازم برای این صفحه (LIMIT/OFFSET)، نه کل جدول تو RAM."""
    total = _count_phone_verified()
    total_pages = max(1, (total + PHONES_PER_PAGE - 1) // PHONES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = _get_phones_page(page * PHONES_PER_PAGE, PHONES_PER_PAGE)

    lines = [f"📱 *شماره‌های ثبت‌شده*\n\n🔢 مجموع: {total}\n"]
    start_num = page * PHONES_PER_PAGE + 1
    if rows:
        for i, r in enumerate(rows):
            name = r["first_name"] or "بی‌نام"
            uname = f"@{r['username']}" if r["username"] else "بدون یوزرنیم"
            verified_dt = (
                datetime.fromtimestamp(r["verified_at"]).strftime("%Y-%m-%d %H:%M")
                if r["verified_at"] else "-"
            )
            lines.append(
                f"{start_num + i}️⃣ {name} ({uname})\n"
                f"🆔 `{r['user_id']}`\n"
                f"📱 {r['phone_number'] or '-'}\n"
                f"🕒 تایید: {verified_dt}\n"
            )
    else:
        lines.append("(هیچ شماره‌ای هنوز ثبت نشده)")

    text = "\n".join(lines)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"ownerinfo:phones:{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"📄 صفحه {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"ownerinfo:phones:{page + 1}"))

    kb = InlineKeyboardMarkup([nav_row, [InlineKeyboardButton("🔙 بازگشت", callback_data="ownerinfo:back")]])
    return text, kb


async def owner_control_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """با فرستادن «شماره» یا «لیست کامل» تو پیوی، فقط برای Owner. چک Owner
    اینجا هم انجام می‌شه (علاوه بر جایی که این تابع صدا زده می‌شه) تا این
    Entry Point به‌تنهایی هم امن باشه."""
    if not _is_owner(update):
        return
    await update.message.reply_text(
        OWNER_PANEL_TEXT, reply_markup=build_owner_panel_keyboard(), parse_mode="Markdown"
    )


async def owner_control_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback Handler اختصاصی پنل Owner. Permission Check دوباره اینجا هم
    انجام می‌شه — حتی اگه یه Non-owner دستی callback_data بسازه و بفرسته
    (مثلاً ownerinfo:phones:0)، چون چک روی نمایش پنل به‌تنهایی کافی نیست."""
    query = update.callback_query
    if not _is_owner(update):
        await query.answer("🔒 این بخش فقط برای Owner فعاله.", show_alert=True)
        return
    data = query.data

    if data in ("ownerinfo:panel", "ownerinfo:main", "ownerinfo:back"):
        await query.answer()
        await query.edit_message_text(
            OWNER_PANEL_TEXT, reply_markup=build_owner_panel_keyboard(), parse_mode="Markdown"
        )
        return

    if data.startswith("ownerinfo:phones:"):
        page = int(data.split(":")[2])
        text, kb = await db_run(_build_phones_list_page, page)
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("ownerinfo:users:"):
        page = int(data.split(":")[2])
        text, kb = await db_run(_build_ownerinfo_users_page, page)
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("ownerinfo:chats:"):
        page = int(data.split(":")[2])
        text, kb = await db_run(_build_ownerinfo_chats_page, page)
        await query.answer()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    await query.answer()


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اعلامیه‌ی GCPD — فقط اونر می‌تونه به همه‌ی گروه‌ها پیام بفرسته. /broadcast <متن>"""
    if not _is_owner(update):
        await update.message.reply_text("⛔️ این دستور فقط برای سازنده‌ی ربات فعاله.")
        return
    text = update.effective_message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("✏️ استفاده: /broadcast متن اعلامیه")
        return
    chat_ids = await db_run(_get_all_group_chat_ids)
    sent = 0
    for cid in chat_ids:
        try:
            await context.bot.send_message(chat_id=cid, text=f"📡 *اعلامیه‌ی GCPD*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ اعلامیه به {sent} گروه فرستاده شد.")


async def msggroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-Only: /msggroup <chat_id> <متن> — پیام مستقیم به یه گروه خاص، از
    داخل پنل «لیست گروه‌ها» لینک می‌شه."""
    if not _is_owner(update):
        await update.message.reply_text("⛔️ این دستور فقط برای سازنده‌ی ربات فعاله.")
        return
    parts = (update.effective_message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("✏️ استفاده: /msggroup <chat_id> متن پیام")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("⚠️ chat_id باید عددی باشه.")
        return
    text = parts[2]
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
        await update.message.reply_text("✅ پیام فرستاده شد.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ نتونستم پیام رو بفرستم: {e}")


async def gotham_archive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db_run(_get_gotham_events, 5)
    if not rows:
        await update.message.reply_text("🌑 هنوز هیچ رویداد نیمه‌شبی ثبت نشده.")
        return
    lines = ["🗂 *آرشیو گاتهام — آخرین رویدادها:*\n"]
    for r in rows:
        dt = datetime.fromtimestamp(r["ts"]).strftime("%m/%d")
        lines.append(f"`{dt}` {r['event_text']}\n«{r['dialogue_text']}»\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def norm(text: str) -> str:
    return (text or "").strip().replace("ي", "ی").replace("ك", "ک").lower()


def _todays_riddle():
    idx = date.today().timetuple().tm_yday % len(RIDDLES)
    return RIDDLES[idx]


async def riddle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    question, _ = _todays_riddle()
    today = date.today().isoformat()
    winner = _list_get_one(chat_id, "riddle_solved", today)
    if winner:
        await update.message.reply_text(f"❓ *معمای امروز ریدلر:*\n{question}\n\n✅ قبلاً توسط {winner} جواب داده شد.", parse_mode="Markdown")
        return
    await update.message.reply_text(
        f"❓ *معمای امروز ریدلر:*\n{question}\n\nهر کی اول جوابشو تو چت بنویسه، ۵۰ پوینت جایزه می‌گیره!",
        parse_mode="Markdown",
    )


async def _check_riddle_answer(update: Update, chat_id, stripped, player) -> bool:
    """اگه پیام جواب درستِ معمای امروزه و هنوز کسی جواب نداده، جایزه می‌ده و True برمی‌گردونه."""
    _, answer = _todays_riddle()
    today = date.today().isoformat()
    if _list_get_one(chat_id, "riddle_solved", today):
        return False
    if norm(stripped) != norm(answer):
        return False
    _list_add(chat_id, "riddle_solved", today, update.effective_user.first_name)
    player["points_balance"] = min(player["points_capacity"], player["points_balance"] + 50)
    await update.effective_message.reply_text(
        f"🎉 آفرین {update.effective_user.first_name}! جواب درست بود، ۵۰ پوینت گرفتی."
    )
    return True


def _bump_daily_activity(chat_id):
    today = date.today().isoformat()
    conn = _connect()
    c = conn.cursor()
    c.execute(
        "SELECT item_value FROM group_lists WHERE chat_id=? AND list_type='daily_activity' AND item_key=?",
        (chat_id, today),
    )
    row = c.fetchone()
    count = int(row["item_value"]) + 1 if row else 1
    c.execute(
        "INSERT OR REPLACE INTO group_lists (chat_id, list_type, item_key, item_value, added_at) "
        "VALUES (?,'daily_activity',?,?,?)",
        (chat_id, today, str(count), time.time()),
    )
    conn.commit()
    conn.close()
    return count


def _get_last_msg_gap(chat_id) -> float:
    """چند ثانیه از آخرین پیام گروه گذشته؛ همزمان تایم‌استمپ رو آپدیت می‌کنه."""
    now = time.time()
    last = _list_get_one(chat_id, "meta", "last_msg_ts")
    _list_add(chat_id, "meta", "last_msg_ts", str(now))
    if not last:
        return 0
    return now - float(last)


async def security_code_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    today = date.today().isoformat()
    count = int(_list_get_one(chat_id, "daily_activity", today) or 0)
    if count < 20:
        code, emoji = "سبز", "🟢"
    elif count < 60:
        code, emoji = "زرد", "🟡"
    else:
        code, emoji = "قرمز", "🔴"
    await update.message.reply_text(
        f"{emoji} *کد امنیتی گاتهام امروز: {code}*\n"
        f"بر اساس {count} پیام امروز تو این گروه.\n"
        f"«{gotham_signature_line()}»",
        parse_mode="Markdown",
    )


async def handle_bot_removed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی وضعیت عضویت ربات تو یه گروه عوض می‌شه (اضافه/حذف/ارتقا به ادمین و...).

    قبلاً وقتی ربات Kick/Leave می‌شد، ردیف گروه از جدول chats کامل DELETE
    می‌شد — یعنی تاریخچه‌ی گروه برای همیشه از بین می‌رفت و اگه ربات دوباره به
    همون گروه اضافه می‌شد، یه ردیف کاملاً جدید (با first_seen غلط) ساخته می‌شد.
    الان: هیچ‌وقت DELETE نمی‌کنیم؛ فقط is_active/bot_status رو آپدیت می‌کنیم،
    پس تاریخچه‌ی کامل گروه (از جمله first_seen واقعی) همیشه می‌مونه."""
    result = update.my_chat_member
    if not result:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    chat = result.chat
    if old_status in ("member", "administrator") and new_status in ("left", "kicked"):
        await db_run(_set_chat_active, chat.id, chat.title or "", chat.type, False, new_status)
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text="🌑 من رفتم... ولی سایه‌ی گاتهام همیشه یه‌جایی هست.",
            )
        except Exception:
            pass  # معمولاً بعد از اخراج، ارسال پیام دیگه ممکن نیست — طبیعیه
    elif new_status in ("member", "administrator"):
        # ورود اولیه یا ورود دوباره (Rejoin) بعد از یه Kick/Leave قبلی — همون
        # ردیف قبلی رو Update می‌کنیم، ردیف تکراری نمی‌سازیم.
        await db_run(_set_chat_active, chat.id, chat.title or "", chat.type, True, new_status)


async def intro_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🦇 *معرفی نگهبان تاریک گاتهام*\n\n"
        "🎭 ۱۹ شخصیت قابل انتخاب، هر کدوم لحن خودشونو دارن\n"
        "🎮 بازی‌های زیاد: دوز، چهار در ردیف، مافیا، هنگمن، وردل، مین‌یاب و بیشتر\n"
        "🏆 رکورد، بج، استریک، تورنمنت\n"
        "🛡 مدیریت گروه کامل: بن/کیک/میوت/اخطار با انقضا، کپچای عضو جدید\n"
        "🌑 پیام نیمه‌شب خودکار، معمای روزانه، کد امنیتی گروه\n"
        "💞 ازدواج، هدیه، نظرسنجی، ریپورت\n\n"
        "برای دیدن امکانات دکمه‌به‌دکمه بنویس «تنظیمات» یا «پنل»."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def midnight_countdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from midnight_announcement import TEHRAN_TZ
    now = datetime.now(TEHRAN_TZ)
    tomorrow_midnight = datetime.combine(now.date(), datetime.min.time(), tzinfo=TEHRAN_TZ)
    tomorrow_midnight = tomorrow_midnight.replace(day=now.day) + timedelta(days=1)
    remaining = tomorrow_midnight - now
    hours, rem = divmod(int(remaining.total_seconds()), 3600)
    minutes = rem // 60
    await update.message.reply_text(
        f"⏳ تا رویداد بعدی نیمه‌شب گاتهام: {hours} ساعت و {minutes} دقیقه مونده."
    )


async def lockdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قرنطینه‌ی گروه — فقط ادمین. /lockdown <دقیقه>"""
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    minutes = 10
    if context.args and context.args[0].isdigit():
        minutes = int(context.args[0])
    chat_id = update.effective_chat.id
    try:
        await context.bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=False))
    except Exception as e:
        await update.message.reply_text(f"⚠️ نشد: {e}")
        return
    _log_mod_action(chat_id, update.effective_user.first_name, f"قرنطینه ({minutes} دقیقه)", "کل گروه")
    await update.message.reply_text(
        f"🔒 *City Lockdown* — گاتهام برای {minutes} دقیقه قرنطینه شد. هیچ‌کس حرف نمی‌زنه.",
        parse_mode="Markdown",
    )

    async def _lift():
        await asyncio.sleep(minutes * 60)
        try:
            await context.bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=True))
            await context.bot.send_message(chat_id, "🔓 قرنطینه تموم شد. گاتهام دوباره باز شد.")
        except Exception as e:
            # قبلاً این خطا کاملاً بی‌صدا گم می‌شد — یعنی اگه رفع قرنطینه‌ی
            # خودکار شکست می‌خورد (مثلاً چون ربات دیگه دسترسی ادمین نداره)،
            # گروه برای همیشه قفل می‌موند و هیچ‌کس (حتی اونر) خبردار نمی‌شد.
            log.warning(f"رفع خودکار قرنطینه‌ی گروه {chat_id} شکست خورد: {e}")
            try:
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=f"⚠️ نتونستم قرنطینه‌ی گروه `{chat_id}` رو خودکار رفع کنم:\n{e}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    asyncio.create_task(_lift())


async def award_title_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لقب افتخاری اختصاصی — فقط ادمین. رو پیام کسی ریپلای کن: لقب <متن>"""
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    msg = update.effective_message
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🏷 باید رو پیام کسی که می‌خوای بهش لقب بدی ریپلای بزنی.")
        return
    title = msg.text.partition(" ")[2].strip()
    if not title:
        await msg.reply_text("✏️ استفاده: رو پیام کسی ریپلای کن و بنویس «لقب <متن>»")
        return
    target = msg.reply_to_message.from_user
    chat_id = update.effective_chat.id
    _list_add(chat_id, "custom_title", target.id, title[:40])
    await msg.reply_text(f"🏷 از این به بعد {target.first_name} با لقب «{title[:40]}» شناخته می‌شه.")


async def case_file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پرونده‌ی شخصی به سبک GCPD — نسخه‌ی نمایشیِ پروفایل."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    player = await db_run(_get_player, chat_id, user.id, user.username or "")
    title = _list_get_one(chat_id, "custom_title", user.id)
    badges = get_earned_badges(player)
    lines = [
        f"📁 *پرونده‌ی GCPD — {user.first_name}*",
        "〰️〰️〰️〰️〰️〰️〰️",
        f"🎖 رتبه: {RANKS[player['rank_index']]}",
    ]
    if title:
        lines.append(f"🏷 لقب: {title}")
    lines += [
        f"🏆 امتیاز: {int(player['score'])}",
        f"⚔️ برد/باخت: {player['game_wins']} / {player['game_losses']}",
        f"🔥 استریک: {player.get('streak_days', 0) or 0} روز",
    ]
    if badges:
        lines.append(f"🎖 نشان‌ها: {' '.join(badges)}")
    lines.append("〰️〰️〰️〰️〰️〰️〰️")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def datetime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from midnight_announcement import build_full_datetime_text
    text = build_full_datetime_text()
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(text.replace("*", ""))


async def gift_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, item_key: str):
    msg = update.effective_message
    user = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("🎁 باید رو پیام کسی که می‌خوای بهش آیتم بدی ریپلای بزنی.")
        return
    target = msg.reply_to_message.from_user
    if target.id == user.id or target.is_bot:
        await msg.reply_text("🙂 نمی‌تونی به خودت هدیه بدی.")
        return
    chat_id = update.effective_chat.id
    sender = await db_run(_get_player, chat_id, user.id, user.username or "")
    inv = get_inventory(sender)
    if inv.get(item_key, 0) < 1:
        label = ITEMS.get(item_key, {}).get("label", item_key)
        await msg.reply_text(f"⚠️ {label} تو کوله‌پشتیت نداری.")
        return
    inv[item_key] = inv[item_key] - 1
    set_inventory(sender, inv)
    receiver = await db_run(_get_player, chat_id, target.id, target.username or "")
    r_inv = get_inventory(receiver)
    r_inv[item_key] = r_inv.get(item_key, 0) + 1
    set_inventory(receiver, r_inv)
    await db_run(_save_player, sender)
    await db_run(_save_player, receiver)
    label = ITEMS.get(item_key, {}).get("label", item_key)
    await msg.reply_text(f"🎁 {user.first_name} یه {label} به {target.first_name} هدیه داد!")


async def best_friend_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    rows = _list_get(chat_id, "h2h")
    tally = {}
    for key, _value in rows:
        ids = key.split("_")
        if str(user.id) not in ids:
            continue
        other_id = ids[0] if ids[1] == str(user.id) else ids[1]
        tally[other_id] = tally.get(other_id, 0) + 1
    if not tally:
        await update.message.reply_text("😔 هنوز با کسی تو بازی‌ها رقابت نکردی.")
        return
    best_id = max(tally, key=tally.get)
    try:
        member = await context.bot.get_chat_member(chat_id, int(best_id))
        name = member.user.first_name
    except Exception:
        name = "یه رقیب سرسخت"
    await update.message.reply_text(
        f"🤝 بیشترین رقابت بازی {user.first_name} با «{name}» بوده ({tally[best_id]} بازی).\n"
        "بهترین دوست شاید بزرگ‌ترین رقیب باشه، نه؟"
    )


async def compare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        await msg.reply_text("📊 باید رو پیام کسی که می‌خوای باهاش مقایسه بشی ریپلای بزنی.")
        return
    rival = msg.reply_to_message.from_user
    if rival.id == user.id:
        await msg.reply_text("🙂 نمی‌تونی با خودت مقایسه بشی.")
        return
    chat_id = update.effective_chat.id
    p1 = await db_run(_get_player, chat_id, user.id, user.username or "")
    p2 = await db_run(_get_player, chat_id, rival.id, rival.username or "")
    text = (
        f"📊 *{user.first_name} در برابر {rival.first_name}*\n\n"
        f"🏆 امتیاز: {int(p1['score'])} — {int(p2['score'])}\n"
        f"⚔️ برد بازی: {p1['game_wins']} — {p2['game_wins']}\n"
        f"💀 باخت بازی: {p1['game_losses']} — {p2['game_losses']}\n"
        f"🔥 استریک: {p1.get('streak_days', 0) or 0} — {p2.get('streak_days', 0) or 0}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def tournament_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tournament start | join | begin | status — تورنمنت سادهی حذفی برای دوز."""
    chat_id = update.effective_chat.id
    args = context.args
    sub = args[0].lower() if args else "status"

    if sub == "start":
        if not await is_group_admin(update, context):
            await update.message.reply_text("⛔️ فقط ادمین می‌تونه تورنمنت شروع کنه.")
            return
        _list_add(chat_id, "tournament", "players", json.dumps([]))
        _list_add(chat_id, "tournament", "status", "registering")
        await update.message.reply_text(
            "🏆 ثبت‌نام تورنمنت دوز باز شد!\nبرای شرکت بنویس: /tournament join"
        )
        return

    if sub == "join":
        status = _list_get_one(chat_id, "tournament", "status")
        if status != "registering":
            await update.message.reply_text("⚠️ الان تورنمنتی برای ثبت‌نام باز نیست.")
            return
        players = json.loads(_list_get_one(chat_id, "tournament", "players") or "[]")
        uid = update.effective_user.id
        if any(p["id"] == uid for p in players):
            await update.message.reply_text("قبلاً ثبت‌نام کردی.")
            return
        players.append({"id": uid, "name": update.effective_user.first_name})
        _list_add(chat_id, "tournament", "players", json.dumps(players))
        await update.message.reply_text(f"✅ {update.effective_user.first_name} ثبت‌نام شد. ({len(players)} نفر)")
        return

    if sub == "begin":
        if not await is_group_admin(update, context):
            await update.message.reply_text("⛔️ فقط ادمین می‌تونه تورنمنت رو شروع کنه.")
            return
        players = json.loads(_list_get_one(chat_id, "tournament", "players") or "[]")
        if len(players) < 2:
            await update.message.reply_text("⚠️ حداقل ۲ نفر باید ثبت‌نام کرده باشن.")
            return
        random.shuffle(players)
        pairs = [players[i:i + 2] for i in range(0, len(players), 2)]
        lines = ["🏆 *براکت تورنمنت دوز:*\n"]
        for i, pair in enumerate(pairs, 1):
            if len(pair) == 2:
                lines.append(f"مسابقه {i}: {pair[0]['name']} 🆚 {pair[1]['name']}")
            else:
                lines.append(f"مسابقه {i}: {pair[0]['name']} — استراحت (اتوماتیک صعود)")
        lines.append("\nهر دو نفر با هم «دوز» رو بازی کنن و برنده رو با ریپلای به پیام برد، به ادمین اطلاع بدن.")
        _list_add(chat_id, "tournament", "status", "playing")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    status = _list_get_one(chat_id, "tournament", "status") or "بدون تورنمنت فعال"
    players = json.loads(_list_get_one(chat_id, "tournament", "players") or "[]")
    await update.message.reply_text(f"🏆 وضعیت تورنمنت: {status}\nثبت‌نام‌شده‌ها: {len(players)} نفر")


async def record_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رکورد شخصی برد/باخت بازی‌ها؛ اگه ریپلای به یه نفر باشه، رکورد رودررو رو نشون می‌ده."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    player = await db_run(_get_player, chat_id, user.id, user.username or "")

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        rival = update.message.reply_to_message.from_user
        if rival.id != user.id and not rival.is_bot:
            key = f"{min(user.id, rival.id)}_{max(user.id, rival.id)}"
            raw = _list_get_one(chat_id, "h2h", key) or "{}"
            try:
                h2h = json.loads(raw)
            except Exception:
                h2h = {}
            my_wins = h2h.get(str(user.id), 0)
            their_wins = h2h.get(str(rival.id), 0)
            await update.message.reply_text(
                f"⚔️ رکورد رودررو {user.first_name} در برابر {rival.first_name}:\n"
                f"{user.first_name}: {my_wins} برد\n"
                f"{rival.first_name}: {their_wins} برد"
            )
            return

    total = player["game_wins"] + player["game_losses"]
    rate = (player["game_wins"] / total * 100) if total else 0
    await update.message.reply_text(
        f"🏆 رکورد بازی‌های {user.first_name}:\n"
        f"برد: {player['game_wins']} | باخت: {player['game_losses']}\n"
        f"درصد برد: {rate:.0f}٪\n\n"
        f"«{gotham_signature_line()}»"
    )


async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛒 فروشگاه گاتهام:", reply_markup=build_shop_keyboard())


async def bag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = await db_run(_get_player, update.effective_chat.id, update.effective_user.id,
                           update.effective_user.username or "")
    await update.message.reply_text(build_bag_text(player))


async def missions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = await db_run(_get_player, update.effective_chat.id, update.effective_user.id,
                           update.effective_user.username or "")
    player = reset_daily_mission_if_needed(player)
    await db_run(_save_player, player)

    text = (
        "📅 ماموریت روزانه:\n\n"
        f"⚔️ ۳ جنگ رو ببر ({player['wins_today']}/{DAILY_MISSION_TARGET})\n"
    )
    keyboard = None
    if player["wins_today"] >= DAILY_MISSION_TARGET and not player["mission_claimed"]:
        text += "\n✅ ماموریت تکمیل شد! جایزه‌ت رو بگیر."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 دریافت جایزه", callback_data="claim_mission")]])
    elif player["mission_claimed"]:
        text += "\n🎉 جایزه امروز رو گرفتی، فردا دوباره بیا."
    await update.message.reply_text(text, reply_markup=keyboard)


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db_run(_get_leaderboard, update.effective_chat.id, 10)
    if not rows:
        await update.message.reply_text("هنوز کسی امتیازی نگرفته!")
        return
    lines = ["🏆 رتبه‌بندی گروه:\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = r["username"] or "کاربر ناشناس"
        lines.append(f"{medal} @{name} — {r['score']} امتیاز")
    await update.message.reply_text("\n".join(lines))


# =========================================================
#  CALLBACK HANDLER
# =========================================================

    # پیشوندهای callback_data که مال این هندلر نیستن (پنل مدیریت، بازی‌ها، دانلودر،
    # کپچا و ...). قبلاً این هندلر بدون pattern ثبت شده بود و چون گروه ۰ زودتر از
    # گروه‌های ۱ به بعد چک می‌شه، برای هر تاچ روی این دکمه‌ها اول اینجا falseهای
    # الکی answer/save می‌کرد و کلی پردازش اضافه/ریسک بی‌مورد داشت. الان صریح ردشون
    # می‌کنیم تا فقط هندلر واقعیِ خودشون (تو گروه درست) پردازشش کنه.
_FOREIGN_CALLBACK_PREFIXES = (
    "adm:", "captcha:", "gm:", "dl:pick:",
    "chess:", "ludo:", "snake:",
    "rps:", "trivia:", "ttt:", "c4:", "lobby:",
    "g2048:", "lo:", "mm:", "bs:", "lobby2:", "tg:", "noop",
    "ms:", "dots:", "tiko:", "jamshid:", "bazar:", "lobby4:",
    "uno:", "ter:", "bil:", "lobby5:", "race:", "noop5",
    "gttt:", "ittt:", "sec:", "tool:", "fun:", "quiz:",
    "grps:", "bday:",
    # 🃏 اتاق پاسور (card_room.py) — قبلاً اینجا نبودن، برای همین button_handler
    # (گروه ۰، بدون pattern) اول query.answer() رو مصرف می‌کرد و بعد هندلر واقعیِ
    # card_room (گروه ۵) موقع answer/edit خودش با خطای "قبلاً answer شده" مواجه
    # می‌شد و دکمه بی‌صدا هیچ کاری نمی‌کرد. رفع باگ: همون الگوی lobby4/lobby5/gttt/grps.
    "cr:", "war:", "bj21:", "bjd:", "hokm:", "haft:", "charbarg:", "rummy:", "poker:",
    # 🕵️ پرونده روز (fortune_and_extras.py, Phase 6) — همون کلاس باگِ Phase 0؛
    # هر callback جدید که تو یه فایل دیگه (نه bot.py) با CallbackQueryHandler
    # مخصوص خودش ثبت می‌شه، باید همینجا هم اضافه بشه وگرنه button_handler
    # (گروه ۰، بدون pattern) اول answer() رو مصرف می‌کنه.
    "case:",
    # 🎬 تشخیص رسانه (media_recognition.py) — همون کلاس باگ؛ دکمه‌های "تشخیص
    # فیلم/سریال" و "تشخیص آهنگ" هم قبلاً تو این لیست نبودن.
    "mr:",
    # 🦇 پنل کنترل Owner (شماره‌ها/کاربران/گروه‌ها) — هندلر مخصوص خودش
    # (owner_control_callback) با pattern جدا ثبت می‌شه؛ دفاع دوم اینجا.
    "ownerinfo:",
)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if data.startswith(_FOREIGN_CALLBACK_PREFIXES):
        return

    # نکته‌ی مهم: تلگرام فقط یه‌بار اجازه‌ی answer به هر callback query رو می‌ده.
    # قبلاً اینجا همیشه query.answer() خالی صدا زده می‌شد و بعد پایین‌تر
    # برای دکمه‌های PANEL_INFO_TEXTS دوباره query.answer(..., show_alert=True)
    # صدا زده می‌شد که همیشه خطا می‌داد و باعث می‌شد دکمه هیچ کاری نکنه.
    if data in PANEL_INFO_TEXTS:
        await query.answer(PANEL_INFO_TEXTS[data], show_alert=True)
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    await query.answer()

    chat = await db_run(_get_chat, chat_id)
    player = await db_run(_get_player, chat_id, user_id, username)
    player = collect_points(player)

    if data == "show_characters":
        await query.edit_message_text("🎭 یه شخصیت انتخاب کن:", reply_markup=build_characters_keyboard(player))
        await db_run(_save_player, player)
        return

    if data == "show_profile":
        await query.edit_message_text(build_profile_text(chat, player), reply_markup=build_profile_keyboard(player))
        await db_run(_save_player, player)
        return

    if data == "show_shop":
        await query.edit_message_text("🛒 فروشگاه گاتهام:", reply_markup=build_shop_keyboard())
        await db_run(_save_player, player)
        return

    if data == "show_bag":
        await query.edit_message_text(build_bag_text(player))
        await db_run(_save_player, player)
        return

    if data == "panel:main":
        is_owner = query.from_user.id == OWNER_ID
        await query.edit_message_text(
            PANEL_MAIN_TEXT, reply_markup=build_panel_main_keyboard(is_owner), parse_mode="Markdown"
        )
        return

    if data == "panel:persona":
        await query.edit_message_text("🎭 یه شخصیت انتخاب کن:", reply_markup=build_persona_panel_keyboard())
        return

    if data == "panel:daily":
        # 🎯 GOTHAM DAILY CHALLENGE (Phase 6) — سیستم ماموریت روزانه از قبل
        # کامل تو bot.py وجود داشت (reset_daily_mission_if_needed، دکمه‌ی
        # claim_mission، همه‌چی) ولی فقط از دستور /missions در دسترس بود؛
        # سیستم اقتصادی جدیدی ساخته نشد، فقط از Control Center بهش وصل شدیم.
        player = await db_run(_get_player, chat_id, update.effective_user.id, update.effective_user.username or "")
        player = reset_daily_mission_if_needed(player)
        await db_run(_save_player, player)
        text = (
            "🎯 *چالش روزانه گاتهام*\n\n"
            f"⚔️ ۳ جنگ رو ببر ({player['wins_today']}/{DAILY_MISSION_TARGET})\n"
        )
        kb_rows = []
        if player["wins_today"] >= DAILY_MISSION_TARGET and not player["mission_claimed"]:
            text += "\n✅ ماموریت تکمیل شد! جایزه‌ت رو بگیر."
            kb_rows.append([InlineKeyboardButton("🎁 دریافت جایزه", callback_data="claim_mission")])
        elif player["mission_claimed"]:
            text += "\n🎉 جایزه امروز رو گرفتی، فردا دوباره بیا."
        kb_rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel:new")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode="Markdown")
        return

    if data == "panel:lists":
        text = await build_lists_summary_text(context, chat_id)
        await query.edit_message_text(text, reply_markup=build_lists_keyboard(), parse_mode="Markdown")
        return

    if data == "panel:gdb":
        await query.edit_message_text(
            GOTHAM_DATABASE_TEXT, reply_markup=build_gotham_database_keyboard(), parse_mode="Markdown"
        )
        return

    if data.startswith("gdb:"):
        section = data.split(":", 1)[1]
        text = await build_gdb_detail_text(context, chat_id, section)
        try:
            await query.edit_message_text(text, reply_markup=build_gdb_detail_keyboard(), parse_mode="Markdown")
        except Exception:
            await query.edit_message_text(text.replace("*", ""), reply_markup=build_gdb_detail_keyboard())
        return

    if data == "panel:bug" or data.startswith("bug:"):
        # 🛠 رفع باگ ربات — فقط Owner/Admin. قبلاً هیچ محدودیتی نبود و Traceback
        # کامل برای هر کسی که این دکمه رو می‌زد نمایش داده می‌شد؛ این خودش یه
        # نشتی اطلاعاتیه که با همین تغییر جمع شد.
        if not _is_owner(update):
            await query.answer("این بخش فقط برای مالک ربات در دسترسه.", show_alert=True)
            return

        if data == "panel:bug":
            text = (
                "🛠 *رفع باگ ربات*\n\n"
                f"🚨 {len(RECENT_ERRORS)} خطا تو حافظه‌ی این اجرای ربات ثبت شده.\n\n"
                "یه بخش رو انتخاب کن:"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 خطاهای اخیر", callback_data="bug:recent"),
                 InlineKeyboardButton("📜 لاگ خطاها (دسته‌بندی)", callback_data="bug:cat")],
                [InlineKeyboardButton("📊 وضعیت ربات", callback_data="bug:status"),
                 InlineKeyboardButton("🧹 پاک کردن لاگ", callback_data="bug:clear")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
            ])
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            return

        if data == "bug:recent":
            await query.edit_message_text(
                recent_errors_text(), reply_markup=build_back_keyboard("panel:bug"), parse_mode="Markdown"
            )
            return

        if data == "bug:cat":
            counts = category_counts()
            rows = []
            for cat_key, (label, _kw) in BUG_CATEGORIES.items():
                rows.append([InlineKeyboardButton(f"{label} ({counts.get(cat_key, 0)})", callback_data=f"bug:cat:{cat_key}")])
            rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="panel:bug")])
            await query.edit_message_text(
                "📜 *لاگ خطاها*\n\nیه دسته رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown"
            )
            return

        if data.startswith("bug:cat:"):
            cat_key = data.split(":", 2)[2]
            text = errors_by_category_text(cat_key)
            await query.edit_message_text(text, reply_markup=build_back_keyboard("bug:cat"), parse_mode="Markdown")
            return

        if data == "bug:status":
            text = await health_check_text(context)
            await query.edit_message_text(text, reply_markup=build_back_keyboard("panel:bug"), parse_mode="Markdown")
            return

        if data == "bug:clear":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، پاک کن", callback_data="bug:clear:confirm"),
                 InlineKeyboardButton("❌ نه", callback_data="panel:bug")],
            ])
            await query.edit_message_text(
                f"🧹 مطمئنی می‌خوای هر {len(RECENT_ERRORS)} خطای ثبت‌شده رو پاک کنی؟", reply_markup=kb
            )
            return

        if data == "bug:clear:confirm":
            n = clear_log()
            await query.answer(f"{n} خطا پاک شد.")
            await query.edit_message_text(
                recent_errors_text(), reply_markup=build_back_keyboard("panel:bug"), parse_mode="Markdown"
            )
            return

        return

    if data == "panel:games":
        await query.edit_message_text(
            GAMES_MENU_MAIN_TEXT, reply_markup=build_games_menu_root_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "panel:active_games":
        # 🎮 بازی‌های فعال من (Phase 5). فعلاً روی بازی‌های کارتیِ war/bj21/
        # blackjack/charbarg/rummy/poker/hokm/haft و سنگ‌کاغذقیچی گروهی کار می‌کنه —
        # دورهم‌جمع (تیک‌تاک‌تو) تو state‌ش chat_id نداره (جزئیات کامل تو گزارش نهایی).
        uid = update.effective_user.id
        lines = ["🎮 *بازی‌های فعال من*", ""]
        found = False
        for label, chat_id, opp_name, my_turn in active_card_games_for_user(uid):
            found = True
            turn_note = "📍 نوبت توئه" if my_turn else ("📍 نوبت حریفه" if my_turn is False else "")
            lines.append(f"🃏 {label} — 👥 {opp_name}" + (f" — {turn_note}" if turn_note else ""))
        for gid, game in GRPS_GAMES.items():
            if uid not in (game.get("creator_id"), game.get("opponent_id")):
                continue
            found = True
            opp = game["opponent_name"] if uid == game["creator_id"] else game["creator_name"]
            phase_note = "⏳ منتظر بازیکن دوم" if game.get("phase") == "waiting" else "⚔️ در حال نبرد"
            lines.append(f"🎮 سنگ‌کاغذقیچی — 👥 {opp} — {phase_note}")
        gotham_lines = gotham_status_lines_for_user(uid)
        if gotham_lines:
            found = True
            lines.extend(gotham_lines)
        if not found:
            lines.append("فعلاً تو هیچ بازی‌ای نیستی.")
        await query.edit_message_text(
            "\n".join(lines), reply_markup=build_back_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "panel:downloader":
        await query.edit_message_text(DOWNLOADER_HELP_TEXT, reply_markup=dl_menu_markup())
        return

    if data == "panel:mod":
        # باگ رفع‌شده: قبلاً این دکمه با «درباره ربات»/«کلمات ربات» یکی بود و فقط
        # یه متن ساکن نشون می‌داد (بدون هیچ دکمه‌ی عملیاتی) — از این‌جهت با
        # «لیست‌ها» (که واقعاً دکمه‌های عملیاتی داشت) قاطی/جابجا به‌نظر می‌رسید.
        # الان خودش هم دکمه‌های واقعی داره (میانبر به لیست‌های بن/میوت/اخطار/فیلتر).
        try:
            await query.edit_message_text(
                PANEL_TEXTS["mod"], reply_markup=build_mod_panel_keyboard(), parse_mode="Markdown"
            )
        except Exception:
            await query.edit_message_text(
                PANEL_TEXTS["mod"].replace("*", ""), reply_markup=build_mod_panel_keyboard()
            )
        return

    if data == "panel:new":
        await query.edit_message_text(NEW_FEATURES_TEXT, reply_markup=build_new_features_keyboard(), parse_mode="Markdown")
        return

    if data == "panel:security":
        text, kb = await build_security_text_and_kb(context.application.bot_data["security_deps"], chat_id)
        await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "panel:tools":
        await query.edit_message_text(TOOLS_TEXT, reply_markup=tools_menu_keyboard(), parse_mode="Markdown")
        return

    if data == "panel:fun":
        await query.edit_message_text(FUN_TEXT, reply_markup=fun_menu_keyboard(), parse_mode="Markdown")
        return

    if data == "panel:words":
        if query.from_user.id != OWNER_ID:
            await query.answer("⛔ این بخش فقط برای مالک ربات در دسترسه.", show_alert=True)
            return
        try:
            await query.edit_message_text(
                PANEL_TEXTS["words"], reply_markup=build_back_keyboard(), parse_mode="Markdown"
            )
        except Exception:
            await query.edit_message_text(
                PANEL_TEXTS["words"].replace("*", ""), reply_markup=build_back_keyboard()
            )
        return

    if data == "panel:about":
        try:
            await query.edit_message_text(
                PANEL_TEXTS["about"], reply_markup=build_back_keyboard(), parse_mode="Markdown"
            )
        except Exception:
            await query.edit_message_text(
                PANEL_TEXTS["about"].replace("*", ""), reply_markup=build_back_keyboard()
            )
        return

    if data.startswith("lists:"):
        list_type = data.split(":", 1)[1]
        text = await build_list_detail_text(context, chat_id, list_type)
        await query.edit_message_text(text, reply_markup=build_list_detail_keyboard())
        return

    if data.startswith("persona:"):
        persona_key = data.split(":", 1)[1]
        info = PERSONAS.get(persona_key)
        if info is None:
            return
        if player["char_level"] < info["unlock_level"]:
            await query.answer(
                f"🔒 قفله! باید سطح شخصیتت حداقل {info['unlock_level']} باشه (الان: {player['char_level']}).",
                show_alert=True,
            )
            return
        chat["persona"] = persona_key
        chat["since_switch"] = 0
        chat["next_switch_at"] = random.randint(8, 15)
        await db_run(_save_chat, chat)
        await query.edit_message_text(f"{info['label']} فعال شد. بنویس تا جواب بده!")
        await db_run(_save_player, player)
        return

    if data == "upgrade_level":
        cost = player["char_level"] * CHAR_LEVEL_COST
        if player["char_level"] >= MAX_CHAR_LEVEL:
            await query.answer("در حداکثر سطحی!", show_alert=True)
        elif player["score"] >= cost:
            player["score"] -= cost
            player["char_level"] += 1
            await db_run(_save_player, player)
            await query.edit_message_text(build_profile_text(chat, player), reply_markup=build_profile_keyboard(player))
        else:
            await query.answer(f"امتیاز کافی نداری! به {cost} امتیاز نیاز داری.", show_alert=True)
        return

    if data == "upgrade_rank":
        cost = (player["rank_index"] + 1) * RANK_COST_BASE
        if player["rank_index"] >= len(RANKS) - 1:
            await query.answer("در بالاترین مقامی!", show_alert=True)
        elif player["score"] >= cost:
            player["score"] -= cost
            player["rank_index"] += 1
            await db_run(_save_player, player)
            await query.edit_message_text(build_profile_text(chat, player), reply_markup=build_profile_keyboard(player))
        else:
            await query.answer(f"امتیاز کافی نداری! به {cost} امتیاز نیاز داری.", show_alert=True)
        return

    if data == "upgrade_pps":
        if player["points_balance"] >= PPS_UPGRADE_COST:
            player["points_balance"] -= PPS_UPGRADE_COST
            player["pps"] += PPS_UPGRADE_GAIN
            await db_run(_save_player, player)
            await query.edit_message_text(build_profile_text(chat, player), reply_markup=build_profile_keyboard(player))
        else:
            await query.answer(f"پوینت کافی نداری! به {PPS_UPGRADE_COST} پوینت نیاز داری.", show_alert=True)
        return

    if data == "upgrade_capacity":
        if player["points_balance"] >= CAPACITY_UPGRADE_COST:
            player["points_balance"] -= CAPACITY_UPGRADE_COST
            player["points_capacity"] += CAPACITY_UPGRADE_GAIN
            await db_run(_save_player, player)
            await query.edit_message_text(build_profile_text(chat, player), reply_markup=build_profile_keyboard(player))
        else:
            await query.answer(f"پوینت کافی نداری! به {CAPACITY_UPGRADE_COST} پوینت نیاز داری.", show_alert=True)
        return

    if data.startswith("buy:"):
        item_key = data.split(":", 1)[1]
        item = ITEMS.get(item_key)
        if item is None:
            return
        if player["points_balance"] >= item["price"]:
            player["points_balance"] -= item["price"]
            inv = get_inventory(player)
            inv[item_key] = inv.get(item_key, 0) + 1
            set_inventory(player, inv)
            await db_run(_save_player, player)
            await query.answer(f"{item['label']} خریداری شد!", show_alert=True)
        else:
            await query.answer(f"پوینت کافی نداری! به {item['price']} پوینت نیاز داری.", show_alert=True)
        return

    if data == "claim_mission":
        player = reset_daily_mission_if_needed(player)
        if player["wins_today"] >= DAILY_MISSION_TARGET and not player["mission_claimed"]:
            player["mission_claimed"] = 1
            player["score"] += DAILY_MISSION_REWARD_SCORE
            player["points_balance"] = min(
                player["points_capacity"], player["points_balance"] + DAILY_MISSION_REWARD_POINTS
            )
            await db_run(_save_player, player)
            await query.edit_message_text(
                f"🎁 جایزه گرفتی: +{DAILY_MISSION_REWARD_SCORE} امتیاز و +{DAILY_MISSION_REWARD_POINTS} پوینت!"
            )
        else:
            await query.answer("هنوز ماموریت تکمیل نشده یا قبلاً گرفتیش.", show_alert=True)
        return

    await db_run(_save_player, player)


# =========================================================
#  BATTLE LOGIC
# =========================================================

async def maybe_start_battle(update: Update, chat) -> bool:
    chat["since_battle"] += 1
    if chat["battle_enemy"] == "" and chat["since_battle"] >= chat["next_battle_at"]:
        enemy = random.choice(ENEMIES)
        chat["battle_enemy"] = enemy["name"]
        chat["battle_attempts"] = 0
        chat["battle_max_attempts"] = 3
        chat["since_battle"] = 0
        chat["next_battle_at"] = random.randint(10, 20)
        await update.message.reply_text(
            f"🚨 جنگ شد! گاتهام تو خطره!\n{enemy['clue']}\n"
            f"زود حدس بزن این دشمن کیه (فقط اسمشو بنویس)!"
        )
        return True
    return False


async def handle_battle_guess(update: Update, chat, player, text: str) -> bool:
    if not chat["battle_enemy"]:
        return False

    enemy = next((e for e in ENEMIES if e["name"] == chat["battle_enemy"]), None)
    if enemy is None:
        chat["battle_enemy"] = ""
        return False

    guess = text.strip().lower()
    if guess in [a.lower() for a in enemy["aliases"]]:
        inv = get_inventory(player)
        multiplier = 1
        if inv.get("batarang", 0) > 0:
            inv["batarang"] -= 1
            set_inventory(player, inv)
            multiplier = 2
        reward = 10 * player["char_level"] * multiplier
        player["score"] += reward
        player = reset_daily_mission_if_needed(player)
        player["wins_today"] += 1

        chat["battle_enemy"] = ""
        chat["battle_attempts"] = 0

        extra = " (باتارنگ استفاده شد ⚡️۲x)" if multiplier == 2 else ""
        msg = f"✅ درسته! {enemy['name']} رو شکست دادی و گاتهام رو نجات دادی!\n+{reward} امتیاز 🏆{extra}"
        if player["wins_today"] == DAILY_MISSION_TARGET and not player["mission_claimed"]:
            msg += "\n\n📅 ماموریت روزانه تکمیل شد! با /missions جایزه‌تو بگیر."
        await update.message.reply_text(msg)
    else:
        chat["battle_attempts"] += 1
        inv = get_inventory(player)
        if chat["battle_attempts"] >= chat["battle_max_attempts"] and inv.get("antidote", 0) > 0:
            inv["antidote"] -= 1
            set_inventory(player, inv)
            chat["battle_max_attempts"] += 1
            await update.message.reply_text("🧪 پادزهر مصرف شد! یه فرصت اضافه گرفتی.")
        if chat["battle_attempts"] >= chat["battle_max_attempts"]:
            enemy_name = enemy["name"]
            chat["battle_enemy"] = ""
            chat["battle_attempts"] = 0
            await update.message.reply_text(
                f"❌ وقتت تموم شد! دشمن {enemy_name} بود. گاتهام یه ضربه خورد، ولی بازم می‌جنگیم!"
            )
        else:
            await update.message.reply_text("❌ غلطه، دوباره حدس بزن!")
    return True


# =========================================================
#  MODERATION COMMANDS (فقط ادمین‌ها)
# =========================================================

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
    except Exception as e:
        await update.message.reply_text(f"⚠️ نشد: {e}")
        return
    _list_add(chat_id, "banned", target.id, target.username or target.first_name or "")
    _list_remove(chat_id, "muted", target.id)
    _log_mod_action(chat_id, update.effective_user.first_name, "بن", target.first_name)
    ban_count = int(_list_get_one(chat_id, "ban_count", target.id) or 0) + 1
    _list_add(chat_id, "ban_count", target.id, ban_count)
    await update.message.reply_text(f"🔨 {target.first_name} فرستاده شد به آرکهام، برای همیشه.")


async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await context.bot.unban_chat_member(chat_id, target.id, only_if_banned=True)
    except Exception as e:
        await update.message.reply_text(f"⚠️ نشد: {e}")
        return
    _log_mod_action(chat_id, update.effective_user.first_name, "کیک", target.first_name)
    await update.message.reply_text(f"👢 {target.first_name} از گاتهام بیرون انداخته شد (موقت، می‌تونه برگرده).")


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    minutes = 60
    if context.args:
        try:
            minutes = int(context.args[0])
        except ValueError:
            pass
    until = int(time.time() + minutes * 60)
    try:
        await context.bot.restrict_chat_member(
            chat_id, target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ نشد: {e}")
        return
    _list_add(chat_id, "muted", target.id, target.username or target.first_name or "")
    _log_mod_action(chat_id, update.effective_user.first_name, f"میوت {minutes} دقیقه", target.first_name)
    await update.message.reply_text(f"🔇 {target.first_name} به مدت {minutes} دقیقه تو سلول سکوت آرکهام موند.")


async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.restrict_chat_member(
            chat_id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ نشد: {e}")
        return
    _list_remove(chat_id, "muted", target.id)
    _log_mod_action(chat_id, update.effective_user.first_name, "آنمیوت", target.first_name)
    await update.message.reply_text(f"🔊 {target.first_name} از سلول سکوت آزاد شد.")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ باید این دستور رو روی یه پیام ریپلای کنی.")
        return
    try:
        await update.message.reply_to_message.delete()
        await update.message.delete()
    except Exception as e:
        await update.message.reply_text(f"⚠️ نشد: {e}")


async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    current, added_at = _list_get_one_added_at(chat_id, "warn", target.id)
    # اگه آخرین اخطار بیش از ۳۰ روز پیش بوده، از صفر شروع کن
    if current and added_at and (time.time() - added_at) > WARN_EXPIRY_SECONDS:
        current = None
    count = int(current) + 1 if current else 1
    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat_id, target.id)
            _list_add(chat_id, "banned", target.id, target.username or target.first_name or "")
            _list_remove(chat_id, "warn", target.id)
            _log_mod_action(chat_id, update.effective_user.first_name, "بن (۳ اخطار)", target.first_name)
            await update.message.reply_text(f"🚨 {target.first_name} به ۳ اخطار رسید و بن شد.")
        except Exception as e:
            await update.message.reply_text(f"⚠️ نشد بن کنم: {e}")
        return
    _list_add(chat_id, "warn", target.id, count)
    _log_mod_action(chat_id, update.effective_user.first_name, f"اخطار ({count}/۳)", target.first_name)
    await update.message.reply_text(
        f"⚠️ {target.first_name} اخطار گرفت ({count}/۳). اخطارها بعد ۳۰ روز بدون تکرار پاک می‌شن."
    )
    if count == 2:
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            mentions = " ".join(
                f"[{a.user.first_name}](tg://user?id={a.user.id})" for a in admins if not a.user.is_bot
            )
            await update.message.reply_text(
                f"🚨 هشدار GCPD: {target.first_name} یه اخطار دیگه تا اخراج فاصله داره.\n{mentions}",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def unwarn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "warn", target.id)
    _log_mod_action(chat_id, update.effective_user.first_name, "پاک‌کردن اخطار", target.first_name)
    await update.message.reply_text(f"✅ اخطارهای {target.first_name} پاک شد.")


async def modlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    rows = _get_mod_log(update.effective_chat.id, limit=15)
    if not rows:
        await update.message.reply_text("📋 GCPD هنوز هیچ پرونده‌ای ثبت نکرده.")
        return
    lines = ["📋 *پرونده‌های GCPD — آخرین اکشن‌های مدیریتی:*\n"]
    for r in rows:
        dt = datetime.fromtimestamp(r["ts"]).strftime("%m/%d %H:%M")
        lines.append(f"`{dt}` — {r['admin_name']} ⬅️ {r['action']} ⬅️ {r['target_name']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def exempt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    _list_add(chat_id, "exempt", target.id, target.username or target.first_name or "")
    await update.message.reply_text(f"🛡 {target.first_name} از فیلترها و اخطارها معاف شد.")


async def unexempt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "exempt", target.id)
    await update.message.reply_text(f"معافیت {target.first_name} برداشته شد.")


async def special_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    _list_add(chat_id, "special", target.id, target.username or target.first_name or "")
    await update.message.reply_text(f"⭐ {target.first_name} عضو ویژه شد.")


async def unspecial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = await require_admin_reply(update, context)
    if not target:
        return
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "special", target.id)
    await update.message.reply_text(f"عضویت ویژه {target.first_name} برداشته شد.")


async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /filter کلمه")
        return
    word = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    _list_add(chat_id, "filter", word)
    await update.message.reply_text(f"🚫 کلمه «{word}» به فیلتر اضافه شد.")


async def unfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /unfilter کلمه")
        return
    word = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "filter", word)
    await update.message.reply_text(f"کلمه «{word}» از فیلتر حذف شد.")


async def autoreply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    full = " ".join(context.args)
    if "|" not in full:
        await update.message.reply_text("✏️ استفاده: /autoreply کلیدواژه | پاسخ")
        return
    trigger, response = full.split("|", 1)
    trigger, response = trigger.strip(), response.strip()
    if not trigger or not response:
        await update.message.reply_text("✏️ استفاده: /autoreply کلیدواژه | پاسخ")
        return
    chat_id = update.effective_chat.id
    _list_add(chat_id, "autoreply", trigger, response)
    await update.message.reply_text(f"🤖 پاسخ خودکار برای «{trigger}» ثبت شد.")


async def unautoreply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /unautoreply کلیدواژه")
        return
    trigger = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "autoreply", trigger)
    await update.message.reply_text(f"پاسخ خودکار «{trigger}» حذف شد.")


async def allowusername_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /allowusername یوزرنیم")
        return
    uname = context.args[0].lstrip("@")
    chat_id = update.effective_chat.id
    _list_add(chat_id, "allowed_username", uname)
    await update.message.reply_text(f"✅ یوزرنیم @{uname} مجاز شد.")


async def unallowusername_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /unallowusername یوزرنیم")
        return
    uname = context.args[0].lstrip("@")
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "allowed_username", uname)
    await update.message.reply_text(f"یوزرنیم @{uname} از لیست مجاز حذف شد.")


async def allowforward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /allowforward یوزرنیم کانال")
        return
    uname = context.args[0].lstrip("@")
    chat_id = update.effective_chat.id
    _list_add(chat_id, "allowed_forward", uname)
    await update.message.reply_text(f"✅ فوروارد از @{uname} مجاز شد.")


async def unallowforward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if not context.args:
        await update.message.reply_text("✏️ استفاده: /unallowforward یوزرنیم کانال")
        return
    uname = context.args[0].lstrip("@")
    chat_id = update.effective_chat.id
    _list_remove(chat_id, "allowed_forward", uname)
    await update.message.reply_text(f"مجوز فوروارد از @{uname} برداشته شد.")


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        await update.message.reply_text("⛔️ این دستور فقط برای ادمین‌هاست.")
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "✏️ استفاده: /schedule YYYY-MM-DD HH:MM متن پیام\n"
            "مثال: /schedule 2026-08-10 20:30 گاتهام همیشه بیدار می‌مونه!"
        )
        return
    date_str, time_str = context.args[0], context.args[1]
    post_text = " ".join(context.args[2:])
    try:
        run_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("⚠️ فرمت تاریخ درست نیست. مثال: 2026-08-10 20:30")
        return
    delay = (run_at - datetime.now()).total_seconds()
    if delay <= 0:
        await update.message.reply_text("⚠️ این زمان گذشته! یه زمان تو آینده بده.")
        return
    if context.job_queue is None:
        await update.message.reply_text(
            "⚠️ سیستم زمانبندی فعال نیست. باید 'python-telegram-bot[job-queue]' رو تو "
            "requirements.txt بذاری."
        )
        return
    chat_id = update.effective_chat.id
    sched_key = run_at.isoformat()
    context.job_queue.run_once(
        send_scheduled_post, when=delay, chat_id=chat_id,
        data={"text": post_text, "key": sched_key}, name=f"sched_{chat_id}_{sched_key}",
    )
    _list_add(chat_id, "scheduled", sched_key, post_text)
    await update.message.reply_text(f"🗓 پیام برای {run_at.strftime('%Y-%m-%d %H:%M')} زمانبندی شد.")


async def send_scheduled_post(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    await context.bot.send_message(job.chat_id, data["text"])
    _list_remove(job.chat_id, "scheduled", data["key"])


NATURAL_MOD_TRIGGERS = {
    "بن کن": ban_cmd,
    "بنش کن": ban_cmd,
    "میوت کن": mute_cmd,
    "سکوتش کن": mute_cmd,
    "کیک کن": kick_cmd,
    "پاکش کن": delete_cmd,
    "حذفش کن": delete_cmd,
}


async def handle_natural_mod_command(update: Update, context: ContextTypes.DEFAULT_TYPE, stripped: str) -> bool:
    """دستورات مدیریتی به زبان طبیعی، فقط وقتی ریپلای شده و گوینده ادمینه."""
    if not update.message.reply_to_message:
        return False
    handler_fn = None
    for phrase, fn in NATURAL_MOD_TRIGGERS.items():
        if phrase in stripped:
            handler_fn = fn
            break
    if handler_fn is None:
        return False
    if not await is_group_admin(update, context):
        return False
    await handler_fn(update, context)
    return True


async def handle_filter_check(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, user_id, text) -> bool:
    """اگه پیام حاوی کلمه فیلترشده باشه حذفش می‌کنه. True یعنی پیام مصرف شد."""
    words = _list_get(chat_id, "filter")
    if not words:
        return False
    exempt_ids = {k for k, _ in _list_get(chat_id, "exempt")}
    if str(user_id) in exempt_ids:
        return False
    if await is_group_admin(update, context):
        return False
    lowered = text.lower()
    for word, _ in words:
        if word.lower() in lowered:
            try:
                await update.message.delete()
            except Exception:
                pass
            return True
    return False


async def handle_autoreply_check(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, text) -> bool:
    """اگه پیام یکی از کلیدواژه‌های پاسخ خودکار رو داشته باشه، جواب می‌ده. True یعنی پیام مصرف شد."""
    replies = _list_get(chat_id, "autoreply")
    if not replies:
        return False
    lowered = text.lower()
    for trigger, response in replies:
        if trigger.lower() in lowered:
            await update.message.reply_text(response)
            return True
    return False


# =========================================================
#  MAIN MESSAGE HANDLER
# =========================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # 🎬 اگه کاربر تو سشن «پست‌ساز گاتهام» فعاله، این پیام مالِ همون ابزاره
    # (مثلاً ویرایش متن/کپشن) — نباید AI/بازی‌ها روش واکنش نشون بدن.
    if await postsaz_intercept(update, context):
        return

    # 🎵 اگه کاربر تو سشن «ساخت آهنگ با هوش مصنوعی» فعاله (منتظر متن/سبک/عنوان)،
    # این پیام مال همون ابزاره — نباید AI/بازی‌ها روش واکنش نشون بدن. دقیقاً همون
    # الگوی postsaz_intercept بالا (context.user_data، صفر تداخل با بقیه‌ی هندلرها).
    if await mureka_intercept(update, context):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    text = update.message.text
    stripped = text.strip()
    is_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
        await db_run(_bump_daily_activity, chat_id)
        gap_seconds = await db_run(_get_last_msg_gap, chat_id)
        if gap_seconds > 6 * 3600:  # بیش از ۶ ساعت سکوت گروه
            await update.message.reply_text("🌑 بالاخره یکی بیدار شد... گاتهام یه مدت ساکت بود.")

        # 🚨 حالت محاصره — اگه گروه یهو خیلی شلوغ بشه (اسپم/فلود)، یه هشدار بده
        now_ts = time.time()
        SIEGE_TRACKER[chat_id].append(now_ts)
        recent = [t for t in SIEGE_TRACKER[chat_id] if now_ts - t < 20]
        if len(recent) >= 15 and (now_ts - SIEGE_LAST_WARNED.get(chat_id, 0)) > 300:
            SIEGE_LAST_WARNED[chat_id] = now_ts
            await update.message.reply_text(
                "🚨 *City Lockdown Alert*\nگاتهام داره محاصره می‌شه... یه‌کم آروم‌تر.",
                parse_mode="Markdown",
            )

    # --- امنیت گروه: آنتی‌لینک/آنتی‌فلود (اگه ادمین فعالشون کرده باشه) ---
    if is_group:
        guard_fn = context.application.bot_data.get("security_guard_fn")
        if guard_fn and await guard_fn(update, context):
            return

    # --- فیلتر کلمات، پاسخ خودکار و دستورات مدیریتی به زبان طبیعی: همیشه فعالن، حتی بدون منشن ---
    if is_group:
        if await handle_filter_check(update, context, chat_id, user_id, text):
            return
        if await handle_natural_mod_command(update, context, stripped):
            return
        if await handle_autoreply_check(update, context, chat_id, text):
            return
        # 🃏 رویداد تصادفی جوکر — به‌ندرت، یه پیام مرموز اتمسفری میاد (بدون نیاز به کاری)
        if random.random() < 0.004:
            await update.message.reply_text(f"🃏 {gotham_signature_line()}")

    player = await db_run(_get_player, chat_id, user_id, username)
    player = collect_points(player)
    player = update_activity(player)

    # --- کلیدواژه "شماره"/"لیست کامل" برای پنل خصوصی Owner (فقط پیوی) ---
    # عمداً فقط برای Owner واکنش نشون می‌ده و برای بقیه بی‌صدا رد می‌شه (نه پیام
    # خطا، نه هیچ نشونه‌ای)؛ این‌جوری وجود این پنل برای کاربرای عادی لو نمی‌ره.
    if update.effective_chat.type == ChatType.PRIVATE and stripped in ("شماره", "لیست کامل", "لیست") and _is_owner(update):
        await owner_control_panel_cmd(update, context)
        await db_run(_save_player, player)
        return

    # --- کلیدواژه "تنظیمات"/"پنل" برای باز کردن پنل تنظیمات، حتی بدون منشن ---
    if stripped in ("تنظیمات", "پنل"):
        await update.message.reply_text(
            PANEL_MAIN_TEXT, reply_markup=build_panel_main_keyboard(_is_owner(update)), parse_mode="Markdown"
        )
        await db_run(_save_player, player)
        return

    # --- کلیدواژه "کلمات ربات"/"لیست کلمات" — طبق درخواست فقط برای Owner ---
    if stripped in ("کلمات ربات", "لیست کلمات", "همه کلمات"):
        if not _is_owner(update):
            await db_run(_save_player, player)
            return  # برای کاربر عادی این کلیدواژه اصلاً وجود نداره (بی‌صدا نادیده گرفته می‌شه)
        try:
            await update.message.reply_text(
                PANEL_TEXTS["words"], reply_markup=build_back_keyboard(), parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text(
                PANEL_TEXTS["words"].replace("*", ""), reply_markup=build_back_keyboard()
            )
        await db_run(_save_player, player)
        return

    # --- کلیدواژه "گزارش گروه" برای گزارش فعالیت هفتگی (فقط ادمین) ---
    if is_group and stripped in ("گزارش گروه", "گزارش فعالیت"):
        await grouptreport_cmd(update, context)
        await db_run(_save_player, player)
        return

    # --- معمای روزانه‌ی ریدلر: هم دستور نمایش، هم چک جواب، همیشه فعالن ---
    if is_group:
        if stripped in ("معما", "معمای امروز", "معمای ریدلر"):
            await riddle_cmd(update, context)
            await db_run(_save_player, player)
            return
        if await _check_riddle_answer(update, chat_id, stripped, player):
            await db_run(_save_player, player)
            return
        if stripped in ("آرشیو گاتهام", "آرشیو"):
            await gotham_archive_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in ("کد امنیتی", "کد امنیتی گاتهام"):
            await security_code_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in ("تاریخ", "ساعت", "تاریخ و ساعت"):
            await datetime_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in ("بهترین دوست", "بهترین دوستم"):
            await best_friend_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in ("معرفی", "معرفی ربات"):
            await intro_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in ("شمارش معکوس", "تا نیمه شب"):
            await midnight_countdown_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in ("پرونده من", "پرونده"):
            await case_file_cmd(update, context)
            await db_run(_save_player, player)
            return
        gift_item_match = re.match(r"^هدیه آیتم (باتارنگ|پادزهر)$", stripped)
        if gift_item_match:
            key = "batarang" if gift_item_match.group(1) == "باتارنگ" else "antidote"
            await gift_item_cmd(update, context, key)
            await db_run(_save_player, player)
            return

    # --- کلیدواژه "رکورد من"/"بج های من" برای رکورد و بج‌ها، حتی بدون منشن ---
    if stripped in ("رکورد من", "رکورد", "بج های من", "بج‌های من"):
        await record_cmd(update, context)
        await db_run(_save_player, player)
        return

    # --- ازدواج/طلاق/رابطه، هدیه، نظرسنجی، ریپورت — همیشه فعالن، حتی بدون منشن ---
    if is_group:
        if stripped in MARRY_TRIGGERS:
            await marry_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in DIVORCE_TRIGGERS:
            await divorce_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in COUPLE_TRIGGERS:
            await couple_cmd(update, context)
            await db_run(_save_player, player)
            return
        if stripped in REPORT_TRIGGERS:
            await report_cmd(update, context)
            await db_run(_save_player, player)
            return
        gift_match = GIFT_RE.match(stripped)
        if gift_match:
            await gift_points_cmd(update, context, int(gift_match.group(1)))
            await db_run(_save_player, player)
            return
        poll_match = POLL_RE.match(stripped)
        if poll_match:
            await send_quick_poll(update, context, poll_match.group(1))
            await db_run(_save_player, player)
            return

    # --- کلیدواژه "بتمن" برای گرفتن پوینت، حتی بدون منشن، تو گروه‌ها ---
    if KEYWORD_POINT in text:
        now = time.time()
        if now - player.get("last_keyword_ts", 0) >= KEYWORD_COOLDOWN:
            player["last_keyword_ts"] = now
            player["points_balance"] = min(
                player["points_capacity"], player["points_balance"] + KEYWORD_REWARD
            )

    # --- اگه این پیام مال بازی‌هاست (کلمه‌ی شروع بازی یا حرکت داخل یه بازیِ فعال)،
    # بی‌خیال پاسخ هوش مصنوعی شو - حتی اگه کاربر رو پیام خود بتمن ریپلای زده باشه یا
    # قبل/بعد کلمه‌ی بازی، اسم/لقب بتمن رو هم نوشته باشه (مثلاً "بتمن دوز").
    # (خود سیستم بازی‌ها تو گروه‌های جدا هندلرش رو داره و همینجوری اجرا می‌شه.)
    game_text_candidate = stripped
    if context.bot.username:
        game_text_candidate = game_text_candidate.replace(f"@{context.bot.username}", "").strip()
    for nick in NICKNAME_TRIGGERS:
        game_text_candidate = game_text_candidate.replace(nick, "").strip()
    if is_any_game_text(chat_id, stripped) or is_any_game_text(chat_id, game_text_candidate):
        await db_run(_save_player, player)
        return

    mentioned = is_bot_mentioned(update, context)
    if is_group and not mentioned:
        await db_run(_save_player, player)
        return  # تو گروه فقط با منشن ادامه بده

    # محدودیت نرخ فقط رو مسیری که واقعاً قراره هوش مصنوعی صدا زده بشه اعمال می‌شه؛
    # اینجوری چت عادی گروه بدون منشن، بودجه‌ی محدودیت رو مصرف نمی‌کنه و منشن واقعی
    # هیچ‌وقت به‌خاطر شلوغی گروه بی‌صدا حذف نمی‌شه.
    if not check_rate_limit(user_id):
        await db_run(_save_player, player)
        return  # ضد اسپم: سکوت کامل تا پنجره زمانی تموم بشه

    chat = await db_run(_get_chat, chat_id)

    if chat["battle_enemy"]:
        consumed = await handle_battle_guess(update, chat, player, text)
        if consumed:
            await db_run(_save_chat, chat)
            await db_run(_save_player, player)
            return

    started = await maybe_start_battle(update, chat)
    if started:
        await db_run(_save_chat, chat)
        await db_run(_save_player, player)
        return

    chat["since_switch"] += 1
    if chat["since_switch"] >= chat["next_switch_at"]:
        new_persona = random.choice([p for p in PERSONAS if p != chat["persona"]])
        chat["persona"] = new_persona
        chat["since_switch"] = 0
        chat["next_switch_at"] = random.randint(8, 15)
        await update.message.reply_text(f"🔄 شخصیت عوض شد: {PERSONAS[new_persona]['label']}")

    ai_input = text
    if user_id == OWNER_ID:
        ai_input = (
            "(این پیام از سازنده‌ی خودتی، رئیس واقعی گاتهام — باهاش با احترام ویژه و "
            f"صمیمیت حرف بزن) پیام: {text}"
        )
    reply = await call_ai(chat_id, chat["persona"], player["char_level"], ai_input)
    await update.message.reply_text(reply)

    await db_run(_save_chat, chat)
    await db_run(_save_player, player)


async def handle_gif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.animation:
        return

    # 🎬 اگه کاربر تو سشن «پست‌ساز گاتهام» فعاله، این گیف مالِ همون ابزاره.
    if await postsaz_intercept(update, context):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    is_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    mentioned = is_bot_mentioned(update, context)
    if is_group and not mentioned:
        return  # تو گروه فقط اگه ریپلای رو ربات باشه یا تو کپشن منشن شده باشه

    if not check_rate_limit(user_id):
        return

    player = await db_run(_get_player, chat_id, user_id, username)
    player = collect_points(player)
    chat = await db_run(_get_chat, chat_id)

    prompt = (
        "کاربر بدون متن، فقط یه گیف/تصویر متحرک فرستاده. یه واکنش کوتاه و خفن به سبک "
        "شخصیتت بده، انگار واقعاً گیفش رو دیدی."
    )
    reply = await call_ai(chat_id, chat["persona"], player["char_level"], prompt)
    await update.message.reply_text(reply)

    await db_run(_save_chat, chat)
    await db_run(_save_player, player)


async def handle_photo_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not (update.message.photo or update.message.sticker):
        return

    # 🎬 اگه کاربر تو سشن «پست‌ساز گاتهام» فعاله، این عکس مالِ همون ابزاره
    # (استیکر رو دست نمی‌زنیم چون پست‌ساز فقط عکس/ویدیو/گیف/صدا/متن می‌فهمه).
    if update.message.photo and await postsaz_intercept(update, context):
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    is_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    mentioned = is_bot_mentioned(update, context)
    if is_group and not mentioned:
        return

    if not check_rate_limit(user_id):
        return

    player = await db_run(_get_player, chat_id, user_id, username)
    player = collect_points(player)
    chat = await db_run(_get_chat, chat_id)

    kind = "استیکر" if update.message.sticker else "عکس"
    prompt = (
        f"کاربر بدون متن، فقط یه {kind} فرستاده. یه واکنش کوتاه و خفن به سبک "
        f"شخصیتت بده، انگار واقعاً {kind}‌شو دیدی."
    )
    reply = await call_ai(chat_id, chat["persona"], player["char_level"], prompt)
    await update.message.reply_text(reply)

    await db_run(_save_chat, chat)
    await db_run(_save_player, player)


# =========================================================
#  کپچای اعضای جدید (ضد ربات/اسپم‌بات)
# =========================================================

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            # خود ربات به یه گروه جدید اضافه شده — کپچا لازم نداره، فقط خوش‌آمد بگه
            group_name = update.effective_chat.title or "این گروه"
            await update.message.reply_text(
                "🦇 *به دنیای بتمن خوش اومدی*\n\n"
                f"از امروز، *{group_name}* تحت محافظت گاتهامه.\n"
                "بازی، مدیریت، و یه‌کم جو گاتهامی — همه رو دارم.\n"
                "برای شروع بنویس «تنظیمات» تا همه‌چی رو ببینی.",
                parse_mode="Markdown",
            )
            continue
        if member.is_bot:
            continue

        ban_count = int(_list_get_one(chat_id, "ban_count", member.id) or 0)
        if ban_count >= 2:
            try:
                await context.bot.ban_chat_member(chat_id, member.id)
                await update.message.reply_text(
                    f"🚫 {member.first_name} قبلاً {ban_count} بار از گاتهام اخراج شده — راهش نمی‌دیم."
                )
            except Exception as e:
                # قبلاً این خطا کاملاً بی‌صدا گم می‌شد — یعنی اگه بن یه مجرم
                # سابقه‌دار به‌خاطر نبود دسترسی ادمین شکست می‌خورد، هیچ ادمینی
                # خبردار نمی‌شد که این کاربر بدون بن وارد گروه شده.
                log.warning(f"بن خودکار عضو سابقه‌دار {member.id} تو گروه {chat_id} شکست خورد: {e}")
            continue

        # 🔁 کاربری که قبلاً همین‌جا کپچا رو پاس کرده (مثلاً Leave و دوباره Join
        # کرده) نباید هر بار دوباره میوت و مجبور به تاییدِ مجدد بشه. این چک از
        # روی group_lists (لیست "captcha_ok"، پایدار تو دیتابیس، برخلاف
        # وضعیت محدودیتِ خودِ تلگرام که با Leave پاک می‌شه) انجام می‌شه — وگرنه
        # State قدیمی (میوت‌شدن) دوباره روی کاربر verified اعمال می‌شه و همه‌ی
        # پیام‌های بعدیش (مثلاً لینک دانلودر) رو تا قبل از تاییدِ دوباره بلاک
        # می‌کنه، چون تلگرام اصلاً پیام کاربر میوت‌شده رو به ربات نمی‌رسونه.
        already_verified = _list_get_one(chat_id, "captcha_ok", member.id)
        if already_verified:
            continue

        try:
            await context.bot.restrict_chat_member(
                chat_id, member.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
        except Exception:
            pass  # اگه ربات ادمین نباشه یا دسترسی نداشته باشه، بی‌خیال کپچا شو
            continue
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ من دلقک جوکر نیستم", callback_data=f"captcha:{member.id}")
        ]])
        sent = await update.message.reply_text(
            f"🦇 {member.first_name}، به گاتهام خوش اومدی. مراقب سایه‌ها باش.\n"
            f"برای فعال شدن، تا {CAPTCHA_TIMEOUT_SECONDS // 60} دقیقه دیگه دکمه‌ی زیر رو بزن،"
            f" ثابت کن یکی از دلقک‌های جوکر نیستی.",
            reply_markup=keyboard,
        )
        asyncio.create_task(_captcha_timeout_watch(chat_id, member.id, sent.message_id, context.bot))


async def _captcha_timeout_watch(chat_id, user_id, message_id, bot):
    await asyncio.sleep(CAPTCHA_TIMEOUT_SECONDS)
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status not in ("left", "kicked") and member.permissions and not member.permissions.can_send_messages:
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)  # کیک، نه بن دائم
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text="⏰ وقت تایید تموم شد و عضو اخراج شد.",
            )
    except Exception:
        pass


async def captcha_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, target_id = query.data.split(":")
    target_id = int(target_id)
    if query.from_user.id != target_id:
        await query.answer("این دکمه‌ی تو نیست 🙂", show_alert=True)
        return
    chat_id = query.message.chat.id
    try:
        await context.bot.restrict_chat_member(
            chat_id, target_id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except Exception as e:
        await query.answer(f"مشکلی پیش اومد: {e}", show_alert=True)
        return
    # ثبت پایدار تایید کپچا تو همین گروه؛ این‌جوری اگه کاربر بعداً Leave/Rejoin
    # کنه، handle_new_member دیگه دوباره میوتش نمی‌کنه (رفع باگ گیر کردن
    # Downloader/هر قابلیت متنی دیگه بعد از Rejoin).
    _list_add(chat_id, "captcha_ok", target_id, "1")
    await query.edit_message_text(f"✅ {query.from_user.first_name} تایید شد، خوش اومدی!")
    await query.answer()


# =========================================================
#  🔐 PERMISSION GATE مرکزی — احراز اجباری شماره تلفن
# =========================================================
# این دو هندلر با group=-1 ثبت می‌شن، یعنی قبل از هر Command/Callback/Message
# handler دیگه‌ای (که تو bot.py یا هر کدوم از ماژول‌های register_x دیگه ثبت
# شدن) اجرا می‌شن. اگه کاربر تایید نشده باشه و مسیر دور زدن نداشته باشه،
# ApplicationHandlerStop می‌ندازیم تا هیچ Handler دیگه‌ای (حتی تو گروه‌های
# دیگه) برای همون Update اجرا نشه — یعنی نه Command مستقیم، نه Callback
# مستقیم، نه هیچ Handler دیگه‌ای نمی‌تونه این مرحله رو دور بزنه.

_GATE_GROUP_COOLDOWN = {}  # (chat_id, user_id) -> ts آخرین پیامِ یادآوریِ گیت تو گروه
_GATE_GROUP_COOLDOWN_SECONDS = 30  # جلوگیری از Flood اگه کاربر پشت‌سرهم تو گروه پیام بده


async def _permission_gate_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قانون نهایی دسترسی:

    - گروه/سوپرگروه: هیچ Gate ای اصلاً اجرا نمی‌شه — نه شماره، نه عضویت
      کانال، نه هیچ پیام «اول تو پیوی تایید کن». کاربر مستقیم به هر Handler
      دیگه‌ای (دانلودر، بازی‌ها، ابزارها، هرچی) دسترسی داره.
    - پیوی: عضویت کانال فقط اگه REQUIRED_CHANNEL روشن باشه چک می‌شه؛ شماره
      تلفن، صرف‌نظر از REQUIRED_CHANNEL، همیشه و بدون استثنا اجباریه.
    """
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return  # آپدیت‌های بی‌کاربر (مثلاً چنل پست) دست‌نخورده رد می‌شن

    # پیام‌های سرویسی تلگرام (عضو جدید، خروج عضو، پین و ...) بخشی از سیستم‌های
    # دیگه‌ان (مثلاً کپچای ورود اعضای جدید) و نباید با گیت شماره قاطی بشن.
    if (msg.new_chat_members or msg.left_chat_member or msg.pinned_message
            or msg.migrate_to_chat_id or msg.migrate_from_chat_id):
        return

    # 🏠 ردیابی گروه (عنوان/آخرین‌فعالیت) برای داشبورد Owner — قبل از هر Gate
    # ای انجام می‌شه چون فقط یه UPDATE سبکه، ربطی به احراز کاربر نداره.
    chat = update.effective_chat
    if chat and chat.type != ChatType.PRIVATE:
        try:
            await db_run(_track_group_chat, chat.id, chat.title or "", chat.type)
        except Exception:
            pass
        # 🔓 قانون مطلق: تو گروه/سوپرگروه هیچ Gate ای اجرا نمی‌شه — نه شماره،
        # نه عضویت کانال. کاربر مستقیم به بقیه‌ی Handlerها می‌ره.
        return

    if msg.contact:
        return  # بذار handle_shared_contact خودش پردازشش کنه

    if msg.text and msg.text.startswith("/start"):
        return  # /start خودش مسئول نمایش درخواست شماره‌ست

    if user.id == OWNER_ID:
        return

    # 📢 مرحله‌ی اول Gate (فقط پیوی): عضویت کانال — فقط اگه REQUIRED_CHANNEL
    # روشن باشه چک می‌شه (از کش سریع؛ چک زنده‌ی API فقط سر /start و دکمه‌ی
    # «بررسی عضویت» انجام می‌شه تا ریت‌لیمیت نخوریم).
    channel_ok = True if not FORCE_JOIN_ENABLED else await db_run(_is_channel_verified_cached, user.id)

    # 📱 مرحله‌ی دوم Gate (فقط پیوی): شماره تلفن — صرف‌نظر از وضعیت
    # REQUIRED_CHANNEL، همیشه اجباریه؛ فقط وقتی چک می‌شه که مرحله‌ی کانال رد
    # شده باشه (وگرنه کاربر باید اول کانال رو جواب بده).
    phone_ok = channel_ok and await db_run(_is_phone_verified, user.id)

    if channel_ok and phone_ok:
        return

    if not channel_ok:
        await msg.reply_text(CHANNEL_JOIN_PROMPT_TEXT, reply_markup=_channel_join_keyboard(), parse_mode="Markdown")
    else:
        await msg.reply_text(PHONE_VERIFY_PROMPT_TEXT, reply_markup=_phone_request_keyboard())
    raise ApplicationHandlerStop


async def _permission_gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """همون قانون _permission_gate_message، برای کلیک روی دکمه‌ها هم اعمال
    می‌شه: تو گروه/سوپرگروه هیچ Gate ای نیست؛ فقط تو پیوی، و شماره همیشه
    اجباریه صرف‌نظر از REQUIRED_CHANNEL."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    chat = update.effective_chat
    if chat and chat.type != ChatType.PRIVATE:
        return  # 🔓 گروه/سوپرگروه: هیچ Gate ای روی هیچ دکمه‌ای اعمال نمی‌شه

    data = query.data or ""
    if data.startswith("captcha:"):
        return  # کپچای عضو جدید یه سیستم کاملاً جداست، نباید به گیت شماره گره بخوره
    if data == "checkjoin":
        return  # این دکمه دقیقاً برای کاربر تاییدنشده‌ست، نباید خودش بلاک بشه

    if user.id == OWNER_ID:
        return

    channel_ok = True if not FORCE_JOIN_ENABLED else await db_run(_is_channel_verified_cached, user.id)
    if not channel_ok:
        try:
            await query.answer(CHANNEL_JOIN_PROMPT_TEXT.replace("*", ""), show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop

    if await db_run(_is_phone_verified, user.id):
        return

    try:
        await query.answer("🔒 اول باید شماره‌ت رو توی پیوی ربات تایید کنی.", show_alert=True)
    except Exception:
        pass
    raise ApplicationHandlerStop


async def global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر سراسری خطا — قبلاً هیچ‌کدوم از هندلرها به Application وصل نبودن،
    یعنی هر خطای پیش‌بینی‌نشده (مثلاً تو دیتابیس یا هر جای دیگه) کاملاً بی‌صدا
    گم می‌شد: نه پیامی به کاربر، نه هیچ اطلاعی به مالک ربات. این باعث می‌شد
    باگ‌هایی مثل «لیست بازی‌ها نمیاد» بدون هیچ ردی رد بشن. حالا هر خطا هم لاگ
    می‌شه، هم (اگه ممکن باشه) خلاصه‌ش برای مالک ربات فرستاده می‌شه.

    یه استثنا: Conflict (409 - terminated by other getUpdates request) کاملاً
    طبیعیه و خودش چند ثانیه بعد از هر Redeploy حل می‌شه (نسخه‌ی قدیمی هنوز کامل
    خاموش نشده، نسخه‌ی جدید بالا اومده). خودِ python-telegram-bot به‌صورت
    خودکار Retry می‌کنه و نیازی به دخالت نیست؛ برای همین این خطا رو گزارش
    نمی‌کنیم تا لاگ/پیام به مالک با نویز بی‌ربط شلوغ نشه.

    🩺 Debug موقت (برای پیدا کردن دو باگ NoneType که هنوز traceback ازشون
    نداریم): این هندلر الان علاوه بر متن خطا، محل دقیق وقوعش تو کد (فایل/
    خط/تابع، از bug_reporter.locate_exception روی خودِ traceback واقعی —
    نه حدس)، نوع خود Update، و callback_data (اگه از دکمه اومده باشه) رو هم
    جمع و برای Owner می‌فرسته. هر مرحله جدا try/except شده تا اگه یه بخش از
    استخراج context شکست خورد (مثلاً یه فیلد عجیب رو یه Update غیرعادی)،
    بقیه‌ی گزارش (و از همه مهم‌تر، خودِ ارسال traceback) لطمه نبینه — و از
    اون مهم‌تر، خودِ این تابع (که قراره خطاهای بقیه‌ی ربات رو گزارش کنه)
    هرگز نباید خودش یه Exception جدیدِ گزارش‌نشده بترکونه."""
    err = context.error
    from telegram.error import Conflict as TgConflict
    if isinstance(err, TgConflict):
        log.warning("Conflict گذرا حین ری‌دیپلوی — نادیده گرفته شد (خودکار حل می‌شه)")
        return
    log.exception("خطای پیش‌بینی‌نشده در پردازش یک آپدیت", exc_info=err)

    # --- استخراج context از Update، هر فیلد جدا و ایمن ---
    chat_id = user_id = update_type = callback_data = None
    is_real_update = isinstance(update, Update)

    if is_real_update:
        try:
            if update.effective_chat:
                chat_id = update.effective_chat.id
        except Exception:
            pass
        try:
            if update.effective_user:
                user_id = update.effective_user.id
        except Exception:
            pass
        try:
            if update.callback_query is not None:
                update_type = "callback_query"
                callback_data = update.callback_query.data
            elif update.edited_message is not None:
                update_type = "edited_message"
            elif update.message is not None:
                update_type = "message"
            elif update.channel_post is not None:
                update_type = "channel_post"
            elif update.my_chat_member is not None:
                update_type = "my_chat_member"
            elif update.chat_member is not None:
                update_type = "chat_member"
            elif update.poll is not None:
                update_type = "poll"
            elif update.poll_answer is not None:
                update_type = "poll_answer"
            else:
                update_type = type(update).__name__
        except Exception:
            pass
    else:
        try:
            update_type = f"non-Update ({type(update).__name__})"
        except Exception:
            update_type = "non-Update"

    # --- ثبت و ارسال گزارش — هیچ استثنایی از اینجا نباید بیرون بره ---
    try:
        from bug_reporter import remember_error, format_error
        item = remember_error(
            "handle_update", err, chat_id=chat_id, user_id=user_id,
            update_type=update_type, callback_data=callback_data,
        )
        try:
            report_text = format_error(item)
        except Exception as fmt_err:
            # حتی اگه فرمت‌کردن گزارش هم شکست بخوره، حداقل یه پیام خام برو
            log.exception("format_error خودش شکست خورد", exc_info=fmt_err)
            report_text = (
                f"🚨 خطای جدید ربات گاتهام (فرمت گزارش شکست خورد)\n"
                f"خطا: {type(err).__name__}: {err}"
            )
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=report_text, parse_mode="Markdown")
        except Exception:
            # اگه parse_mode مارک‌داون به‌خاطر کاراکتر خاصی تو traceback خطا
            # داد، بدون فرمت هم امتحان کن — مهم‌تر از قشنگی، رسیدنِ خبره.
            try:
                await context.bot.send_message(chat_id=OWNER_ID, text=report_text)
            except Exception as send_err:
                log.error(f"ارسال گزارش خطا به OWNER_ID هم شکست خورد: {send_err}")
    except Exception as reporter_err:
        # خودِ global_error_handler نباید هیچ‌وقت یه Exception گزارش‌نشده
        # جدید بترکونه؛ اگه کل مسیر remember_error/format_error/send_message
        # شکست خورد، فقط لاگ می‌کنیم و همین‌جا تمومش می‌کنیم.
        log.error(f"خودِ گزارش‌گر خطا (bug_reporter) شکست خورد: {reporter_err}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده! برو تو Railway Variables اضافه‌اش کن.")

    # 🦇 لاگ نسخه‌ی yt-dlp موقع استارت (رفع مشکل «خطای [youtube] The page needs
    # to be reloaded.» — این خطا معمولاً یعنی نسخه‌ی yt-dlp نصب‌شده رو Railway
    # قدیمیه و الگوریتم استخراج امضای جدید یوتیوب رو نمی‌شناسه). این فقط شماره
    # نسخه رو لاگ می‌کنه — هیچ اطلاعات حساسی (کوکی/توکن) چاپ نمی‌شه — تا بشه
    # سریع از لاگ Railway چک کرد که آیا نسخه‌ی درست واقعاً نصب و استفاده شده.
    try:
        import yt_dlp as _yt_dlp_version_check
        log.info(f"🦇 Gotham Downloader — yt-dlp version: {_yt_dlp_version_check.version.__version__}")
    except Exception as e:
        log.warning(f"🦇 Gotham Downloader — yt-dlp version check failed: {e}")

    # 🩹 رفع مرکزیِ باگ‌های تکراریِ لاگ (Message text is empty / Message is not
    # modified / Can't parse entities / RetryAfter / TimedOut) — باید قبل از
    # هر send/edit دیگه‌ای نصب بشه.
    install_safe_telegram_patches()

    _init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # قبلاً هیچ error handler سراسری‌ای ثبت نشده بود، یعنی هر خطای پیش‌بینی‌نشده
    # (تو دیتابیس، پارس مارک‌داون، هرچی) کاملاً بی‌صدا گم می‌شد — نه به کاربر
    # پیامی می‌رسید، نه به مالک ربات. همینه که bug_reporter.py هم تا الان
    # وصل نشده بود و «رفع باگ ربات» همیشه خالی می‌موند.
    app.add_error_handler(global_error_handler)

    # 🔐 Permission Gate مرکزی — باید قبل از همه‌چیز (group=-1) ثبت بشه تا هیچ
    # Command/Callback/Message دیگه‌ای (چه تو bot.py، چه تو ماژول‌های دیگه)
    # نتونه احراز اجباری شماره تلفن رو دور بزنه.
    app.add_handler(MessageHandler(filters.ALL, _permission_gate_message), group=-1)
    app.add_handler(CallbackQueryHandler(_permission_gate_callback), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quote", quote))
    app.add_handler(CommandHandler("characters", characters_cmd))
    app.add_handler(CommandHandler("settings", settings_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("shop", shop_cmd))
    app.add_handler(CommandHandler("bag", bag_cmd))
    app.add_handler(CommandHandler("missions", missions_cmd))
    app.add_handler(CommandHandler("top", top_cmd))

    # مدیریت گروه (فقط ادمین)
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("kick", kick_cmd))
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("unmute", unmute_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("warn", warn_cmd))
    app.add_handler(CommandHandler("unwarn", unwarn_cmd))
    app.add_handler(CommandHandler("exempt", exempt_cmd))
    app.add_handler(CommandHandler("unexempt", unexempt_cmd))
    app.add_handler(CommandHandler("special", special_cmd))
    app.add_handler(CommandHandler("unspecial", unspecial_cmd))
    app.add_handler(CommandHandler("filter", filter_cmd))
    app.add_handler(CommandHandler("unfilter", unfilter_cmd))
    app.add_handler(CommandHandler("autoreply", autoreply_cmd))
    app.add_handler(CommandHandler("unautoreply", unautoreply_cmd))
    app.add_handler(CommandHandler("allowusername", allowusername_cmd))
    app.add_handler(CommandHandler("unallowusername", unallowusername_cmd))
    app.add_handler(CommandHandler("allowforward", allowforward_cmd))
    app.add_handler(CommandHandler("unallowforward", unallowforward_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("log", modlog_cmd))
    app.add_handler(CommandHandler("record", record_cmd))
    app.add_handler(CommandHandler("groupreport", grouptreport_cmd))
    app.add_handler(CommandHandler("starters", starters_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("groupscount", groupscount_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CommandHandler("userslist", userslist_cmd))
    app.add_handler(CommandHandler("groupslist", groupslist_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("msggroup", msggroup_cmd))
    app.add_handler(CommandHandler("archive", gotham_archive_cmd))
    app.add_handler(CommandHandler("riddle", riddle_cmd))
    app.add_handler(CommandHandler("securitycode", security_code_cmd))
    app.add_handler(CommandHandler("tournament", tournament_cmd))
    app.add_handler(CommandHandler("compare", compare_cmd))
    app.add_handler(CommandHandler("bestfriend", best_friend_cmd))
    app.add_handler(CommandHandler("intro", intro_cmd))
    app.add_handler(CommandHandler("countdown", midnight_countdown_cmd))
    app.add_handler(CommandHandler("lockdown", lockdown_cmd))
    app.add_handler(CommandHandler("title", award_title_cmd))
    app.add_handler(CommandHandler("case", case_file_cmd))
    app.add_handler(ChatMemberHandler(handle_bot_removed, ChatMemberHandler.MY_CHAT_MEMBER))

    # باگ رفع‌شده: button_handler قبلاً بدون pattern ثبت شده بود و هر callback
    # query‌ای (پنل مدیریت، بازی‌ها، دانلودر، کپچا و ...) رو قاپ می‌زد. الان خودِ
    # تابع (بالا، _FOREIGN_CALLBACK_PREFIXES) صریح این پیشوندها رو رد می‌کنه، پس
    # ترتیب ثبت دیگه اهمیتی نداره؛ ولی ثبت captcha رو قبل از button_handler
    # نگه داشتیم برای وضوح بیشتر.
    app.add_handler(CallbackQueryHandler(captcha_verify_callback, pattern=r"^captcha:"))
    app.add_handler(CallbackQueryHandler(checkjoin_callback, pattern=r"^checkjoin$"))
    app.add_handler(CallbackQueryHandler(
        list_pagination_callback,
        pattern=r"^(ulist:|glist:|noop|gdetail:|gleave:|gleaveconfirm:)"
    ))
    app.add_handler(CallbackQueryHandler(owner_control_callback, pattern=r"^ownerinfo:"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.CONTACT, handle_shared_contact))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(MessageHandler(filters.ANIMATION, handle_gif))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Sticker.ALL, handle_photo_sticker))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # --- منوی دکمه‌ای بازی‌ها: باید قبل از register_games ثبت بشه چون هر دو
    # تو group=1 هستن و کسی که زودتر ثبت بشه، تطبیق «گیم» رو می‌گیره ---
    register_games_menu(app)

    # --- پنل مدیریت گروه دکمه‌ای (بدون نیاز به یادگیری /ban و امثالش) ---
    register_admin_panel(app, {
        "is_group_admin": is_group_admin,
        "list_add": _list_add,
        "list_remove": _list_remove,
        "list_get_one_added_at": _list_get_one_added_at,
        "log_mod_action": _log_mod_action,
        "warn_expiry_seconds": WARN_EXPIRY_SECONDS,
    })

    # --- امنیت گروه (آنتی‌لینک / آنتی‌فلود) ---
    security_deps = {
        "is_group_admin": is_group_admin,
        "list_add": _list_add,
        "list_remove": _list_remove,
        "list_get_one": _list_get_one,
        "db_run": db_run,
    }
    app.bot_data["security_deps"] = security_deps
    register_security(app, security_deps)

    # --- ابزارها (ترجمه/کیوآر/پسورد) و سرگرمی (جوک/فکت) ---
    register_tools_and_fun(app)

    # --- 🦇💵 قیمت لحظه‌ای دلار آزاد (کلمه‌ی «دلار» + دکمه‌ی «🛠 ابزارها») ---
    register_dollar_price(app)

    # --- تبدیل و فشرده‌سازی فایل (ریپلای + «فشرده») ---
    register_compress(app)
    register_post_saz(app, {"db_path": DB_PATH})  # 🎬 پست‌ساز گاتهام — «🛠 ابزارها»
    register_mureka_music(app)  # 🎵 ساخت آهنگ با هوش مصنوعی (Mureka) — «🛠 ابزارها»

    # --- تبدیل صدا به متن ---
    register_voice_to_text(app)

    # --- مدیریت گروه: قفل/باز کردن + پاکسازی ---
    register_group_admin_extra(app, {"is_group_admin": is_group_admin, "owner_id": OWNER_ID})

    # --- امکانات جدید: چرخ گردون، تیکت پشتیبانی، یادآور تولد ---
    register_new_features(app, {
        "get_player": _get_player,
        "save_player": _save_player,
        "get_inventory": get_inventory,
        "set_inventory": set_inventory,
        "db_run": db_run,
        "owner_id": OWNER_ID,
        "db_path": DB_PATH,
    })

    # --- یادآور واقعی با زمان‌بندی (JobQueue + دیتابیس) ---
    register_reminders(app, {"db_path": DB_PATH})

    # --- تشخیص فیلم/سریال از عکس یا ویدیو، تشخیص آهنگ، خلاصه‌ی گروه ---
    register_media_recognition(app)

    # --- ۶ امکان دیگه: فال، اسلات، پرونده روز، کوییز شخصیت، کپسول زمان، شهروند نمونه ---
    register_fortune_and_extras(app, {
        "get_player": _get_player,
        "save_player": _save_player,
        "get_inventory": get_inventory,
        "set_inventory": set_inventory,
        "db_run": db_run,
        "get_leaderboard": _get_leaderboard,
        "get_all_chat_ids": _get_all_chat_ids,
    })

    # --- بازی‌ها (کلمه‌محور، بدون /) — باید بعد از handle_message اضافه بشن ---
    register_games(app)
    register_extra_games(app)
    register_extra_lists(app)
    register_extra_games2(app)
    register_board_games(app)  # شطرنج / منچ / مار و پله — فقط با نوشتن اسم بازی
    register_extra_games3(app)  # یونو / قلمرو / بیلیارد / مسابقه ماشین
    register_group_rps(app)  # سنگ کاغذ قیچی گروهی — PvP واقعی، از منوی بازی‌ها ← 👥 گروهی
    register_card_room(app)  # 🃏 اتاق پاسور: جنگ / بیست‌ویک / بلک‌جک / حکم (دونفره)
    register_gotham_games(app)  # 🎟️ بازی‌های استیکری بتمن: بازی سریع + نفرین ریدلر
    register_downloader(app)  # دانلودر اینستاگرام / یوتیوب / پینترست
    register_ttt_inline(app)  # دوز inline (۳×۳ تا ۸×۸، با دوست یا با ربات) — نیاز به فعال بودن Inline Mode تو BotFather
    register_ttt_gotham(app)  # دوز گاتهام — با نوشتن کلمه تو چت، بدون نیاز به inline mode

    # --- پیام نیمه‌شب گاتهام (رویداد + دیالوگ)، خودکار برای همه‌ی گروه‌ها ---
    try:
        from midnight_announcement import register_midnight_job
        register_midnight_job(app)
    except Exception as e:
        log.warning(f"⚠️ پیام نیمه‌شب فعال نشد: {e}")

    log.info("🦇 Batman Gotham Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
