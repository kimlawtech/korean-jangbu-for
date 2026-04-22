"""Level 2 마스킹 레이어.

- 사업자번호·주민번호·카드번호·계좌번호를 토큰으로 치환
- 토큰↔원본 매핑은 tokens.db에만 저장
- LLM에 전달되는 뷰에서만 적용, 내부 보고서 생성 시 언마스킹
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from jangbu_mcp.storage import tokens_conn


# 한국 식별번호 패턴
# 순서 주의: rrn(full) → rrn_partial → 나머지. 첫 매칭이 이기도록.
PATTERNS = {
    "biz_id": re.compile(r"\b\d{3}-?\d{2}-?\d{5}\b"),                # 사업자번호 000-00-00000
    "rrn": re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),                      # 주민번호 전체 13자리
    "rrn_partial": re.compile(r"\d{6}-?[1-4]\*{3,}"),                 # 부분 마스킹된 주민번호 포맷 (\b 사용 불가)
    "card_no": re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    "account_no": re.compile(r"\b\d{3,6}-\d{2,6}-\d{2,8}\b"),         # 계좌번호 (일반 형식)
}


@dataclass
class MaskedValue:
    masked: str
    tokens: dict[str, str]   # token → original


def _new_token(field_type: str) -> str:
    return f"TK_{field_type.upper()}_{secrets.token_hex(4)}"


def tokenize(original: str, field_type: str) -> str:
    """원본 값을 토큰으로 저장하고 토큰 반환. 동일 값은 기존 토큰 재사용."""
    with tokens_conn() as conn:
        row = conn.execute(
            "SELECT token FROM tokens WHERE original_value = ? AND field_type = ?",
            (original, field_type),
        ).fetchone()
        if row:
            return row["token"]
        token = _new_token(field_type)
        conn.execute(
            "INSERT INTO tokens(token, field_type, original_value) VALUES(?, ?, ?)",
            (token, field_type, original),
        )
        return token


def detokenize(token: str) -> str | None:
    with tokens_conn() as conn:
        row = conn.execute(
            "SELECT original_value FROM tokens WHERE token = ?",
            (token,),
        ).fetchone()
        return row["original_value"] if row else None


def mask_text(text: str) -> str:
    """텍스트 내 민감 번호류를 모두 토큰으로 치환."""
    if not text:
        return text
    result = text
    for field_type, pat in PATTERNS.items():
        def _sub(m: re.Match) -> str:
            return tokenize(m.group(0), field_type)
        result = pat.sub(_sub, result)
    return result


def mask_transaction_row(row: dict) -> dict:
    """거래 행을 LLM용 마스킹 뷰로 변환.

    - account_id: 통째로 토큰화
    - description/raw_description/counterparty: 번호류만 부분 마스킹
    - 기타 필드는 원본 유지
    """
    masked = dict(row)

    if row.get("account_id"):
        masked["account_id"] = tokenize(row["account_id"], "account_id")

    for field in ("counterparty", "description", "raw_description"):
        val = row.get(field)
        if val:
            masked[field] = mask_text(val)

    return masked
