# -*- coding: utf-8 -*-
"""
post_saz.py
================
🎬 پست‌ساز گاتهام — ابزار مستقلِ ویرایش/آماده‌سازی پست (به سبک PostSaz)،
داخل «🛠 ابزارها».

قابلیت‌ها:
    - دریافت Video / Photo / GIF / Audio / Document / Text
    - ویرایش متن (caption)، حذف‌کننده‌ی تگ/لینک، امضای کانال، دکمه‌ی شیشه‌ای
    - لوگو (فایل آماده‌ی PNG شفاف یا متنِ ساده)، با اندازه و موقعیت قابل‌تنظیم
    - فشرده‌سازی ویدیو با FFmpeg (مرحله‌ای، درصد واقعی از حجم فایل)
    - تبدیل ویدیو/گیف به خروجی مربعی (۱:۱)
    - تبدیل ویدیو به GIF
    - ارسال مستقیم به کانال ذخیره‌شده‌ی کاربر (با بررسی دسترسی/ادمین‌بودن ربات)

register_post_saz(app, deps):
    deps = {"db_path": ...}

معماری: این ماژول کاملاً مستقل ذخیره‌سازی/state خودش رو داره (جدول
postsaz_settings تو همون دیتابیس مشترک پروژه + context.user_data برای
سشن فعالِ هر کاربر) و با بقیه‌ی ماژول‌ها تداخل نداره. تنها نقطه‌ی اتصالش با
بقیه‌ی پروژه، فراخوانی postsaz_intercept(update, context) از داخل چند
هندلر موجوده (بدون تغییر رفتارشون وقتی کاربر تو سشن پست‌ساز نیست) — دقیقاً
همون الگویی که برای رفع تداخل بازی‌ها/AI قبلاً استفاده شده.
"""

import os
import re
import json
import shutil
import logging
import sqlite3
import tempfile
import subprocess

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

log = logging.getLogger(__name__)

_DB_PATH = "bot.db"

BATMAN_FAIL_MSG = "🦇 گاتهام نتوانست این فایل را پردازش کند.\n\nلطفاً دوباره تلاش کنید."

CANCEL_WORDS = ("❌ انصراف", "انصراف", "خروج", "/cancel")

# =========================================================
#  DATABASE
# =========================================================


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS postsaz_settings (
            user_id INTEGER PRIMARY KEY,
            tag_link_cleaner INTEGER DEFAULT 1,
            channel_id TEXT DEFAULT '',
            signature TEXT DEFAULT '',
            logo_file_id TEXT DEFAULT '',
            logo_size REAL DEFAULT 1.0,
            logo_pos_v TEXT DEFAULT 'bottom',
            logo_pos_h TEXT DEFAULT 'right',
            buttons_json TEXT DEFAULT '[]'
        )
    """)
    conn.commit()
    conn.close()


def _get_settings(user_id: int) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM postsaz_settings WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO postsaz_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM postsaz_settings WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row)


def _save_settings(user_id: int, **fields):
    if not fields:
        return
    _get_settings(user_id)  # اطمینان از وجود ردیف
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = _connect()
    conn.execute(f"UPDATE postsaz_settings SET {cols} WHERE user_id=?", (*fields.values(), user_id))
    conn.commit()
    conn.close()


# =========================================================
#  FFMPEG / FFPROBE HELPERS
# =========================================================


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _probe(path: str) -> dict:
    """اطلاعات فایل رو با ffprobe برمی‌گردونه؛ اگه فایل خراب/نامعتبر باشه {} میده."""
    if not _ffprobe_available():
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=30, check=True,
        )
        return json.loads(out.stdout or b"{}")
    except Exception as e:
        log.info(f"ffprobe failed: {e}")
        return {}


def _is_valid_media(path: str) -> bool:
    """بعد از هر پردازش FFmpeg، خروجی رو validate می‌کنه تا فایل خراب/duration=0
    ارسال نشه."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    info = _probe(path)
    if not info:
        # ffprobe نصب نیست؛ فقط چک حجم فایل کافیه (بهتر از رد کردن کامل قابلیت)
        return True
    fmt = info.get("format", {})
    try:
        duration = float(fmt.get("duration", "0") or "0")
    except ValueError:
        duration = 0.0
    streams = info.get("streams", [])
    if not streams:
        return False
    if duration <= 0 and any(s.get("codec_type") == "video" for s in streams):
        return False
    return True


def _reduction_percent(before: int, after: int) -> float:
    if before <= 0:
        return 0.0
    pct = ((before - after) / before) * 100
    return max(0.0, round(pct, 1))


def _run_ffmpeg(args, timeout=280):
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, timeout=timeout)


