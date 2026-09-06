# -*- coding: utf-8 -*-
"""
image_to_video.py
================
🎞️ عکس به ویدیو — کاربر چندتا عکس می‌فرسته، به یه اسلایدشوی MP4 تبدیل می‌شه.

با FFmpeg محلی کار می‌کنه (بدون نیاز به هیچ API/کلیدی)، و از همون هلپرِ
`_run_ffmpeg` که تو post_saz.py هست استفاده می‌کنه تا سیستم تکراری برای
اجرای ffmpeg ساخته نشه.

روش استفاده:
    ۱) از «🛠 ابزارها» دکمه‌ی «🎞 عکس به ویدیو» رو بزن (یا بنویس «عکس به ویدیو»).
    ۲) عکس‌هات رو یکی‌یکی (یا آلبومی) بفرست — حداقل ۱، حداکثر ۲۰ تا.
    ۳) «✅ ساخت ویدیو» رو بزن و مدت نمایش هر عکس رو انتخاب کن.

همه‌ی عکس‌ها قبل از ترکیب، با ffmpeg به یه سایز استاندارد (1280x720) بدون
کشیدگی تبدیل می‌شن (با پدینگ مشکی دور تصاویری که نسبت ابعادشون فرق داره).

register_image_to_video(app) — مستقل از بقیه‌ی ماژول‌ها.
"""

import os
import shutil
import logging
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ApplicationHandlerStop,
)

from post_saz import _run_ffmpeg  # همون هلپر موجود؛ از تکرار کد جلوگیری می‌کنه

log = logging.getLogger(__name__)

MAX_PHOTOS = 20
OUT_W, OUT_H = 1280, 720

START_TRIGGER = filters.Regex(r"(?i)^\s*عکس\s*به\s*ویدیو\s*$")

IMG2V_HOWTO = (
    "🎞 *عکس به ویدیو*\n\n"
    f"عکس‌هات رو یکی‌یکی (یا آلبومی) بفرست — حداقل ۱، حداکثر {MAX_PHOTOS} تا.\n"
    "وقتی تموم شد، «✅ ساخت ویدیو» رو بزن."
)


# ------------------------------------------------------------------
# ⌨️ کیبوردها
# ------------------------------------------------------------------

def _build_start_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ انصراف", callback_data="img2v:cancel")]])


def _build_collect_keyboard(count: int):
    rows = []
    if count >= 1:
        rows.append(
            [InlineKeyboardButton(f"✅ ساخت ویدیو ({count} عکس)", callback_data="img2v:build")]
        )
    rows.append([InlineKeyboardButton("🗑 پاک کردن عکس‌ها", callback_data="img2v:reset")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="img2v:cancel")])
    return InlineKeyboardMarkup(rows)


def _build_duration_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("۱ ثانیه", callback_data="img2v:dur:1"),
                InlineKeyboardButton("۲ ثانیه", callback_data="img2v:dur:2"),
                InlineKeyboardButton("۳ ثانیه", callback_data="img2v:dur:3"),
            ],
            [InlineKeyboardButton("۵ ثانیه", callback_data="img2v:dur:5")],
            [InlineKeyboardButton("❌ انصراف", callback_data="img2v:cancel")],
        ]
    )


# ------------------------------------------------------------------
# 🧹 کمک‌کننده‌ها
# ------------------------------------------------------------------

def _cleanup(session: dict):
    tmpdir = session.get("tmpdir") if session else None
    if tmpdir and os.path.isdir(tmpdir):
        shutil.rmtree(tmpdir, ignore_errors=True)


def _new_session() -> dict:
    return {"photos": [], "tmpdir": tempfile.mkdtemp(prefix="img2v_")}


async def _build_video(bot, session: dict, duration: int) -> str:
    tmpdir = session["tmpdir"]
    photos = session["photos"]

    # ۱) دانلود عکس‌ها
    img_paths = []
    for i, file_id in enumerate(photos):
        tg_file = await bot.get_file(file_id)
        path = os.path.join(tmpdir, f"img{i:03d}.jpg")
        await tg_file.download_to_drive(path)
        img_paths.append(path)

    # ۲) نرمال‌سازی به یه سایز استاندارد، بدون کشیدگی (پدینگ مشکی دور تصویر)
    vf = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    norm_paths = []
    for i, p in enumerate(img_paths):
        norm_path = os.path.join(tmpdir, f"norm{i:03d}.jpg")
        _run_ffmpeg(["-i", p, "-vf", vf, norm_path], timeout=60)
        norm_paths.append(norm_path)

    # ۳) ساخت فایل لیستِ concat (هر عکس با duration مشخص؛ طبق نیاز خودِ
    #    concat demuxer، آخرین فایل یه‌بار دیگه بدون duration تکرار می‌شه)
    list_path = os.path.join(tmpdir, "list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in norm_paths:
            f.write(f"file '{p}'\n")
            f.write(f"duration {duration}\n")
        f.write(f"file '{norm_paths[-1]}'\n")

    # ۴) ساخت ویدیوی نهایی (سازگار با Telegram: h264 + yuv420p)
    out_path = os.path.join(tmpdir, "output.mp4")
    _run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-vf", "fps=30,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-movflags", "+faststart",
            out_path,
        ],
        timeout=180,
    )
    return out_path


