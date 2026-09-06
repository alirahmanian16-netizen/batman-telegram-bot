# -*- coding: utf-8 -*-
"""
config_parser.py
=================
🦇 کانفیگ گاتهام — Decode و Parse واقعیِ کانفیگ‌های V2Ray/Xray.

فقط کانفیگ‌هایی که واقعاً Parse می‌شن (host/port معتبر دارن) برگردونده
می‌شن؛ هیچ‌چیز حدسی یا جعلی ساخته نمی‌شه. اگه یه خط قابل Parse نبود، ساده
نادیده گرفته می‌شه.

پروتکل‌های پشتیبانی‌شده: VLESS, VMess, Trojan, Shadowsocks, Hysteria2
"""

import base64
import json
import logging
import re
import hashlib
from urllib.parse import urlparse, unquote

log = logging.getLogger(__name__)

SCHEME_MARKERS = ("vmess://", "vless://", "trojan://", "ss://", "hysteria2://", "hy2://")

_B64_CHARSET_RE = re.compile(r"^[A-Za-z0-9+/=_\-\s]+$")


def _b64_decode_flex(data: str):
    """Base64 رو با هر دو نوع Standard/URL-safe و Padding خودکار Decode می‌کنه.
    اگه واقعاً Base64 معتبر نبود، None برمی‌گردونه (هیچ Exception ای بیرون نمی‌ره)."""
    if not data:
        return None
    cleaned = re.sub(r"\s+", "", data)
    if not cleaned or not _B64_CHARSET_RE.match(cleaned):
        return None
    pad = (-len(cleaned)) % 4
    cleaned_padded = cleaned + ("=" * pad)
    try:
        if "-" in cleaned or "_" in cleaned:
            raw = base64.urlsafe_b64decode(cleaned_padded)
        else:
            raw = base64.b64decode(cleaned_padded)
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None


def decode_whole_source_if_base64(raw_text: str) -> str:
    """اگه کل محتوای Source واقعاً یه بلاک Base64 باشه که داخلش کانفیگ‌های
    واقعی (با اسکیم‌های شناخته‌شده) داره، نسخه‌ی Decode شده رو برمی‌گردونه؛
    وگرنه متن اصلی رو دست‌نخورده برمی‌گردونه (تا مسیر Parse خط‌به‌خط خودش
    تصمیم بگیره)."""
    decoded = _b64_decode_flex(raw_text)
    if decoded and any(marker in decoded for marker in SCHEME_MARKERS):
        return decoded
    return raw_text


def _make_cfg(protocol: str, host: str, port, ident: str, name: str, raw_line: str):
    if not host or not port:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if port <= 0 or port > 65535:
        return None
    return {
        "protocol": protocol,
        "host": host,
        "port": port,
        "ident": ident or "",
        "name": (name or host)[:60],
        "raw": raw_line.strip(),
    }


def _parse_generic_uri(line: str, protocol: str):
    """برای VLESS / Trojan / Hysteria2 که فرمت‌شون scheme://ident@host:port?query#name هست."""
    try:
        parsed = urlparse(line)
    except Exception:
        return None
    host = parsed.hostname
    port = parsed.port
    ident = unquote(parsed.username) if parsed.username else ""
    name = unquote(parsed.fragment) if parsed.fragment else host
    return _make_cfg(protocol, host, port, ident, name, line)


def _parse_vmess(line: str):
    payload = line[len("vmess://"):]
    # بعضی لینک‌های vmess یه فرگمنت (#name) بعد از بلاک Base64 دارن.
    payload = payload.split("#", 1)[0]
    decoded = _b64_decode_flex(payload)
    if not decoded:
        return None
    try:
        data = json.loads(decoded)
    except Exception:
        return None
    host = data.get("add")
    port = data.get("port")
    ident = data.get("id", "")
    name = data.get("ps") or host
    return _make_cfg("VMess", host, port, ident, name, line)


def _parse_shadowsocks(line: str):
    rest = line[len("ss://"):]
    frag = ""
    if "#" in rest:
        rest, frag = rest.split("#", 1)
        frag = unquote(frag)

    method = password = host = port = None

    if "@" in rest:
        # ss://method:password@host:port  یا  ss://base64(method:password)@host:port
        userinfo, hostport = rest.rsplit("@", 1)
        hostport = hostport.split("?", 1)[0].split("/", 1)[0]
        decoded_userinfo = _b64_decode_flex(userinfo)
        if decoded_userinfo and ":" in decoded_userinfo:
            method, password = decoded_userinfo.split(":", 1)
        elif ":" in userinfo:
            method, password = userinfo.split(":", 1)
        if ":" in hostport:
            host, port = hostport.rsplit(":", 1)
    else:
        # فرمت قدیمی: ss://base64(method:password@host:port)
        body = rest.split("?", 1)[0].split("/", 1)[0]
        decoded = _b64_decode_flex(body)
        if decoded and "@" in decoded:
            cred, hostport = decoded.rsplit("@", 1)
            if ":" in cred and ":" in hostport:
                method, password = cred.split(":", 1)
                host, port = hostport.rsplit(":", 1)

    if not host or not port or password is None:
        return None
    return _make_cfg("Shadowsocks", host, port, password, frag or host, line)


def parse_config_line(line: str):
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None
    if "://" not in line:
        return None
    scheme = line.split("://", 1)[0].lower()
    try:
        if scheme == "vmess":
            return _parse_vmess(line)
        if scheme == "vless":
            return _parse_generic_uri(line, "VLESS")
        if scheme == "trojan":
            return _parse_generic_uri(line, "Trojan")
        if scheme == "ss":
            return _parse_shadowsocks(line)
        if scheme in ("hysteria2", "hy2"):
            return _parse_generic_uri(line, "Hysteria2")
    except Exception as e:
        log.info(f"gotham-config: parse failed for a {scheme} line: {e}")
        return None
    return None


def extract_configs_from_text(raw_text: str) -> list:
    """کل متن یه Source رو به لیست کانفیگ‌های واقعاً Parse‌شده تبدیل می‌کنه."""
    if not raw_text:
        return []
    candidate_text = decode_whole_source_if_base64(raw_text)
    results = []
    for line in candidate_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        cfg = parse_config_line(line)
        if cfg is None and "://" not in line:
            # بعضی Sourceها هر خط رو جدا Base64 می‌کنن (نه کل فایل رو یک‌جا)
            decoded_line = _b64_decode_flex(line)
            if decoded_line:
                cfg = parse_config_line(decoded_line.strip())
        if cfg:
            results.append(cfg)
    return results


def make_signature(cfg: dict) -> str:
    """یه Signature کوتاه و پایدار برای جلوگیری از نمایش تکراریِ یک کانفیگ
    (بر اساس پروتکل + هاست + پورت + شناسه‌ی واقعی کانفیگ، نه محتوای کامل
    خط که ممکنه فقط نام/کوئری‌استرینگش فرق کنه)."""
    key = f"{cfg['protocol']}|{cfg['host'].lower()}|{cfg['port']}|{cfg.get('ident', '')}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def dedupe(configs: list) -> list:
    seen = set()
    out = []
    for cfg in configs:
        sig = make_signature(cfg)
        if sig in seen:
            continue
        seen.add(sig)
        cfg = dict(cfg)
        cfg["sig"] = sig
        out.append(cfg)
    return out