def compress_video_stepped(in_path: str, out_dir: str):
    """فشرده‌سازی مرحله‌ای واقعی: چند crf رو امتحان می‌کنه، درصد کاهش حجم رو از
    حجم واقعیِ هر مرحله حساب می‌کنه، و اگه مرحله‌ی بعدی واقعاً کوچیک‌تر نشد
    (یا افت کیفیت محسوس/فایل نامعتبر بود)، بهترین نسخه‌ی قبلی رو نگه می‌داره.

    برمی‌گردونه: (final_path, stages) — stages لیستی از dict با کلیدهای
    crf / size / reduction_percent برای نمایش تو UI.
    """
    before = os.path.getsize(in_path)
    stages = []
    best_path = in_path
    best_size = before
    for i, crf in enumerate((23, 28, 33, 38), start=1):
        candidate = os.path.join(out_dir, f"stage{i}.mp4")
        try:
            _run_ffmpeg([
                "-i", in_path,
                "-vcodec", "libx264", "-crf", str(crf), "-preset", "veryfast",
                "-acodec", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                candidate,
            ])
        except Exception as e:
            log.info(f"compress stage {i} (crf={crf}) failed: {e}")
            break
        if not _is_valid_media(candidate):
            break
        size = os.path.getsize(candidate)
        stages.append({"crf": crf, "size": size, "reduction_percent": _reduction_percent(before, size)})
        if size < best_size:
            best_path = candidate
            best_size = size
        else:
            # این مرحله دیگه واقعاً کوچیک‌تر نکرد؛ همینجا متوقف شو
            break
    if best_path == in_path:
        # هیچ مرحله‌ای واقعاً کمک نکرد (یا ffmpeg در دسترس نبود) — همون اصلی رو نگه دار
        return in_path, stages
    return best_path, stages


def make_square(in_path: str, out_path: str, is_video: bool):
    """کراپ مرکزی به‌نسبت ۱:۱. زیرنویس: بدون تشخیص سوژه، مرکز فریم حفظ می‌شه —
    برای اکثر ویدیوها/گیف‌ها بهترین حدس بی‌نیاز از هوش مصنوعیه."""
    vf = "crop='min(iw,ih)':'min(iw,ih)'"
    args = ["-i", in_path, "-vf", vf]
    if is_video:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "copy"]
    args += [out_path]
    _run_ffmpeg(args)


def video_to_gif(in_path: str, out_path: str, fps: int = 12, width: int = 480):
    palette = out_path + ".palette.png"
    try:
        _run_ffmpeg(["-i", in_path, "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen", palette])
        _run_ffmpeg([
            "-i", in_path, "-i", palette,
            "-lavfi", f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse",
            out_path,
        ])
    finally:
        if os.path.exists(palette):
            try:
                os.remove(palette)
            except OSError:
                pass


_POS_MARGIN = 16


def _logo_xy_expr(pos_h: str, pos_v: str):
    x = {"left": f"{_POS_MARGIN}", "center": "(main_w-overlay_w)/2", "right": f"main_w-overlay_w-{_POS_MARGIN}"}.get(pos_h, f"main_w-overlay_w-{_POS_MARGIN}")
    y = {"top": f"{_POS_MARGIN}", "middle": "(main_h-overlay_h)/2", "bottom": f"main_h-overlay_h-{_POS_MARGIN}"}.get(pos_v, f"main_h-overlay_h-{_POS_MARGIN}")
    return x, y


def overlay_logo_video(in_path: str, logo_path: str, out_path: str, size_ratio: float, pos_h: str, pos_v: str):
    size_ratio = max(0.2, min(3.0, size_ratio))
    x, y = _logo_xy_expr(pos_h, pos_v)
    filter_complex = f"[1:v]scale=iw*{size_ratio}:-1[logo];[0:v][logo]overlay={x}:{y}"
    _run_ffmpeg([
        "-i", in_path, "-i", logo_path,
        "-filter_complex", filter_complex,
        "-codec:a", "copy",
        out_path,
    ])