# ------------------------------------------------------------------
# 📷 جمع‌آوری عکس‌ها
# ------------------------------------------------------------------

async def img2v_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اگه کاربر تو فلوی جمع‌آوری عکس‌ها نباشه، بدون هیچ کاری برمی‌گرده تا
    هندلرهای دیگه‌ی عکس (مثل پیشنهاد تشخیص فیلم) عادی کار کنن."""
    session = context.user_data.get("img2v")
    if session is None:
        return

    msg = update.effective_message
    if len(session["photos"]) >= MAX_PHOTOS:
        await msg.reply_text(
            f"⚠️ حداکثر {MAX_PHOTOS} عکس قابل قبوله. بزن «✅ ساخت ویدیو».",
            reply_markup=_build_collect_keyboard(len(session["photos"])),
        )
        raise ApplicationHandlerStop

    photo = msg.photo[-1]  # بزرگ‌ترین سایز موجود
    session["photos"].append(photo.file_id)
    count = len(session["photos"])
    await msg.reply_text(
        f"📷 عکس شماره {count} اضافه شد.", reply_markup=_build_collect_keyboard(count)
    )
    raise ApplicationHandlerStop


async def img2v_start_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["img2v"] = _new_session()
    await update.effective_message.reply_text(
        IMG2V_HOWTO, reply_markup=_build_start_keyboard(), parse_mode="Markdown"
    )


# ------------------------------------------------------------------
# 🆕 منوی دکمه‌ای
# ------------------------------------------------------------------

async def img2v_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    await query.answer()
    session = context.user_data.get("img2v")

    if data == "img2v:start":
        context.user_data["img2v"] = _new_session()
        await query.edit_message_text(
            IMG2V_HOWTO, reply_markup=_build_start_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "img2v:cancel":
        if session:
            _cleanup(session)
        context.user_data.pop("img2v", None)
        await query.edit_message_text("❌ عملیات «عکس به ویدیو» لغو شد.")
        return

    if data == "img2v:reset":
        if session is None:
            await query.answer("⚠️ اول از دکمه‌ی «🎞 عکس به ویدیو» شروع کن.", show_alert=True)
            return
        session["photos"] = []
        await query.edit_message_text(
            IMG2V_HOWTO, reply_markup=_build_start_keyboard(), parse_mode="Markdown"
        )
        return

    if data == "img2v:build":
        if not session or not session["photos"]:
            await query.answer("⚠️ اول چندتا عکس بفرست.", show_alert=True)
            return
        await query.edit_message_text(
            f"🔁 {len(session['photos'])} عکس داری. هر عکس چند ثانیه نمایش داده بشه؟",
            reply_markup=_build_duration_keyboard(),
        )
        return

    if data.startswith("img2v:dur:"):
        if not session or not session["photos"]:
            await query.answer("⚠️ عکسی برای پردازش نیست.", show_alert=True)
            return
        duration = int(data.split(":")[2])
        await query.edit_message_text("🎬 در حال ساخت ویدیو... چند لحظه صبر کن 🦇")
        try:
            out_path = await _build_video(context.bot, session, duration)
            with open(out_path, "rb") as f:
                await query.message.reply_video(f, caption="🎞 ویدیوی گاتهامی‌ت آماده‌ست!")
        except FileNotFoundError:
            await query.message.reply_text(
                "⚠️ FFmpeg رو سرور پیدا نشد. اگه صاحب رباتی، مطمئن شو FFmpeg رو محیط Deploy نصبه."
            )
        except Exception as e:
            log.error(f"image_to_video build failed: {e}")
            await query.message.reply_text(
                "⚠️ ساخت ویدیو با خطا مواجه شد (شاید عکس خراب بود یا زمان زیاد طول کشید)، دوباره امتحان کن."
            )
        finally:
            _cleanup(session)
            context.user_data.pop("img2v", None)
        return


def register_image_to_video(app):
    app.add_handler(MessageHandler(START_TRIGGER, img2v_start_text_handler), group=20)
    app.add_handler(MessageHandler(filters.PHOTO, img2v_photo_handler), group=20)
    app.add_handler(CallbackQueryHandler(img2v_button_callback, pattern=r"^img2v:"), group=28)
