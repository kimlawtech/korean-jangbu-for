"""OCR 보정 레이어 테스트."""
import pytest

from jangbu_mcp import ocr_corrections, storage


def _insert_tx(conn, tx_id, counterparty, account_id=None, amount="1000"):
    conn.execute(
        """INSERT INTO transactions(
            transaction_id, date, amount, currency, direction,
            counterparty, description, source, source_ref, account_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (tx_id, "2025-10-01", amount, "KRW", "outflow",
         counterparty, counterparty, "card", f"ref_{tx_id}", account_id),
    )


# ---- 정규화 / 유사도 ----

def test_normalize_name_strips_whitespace_and_case():
    assert ocr_corrections._normalize_name("GS 25") == "gs25"
    assert ocr_corrections._normalize_name("GS25") == "gs25"
    assert ocr_corrections._normalize_name("  Star Bucks  ") == "starbucks"


def test_similar_high_for_close_strings():
    assert ocr_corrections._similar("GS25 강남점", "GS25 강남점") == 1.0
    assert ocr_corrections._similar("스타벅스 강남", "스타벅스 강남점") > 0.8
    assert ocr_corrections._similar("완전히 다른", "ABC") < 0.3


# ---- analyze_unparsed ----

def test_analyze_unparsed_empty():
    result = ocr_corrections.analyze_unparsed([])
    assert result["total"] == 0
    assert result["by_token_count"] == {}


def test_analyze_unparsed_detects_missing_amount():
    # 금액 없이 날짜·카드·가맹점·사업자만 있는 행
    rows = [
        ["2025.09.01", "2025.10.14", "본인 039", "롯데리아", "일시불", "1068123498"],
        ["2025.09.02", "2025.10.14", "본인 039", "이마트24", "일시불", "1208152063"],
    ]
    result = ocr_corrections.analyze_unparsed(rows)
    assert result["total"] == 2
    assert result["missing_column_estimate"].get("amount", 0) == 2
    assert result["missing_column_estimate"].get("biz_id", 0) == 0


# ---- suggest_counterparty_aliases ----

def test_suggest_counterparty_aliases_basic():
    with storage.finance_conn() as conn:
        _insert_tx(conn, "t1", "스타벅스 강남점")
        _insert_tx(conn, "t2", "스타벅스 강남점")
        _insert_tx(conn, "t3", "스타벅스 강남점")
        _insert_tx(conn, "t4", "스타벅스 강남점")
        _insert_tx(conn, "t5", "스타벅스 강남점")
        _insert_tx(conn, "t6", "스타벅스 강남")
        _insert_tx(conn, "t7", "스타벅스 강남")

    suggestions = ocr_corrections.suggest_counterparty_aliases(
        min_similarity=0.8, min_occurrences=2
    )
    matches = [s for s in suggestions if s["source"] == "스타벅스 강남"]
    assert len(matches) == 1
    assert matches[0]["target"] == "스타벅스 강남점"
    assert matches[0]["similarity"] > 0.8


def test_suggest_skips_low_frequency():
    with storage.finance_conn() as conn:
        _insert_tx(conn, "t1", "스타벅스")
        _insert_tx(conn, "t2", "스타벅스")
        _insert_tx(conn, "t3", "스타벅스")
        _insert_tx(conn, "t4", "스타벅ㅅ")  # 1건만

    suggestions = ocr_corrections.suggest_counterparty_aliases(min_occurrences=2)
    assert not any(s["source"] == "스타벅ㅅ" for s in suggestions)


def test_suggest_excludes_existing_aliases():
    with storage.finance_conn() as conn:
        _insert_tx(conn, "t1", "CU편의점")
        _insert_tx(conn, "t2", "CU편의점")
        _insert_tx(conn, "t3", "CU편의점")
        _insert_tx(conn, "t4", "CU편의점 본점")
        _insert_tx(conn, "t5", "CU편의점 본점")

    # 먼저 등록
    ocr_corrections.apply_alias("counterparty_alias", "CU편의점 본점", "CU편의점")

    suggestions = ocr_corrections.suggest_counterparty_aliases(min_occurrences=2)
    assert not any(s["source"] == "CU편의점 본점" for s in suggestions)


# ---- apply_alias ----

def test_apply_alias_updates_transactions():
    with storage.finance_conn() as conn:
        _insert_tx(conn, "t1", "GS 2S")
        _insert_tx(conn, "t2", "GS 2S")
        _insert_tx(conn, "t3", "GS25")

    res = ocr_corrections.apply_alias("counterparty_alias", "GS 2S", "GS25")
    assert res["saved"] is True
    assert res["updated_transactions"] == 2

    with storage.finance_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE counterparty='GS25'"
        ).fetchone()[0]
        assert row == 3


def test_apply_alias_card_last3():
    with storage.finance_conn() as conn:
        _insert_tx(conn, "t1", "편의점", account_id="card_shinhan_0S9")
        _insert_tx(conn, "t2", "편의점", account_id="card_shinhan_0S9")

    res = ocr_corrections.apply_alias(
        "card_last3_alias", "card_shinhan_0S9", "card_shinhan_039"
    )
    assert res["updated_transactions"] == 2


def test_apply_alias_persists_record():
    ocr_corrections.apply_alias("counterparty_alias", "AAA", "BBB")
    records = ocr_corrections.list_corrections("counterparty_alias")
    assert any(r["source_pattern"] == "AAA" for r in records)


def test_apply_alias_rejects_unknown_type():
    with pytest.raises(ValueError):
        ocr_corrections.apply_alias("unknown_type", "x", "y")


# ---- load_alias_map ----

def test_load_alias_map():
    ocr_corrections.apply_alias("counterparty_alias", "SRC1", "TGT1")
    ocr_corrections.apply_alias("counterparty_alias", "SRC2", "TGT2")

    m = ocr_corrections.load_alias_map("counterparty_alias")
    assert m.get("SRC1") == "TGT1"
    assert m.get("SRC2") == "TGT2"


# ---- summarize_for_llm ----

def test_summarize_for_llm_limits_items():
    analysis = {"total": 10, "by_token_count": {}, "missing_column_estimate": {}}
    cp = [{"source": f"s{i}", "target": f"t{i}", "source_count": 1, "target_count": 10, "similarity": 0.9} for i in range(20)]
    summary = ocr_corrections.summarize_for_llm(analysis, cp, [])
    # 10개로 제한
    assert len(summary["counterparty_alias_suggestions"]) == 10
