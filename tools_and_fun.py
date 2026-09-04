# -*- coding: utf-8 -*-
"""
tools_and_fun.py
================
دو بخش از پنل تنظیمات: «🧰 ابزارها» و «🎉 سرگرمی».

ابزارها:
    - ترجمه هوشمند: بنویس «ترجمه <متن>» یا روی یه پیام ریپلای کن و بنویس «ترجمه».
      تشخیص خودکار فارسی↔انگلیسی (از سرویس رایگان MyMemory، بدون نیاز به کلید API).
    - کیوآر: بنویس «کیوآر <متن یا لینک>» تا عکس QR Code بسازه.
    - پسورد: بنویس «پسورد» یا «رمز عبور» تا یه رمز قوی و تصادفی بسازه (کاملاً محلی،
      بدون تماس با اینترنت).

سرگرمی:
    - «جوک» — یه جوک تصادفی
    - «واقعیت جالب» — یه فکت تصادفی
    - «جمله بتمنی» همون /quote موجوده، از پنل هم قابل دسترسیه.

register_tools_and_fun(app) — مستقل از بقیه‌ی ماژول‌ها، فقط برای تولید QR از
httpx استفاده می‌کنه که از قبل تو requirements هست.
"""

import re
import ast
import math
import random
import secrets
import string
import logging
import operator as op
from urllib.parse import quote

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

TOOLS_TEXT = (
    "🧰 *ابزارها*\n\n"
    "🌐 ترجمه هوشمند: بنویس «ترجمه <متن>» یا روی یه پیام ریپلای کن و فقط بنویس «ترجمه».\n"
    "📱 کیوآر: بنویس «کیوآر <متن یا لینک>» تا عکس بارکد بسازم.\n"
    "🔑 پسورد قوی: بنویس «پسورد» یا «رمز عبور» تا یه رمز تصادفی امن بسازم.\n"
    "📐 تبدیل واحد: بنویس «تبدیل <عدد> <واحد۱> به <واحد۲>» (وزن/طول/دما/ارز).\n"
    "🧮 ماشین‌حساب: بنویس «حساب <عبارت>» (مثل «حساب (۱۲+۳)*۲» یا «حساب sqrt(81)»).\n"
    "💵 قیمت دلار: فقط بنویس «دلار» (یا دکمه‌ی زیر) تا قیمت لحظه‌ای دلار آزاد رو بگیری.\n"
    "🎬 پست‌ساز گاتهام: ویرایش/فشرده‌سازی/لوگو/کپشن برای ویدیو، عکس و گیف — از دکمه‌ی زیر.\n"
    "🎵 ساخت آهنگ با هوش مصنوعی: از متن آهنگ + سبک دلخواهت، یه آهنگ کامل می‌سازم — از دکمه‌ی زیر."
)

PERSIAN_DIGITS_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

# ---------- ماشین‌حساب امن (بدون eval واقعی) ----------

_ALLOWED_OPERATORS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow,
    ast.USub: op.neg, ast.UAdd: op.pos,
}
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "abs": abs, "round": round,
    "exp": math.exp, "factorial": math.factorial,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