def overlay_logo_photo(in_path: str, logo_path: str, out_path: str, size_ratio: float, pos_h: str, pos_v: str):
    from PIL import Image
    size_ratio = max(0.2, min(3.0, size_ratio))
    with Image.open(in_path) as base:
        base = base.convert("RGBA")
        with Image.open(logo_path) as logo:
            logo = logo.convert("RGBA")
            target_w = max(1, int(base.width * 0.2 * size_ratio))
            ratio = target_w / logo.width
            logo = logo.resize((target_w, max(1, int(logo.height * ratio))))
            mx, my = _POS_MARGIN, _POS_MARGIN
            x = {"left": mx, "center": (base.width - logo.width) // 2, "right": base.width - logo.width - mx}.get(pos_h, base.width - logo.width - mx)
            y = {"top": my, "middle": (base.height - logo.height) // 2, "bottom": base.height - logo.height - my}.get(pos_v, base.height - logo.height - my)
            base.alpha_composite(logo, (int(x), int(y)))
        base.convert("RGB").save(out_path, "JPEG", quality=92)


# =========================================================
#  متن: پاک‌سازی تگ/لینک + امضا + دکمه‌های شیشه‌ای
# =========================================================

_URL_RE = re.compile(r"(?i)\b((https?://|www\.|t\.me/)\S+)")
_USERNAME_RE = re.compile(r"(?<!\w)@\w{4,32}")
_AD_PHRASES_RE = re.compile(r"(?im)^.*(کانال\s+ما|عضو\s+کانال|لینک\s+کانال|جوین\s+شوید|join\s+(now|us)).*$")


def sanitize_text(text: str, enabled: bool) -> str:
    if not text or not enabled:
        return text or ""
    cleaned = _URL_RE.sub("", text)
    cleaned = _USERNAME_RE.sub("", cleaned)
    cleaned = _AD_PHRASES_RE.sub("", cleaned)
    # فاصله‌های اضافه‌ی به‌جامونده رو جمع کن، ولی خط‌های خالیِ خودِ کاربر رو دست نزن
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def apply_signature(text: str, signature: str) -> str:
    text = (text or "").rstrip()
    if not signature:
        return text
    return f"{text}\n\n{signature}" if text else signature


def build_buttons_markup(buttons_json: str):
    try:
        rows = json.loads(buttons_json or "[]")
    except (ValueError, TypeError):
        rows = []
    if not rows:
        return None
    kb = []
    for row in rows:
        kb_row = [InlineKeyboardButton(b["text"], url=b["url"]) for b in row if b.get("text") and b.get("url")]
        if kb_row:
            kb.append(kb_row)
    return InlineKeyboardMarkup(kb) if kb else None


def parse_buttons_text(text: str):
    """هر خط: «متن دکمه - لینک» ؛ خط‌های پشت‌سرهم = یه ردیف جدا (سطر ساده)."""
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        label, url = line.rsplit(" - ", 1)
        label, url = label.strip(), url.strip()
        if label and (url.startswith("http://") or url.startswith("https://")):
            rows.append([{"text": label, "url": url}])
    return rows


# =========================================================
#  کیبوردها / متن‌های پنل
# =========================================================


def postsaz_intro_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 شروع", callback_data="postsaz:begin")],
        [InlineKeyboardButton("⚙️ تنظیمات پست‌ساز", callback_data="postsaz:settings")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="panel:tools")],
    ])


POSTSAZ_INTRO_TEXT = (
    "🎬 *پست‌ساز گاتهام*\n\n"
    "🎬 ویدیو / 🖼 عکس / 🎞 گیف / 🔊 صدا / 📝 متن / 📦 فایل بفرست تا روش کار کنم.\n"
    "هر وقت خواستی، «❌ انصراف» رو بزن یا بنویس تا از این ابزار خارج بشی."
)


def _settings_text(s: dict) -> str:
    def onoff(v):
        return "✅ فعال" if v else "❌ غیرفعال"

    return (
        "🔧 *تنظیمات پست‌ساز*\n\n"
        f"➖ حذف کننده تگ و لینک: {onoff(s['tag_link_cleaner'])}\n"
        f"➖ آیدی کانال: {s['channel_id'] or '❌ وارد نشده'}\n"
        f"➖ امضاء کانال: {s['signature'] or '❌ وارد نشده'}\n"
        f"➖ دکمه شیشه ای: {'✅ ' + str(len(json.loads(s['buttons_json'] or '[]'))) + ' ردیف' if s['buttons_json'] not in ('', '[]') else '❌ وارد نشده'}\n"
        f"➖ لوگوی کانال: {'✅ ذخیره شده' if s['logo_file_id'] else '❌ وارد نشده'}\n"
    )


def _settings_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 حذف کننده تگ و لینک", callback_data="postsaz:set:tag_toggle")],
        [InlineKeyboardButton("🏷 آیدی کانال", callback_data="postsaz:set:channel_id")],
        [InlineKeyboardButton("✍️ امضاء کانال", callback_data="postsaz:set:signature")],
        [InlineKeyboardButton("🔘 دکمه شیشه‌ای", callback_data="postsaz:set:buttons")],
        [InlineKeyboardButton("🎨 لوگو", callback_data="postsaz:set:logo")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="postsaz:open")],
    ])


