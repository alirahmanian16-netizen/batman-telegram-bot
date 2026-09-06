# -*- coding: utf-8 -*-
"""
config_tester.py
=================
🦇 کانفیگ گاتهام — تست واقعیِ در دسترس بودن سرور هر کانفیگ.

⚠️ صادقانه: چون این ربات یه Client واقعیِ V2Ray/Xray (با Handshake کامل
پروتکل، TLS/Reality/WS و ...) داخل خودش نداره و نمی‌تونیم چنین چیزی رو
داخل یه ربات تلگرام روی Railway بدون هیچ باینری/سرویس اضافه پیاده کنیم،
تنها تستِ واقعی و صادقانه‌ای که می‌تونیم انجام بدیم یه اتصال TCP واقعی به
host:port خودِ سرور کانفیگه و اندازه‌گیری زمان واقعی همون Handshake اتصال.

این یعنی:
    - این HTTP ping جعلی نیست و Latency ساختگی تولید نمی‌کنه — واقعاً به
      سرور وصل می‌شه و زمان واقعی رو اندازه می‌گیره.
    - ولی معادل یه تست کامل پروتکل V2Ray نیست، برای همین همه‌جا با برچسب
      "Verified (TCP)" نشون داده می‌شه، نه یه "تست کامل پروتکل".
    - Hysteria2 روی UDP/QUIC کار می‌کنه، نه TCP؛ چون نمی‌تونیم واقعاً
      تستش کنیم، طبق قانونِ «اگه نمی‌تونی تست کنی، Verified اعلامش نکن»،
      این پروتکل اصلاً وارد فرآیند تست نمی‌شه و در نتیجه هیچ‌وقت به‌عنوان
      Verified نمایش داده نمی‌شه.
"""

import asyncio
import time
import logging

log = logging.getLogger(__name__)

# پروتکل‌هایی که واقعاً می‌تونیم با یه اتصال TCP تستشون کنیم.
# Hysteria2 عمداً اینجا نیست (UDP/QUIC است، تست TCP روش گمراه‌کننده می‌شه).
TCP_TESTABLE_PROTOCOLS = {"VLESS", "VMess", "Trojan", "Shadowsocks"}

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_CONCURRENCY = 15
DEFAULT_TEST_LIMIT = 120  # سقف تعداد کانفیگ‌هایی که در هر Refresh واقعاً تست می‌شن


async def tcp_latency_ms(host: str, port: int, timeout: float = DEFAULT_TIMEOUT_SECONDS):
    """یه اتصال TCP واقعی به host:port می‌زنه و زمان واقعیِ برقراری اتصال رو
    به میلی‌ثانیه برمی‌گردونه. اگه اتصال ناموفق/Timeout بود، None برمی‌گردونه
    (یعنی این کانفیگ Verified اعلام نمی‌شه — هیچ عدد ساختگی جایگزینش نمی‌شه)."""
    start = time.monotonic()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        return round(elapsed_ms, 1)
    except Exception:
        return None
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def test_configs(
    configs: list,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    limit: int = DEFAULT_TEST_LIMIT,
):
    """کانفیگ‌های قابل‌تست (TCP) رو موازی تست می‌کنه.

    برمی‌گردونه: (verified_list, tested_count, untested_count)
        verified_list  -> فقط کانفیگ‌هایی که واقعاً وصل شدن، هرکدوم با latency_ms
                           واقعی، مرتب‌شده از کم‌تاخیرترین به بیشترین
        tested_count   -> چندتا کانفیگ واقعاً تست شدن (سقف‌خورده با limit)
        untested_count -> چندتا کانفیگ اصلاً وارد تست نشدن (مثل Hysteria2،
                           یا مازاد روی سقف limit)
    """
    testable_all = [c for c in configs if c.get("protocol") in TCP_TESTABLE_PROTOCOLS]
    testable = testable_all[:limit]
    untested_count = len(configs) - len(testable)

    if not testable:
        return [], 0, untested_count

    semaphore = asyncio.Semaphore(concurrency)
    verified = []

    async def _run(cfg):
        async with semaphore:
            latency = await tcp_latency_ms(cfg["host"], cfg["port"], timeout=timeout)
            if latency is not None:
                cfg_copy = dict(cfg)
                cfg_copy["latency_ms"] = latency
                verified.append(cfg_copy)

    await asyncio.gather(*[_run(c) for c in testable])
    verified.sort(key=lambda c: c["latency_ms"])
    return verified, len(testable), untested_count