def _safe_eval_node(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("مقدار نامعتبر")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError("عملگر مجاز نیست")
        return _ALLOWED_OPERATORS[op_type](_safe_eval_node(node.left), _safe_eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError("عملگر مجاز نیست")
        return _ALLOWED_OPERATORS[op_type](_safe_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError("تابع مجاز نیست")
        args = [_safe_eval_node(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[node.id]
        raise ValueError("نام مجاز نیست")
    raise ValueError("عبارت مجاز نیست")


def safe_calc(expr: str):
    expr = expr.translate(PERSIAN_DIGITS_TRANS)
    expr = expr.replace("^", "**").replace("×", "*").replace("÷", "/").replace("٪", "%")
    tree = ast.parse(expr, mode="eval")
    return _safe_eval_node(tree)


# ---------- تبدیل واحد ----------

LENGTH_TO_M = {"m": 1, "cm": 0.01, "mm": 0.001, "km": 1000, "mile": 1609.344, "yard": 0.9144, "foot": 0.3048, "inch": 0.0254}
LENGTH_ALIASES = {
    "متر": "m", "m": "m",
    "سانتی متر": "cm", "سانتیمتر": "cm", "cm": "cm",
    "میلی متر": "mm", "میلیمتر": "mm", "mm": "mm",
    "کیلومتر": "km", "km": "km",
    "مایل": "mile", "mile": "mile",
    "یارد": "yard", "yard": "yard",
    "فوت": "foot", "foot": "foot", "ft": "foot",
    "اینچ": "inch", "inch": "inch", "in": "inch",
}

WEIGHT_TO_G = {"g": 1, "kg": 1000, "mg": 0.001, "lb": 453.592, "oz": 28.3495, "ton": 1_000_000}
WEIGHT_ALIASES = {
    "گرم": "g", "g": "g",
    "کیلوگرم": "kg", "کیلو": "kg", "kg": "kg",
    "میلی گرم": "mg", "میلیگرم": "mg", "mg": "mg",
    "پوند": "lb", "lb": "lb",
    "اونس": "oz", "oz": "oz",
    "تن": "ton", "ton": "ton",
}

TEMP_ALIASES = {
    "سلسیوس": "c", "celsius": "c", "c": "c",
    "فارنهایت": "f", "fahrenheit": "f", "f": "f",
    "کلوین": "k", "kelvin": "k", "k": "k",
}

CURRENCY_ALIASES = {
    "دلار": "USD", "dollar": "USD", "usd": "USD",
    "یورو": "EUR", "euro": "EUR", "eur": "EUR",
    "تومان": "TOMAN", "toman": "TOMAN",
    "ریال": "IRR", "rial": "IRR", "irr": "IRR",
    "پوند انگلیس": "GBP", "گبپ": "GBP", "gbp": "GBP",
    "لیر": "TRY", "try": "TRY",
    "درهم": "AED", "aed": "AED",
    "ین": "JPY", "jpy": "JPY",
    "یوان": "CNY", "cny": "CNY",
}


def _convert_temp(value, uf, ut):
    if uf == ut:
        return value
    if uf == "f":
        c = (value - 32) * 5 / 9
    elif uf == "k":
        c = value - 273.15
    else:
        c = value
    if ut == "f":
        return c * 9 / 5 + 32
    if ut == "k":
        return c + 273.15
    return c


async def _get_usd_rates():
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://open.er-api.com/v6/latest/USD")
        resp.raise_for_status()
        data = resp.json()
    if data.get("result") != "success":
        raise ValueError("دریافت نرخ ارز الان جواب نداد")
    return data["rates"]


async def _convert_currency(amount, cf, ct):
    rates = await _get_usd_rates()

    def rate_for(code):
        if code == "TOMAN":
            return rates["IRR"] / 10
        if code not in rates:
            raise ValueError(f"نرخ {code} در دسترس نیست")
        return rates[code]

    usd_amount = amount / rate_for(cf)
    return usd_amount * rate_for(ct)


async def do_unit_convert(amount: float, unit_from_raw: str, unit_to_raw: str) -> float:
    uf = unit_from_raw.strip().lower()
    ut = unit_to_raw.strip().lower()
    if uf in CURRENCY_ALIASES or ut in CURRENCY_ALIASES:
        if uf not in CURRENCY_ALIASES or ut not in CURRENCY_ALIASES:
            raise ValueError("واحدهای پولی رو باید هر دو ارز بنویسی")
        return await _convert_currency(amount, CURRENCY_ALIASES[uf], CURRENCY_ALIASES[ut])
    if uf in TEMP_ALIASES and ut in TEMP_ALIASES:
        return _convert_temp(amount, TEMP_ALIASES[uf], TEMP_ALIASES[ut])
    if uf in LENGTH_ALIASES and ut in LENGTH_ALIASES:
        base = amount * LENGTH_TO_M[LENGTH_ALIASES[uf]]
        return base / LENGTH_TO_M[LENGTH_ALIASES[ut]]
    if uf in WEIGHT_ALIASES and ut in WEIGHT_ALIASES:
        base = amount * WEIGHT_TO_G[WEIGHT_ALIASES[uf]]
        return base / WEIGHT_TO_G[WEIGHT_ALIASES[ut]]
    raise ValueError("این واحدها رو نشناختم یا با هم سازگار نیستن")


UNIT_RE = re.compile(r"^\s*تبدیل\s+([۰-۹0-9.]+)\s+(.+?)\s+به\s+(.+?)\s*$")
CALC_RE = re.compile(r"^\s*(?:حساب|محاسبه)\s+(.+)$")

FUN_TEXT = "🎉 *سرگرمی*\n\nیکی رو انتخاب کن یا کلمه‌ش رو بنویس:"

_PERSIAN_CHARS = set("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")


def _is_persian(text: str) -> bool:
    return sum(1 for ch in text if ch in _PERSIAN_CHARS) > len(text) / 6


async def _translate(text: str) -> str:
    langpair = "fa|en" if _is_persian(text) else "en|fa"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:490], "langpair": langpair},
        )
        resp.raise_for_status()
        data = resp.json()
    translated = (data.get("responseData") or {}).get("translatedText")
    if not translated:
        raise ValueError("empty translation")
    return translated


def _gen_password(length=16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


JOKES = [
    "به مهندس نرم‌افزار گفتن چراغو خاموش کن، گفت صبر کن دارم debug می‌کنم چرا روشنه!",
    "دو تا آنتن رو پشت‌بوم همو دیدن، یکی گفت سلام، اون یکی گفت این چه استقبال گرمی بود، من که فقط signal دادم!",
    "چرا برنامه‌نویس‌ها تاریک رو دوست دارن؟ چون light attracts bugs!",
    "به کامپیوتر گفتن مشکلت چیه؟ گفت یه Ctrl از دستم رفته، الان کنترل هیچی رو ندارم!",
    "چرا پایتون هیچ‌وقت عصبانی نمی‌شه؟ چون indentation داره، همیشه تو خط خودشه!",
]

FACTS = [
    "قلب میگو تو سرشه، نه تو سینه‌ش.",
    "عسل هیچ‌وقت فاسد نمی‌شه؛ عسل هزاران‌ساله تو مقبره‌های مصری هنوز قابل‌خوردنه.",
    "اختاپوس سه تا قلب داره و خونش آبی‌رنگه.",
    "یه روز روی سیاره‌ی زهره از یه سال زهره طولانی‌تره.",
    "موزها از نظر گیاه‌شناسی نوعی توت (Berry) حساب می‌شن.",
]


def tools_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 ترجمه", callback_data="tool:howto:translate"),
         InlineKeyboardButton("📱 کیوآر", callback_data="tool:howto:qr")],
        [InlineKeyboardButton("🔑 پسورد تصادفی", callback_data="tool:password")],
        [InlineKeyboardButton("📐 تبدیل واحد", callback_data="tool:howto:convert"),
         InlineKeyboardButton("🧮 ماشین‌حساب", callback_data="tool:howto:calc")],
        [InlineKeyboardButton("🎬 پست‌ساز گاتهام", callback_data="postsaz:open")],
        [InlineKeyboardButton("🎵 ساخت آهنگ با هوش مصنوعی", callback_data="treblo:start")],
        [InlineKeyboardButton("💵 قیمت دلار", callback_data="gdollar:show")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ])


def fun_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😂 جوک", callback_data="fun:joke"),
         InlineKeyboardButton("💡 واقعیت جالب", callback_data="fun:fact")],
        [InlineKeyboardButton("🦇 جمله بتمنی", callback_data="fun:quote")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:main")],
    ])