def _menu_text(session: dict) -> str:
    before = session.get("orig_size", 0)
    lines = ["🎬 *پست‌ساز گاتهام*", ""]
    lines.append(f"📦 حجم اصلی: {before/1024/1024:.1f}MB")
    ops = session["ops"]
    active = []
    if ops.get("square"):
        active.append("🔳 مربعی")
    if ops.get("to_gif"):
        active.append("🎞 تبدیل به GIF")
    if ops.get("logo"):
        active.append("🎨 لوگو")
    if ops.get("max_compress"):
        active.append("🗜 فشرده‌سازی حداکثری")
    if ops.get("caption") is not None:
        active.append("📝 متن ویرایش‌شده")
    if active:
        lines.append("✅ فعال: " + "، ".join(active))
    return "\n".join(lines)


def _menu_kb(session: dict):
    kind = session["kind"]
    rows = [[InlineKeyboardButton("📝 ویرایش متن", callback_data="postsaz:op:caption")]]
    if kind in ("video", "gif", "photo"):
        rows.append([InlineKeyboardButton("🎨 درج لوگو", callback_data="postsaz:op:logo"),
                     InlineKeyboardButton("📐 سایز لوگو", callback_data="postsaz:op:logosize")])
    if kind in ("video", "gif"):
        rows.append([InlineKeyboardButton("🔳 تصویر مربعی", callback_data="postsaz:op:square")])
    if kind == "video":
        rows.append([InlineKeyboardButton("🎞 تبدیل به GIF", callback_data="postsaz:op:togif"),
                     InlineKeyboardButton("🗜 فشرده‌سازی حداکثری", callback_data="postsaz:op:maxcompress")])
    rows.append([InlineKeyboardButton("✅ اعمال تغییرات", callback_data="postsaz:apply")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="postsaz:cancel")])
    return InlineKeyboardMarkup(rows)


def _logo_size_kb():
    sizes = [0.2, 0.5, 1.0, 1.5, 2.0, 3.0]
    row = [InlineKeyboardButton(f"{s}x", callback_data=f"postsaz:logosize:{s}") for s in sizes]
    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("⬆️ بالا", callback_data="postsaz:logopos:v:top"),
         InlineKeyboardButton("↔️ وسط", callback_data="postsaz:logopos:v:middle"),
         InlineKeyboardButton("⬇️ پایین", callback_data="postsaz:logopos:v:bottom")],
        [InlineKeyboardButton("⬅️ چپ", callback_data="postsaz:logopos:h:left"),
         InlineKeyboardButton("↔️ وسط", callback_data="postsaz:logopos:h:center"),
         InlineKeyboardButton("➡️ راست", callback_data="postsaz:logopos:h:right")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="postsaz:menu")],
    ])


# =========================================================
#  کمکی‌های سشن
# =========================================================


def _get_session(context) -> dict:
    return context.user_data.get("postsaz")


def _new_session() -> dict:
    return {
        "active": True,
        "tmpdir": tempfile.mkdtemp(prefix="postsaz_"),
        "kind": None,
        "orig_path": None,
        "orig_size": 0,
        "caption_base": "",
        "logo_ratio": 1.0,
        "logo_pos_v": "bottom",
        "logo_pos_h": "right",
        "ops": {},
        "awaiting": None,
    }


def _cleanup_session(context):
    session = _get_session(context)
    if session and session.get("tmpdir") and os.path.isdir(session["tmpdir"]):
        shutil.rmtree(session["tmpdir"], ignore_errors=True)
    context.user_data.pop("postsaz", None)


def _detect_kind(msg):
    if msg.video:
        return "video"
    if msg.animation:
        return "gif"
    if msg.photo:
        return "photo"
    if msg.audio or msg.voice:
        return "audio"
    if msg.document:
        mime = (msg.document.mime_type or "")
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/gif"):
            return "gif"
        if mime.startswith("image/"):
            return "photo"
        return "document"
    if msg.text:
        return "text"
    return None


async def _download_original(msg, kind, tmpdir) -> str:
    tg_file = None
    if kind == "video":
        tg_file = await (msg.video or msg.document).get_file()
        ext = ".mp4"
    elif kind == "gif":
        tg_file = await (msg.animation or msg.document).get_file()
        ext = ".mp4"
    elif kind == "photo":
        tg_file = await (msg.photo[-1] if msg.photo else msg.document).get_file()
        ext = ".jpg"
    elif kind == "audio":
        tg_file = await (msg.audio or msg.voice).get_file()
        ext = ".mp3"
    else:
        tg_file = await msg.document.get_file()
        ext = os.path.splitext(msg.document.file_name or "")[1] or ".bin"
    path = os.path.join(tmpdir, f"original{ext}")
    await tg_file.download_to_drive(path)
    return path


# =========================================================
#  نقطه‌ی ورود مشترک — از هندلرهای موجود پروژه صدا زده می‌شه
# =========================================================


