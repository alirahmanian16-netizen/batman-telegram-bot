# -*- coding: utf-8 -*-
"""
gotham_ai/client.py
=====================
کلاینت async برای صحبت با یه instance از FreeLLMAPI (endpoint سازگار با
OpenAI). هیچ کلیدی داخل کد نیست — همه از config.py (که خودش از env می‌خونه)
میاد.

این فایل مسئول:
- ارسال درخواست HTTP واقعی (chat / models / images / audio speech / audio
  transcription / video در صورت وجود endpoint)
- Automatic Failover بین چند مدل (روی خطاهای 429/5xx/timeout/connection)
- ثبت آمار (موفقیت/خطا/فیل‌اوور) برای صفحه‌ی «📊 وضعیت هوش مصنوعی»
- هیچ‌وقت API Key رو لاگ یا نمایش نمی‌ده
"""

import time
import json
import logging

import asyncio

import httpx

from . import config
from . import store

log = logging.getLogger(__name__)


class GothamAIError(Exception):
    """خطای قابل‌نمایش به کاربر (پیام فارسیِ آماده)."""


def _headers():
    h = {"Content-Type": "application/json"}
    if config.FREELLMAPI_API_KEY:
        h["Authorization"] = f"Bearer {config.FREELLMAPI_API_KEY}"
    return h


def _url(path: str) -> str:
    return f"{config.FREELLMAPI_BASE_URL}/{path.lstrip('/')}"


def _friendly_error(status: int | None, exc: Exception | None) -> str:
    if status == 401 or status == 403:
        return "🔐 دسترسی رد شد — کلید FreeLLMAPI اشتباهه یا منقضی شده."
    if status == 404:
        return "❓ این قابلیت/مدل روی instance فعلیِ FreeLLMAPI پیدا نشد."
    if status == 429:
        return "⏳ محدودیت نرخ (Rate Limit) — یه‌کم بعد دوباره امتحان کن."
    if status and 500 <= status < 600:
        return "🛠️ سرویس FreeLLMAPI موقتاً در دسترس نیست."
    if isinstance(exc, httpx.TimeoutException):
        return "⌛ درخواست بیش از حد طول کشید (timeout)."
    if isinstance(exc, httpx.ConnectError):
        return "🔌 اتصال به FreeLLMAPI برقرار نشد — آدرس/شبکه رو چک کن."
    return "🦇 یه خطای غیرمنتظره پیش اومد."