BATMAN_QUOTES = [
    "من از تاریکی نمی‌ترسم، من خودِ تاریکی‌ام.",
    "این چیزی نیست که من هستم، بلکه کاری‌ست که انجام می‌دهم که مرا تعریف می‌کند.",
    "گاتهام به یک قهرمان نیاز ندارد، به کسی نیاز دارد که واقعیت را بپذیرد.",
]


def register_tools_and_fun(app):

    async def tools_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(TOOLS_TEXT, reply_markup=tools_menu_keyboard(), parse_mode="Markdown")

    async def fun_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(FUN_TEXT, reply_markup=fun_menu_keyboard(), parse_mode="Markdown")

    async def translate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        msg = update.effective_message
        text = (msg.text or "").strip()
        target_text = None
        if text.lower().startswith("ترجمه"):
            rest = text[len("ترجمه"):].strip()
            if rest:
                target_text = rest
            elif msg.reply_to_message and msg.reply_to_message.text:
                target_text = msg.reply_to_message.text
        if target_text is None:
            return False
        try:
            result = await _translate(target_text)
        except Exception as e:
            log.info(f"translate failed: {e}")
            await msg.reply_text("⚠️ ترجمه الان جواب نداد، یه‌کم بعد دوباره امتحان کن.")
            return True
        await msg.reply_text(f"🌐 {result}")
        return True

    async def qr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        msg = update.effective_message
        text = (msg.text or "").strip()
        if not text.lower().startswith("کیوآر") and not text.lower().startswith("qr "):
            return False
        payload = text.split(" ", 1)[1].strip() if " " in text else ""
        if not payload:
            await msg.reply_text("✏️ بعد از «کیوآر» متن یا لینکی که می‌خوای بذار. مثال: کیوآر https://example.com")
            return True
        url = f"https://api.qrserver.com/v1/create-qr-code/?size=350x350&data={quote(payload)}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                await msg.reply_photo(resp.content, caption="📱 کیوآر ساخته شد")
        except Exception as e:
            log.info(f"qr failed: {e}")
            await msg.reply_text("⚠️ ساخت کیوآر الان جواب نداد.")
        return True

    async def password_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        text = (update.effective_message.text or "").strip()
        if text not in ("پسورد", "رمز عبور", "پسورد قوی"):
            return False
        pw = _gen_password()
        await update.effective_message.reply_text(
            f"🔑 رمز پیشنهادی:\n`{pw}`\n\nذخیره‌ش کن، بعد از این پیام دیگه جایی نگهش نمی‌داریم.",
            parse_mode="Markdown",
        )
        return True

    async def unit_convert_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        msg = update.effective_message
        text = (msg.text or "").strip()
        m = UNIT_RE.match(text)
        if not m:
            return False
        amount_raw, uf_raw, ut_raw = m.groups()
        try:
            amount = float(amount_raw.translate(PERSIAN_DIGITS_TRANS))
        except ValueError:
            await msg.reply_text("✏️ عدد رو درست ننوشتی. مثال: تبدیل 10 کیلوگرم به پوند")
            return True
        try:
            result = await do_unit_convert(amount, uf_raw, ut_raw)
        except ValueError as e:
            await msg.reply_text(f"⚠️ {e}")
            return True
        except Exception as e:
            log.info(f"unit convert failed: {e}")
            await msg.reply_text("⚠️ الان نتونستم تبدیل کنم، یه‌کم بعد دوباره امتحان کن.")
            return True
        await msg.reply_text(f"📐 {amount_raw} {uf_raw} = {result:.4g} {ut_raw}")
        return True

    async def calc_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        msg = update.effective_message
        text = (msg.text or "").strip()
        m = CALC_RE.match(text)
        if not m:
            return False
        try:
            result = safe_calc(m.group(1))
        except Exception:
            await msg.reply_text("⚠️ نتونستم این عبارت رو محاسبه کنم. مثال: حساب (12+3)*2 یا حساب sqrt(81)")
            return True
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        await msg.reply_text(f"🧮 نتیجه: {result}")
        return True

    async def fun_keyword_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        text = (update.effective_message.text or "").strip()
        if text == "جوک":
            await update.effective_message.reply_text(random.choice(JOKES))
            return True
        if text in ("واقعیت جالب", "فکت"):
            await update.effective_message.reply_text(f"💡 {random.choice(FACTS)}")
            return True
        return False

    async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """یه هندلر ترکیبی سبک، چون همه‌ی این کلیدواژه‌ها مستقل و کوتاهن."""
        text = (update.effective_message.text or "").strip()
        if text in ("ابزارها", "ابزار"):
            await tools_menu_cmd(update, context)
            return
        if text in ("سرگرمی",):
            await fun_menu_cmd(update, context)
            return
        if await password_handler(update, context):
            return
        if await fun_keyword_handler(update, context):
            return
        if await qr_handler(update, context):
            return
        if await unit_convert_handler(update, context):
            return
        if await calc_handler(update, context):
            return
        if await translate_handler(update, context):
            return

    async def tool_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        if data == "tool:password":
            pw = _gen_password()
            await query.answer()
            await query.message.reply_text(f"🔑 رمز پیشنهادی:\n`{pw}`", parse_mode="Markdown")
            return
        if data == "tool:howto:translate":
            await query.answer("بنویس «ترجمه <متن>» یا روی پیامی ریپلای کن و بنویس «ترجمه»", show_alert=True)
            return
        if data == "tool:howto:qr":
            await query.answer("بنویس «کیوآر <متن یا لینک>»", show_alert=True)
            return
        if data == "tool:howto:convert":
            await query.answer("بنویس مثلاً: تبدیل 10 کیلوگرم به پوند / تبدیل 100 دلار به تومان", show_alert=True)
            return
        if data == "tool:howto:calc":
            await query.answer("بنویس مثلاً: حساب (12+3)*2 یا حساب sqrt(81)", show_alert=True)
            return

    async def fun_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        await query.answer()
        if data == "fun:joke":
            await query.message.reply_text(random.choice(JOKES))
        elif data == "fun:fact":
            await query.message.reply_text(f"💡 {random.choice(FACTS)}")
        elif data == "fun:quote":
            await query.message.reply_text(random.choice(BATMAN_QUOTES))

    app.add_handler(MessageHandler(filters.Regex(r"(?i)^\s*(ابزارها|ابزار)\s*$"), tools_menu_cmd), group=21)
    app.add_handler(MessageHandler(filters.Regex(r"(?i)^\s*سرگرمی\s*$"), fun_menu_cmd), group=21)
    app.add_handler(CallbackQueryHandler(tool_callback, pattern=r"^tool:"), group=21)
    app.add_handler(CallbackQueryHandler(fun_callback, pattern=r"^fun:"), group=21)

    # این هندلر خودش یه سری کلیدواژه رو مصرف می‌کنه و برمی‌گرده؛ اگه هیچ‌کدوم
    # نبود کاری نمی‌کنه (پس با بقیه‌ی هندلرهای group بالاتر تداخلی نداره).
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router), group=21)

    app.bot_data["build_tools_text_kb"] = (TOOLS_TEXT, tools_menu_keyboard)
    app.bot_data["build_fun_text_kb"] = (FUN_TEXT, fun_menu_keyboard)