def is_postsaz_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """چک سبک و بدون-تغییرحالت — برای اینکه ماژول‌های دیگه (مثل
    media_recognition) بدونن الان نباید پیشنهاد موازی نشون بدن، بدون اینکه
    خودشون هم پردازش پست‌ساز رو صدا بزنن (که باعث پردازش دوبل می‌شد)."""
    session = _get_session(context)
    return bool(session and session.get("active"))


async def postsaz_intercept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر تو سشن پست‌ساز باشه، پیام رو خودمون مصرف می‌کنیم و True
    برمی‌گردونیم (هندلر صدازننده باید فوراً return کنه). اگه سشنی فعال نباشه
    False برمی‌گردونیم و هیچ کاری نمی‌کنیم — یعنی صفر تداخل با رفتار قبلی."""
    session = _get_session(context)
    if not session or not session.get("active"):
        return False

    msg = update.effective_message
    if msg is None:
        return False

    text = (msg.text or msg.caption or "").strip()
    if text in CANCEL_WORDS:
        await _do_cancel(update, context)
        return True

    try:
        if session.get("awaiting") == "caption":
            session["ops"]["caption"] = msg.text or msg.caption or ""
            session["awaiting"] = None
            await msg.reply_text("📝 متن ثبت شد.")
            await _send_menu(update, context, session)
            return True

        if session.get("awaiting") == "logo_upload":
            await _handle_logo_upload(update, context, session)
            return True

        if session.get("awaiting") in ("set_channel_id", "set_signature", "set_buttons"):
            await _handle_settings_text_input(update, context, session)
            return True

        if session.get("kind") is None:
            kind = _detect_kind(msg)
            if kind is None:
                await msg.reply_text(
                    "🎬 پست‌ساز فقط ویدیو/عکس/گیف/صدا/فایل/متن رو می‌فهمه. یکی از این‌ها رو بفرست، "
                    "یا «❌ انصراف» بزن."
                )
                return True
            if kind == "document":
                await msg.reply_text("📦 این نوع فایل توسط پست‌ساز پشتیبانی نمی‌شه (فقط ویدیو/عکس/گیف/صدا/متن).")
                return True
            session["kind"] = kind
            if kind == "text":
                session["caption_base"] = msg.text or ""
                session["orig_size"] = len((msg.text or "").encode("utf-8"))
            else:
                status = await msg.reply_text("⌛ در حال دریافت فایل...")
                try:
                    path = await _download_original(msg, kind, session["tmpdir"])
                except Exception as e:
                    log.warning(f"postsaz download failed: {e}")
                    await status.edit_text(BATMAN_FAIL_MSG)
                    return True
                session["orig_path"] = path
                session["orig_size"] = os.path.getsize(path)
                session["caption_base"] = msg.caption or ""
                try:
                    await status.delete()
                except Exception:
                    pass
            await _send_menu(update, context, session)
            return True

        # کاربر از قبل فایل/متن داره؛ یه متن جدید = میان‌بر برای آپدیت caption
        if msg.text:
            session["ops"]["caption"] = msg.text
            await msg.reply_text("📝 متن به‌روزرسانی شد.")
            await _send_menu(update, context, session)
            return True

        await msg.reply_text("🦇 قبلاً یه فایل تو این سشن ثبت شده. اول «✅ اعمال تغییرات» یا «❌ انصراف» بزن.")
        return True
    except Exception as e:
        log.exception(f"postsaz_intercept crashed: {e}")
        try:
            await msg.reply_text(BATMAN_FAIL_MSG)
        except Exception:
            pass
        return True


async def _do_cancel(update, context):
    _cleanup_session(context)
    await update.effective_message.reply_text("🦇 از پست‌ساز خارج شدی. هر وقت خواستی از «🛠 ابزارها» دوباره برگرد.")


async def _send_menu(update, context, session):
    text = _menu_text(session)
    kb = _menu_kb(session)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


# =========================================================
#  لوگو / تنظیمات — ورودی متنی حین سشن
# =========================================================


async def _handle_logo_upload(update, context, session):
    msg = update.effective_message
    photo_file = None
    if msg.photo:
        photo_file = await msg.photo[-1].get_file()
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        photo_file = await msg.document.get_file()
    if photo_file is None:
        await msg.reply_text("🖼 لطفاً یک تصویر (ترجیحاً PNG شفاف) به‌عنوان لوگو بفرست، یا «❌ انصراف» بزن.")
        return
    logo_path = os.path.join(session["tmpdir"], "logo.png")
    await photo_file.download_to_drive(logo_path)
    session["logo_local_path"] = logo_path
    user_id = update.effective_user.id
    _save_settings(user_id, logo_file_id=photo_file.file_id)
    session["awaiting"] = None
    session["ops"]["logo"] = True
    await msg.reply_text("✅ لوگو ذخیره شد و روی این فایل اعمال می‌شه.")
    await _send_menu(update, context, session)


async def _handle_settings_text_input(update, context, session):
    msg = update.effective_message
    text = (msg.text or "").strip()
    user_id = update.effective_user.id
    awaiting = session["awaiting"]
    if awaiting == "set_channel_id":
        _save_settings(user_id, channel_id=text)
        await msg.reply_text(f"🏷 آیدی کانال ذخیره شد: {text}")
    elif awaiting == "set_signature":
        _save_settings(user_id, signature=text)
        await msg.reply_text("✍️ امضای کانال ذخیره شد.")
    elif awaiting == "set_buttons":
        rows = parse_buttons_text(text)
        if not rows:
            await msg.reply_text("⚠️ فرمت درست نبود. هر خط: «متن دکمه - https://لینک»")
            return
        _save_settings(user_id, buttons_json=json.dumps(rows, ensure_ascii=False))
        await msg.reply_text(f"🔘 {len(rows)} دکمه ذخیره شد.")
    session["awaiting"] = None
    s = _get_settings(user_id)
    await msg.reply_text(_settings_text(s), reply_markup=_settings_kb(), parse_mode="Markdown")


# =========================================================
#  اجرای نهایی (اعمال تغییرات)
# =========================================================


async def _apply_and_send(update, context, session):
    user_id = update.effective_user.id
    settings = _get_settings(user_id)
    kind = session["kind"]
    ops = session["ops"]

    caption = ops.get("caption", session.get("caption_base", ""))
    caption = sanitize_text(caption, bool(settings["tag_link_cleaner"]))
    caption = apply_signature(caption, settings["signature"])
    buttons_markup = build_buttons_markup(settings["buttons_json"])

    if kind == "text":
        await update.effective_message.reply_text(caption or "🦇", reply_markup=buttons_markup)
        _cleanup_session(context)
        return

    tmpdir = session["tmpdir"]
    current = session["orig_path"]
    before_size = session["orig_size"]
    stage_report = []

    try:
        if ops.get("square"):
            out = os.path.join(tmpdir, "square" + os.path.splitext(current)[1])
            make_square(current, out, is_video=(kind in ("video", "gif")))
            if _is_valid_media(out):
                current = out

        if ops.get("logo"):
            logo_path = session.get("logo_local_path")
            if not logo_path and settings["logo_file_id"]:
                logo_path = os.path.join(tmpdir, "logo_dl.png")
                tg_file = await context.bot.get_file(settings["logo_file_id"])
                await tg_file.download_to_drive(logo_path)
                session["logo_local_path"] = logo_path
            if logo_path and os.path.exists(logo_path):
                ratio = session.get("logo_ratio", settings["logo_size"] or 1.0)
                pos_v = session.get("logo_pos_v", settings["logo_pos_v"])
                pos_h = session.get("logo_pos_h", settings["logo_pos_h"])
                if kind == "photo":
                    out = os.path.join(tmpdir, "logo_out.jpg")
                    overlay_logo_photo(current, logo_path, out, ratio, pos_h, pos_v)
                else:
                    out = os.path.join(tmpdir, "logo_out.mp4")
                    overlay_logo_video(current, logo_path, out, ratio, pos_h, pos_v)
                if _is_valid_media(out):
                    current = out

        if kind == "video" and ops.get("to_gif"):
            out = os.path.join(tmpdir, "converted.gif")
            video_to_gif(current, out)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                current = out
                kind = "gif"

        if kind == "video" and ops.get("max_compress"):
            current, stage_report = compress_video_stepped(current, tmpdir)

        if not _is_valid_media(current) and current != session["orig_path"]:
            await update.effective_message.reply_text(BATMAN_FAIL_MSG)
            _cleanup_session(context)
            return

        after_size = os.path.getsize(current)
        reduction = _reduction_percent(before_size, after_size)

        report_lines = ["🦇 پردازش با موفقیت انجام شد.", ""]
        report_lines.append(f"📦 حجم اولیه: {before_size/1024/1024:.1f}MB")
        report_lines.append(f"📦 حجم نهایی: {after_size/1024/1024:.1f}MB")
        report_lines.append(f"📉 کاهش حجم: {reduction:.1f}٪")
        if stage_report:
            report_lines.append("")
            for i, st in enumerate(stage_report, start=1):
                report_lines.append(f"🔄 مرحله {i} — کاهش حجم: {st['reduction_percent']:.0f}٪")

        with open(current, "rb") as f:
            if kind == "photo":
                await update.effective_message.reply_photo(f, caption=caption or None, reply_markup=buttons_markup)
            elif kind == "gif":
                await update.effective_message.reply_animation(f, caption=caption or None, reply_markup=buttons_markup)
            else:
                await update.effective_message.reply_video(f, caption=caption or None, reply_markup=buttons_markup, supports_streaming=True)

        post_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 ارسال مستقیم به کانال", callback_data="postsaz:sendchannel")],
        ]) if settings["channel_id"] else None
        await update.effective_message.reply_text("\n".join(report_lines), reply_markup=post_kb)

        session["last_output_path"] = current
        session["last_kind"] = kind
        session["last_caption"] = caption
    except subprocess.CalledProcessError as e:
        log.warning(f"postsaz ffmpeg failed: {e}")
        await update.effective_message.reply_text(BATMAN_FAIL_MSG)
        _cleanup_session(context)
        return
    except Exception as e:
        log.exception(f"postsaz apply failed: {e}")
        await update.effective_message.reply_text(BATMAN_FAIL_MSG)
        _cleanup_session(context)
        return

    # سشن رو برای امکان «ارسال مستقیم به کانال» نگه می‌داریم، ولی از حالت
    # «منتظر فایل جدید» خارجش می‌کنیم تا پیام بعدی دوباره پردازش نشه.
    session["active"] = False


async def _send_to_channel(update, context, session):
    user_id = update.effective_user.id
    settings = _get_settings(user_id)
    channel_id = settings["channel_id"]
    query = update.callback_query
    if not channel_id:
        await query.answer("🏷 اول از تنظیمات، آیدی کانال رو ذخیره کن.", show_alert=True)
        return
    path = session.get("last_output_path")
    kind = session.get("last_kind")
    caption = session.get("last_caption") or ""
    if not path or not os.path.exists(path):
        await query.answer("🦇 چیزی برای ارسال پیدا نشد.", show_alert=True)
        return

    try:
        member = await context.bot.get_chat_member(channel_id, context.bot.id)
        if member.status not in ("administrator", "creator"):
            await query.answer("🦇 ربات تو اون کانال ادمین نیست.", show_alert=True)
            return
    except (BadRequest, Forbidden) as e:
        await query.answer(f"🦇 دسترسی به کانال نیست: {str(e)[:100]}", show_alert=True)
        return
    except Exception as e:
        log.info(f"channel membership check failed: {e}")
        await query.answer("🦇 نتونستم دسترسی کانال رو چک کنم.", show_alert=True)
        return

    buttons_markup = build_buttons_markup(settings["buttons_json"])
    try:
        with open(path, "rb") as f:
            if kind == "photo":
                await context.bot.send_photo(channel_id, f, caption=caption or None, reply_markup=buttons_markup)
            elif kind == "gif":
                await context.bot.send_animation(channel_id, f, caption=caption or None, reply_markup=buttons_markup)
            else:
                await context.bot.send_video(channel_id, f, caption=caption or None, reply_markup=buttons_markup, supports_streaming=True)
        await query.answer("📢 به کانال ارسال شد.")
    except (BadRequest, Forbidden) as e:
        await query.answer(f"🦇 ارسال به کانال شکست خورد: {str(e)[:150]}", show_alert=True)
    except Exception as e:
        log.exception(f"send to channel failed: {e}")
        await query.answer("🦇 ارسال به کانال شکست خورد.", show_alert=True)
    _cleanup_session(context)


# =========================================================
#  ثبت هندلرها
# =========================================================


def register_post_saz(app, deps: dict):
    global _DB_PATH
    _DB_PATH = deps.get("db_path", _DB_PATH)
    _init_db()

    async def open_tool_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(POSTSAZ_INTRO_TEXT, reply_markup=postsaz_intro_kb(), parse_mode="Markdown")

    async def postsaz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        user_id = update.effective_user.id

        if data == "postsaz:begin":
            context.user_data["postsaz"] = _new_session()
            await query.answer()
            await query.message.reply_text("🎬 حالا فایل یا متنت رو بفرست.")
            return

        if data == "postsaz:settings":
            await query.answer()
            s = _get_settings(user_id)
            await query.message.reply_text(_settings_text(s), reply_markup=_settings_kb(), parse_mode="Markdown")
            return

        if data == "postsaz:cancel":
            await query.answer()
            _cleanup_session(context)
            await query.message.reply_text("🦇 از پست‌ساز خارج شدی.")
            return

        if data == "postsaz:menu":
            session = _get_session(context)
            await query.answer()
            if session and session.get("kind"):
                await query.message.reply_text(_menu_text(session), reply_markup=_menu_kb(session), parse_mode="Markdown")
            return

        session = _get_session(context)
        if data.startswith("postsaz:set:"):
            key = data.split(":", 2)[2]
            await query.answer()
            if key == "tag_toggle":
                s = _get_settings(user_id)
                _save_settings(user_id, tag_link_cleaner=0 if s["tag_link_cleaner"] else 1)
                s = _get_settings(user_id)
                await query.message.reply_text(_settings_text(s), reply_markup=_settings_kb(), parse_mode="Markdown")
                return
            if key == "logo":
                if not session:
                    context.user_data["postsaz"] = session = _new_session()
                    session["kind"] = None
                session["awaiting"] = "logo_upload"
                await query.message.reply_text("🖼 لوگوت رو (ترجیحاً PNG شفاف) بفرست.")
                return
            prompts = {
                "channel_id": "🏷 آیدی/یوزرنیم کانال رو بفرست (مثلاً @mychannel یا -100...).",
                "signature": "✍️ متن امضای کانال رو بفرست.",
                "buttons": "🔘 هر خط: «متن دکمه - https://لینک». چند خط = چند دکمه.",
            }
            awaiting_key = {"channel_id": "set_channel_id", "signature": "set_signature", "buttons": "set_buttons"}[key]
            if not session:
                context.user_data["postsaz"] = session = _new_session()
                session["kind"] = None
            session["awaiting"] = awaiting_key
            await query.message.reply_text(prompts[key])
            return

        if not session:
            await query.answer()
            return

        if data == "postsaz:op:caption":
            session["awaiting"] = "caption"
            await query.answer()
            await query.message.reply_text("📝 لطفاً متن دلخواه خود را وارد کنید.")
            return

        if data == "postsaz:op:logo":
            await query.answer()
            s = _get_settings(user_id)
            if s["logo_file_id"] and not session.get("logo_local_path"):
                session["ops"]["logo"] = True
                await query.message.reply_text("🎨 لوگوی ذخیره‌شده انتخاب شد.")
                await _send_menu(update, context, session)
            else:
                session["awaiting"] = "logo_upload"
                await query.message.reply_text("🖼 لوگوت رو (ترجیحاً PNG شفاف) بفرست.")
            return

        if data == "postsaz:op:logosize":
            await query.answer()
            await query.message.reply_text("📐 اندازه و موقعیت لوگو رو انتخاب کن:", reply_markup=_logo_size_kb())
            return

        if data.startswith("postsaz:logosize:"):
            session["logo_ratio"] = float(data.rsplit(":", 1)[1])
            await query.answer(f"اندازه: {session['logo_ratio']}x")
            return

        if data.startswith("postsaz:logopos:"):
            _, _, axis, value = data.split(":")
            if axis == "v":
                session["logo_pos_v"] = value
            else:
                session["logo_pos_h"] = value
            await query.answer("ثبت شد.")
            return

        if data == "postsaz:op:square":
            session["ops"]["square"] = not session["ops"].get("square", False)
            await query.answer("فعال شد ✅" if session["ops"]["square"] else "غیرفعال شد")
            await _send_menu(update, context, session)
            return

        if data == "postsaz:op:togif":
            session["ops"]["to_gif"] = not session["ops"].get("to_gif", False)
            await query.answer("فعال شد ✅" if session["ops"]["to_gif"] else "غیرفعال شد")
            await _send_menu(update, context, session)
            return

        if data == "postsaz:op:maxcompress":
            session["ops"]["max_compress"] = not session["ops"].get("max_compress", False)
            await query.answer("فعال شد ✅" if session["ops"]["max_compress"] else "غیرفعال شد")
            await _send_menu(update, context, session)
            return

        if data == "postsaz:apply":
            if session["kind"] != "text" and (session["kind"] in ("video", "gif") ) and not _ffmpeg_available() and any(session["ops"].get(k) for k in ("square", "to_gif", "max_compress", "logo")):
                await query.answer("⚠️ FFmpeg رو این هاست نصب نیست.", show_alert=True)
                return
            await query.answer("⌛ در حال پردازش...")
            await query.message.reply_text("⌛ لطفاً منتظر بمانید...\n\n🦇 گاتهام در حال پردازش فایل شماست.")
            await _apply_and_send(update, context, session)
            return

        if data == "postsaz:sendchannel":
            await _send_to_channel(update, context, session)
            return

        await query.answer()

    app.add_handler(CallbackQueryHandler(open_tool_callback, pattern=r"^postsaz:open$"), group=21)
    app.add_handler(CallbackQueryHandler(postsaz_callback, pattern=r"^postsaz:"), group=21)

    # ویدیو/صدا/فایل هیچ‌جای دیگه‌ی پروژه هندل نمی‌شن (فقط عکس/گیف/متن هندلر
    # عمومی دارن)، پس اضافه‌کردن این هندلر خطر تداخل نداره؛ وقتی سشن فعال
    # نباشه، فقط بی‌صدا هیچ کاری نمی‌کنه.
    async def _bare_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await postsaz_intercept(update, context)

    app.add_handler(
        MessageHandler(
            (filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL) & ~filters.COMMAND,
            _bare_media_handler,
        ),
        group=21,
    )

    log.info("✅ post_saz: ابزار پست‌ساز گاتهام ثبت شد")
