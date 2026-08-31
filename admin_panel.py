# -*- coding: utf-8 -*-
"""
admin/panel.py
================
پنل مدیریت گروه دکمه‌ای — الهام‌گرفته از تجربه‌ی کاربریِ ربات‌های مدیریتی
معروف مثل Alita (github.com/divkix/Alita_Robot). چون Alita با فریم‌ورک
Pyrogram/aiogram نوشته شده و این ربات با python-telegram-bot، کد آماده‌شون
مستقیم قابل‌کپی نبود؛ به‌جاش همون تجربه‌ی کاربریِ «به‌جای دستور، دکمه» با
همین فریم‌ورک بازسازی شده.

طرز کار:
  ادمین روی پیام یه عضو تو گروه ریپلای می‌کنه و می‌نویسه «مدیریت» —
  یه منوی دکمه‌ای باز می‌شه: بن / کیک / میوت / آنمیوت / اخطار / حذف اخطار /
  ویژه / معاف / حذف پیام. همه‌چیز با یه لمس، بدون تایپ /ban یا /mute.

این ماژول مستقیم به دیتابیس یا هلپرهای bot.py وصل نیست (تا import چرخه‌ای
پیش نیاد). به‌جاش register_admin_panel(app, deps) یه دیکشنری از توابع/مقادیر
مورد نیاز می‌گیره که bot.py موقع راه‌اندازی پاسش می‌ده:

    deps = {
        "is_group_admin": ...,          # async(update, context) -> bool
        "list_add": ...,                # (chat_id, list_type, key, value)
        "list_remove": ...,             # (chat_id, list_type, key)
        "list_get_one_added_at": ...,   # (chat_id, list_type, key) -> (val, ts)
        "log_mod_action": ...,          # (chat_id, admin_name, action, target_name)
        "warn_expiry_seconds": ...,     # int
    }
    register_admin_panel(app, deps)
"""

import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters

TRIGGER_RE = filters.Regex(
    r"(?i)^\s*(مدیریت|پنل مدیریت|منو مدیریت|منوی مدیریت)\s*$"
)
# نکته‌ی مهم (باگ رفع‌شده): قبلاً فیلتر ثبت‌شده «TRIGGER_RE & filters.REPLY» بود،
# یعنی اگه کاربر بدون ریپلای «مدیریت» رو می‌نوشت، این هندلر اصلاً اجرا نمی‌شد
# (چون شرط ریپلای تو خودِ فیلتر هم بود) و چون هیچ هندلر دیگه‌ای اون متن رو
# نمی‌شناخت، ربات کاملاً ساکت می‌موند — کاربر فکر می‌کرد «پنل مدیریت باز نمی‌شه».
# پیام راهنمای «باید روی پیام یه عضو ریپلای کنی» که پایین تو admin_panel_menu
# نوشته شده بود دقیقاً برای همین حالت بود ولی هیچ‌وقت اجرا نمی‌شد (کد مرده).
# الان فیلتر فقط رو متن چک می‌کنه و خودِ تابع تشخیص می‌ده ریپلای هست یا نه.

MUTE_DURATIONS = [("۱۰ دقیقه", 10), ("۱ ساعت", 60), ("۱ روز", 1440), ("دائم 🔒", 0)]

_DEPS_KEY = "admin_panel_deps"


def _panel_keyboard(target_id, msg_id):
    rows = [
        [InlineKeyboardButton("🔨 بن", callback_data=f"adm:ban:{target_id}:{msg_id}"),
         InlineKeyboardButton("👢 کیک", callback_data=f"adm:kick:{target_id}:{msg_id}")],
        [InlineKeyboardButton("🔇 میوت", callback_data=f"adm:mutemenu:{target_id}:{msg_id}"),
         InlineKeyboardButton("🔊 آنمیوت", callback_data=f"adm:unmute:{target_id}:{msg_id}")],
        [InlineKeyboardButton("⚠️ اخطار", callback_data=f"adm:warn:{target_id}:{msg_id}"),
         InlineKeyboardButton("✅ حذف اخطار", callback_data=f"adm:unwarn:{target_id}:{msg_id}")],
        [InlineKeyboardButton("⭐ ویژه", callback_data=f"adm:special:{target_id}:{msg_id}"),
         InlineKeyboardButton("🛡 معاف", callback_data=f"adm:exempt:{target_id}:{msg_id}")],
        [InlineKeyboardButton("🗑 حذف پیامش", callback_data=f"adm:delete:{target_id}:{msg_id}")],
        [InlineKeyboardButton("❌ بستن", callback_data="adm:close")],
    ]
    return InlineKeyboardMarkup(rows)


