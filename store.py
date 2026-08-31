# -*- coding: utf-8 -*-
"""
gotham_ai/store.py
====================
لایه‌ی ذخیره‌سازی مخصوص «امکانات جدید گاتهام» — یه فایل sqlite جدا از دیتابیس
اصلی ربات (تا هیچ ریسکی برای جدول‌ها/قفل دیتابیس اصلی نداشته باشه)، ولی توی
همون پوشه/Volume که DB_PATH اصلی توش هست ذخیره می‌شه تا با هم روی Railway
persist بشن.

هر کاربر/چت session جدای خودش رو داره — context هیچ‌وقت بین کاربرها مخلوط
نمی‌شه (session کلید ترکیبی chat_id+user_id داره).
"""

import os
import json
import time
import asyncio
import sqlite3
import logging

log = logging.getLogger(__name__)

_DB_LOCK = asyncio.Lock()
_DB_PATH = None


def init(main_db_path: str | None = None):
    global _DB_PATH
    base_dir = os.path.dirname(main_db_path) if main_db_path else ""
    _DB_PATH = os.path.join(base_dir, "gotham_ai.db") if base_dir else "gotham_ai.db"
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            active INTEGER DEFAULT 0,
            model TEXT DEFAULT 'auto',
            history TEXT DEFAULT '[]',
            created_at REAL,
            updated_at REAL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            k TEXT PRIMARY KEY,
            v INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            cache_key TEXT PRIMARY KEY,
            user_id INTEGER,
            response TEXT,
            created_at REAL
        )
    """)
    conn.commit()
    conn.close()
    log.info(f"🤖 gotham_ai DB آماده شد: {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(_DB_PATH or "gotham_ai.db")
    conn.row_factory = sqlite3.Row
    return conn


async def db_run(fn, *args):
    async with _DB_LOCK:
        return await asyncio.to_thread(fn, *args)


# ---------------- Sessions ----------------

def _get_session_sync(chat_id, user_id):
    conn = _connect()
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = c.fetchone()
    conn.close()
    if row is None:
        return {"chat_id": chat_id, "user_id": user_id, "active": 0, "model": "auto",
                "history": [], "created_at": None, "updated_at": None}
    return {
        "chat_id": row["chat_id"], "user_id": row["user_id"], "active": row["active"],
        "model": row["model"], "history": json.loads(row["history"] or "[]"),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _save_session_sync(session):
    conn = _connect()
    c = conn.cursor()
    now = time.time()
    c.execute("""
        INSERT INTO sessions (chat_id, user_id, active, model, history, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            active=excluded.active, model=excluded.model, history=excluded.history,
            updated_at=excluded.updated_at
    """, (
        session["chat_id"], session["user_id"], session["active"], session["model"],
        json.dumps(session["history"], ensure_ascii=False),
        session.get("created_at") or now, now,
    ))
    conn.commit()
    conn.close()


async def get_session(chat_id, user_id):
    return await db_run(_get_session_sync, chat_id, user_id)


async def save_session(session):
    await db_run(_save_session_sync, session)


async def is_session_active(chat_id, user_id) -> bool:
    from . import config
    s = await get_session(chat_id, user_id)
    if not s["active"]:
        return False
    if s["updated_at"] and (time.time() - s["updated_at"]) > config.SESSION_IDLE_TIMEOUT:
        s["active"] = 0
        await save_session(s)
        return False
    return True


async def start_session(chat_id, user_id, model="auto"):
    s = await get_session(chat_id, user_id)
    s["active"] = 1
    s["model"] = model
    s["history"] = []
    s["created_at"] = time.time()
    await save_session(s)


async def end_session(chat_id, user_id):
    s = await get_session(chat_id, user_id)
    s["active"] = 0
    await save_session(s)


async def clear_context(chat_id, user_id):
    s = await get_session(chat_id, user_id)
    s["history"] = []
    await save_session(s)


async def set_model(chat_id, user_id, model):
    s = await get_session(chat_id, user_id)
    s["model"] = model
    await save_session(s)


async def append_turn(chat_id, user_id, user_text, assistant_text):
    from . import config
    s = await get_session(chat_id, user_id)
    s["history"].append({"role": "user", "content": user_text})
    s["history"].append({"role": "assistant", "content": assistant_text})
    # 🧹 جلوگیری از memory leak: تاریخچه‌ی ذخیره‌شده هم محدود می‌شه، نه فقط
    # موقع ارسال به مدل — وگرنه دیتابیس برای سشن‌های خیلی طولانی بی‌نهایت رشد می‌کنه.
    max_items = config.MAX_HISTORY_TURNS * 2
    if len(s["history"]) > max_items:
        s["history"] = s["history"][-max_items:]
    await save_session(s)


# ---------------- Stats ----------------

def _bump_stat_sync(key, delta=1):
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        INSERT INTO stats (k, v) VALUES (?, ?)
        ON CONFLICT(k) DO UPDATE SET v = v + excluded.v
    """, (key, delta))
    conn.commit()
    conn.close()


