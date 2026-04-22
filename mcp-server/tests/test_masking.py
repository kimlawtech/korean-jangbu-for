from jangbu_mcp import masking


def test_tokenize_reuses_token_for_same_value():
    t1 = masking.tokenize("123-45-67890", "biz_id")
    t2 = masking.tokenize("123-45-67890", "biz_id")
    assert t1 == t2
    assert masking.detokenize(t1) == "123-45-67890"


def test_tokenize_distinct_for_different_values():
    t1 = masking.tokenize("123-45-67890", "biz_id")
    t2 = masking.tokenize("999-99-99999", "biz_id")
    assert t1 != t2


def test_mask_text_biz_id():
    txt = "공급자 사업자번호 123-45-67890, 합계 10,000원"
    out = masking.mask_text(txt)
    assert "123-45-67890" not in out
    assert "TK_BIZ_ID_" in out


def test_mask_text_rrn_full():
    txt = "주민번호: 900101-1234567"
    out = masking.mask_text(txt)
    assert "900101-1234567" not in out


def test_mask_text_rrn_partial_card_statement():
    # 카드명세서에 일부만 노출되는 주민번호 포맷(가상 번호)
    txt = "주민번호: 990101-1******"
    out = masking.mask_text(txt)
    assert "990101-1" not in out


def test_mask_text_card_no():
    txt = "카드번호 1234-5678-9012-3456 승인"
    out = masking.mask_text(txt)
    assert "1234-5678-9012-3456" not in out


def test_mask_transaction_row_preserves_safe_fields():
    row = {
        "date": "2025-10-01",
        "amount": "50000",
        "direction": "outflow",
        "counterparty": "스타벅스 123-45-67890",
        "description": "아메리카노",
        "account_id": "acct_shinhan_001",
    }
    masked = masking.mask_transaction_row(row)
    assert masked["date"] == "2025-10-01"
    assert masked["amount"] == "50000"
    assert masked["direction"] == "outflow"
    assert "123-45-67890" not in masked["counterparty"]
    assert masked["account_id"].startswith("TK_ACCOUNT_ID_")