def _mute_keyboard(target_id, msg_id):
    rows = [
        [InlineKeyboardButton(label, callback_data=f"adm:mute:{target_id}:{minutes}:{msg_id}")]
        for label, minutes in MUTE_DURATIONS
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"adm:back:{target_id}:{msg_id}")])
    return InlineKeyboardMarkup(rows)


async def admin_panel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deps = context.bot_data.get(_DEPS_KEY)
    if not deps:
        return
    if not await deps["is_group_admin"](update, context):
        await update.effective_message.reply_text("⛔️ این کلمه فقط برای ادمین‌هاست.")
        return
    target_msg = update.message.reply_to_message if update.message else None
    if not target_msg or not target_msg.from_user:
        await update.effective_message.reply_text(
            "⚠️ باید «مدیریت» رو روی پیام یه عضو ریپلای کنی تا منوش باز بشه."
        )
        return
    target = target_msg.from_user
    if target.is_bot:
        await update.effective_message.reply_text("🤖 نمی‌شه رو ربات‌ها اعمال مدیریتی انجام داد.")
        return
    await update.effective_message.reply_text(
        f"🛡 مدیریت {target.first_name}\nیه عملیات رو انتخاب کن:",
        reply_markup=_panel_keyboard(target.id, target_msg.message_id),
    )


