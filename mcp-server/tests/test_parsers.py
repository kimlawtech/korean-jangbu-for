import tempfile
from pathlib import Path

import pandas as pd

from jangbu_mcp import parsers


def _write_csv(rows, columns, encoding="utf-8"):
    tmp = Path(tempfile.mktemp(suffix=".csv"))
    pd.DataFrame(rows, columns=columns).to_csv(tmp, index=False, encoding=encoding)
    return tmp


def test_to_decimal_handles_comma():
    assert parsers._to_decimal("1,500,000") == 1500000
    assert parsers._to_decimal("") == 0
    assert parsers._to_decimal("(1,000)") == -1000


def test_norm_date_variants():
    assert parsers._norm_date("2025-07-01") == "2025-07-01"
    assert parsers._norm_date("2025.07.01") == "2025-07-01"
    assert parsers._norm_date("2025/7/1") == "2025-07-01"
    assert parsers._norm_date("20250701") == "2025-07-01"


def test_detect_bank_woori():
    assert parsers._detect_bank_from_header(["거래일자", "내용", "찾으신금액", "맡기신금액"]) == "woori"


def test_parse_bank_csv_kb():
    rows = [
        {"거래일시": "2025-10-01", "거래내용": "월급", "입금액": "3,000,000", "출금액": "", "거래후잔액": "5,000,000"},
        {"거래일시": "2025-10-02", "거래내용": "월세", "입금액": "", "출금액": "800,000", "거래후잔액": "4,200,000"},
    ]
    path = _write_csv(rows, ["거래일시", "거래내용", "입금액", "출금액", "거래후잔액"])
    txs = list(parsers.parse_bank_csv(path, account_id="kb_001", bank="kb"))
    assert len(txs) == 2
    assert txs[0]["direction"] == "inflow"
    assert txs[0]["amount"] == "3000000"
    assert txs[1]["direction"] == "outflow"
    assert txs[1]["amount"] == "800000"
    assert txs[0]["date"] == "2025-10-01"


def test_parse_card_csv_korean_headers():
    rows = [
        {"이용일자": "2025-10-01", "가맹점명": "스타벅스", "이용금액": "5,500", "승인번호": "ABC123"},
    ]
    path = _write_csv(rows, ["이용일자", "가맹점명", "이용금액", "승인번호"])
    txs = list(parsers.parse_card_csv(path, account_id="card_001"))
    assert len(txs) == 1
    assert txs[0]["counterparty"] == "스타벅스"
    assert txs[0]["amount"] == "5500"
    assert txs[0]["source_ref"] == "ABC123"
