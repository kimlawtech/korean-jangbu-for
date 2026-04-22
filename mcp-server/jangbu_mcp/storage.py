"""SQLite 스키마 및 저장소 초기화.

finance.db  - 거래내역·매핑·분류 결과
tokens.db   - 토큰↔원본 매핑 (권한 분리)
audit.log   - append-only 감사 로그
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(os.path.expanduser("~/.jangbu"))
RAW_IMPORTS = BASE_DIR / "raw" / "imports"
RAW_OCR = BASE_DIR / "raw" / "ocr"
FINANCE_DB = BASE_DIR / "finance.db"
TOKENS_DB = BASE_DIR / "tokens.db"
AUDIT_LOG = BASE_DIR / "audit.log"


FINANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id     TEXT PRIMARY KEY,
    date               TEXT NOT NULL,
    amount             TEXT NOT NULL,  -- decimal as string (정밀도 유지)
    currency           TEXT NOT NULL DEFAULT 'KRW',
    direction          TEXT NOT NULL CHECK(direction IN ('inflow', 'outflow')),
    counterparty       TEXT NOT NULL,
    description        TEXT NOT NULL,
    source             TEXT NOT NULL CHECK(source IN ('bank', 'card', 'manual', 'ocr')),
    source_ref         TEXT NOT NULL,
    raw_description    TEXT,
    account_id         TEXT,
    matched_account    TEXT,
    confidence         REAL,
    notes              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(matched_account);

CREATE TABLE IF NOT EXISTS account_mappings (
    internal_account   TEXT PRIMARY KEY,
    nts_account        TEXT NOT NULL,   -- 국세청 표준계정과목
    nts_code           TEXT,             -- 국세청 코드
    statement          TEXT NOT NULL CHECK(statement IN ('BS', 'PL')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS classification_rules (
    rule_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type       TEXT NOT NULL CHECK(pattern_type IN ('counterparty_exact', 'counterparty_regex', 'description_regex', 'mcc')),
    pattern            TEXT NOT NULL,
    internal_account   TEXT NOT NULL,
    amount_min         REAL,
    amount_max         REAL,
    direction          TEXT,  -- inflow / outflow / NULL(무관)
    priority           INTEGER NOT NULL DEFAULT 100,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_priority ON classification_rules(priority);

CREATE TABLE IF NOT EXISTS ocr_corrections (
    correction_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    correction_type    TEXT NOT NULL CHECK(correction_type IN ('counterparty_alias', 'card_last3_alias', 'biz_id_alias')),
    source_pattern     TEXT NOT NULL,
    target_value       TEXT NOT NULL,
    applied_count      INTEGER NOT NULL DEFAULT 0,
    approved_by        TEXT NOT NULL DEFAULT 'user' CHECK(approved_by IN ('user', 'auto')),
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(correction_type, source_pattern)
);

CREATE INDEX IF NOT EXISTS idx_corr_type ON ocr_corrections(correction_type);
"""

TOKENS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    token              TEXT PRIMARY KEY,
    field_type         TEXT NOT NULL,   -- biz_id, rrn, card_no, account_no, custom
    original_value     TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_token_value ON tokens(original_value);
"""


def ensure_layout() -> None:
    """저장소 디렉토리 및 스키마 초기화."""
    for d in (RAW_IMPORTS, RAW_OCR):
        d.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(FINANCE_DB) as conn:
        conn.executescript(FINANCE_SCHEMA)

    with sqlite3.connect(TOKENS_DB) as conn:
        conn.executescript(TOKENS_SCHEMA)

    if not AUDIT_LOG.exists():
        AUDIT_LOG.touch()

    os.chmod(TOKENS_DB, 0o600)
    os.chmod(AUDIT_LOG, 0o600)


def finance_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(FINANCE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def tokens_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(TOKENS_DB)
    conn.row_factory = sqlite3.Row
    return conn