async def _request(method: str, path: str, *, json_body=None, timeout=None):
    """یه درخواست خام؛ (data_or_None, error_text_or_None, status_or_None) برمی‌گردونه."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout or config.FREELLMAPI_TIMEOUT) as client:
            resp = await client.request(method, _url(path), headers=_headers(), json=json_body)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            await asyncio.to_thread(store.record_request, False, latency_ms)
            return None, _friendly_error(resp.status_code, None), resp.status_code
        await asyncio.to_thread(store.record_request, True, latency_ms)
        try:
            return resp.json(), None, resp.status_code
        except Exception:
            return resp.content, None, resp.status_code
    except httpx.TimeoutException as e:
        await asyncio.to_thread(store.record_request, False, None)
        return None, _friendly_error(None, e), None
    except Exception as e:
        await asyncio.to_thread(store.record_request, False, None)
        log.warning(f"gotham_ai request error ({path}): {e}")
        return None, _friendly_error(None, e), None


async def list_models():
    """لیست مدل‌های واقعیِ موجود رو از /v1/models می‌گیره. هیچ‌چیزی hard-code نمی‌شه."""
    data, err, status = await _request("GET", "/models")
    if err:
        return None, err
    models = data.get("data") if isinstance(data, dict) else None
    if not models:
        return [], None
    return models, None


def _trim_history(history):
    """fail-open prompt compression: تاریخچه رو کوتاه می‌کنه، ولی هیچ‌وقت کل درخواست رو نمی‌شکنه."""
    try:
        trimmed = history[-(config.MAX_HISTORY_TURNS * 2):]
        total = sum(len(m.get("content", "")) for m in trimmed)
        while total > config.MAX_HISTORY_CHARS and len(trimmed) > 2:
            removed = trimmed.pop(0)
            total -= len(removed.get("content", ""))
        return trimmed
    except Exception:
        # fail-open: اگه compression خودش خطا داد، همون تاریخچه‌ی خام رو بده
        return history


async def chat_completion(messages, model_chain=None, *, max_tokens=800, temperature=0.7,
                           tools=None, response_format=None):
    """
    Chat Completions با Automatic Failover روی چند مدل.
    برمی‌گردونه: (reply_text_or_None, used_model_or_None, error_text_or_None)
    """
    if not config.is_configured():
        return None, None, config.missing_config_message()

    # 🗜️ Prompt Compression (fail-open): سیستم/آخرین پیام دست‌نخورده می‌مونه،
    # فقط تاریخچه‌ی وسط در صورت بلند بودن کوتاه می‌شه.
    try:
        if len(messages) > 3:
            head = messages[:1] if messages[0].get("role") == "system" else []
            tail = messages[len(head):]
            last = tail[-1:] if tail else []
            middle = tail[:-1] if len(tail) > 1 else []
            messages = head + _trim_history(middle) + last
    except Exception:
        pass  # fail-open: compression هیچ‌وقت نباید جلوی درخواست اصلی رو بگیره

    chain = [m for m in (model_chain or config.CHAT_FAILOVER_CHAIN) if m]
    if not chain:
        chain = ["auto"]

    last_err = None
    for i, model in enumerate(dict.fromkeys(chain)):  # حذف تکراری، حفظ ترتیب
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format

        data, err, status = await _request("POST", "/chat/completions", json_body=body)

        if err:
            last_err = err
            if status in config.RETRYABLE_STATUS or status is None:
                await asyncio.to_thread(store.record_request, False, None, True)
                log.info(f"gotham_ai: model '{model}' failed ({status}), trying next in chain")
                continue
            # خطای غیرقابل‌ری‌ترای (مثلاً 401/404) — همون‌جا برگردون
            return None, model, err

        try:
            choice = data["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")
            return {"content": content, "tool_calls": tool_calls}, model, None
        except (KeyError, IndexError, TypeError):
            last_err = "🦇 پاسخ مدل قابل‌فهم نبود."
            continue

    return None, None, last_err or "🦇 هیچ‌کدوم از مدل‌ها/providerها الان جواب ندادن."


async def vision_completion(image_bytes: bytes, mime: str, prompt: str, model=None):
    import base64
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    chain = [model or config.FREELLMAPI_VISION_MODEL] + config.CHAT_FAILOVER_CHAIN
    result, used_model, err = await chat_completion(messages, model_chain=chain, max_tokens=700)
    if err:
        return None, err
    return result["content"], None


async def image_generate(prompt: str, model=None, size="1024x1024"):
    if not config.is_configured():
        return None, config.missing_config_message()
    body = {"prompt": prompt, "n": 1, "size": size}
    if model or config.FREELLMAPI_IMAGE_MODEL:
        body["model"] = model or config.FREELLMAPI_IMAGE_MODEL
    data, err, status = await _request("POST", "/images/generations", json_body=body, timeout=180)
    if err:
        if status == 404:
            return None, "🖼️ در حال حاضر هیچ provider فعالی برای تولید تصویر در دسترس نیست."
        return None, err
    try:
        item = data["data"][0]
        return (item.get("url") or item.get("b64_json")), None
    except Exception:
        return None, "🦇 پاسخ تولید تصویر قابل‌فهم نبود."


async def video_generate(prompt: str, model=None):
    """اگه FreeLLMAPI/instance شما endpoint ویدیو داشته باشه امتحان می‌شه؛ وگرنه
    وضعیت «در دسترس نیست» به‌درستی برگردونده می‌شه (fake نمی‌کنیم)."""
    if not config.is_configured():
        return None, config.missing_config_message()
    body = {"prompt": prompt}
    if model or config.FREELLMAPI_VIDEO_MODEL:
        body["model"] = model or config.FREELLMAPI_VIDEO_MODEL
    data, err, status = await _request("POST", "/videos/generations", json_body=body, timeout=300)
    if err:
        if status == 404:
            return None, "🎬 هیچ provider فعالی برای تولید ویدیو روی instance فعلی در دسترس نیست."
        return None, err
    try:
        item = data["data"][0]
        return (item.get("url") or item.get("b64_json")), None
    except Exception:
        return None, "🦇 پاسخ تولید ویدیو قابل‌فهم نبود."


async def audio_speech(text: str, voice=None, model=None):
    if not config.is_configured():
        return None, config.missing_config_message()
    body = {
        "input": text[:4000],
        "voice": voice or config.FREELLMAPI_TTS_VOICE,
        "model": model or config.FREELLMAPI_TTS_MODEL or "auto",
        "response_format": "mp3",
    }
    data, err, status = await _request("POST", "/audio/speech", json_body=body, timeout=120)
    if err:
        if status == 404:
            return None, "🔊 provider فعالی برای تبدیل متن‌به‌صدا در دسترس نیست."
        return None, err
    if isinstance(data, (bytes, bytearray)):
        return bytes(data), None
    return None, "🦇 پاسخ صوتی قابل‌فهم نبود."


async def audio_transcription(file_bytes: bytes, filename: str, model=None):
    if not config.is_configured():
        return None, config.missing_config_message()
    try:
        async with httpx.AsyncClient(timeout=config.FREELLMAPI_TIMEOUT) as client:
            files = {"file": (filename, file_bytes)}
            data_form = {"model": model or config.FREELLMAPI_STT_MODEL or "auto"}
            headers = _headers()
            headers.pop("Content-Type", None)  # multipart خودش boundary می‌سازه
            resp = await client.post(_url("/audio/transcriptions"), headers=headers,
                                      data=data_form, files=files)
        if resp.status_code >= 400:
            await asyncio.to_thread(store.record_request, False, None)
            if resp.status_code == 404:
                return None, "🎤 provider فعالی برای تبدیل صدا‌به‌متن در دسترس نیست."
            return None, _friendly_error(resp.status_code, None)
        await asyncio.to_thread(store.record_request, True, None)
        data = resp.json()
        return data.get("text", ""), None
    except Exception as e:
        await asyncio.to_thread(store.record_request, False, None)
        return None, _friendly_error(None, e)
