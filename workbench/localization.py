"""Shared English UI/API messages, with Chinese source messages as stable identifiers.

Only operational errors are translated. Model output, user prompts and schema values
must never pass through this module: they are business data, not interface content.
"""

import json
import re
from pathlib import Path

ENGLISH = json.loads((Path(__file__).parent / "static/locales/en.json").read_text(encoding="utf-8"))


def translate(message: str, language: str) -> str:
    """Translate a known operational message; preserve unfamiliar technical details."""
    if not language.lower().startswith("en"):
        return message
    if message.startswith("Value error, "):
        return "Invalid configuration: " + translate(message.removeprefix("Value error, "), language)
    status = re.fullmatch(r"模型服务返回 HTTP (\d+)；请检查服务日志与模型配置", message)
    if status:
        return f"Model service returned HTTP {status[1]}. Check the service logs and configuration."
    return ENGLISH.get(message, message)
