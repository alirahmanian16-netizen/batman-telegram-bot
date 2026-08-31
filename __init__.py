# -*- coding: utf-8 -*-
"""
gotham_ai — «✨ امکانات جدید گاتهام»
=====================================
اتصال کامل ربات به FreeLLMAPI (https://github.com/tashfeenahmed/freellmapi):
Chat، Vision، تحلیل فایل، تولید تصویر/ویدیو، TTS/STT، Tool Calling، Structured
Output، Smart Routing + Automatic Failover، Cache، و مدیریت session.

استفاده در bot.py:

    from gotham_ai import (
        register_gotham_ai,
        gotham_ai_intercept_text,
        gotham_ai_intercept_photo,
        build_ai_menu_keyboard,
        AI_MENU_TEXT,
    )

    register_gotham_ai(app, {"db_path": DB_PATH})

    # تو handle_message، بعد از postsaz_intercept:
    if await gotham_ai_intercept_text(update, context):
        return

    # تو handle_photo_sticker، بعد از postsaz_intercept:
    if update.message.photo and await gotham_ai_intercept_photo(update, context):
        return
"""

from .handlers import (
    register_gotham_ai,
    gotham_ai_intercept_text,
    gotham_ai_intercept_photo,
    build_ai_menu_keyboard,
    AI_MENU_TEXT,
)
from . import config

__all__ = [
    "register_gotham_ai",
    "gotham_ai_intercept_text",
    "gotham_ai_intercept_photo",
    "build_ai_menu_keyboard",
    "AI_MENU_TEXT",
    "config",
]