def record_request(success: bool, latency_ms=None, fallback=False):
    """sync، از client.py مستقیم صدا زده می‌شه (نه await) که کند نشه؛ توی thread pool خودش قابل‌قبوله چون sqlite لوکاله."""
    try:
        conn = _connect()
        c = conn.cursor()
        c.execute("INSERT INTO stats (k, v) VALUES ('total_requests', 1) ON CONFLICT(k) DO UPDATE SET v=v+1")
        if success:
            c.execute("INSERT INTO stats (k, v) VALUES ('success', 1) ON CONFLICT(k) DO UPDATE SET v=v+1")
        else:
            c.execute("INSERT INTO stats (k, v) VALUES ('errors', 1) ON CONFLICT(k) DO UPDATE SET v=v+1")
        if fallback:
            c.execute("INSERT INTO stats (k, v) VALUES ('fallbacks', 1) ON CONFLICT(k) DO UPDATE SET v=v+1")
        if latency_ms is not None:
            c.execute("INSERT INTO stats (k, v) VALUES ('latency_sum', ?) ON CONFLICT(k) DO UPDATE SET v=v+excluded.v", (latency_ms,))
            c.execute("INSERT INTO stats (k, v) VALUES ('latency_count', 1) ON CONFLICT(k) DO UPDATE SET v=v+1")
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"gotham_ai stat write failed: {e}")


async def get_stats():
    def _read():
        conn = _connect()
        c = conn.cursor()
        c.execute("SELECT k, v FROM stats")
        rows = {r["k"]: r["v"] for r in c.fetchall()}
        conn.close()
        return rows
    return await db_run(_read)


async def count_active_sessions():
    def _read():
        conn = _connect()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) AS n FROM sessions WHERE active=1")
        n = c.fetchone()["n"]
        conn.close()
        return n
    return await db_run(_read)


# ---------------- Cache (user/session-aware, غیرحساس، کوتاه‌مدت) ----------------

def _cache_key(user_id, model, prompt):
    import hashlib
    raw = f"{user_id}:{model}:{prompt}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()


async def cache_get(user_id, model, prompt):
    from . import config
    key = _cache_key(user_id, model, prompt)

    def _read():
        conn = _connect()
        c = conn.cursor()
        c.execute("SELECT response, created_at FROM cache WHERE cache_key=?", (key,))
        row = c.fetchone()
        conn.close()
        return row
    row = await db_run(_read)
    if not row:
        return None
    if time.time() - row["created_at"] > config.CACHE_TTL_SECONDS:
        return None
    return row["response"]


async def cache_set(user_id, model, prompt, response):
    key = _cache_key(user_id, model, prompt)

    def _write():
        conn = _connect()
        c = conn.cursor()
        c.execute("""
            INSERT INTO cache (cache_key, user_id, response, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET response=excluded.response, created_at=excluded.created_at
        """, (key, user_id, response, time.time()))
        conn.commit()
        conn.close()
    await db_run(_write)
