# -*- coding: utf-8 -*-
"""
gotham_ai/tools.py
=====================
Tool Calling امن. طراحی برای گسترش بعدی: قابلیت‌های داخلی ربات (calculator,
translator, downloader status, ...) می‌تونن به‌عنوان Tool به AI معرفی بشن.

نکته‌ی امنیتی مهم: هیچ Tool‌ای دستور admin/سیستم رو بدون اجازه اجرا نمی‌کنه.
این فایل فقط Toolهای کاملاً بی‌خطر و local (بدون دسترسی به دیتابیس/گروه) رو
مستقیم تو چت هندل می‌کنه؛ اتصال این‌ها به AI به‌عنوان function-calling واقعی
(از طریق response_format/tools تو client.chat_completion) قابل توسعه‌ست ولی
فعلاً به‌صورت پیش‌پردازش محلیِ ساده (بدون نیاز به رفت‌وبرگشت مدل) پیاده شده
که هم سریع‌تره هم امن‌تر.
"""

import ast
import math
import operator as op
import re

_ALLOWED_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.Pow: op.pow, ast.Mod: op.mod, ast.USub: op.neg, ast.UAdd: op.pos,
    ast.FloorDiv: op.floordiv,
}
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "exp": math.exp, "factorial": math.factorial, "abs": abs,
}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


def _safe_eval(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("invalid constant")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
        args = [_safe_eval(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
        return _ALLOWED_NAMES[node.id]
    raise ValueError("expression not allowed")


_CALC_TRIGGER = re.compile(r"^(?:حساب کن|محاسبه کن|calc)[:\s]+(.+)$", re.IGNORECASE)


def try_local_tool(text: str):
    """اگه متن یه فراخوانی Tool محلیه، جواب رشته‌ای برمی‌گردونه؛ وگرنه None
    (یعنی باید بره سراغ AI Chat معمولی)."""
    m = _CALC_TRIGGER.match(text.strip())
    if m:
        expr = m.group(1)
        try:
            tree = ast.parse(expr, mode="eval")
            result = _safe_eval(tree.body)
            return f"🧮 نتیجه: {result}"
        except Exception:
            return "🧮 این عبارت قابل‌محاسبه نبود."
    return None
