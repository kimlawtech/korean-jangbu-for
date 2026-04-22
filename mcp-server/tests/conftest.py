"""pytest 공통 설정.

- 테스트용 격리 HOME 디렉토리 사용 (~/.jangbu 오염 방지)
- SQLite 매 테스트마다 초기화
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="kfintest_"))
    monkeypatch.setenv("HOME", str(tmp))

    # storage 모듈은 import 시점에 경로를 고정하므로 모듈 내부 상수를 재설정
    from jangbu_mcp import storage
    monkeypatch.setattr(storage, "BASE_DIR", tmp / ".jangbu")
    monkeypatch.setattr(storage, "RAW_IMPORTS", tmp / ".jangbu" / "raw" / "imports")
    monkeypatch.setattr(storage, "RAW_OCR", tmp / ".jangbu" / "raw" / "ocr")
    monkeypatch.setattr(storage, "FINANCE_DB", tmp / ".jangbu" / "finance.db")
    monkeypatch.setattr(storage, "TOKENS_DB", tmp / ".jangbu" / "tokens.db")
    monkeypatch.setattr(storage, "AUDIT_LOG", tmp / ".jangbu" / "audit.log")

    storage.ensure_layout()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)