async def admin_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    deps = context.bot_data.get(_DEPS_KEY)
    if not deps:
        await query.answer()
        return

    if not await deps["is_group_admin"](update, context):
        await query.answer("⛔️ این پنل فقط برای ادمین‌هاست.", show_alert=True)
        return

    chat_id = update.effective_chat.id
    admin_user = update.effective_user
    parts = query.data.split(":")
    action = parts[1]

    if action == "close":
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text("بسته شد.")
        await query.answer()
        return

    target_id = int(parts[2])
    # msg_id همیشه بعد از target_id میاد، به‌جز اکشن «mute» که یه پارامتر
    # اضافه (مدت‌زمان) قبلش داره: adm:mute:{target_id}:{minutes}:{msg_id}
    if action == "mute":
        msg_id = int(parts[4]) if len(parts) > 4 else 0
    else:
        msg_id = int(parts[3]) if len(parts) > 3 else 0
    try:
        target_member = await context.bot.get_chat_member(chat_id, target_id)
        target_user = target_member.user
    except Exception:
        await query.answer("⚠️ این عضو دیگه پیدا نشد (شاید گروه رو ترک کرده).", show_alert=True)
        return

    if action == "back":
        await query.edit_message_text(
            f"🛡 مدیریت {target_user.first_name}\nیه عملیات رو انتخاب کن:",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "mutemenu":
        await query.edit_message_text(
            f"🔇 مدت سکوت {target_user.first_name} رو انتخاب کن:",
            reply_markup=_mute_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id, target_id)
        except Exception as e:
            await query.answer(f"⚠️ نشد: {e}", show_alert=True)
            return
        deps["list_add"](chat_id, "banned", target_id, target_user.username or target_user.first_name or "")
        deps["list_remove"](chat_id, "muted", target_id)
        deps["log_mod_action"](chat_id, admin_user.first_name, "بن", target_user.first_name)
        await query.edit_message_text(f"🔨 {target_user.first_name} فرستاده شد به آرکهام، برای همیشه.")
        await query.answer()
        return

    if action == "kick":
        try:
            await context.bot.ban_chat_member(chat_id, target_id)
            await context.bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        except Exception as e:
            await query.answer(f"⚠️ نشد: {e}", show_alert=True)
            return
        deps["log_mod_action"](chat_id, admin_user.first_name, "کیک", target_user.first_name)
        await query.edit_message_text(f"👢 {target_user.first_name} از گاتهام بیرون انداخته شد (موقت).")
        await query.answer()
        return

    if action == "mute":
        minutes = int(parts[3])
        until = None if minutes == 0 else int(time.time() + minutes * 60)
        try:
            await context.bot.restrict_chat_member(
                chat_id, target_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
        except Exception as e:
            await query.answer(f"⚠️ نشد: {e}", show_alert=True)
            return
        deps["list_add"](chat_id, "muted", target_id, target_user.username or target_user.first_name or "")
        label = "دائم" if minutes == 0 else f"{minutes} دقیقه"
        deps["log_mod_action"](chat_id, admin_user.first_name, f"میوت {label}", target_user.first_name)
        await query.edit_message_text(
            f"🔇 {target_user.first_name} به مدت {label} تو سلول سکوت آرکهام موند.",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "unmute":
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
            await query.answer(f"⚠️ نشد: {e}", show_alert=True)
            return
        deps["list_remove"](chat_id, "muted", target_id)
        deps["log_mod_action"](chat_id, admin_user.first_name, "آنمیوت", target_user.first_name)
        await query.edit_message_text(
            f"🔊 {target_user.first_name} از سلول سکوت آزاد شد.",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "warn":
        current, added_at = deps["list_get_one_added_at"](chat_id, "warn", target_id)
        if current and added_at and (time.time() - added_at) > deps["warn_expiry_seconds"]:
            current = None
        count = int(current) + 1 if current else 1
        if count >= 3:
            try:
                await context.bot.ban_chat_member(chat_id, target_id)
                deps["list_add"](chat_id, "banned", target_id, target_user.username or target_user.first_name or "")
                deps["list_remove"](chat_id, "warn", target_id)
                deps["log_mod_action"](chat_id, admin_user.first_name, "بن (۳ اخطار)", target_user.first_name)
                await query.edit_message_text(f"🚨 {target_user.first_name} به ۳ اخطار رسید و بن شد.")
            except Exception as e:
                await query.answer(f"⚠️ نشد بن کنم: {e}", show_alert=True)
                return
            await query.answer()
            return
        deps["list_add"](chat_id, "warn", target_id, count)
        deps["log_mod_action"](chat_id, admin_user.first_name, f"اخطار ({count}/۳)", target_user.first_name)
        await query.edit_message_text(
            f"⚠️ {target_user.first_name} اخطار گرفت ({count}/۳).",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "unwarn":
        deps["list_remove"](chat_id, "warn", target_id)
        deps["log_mod_action"](chat_id, admin_user.first_name, "پاک‌کردن اخطار", target_user.first_name)
        await query.edit_message_text(
            f"✅ اخطارهای {target_user.first_name} پاک شد.",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "special":
        deps["list_add"](chat_id, "special", target_id, target_user.username or target_user.first_name or "")
        deps["log_mod_action"](chat_id, admin_user.first_name, "عضو ویژه", target_user.first_name)
        await query.edit_message_text(
            f"⭐ {target_user.first_name} عضو ویژه شد.",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "exempt":
        deps["list_add"](chat_id, "exempt", target_id, target_user.username or target_user.first_name or "")
        deps["log_mod_action"](chat_id, admin_user.first_name, "معاف", target_user.first_name)
        await query.edit_message_text(
            f"🛡 {target_user.first_name} معاف شد.",
            reply_markup=_panel_keyboard(target_id, msg_id),
        )
        await query.answer()
        return

    if action == "delete":
        # msg_id از بالا (خط مشترک همه‌ی اکشن‌ها) گرفته شده، همیشه صحیحه چون
        # الان تو هر دکمه‌ی پنل propagate می‌شه (باگ رفع‌شده — قبلاً بعد از
        # هر اکشن دیگه صفر می‌شد و این دکمه ادعای دروغین «حذف شد» می‌داد).
        if not msg_id:
            await query.answer(
                "⚠️ شناسه‌ی پیام هدف در دسترس نیست؛ دوباره روی همون پیام ریپلای کن و «مدیریت» بزن.",
                show_alert=True,
            )
            return
        try:
            await context.bot.delete_message(chat_id, msg_id)
            await query.edit_message_text(f"🗑 پیامِ {target_user.first_name} حذف شد.")
        except Exception as e:
            await query.answer(f"⚠️ نشد حذفش کنم (شاید قبلاً حذف شده): {e}", show_alert=True)
            return
        await query.answer()
        return

    await query.answer()


def register_admin_panel(app, deps: dict):
    app.bot_data[_DEPS_KEY] = deps
    app.add_handler(MessageHandler(TRIGGER_RE, admin_panel_menu), group=1)
    app.add_handler(CallbackQueryHandler(admin_panel_callback, pattern=r"^adm:"), group=1)
