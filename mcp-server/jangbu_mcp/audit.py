"""Append-only 감사 로그.

모든 MCP 도구 호출을 기록. 로그 파일은 0o600 권한.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from jangbu_mcp.storage import AUDIT_LOG


def log(
    tool_name: str,
    caller: str,
    transaction_ids: list[str] | None = None,
    fields_accessed: list[str] | None = None,
    masked: bool = True,
    purpose: str = "",
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "caller": caller,
        "transaction_ids": transaction_ids or [],
        "fields_accessed": fields_accessed or [],
        "masked": masked,
        "purpose": purpose,
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
