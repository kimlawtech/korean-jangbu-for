"""카드명세서 행 파서 단위 테스트 (OCR 실행 없이 토큰만 전달).

모든 테스트 데이터는 가상(fictitious) — 실제 사업자번호·가맹점 아님.
"""
from jangbu_mcp import ocr


def test_parse_shinhan_row_basic():
    tokens = ["2025.01.01", "2025.02.14", "복수 100", "가상식당 테스트점", "6,500", "일시불", "1234567890"]
    r = ocr._parse_card_row_shinhan(tokens)
    assert r is not None
    assert r["use_date"] == "2025-01-01"
    assert r["card_last3"] == "100"
    assert r["amount"] == "6500"
    assert r["biz_id"] == "1234567890"
    assert "가상식당" in r["merchant"]


def test_parse_shinhan_row_ocr_noise_in_card_last3():
    # '03g' (9가 g로 오인식)
    tokens = ["2025.01.05", "2025.02.14", "본인 03g", "테스트슈퍼", "3,400", "일시불", "9999999999"]
    r = ocr._parse_card_row_shinhan(tokens)
    assert r is not None
    assert r["card_last3"] == "039"
    assert r["amount"] == "3400"


def test_parse_shinhan_row_refund_negative():
    tokens = ["2025.01.14", "2025.02.14", "본인 039", "테스트샵", "-5,500", "일시불", "1111111111"]
    r = ocr._parse_card_row_shinhan(tokens)
    assert r is not None
    assert r["amount"] == "-5500"


def test_parse_shinhan_row_missing_amount_returns_none():
    # 금액 누락
    tokens = ["2025.01.17", "2025.02.14", "본인 039", "일시불", "2222222222"]
    r = ocr._parse_card_row_shinhan(tokens)
    assert r is None


def test_detect_issuer_shinhan():
    assert ocr._detect_card_issuer("신한카드 (주) 회원이용내역서") == "shinhan"
    assert ocr._detect_card_issuer("KB카드") == "kb"
    assert ocr._detect_card_issuer("삼성카드 명세서") == "samsung"


def test_fix_card_last3():
    assert ocr._fix_card_last3("03g") == "039"
    assert ocr._fix_card_last3("12O") == "120"
    assert ocr._fix_card_last3("l22") == "122"


# ---- 범용 카드 파서 (7대 카드사) ----

def test_parse_generic_kb_card():
    """KB국민카드: 'KB국민 9012' 형식."""
    tokens = ["2025.02.15", "2025.03.15", "KB국민 9012", "가상마트 테스트점", "45,000", "일시불", "3333333333"]
    r = ocr._parse_card_row_generic(tokens, issuer="kb")
    assert r is not None
    assert r["use_date"] == "2025-02-15"
    assert r["card_last3"] == "9012"
    assert r["amount"] == "45000"
    assert r["biz_id"] == "3333333333"


def test_parse_generic_samsung_card():
    """삼성카드: '삼성카드 1234' 형식."""
    tokens = ["2025.02.20", "2025.03.10", "삼성카드 1234", "테스트카페", "5,500", "일시불", "4444444444"]
    r = ocr._parse_card_row_generic(tokens, issuer="samsung")
    assert r is not None
    assert r["card_last3"] == "1234"
    assert r["amount"] == "5500"


def test_parse_generic_hyundai_card():
    """현대카드: '현대카드 M 5678' 형식."""
    tokens = ["2025.02.22", "2025.03.14", "현대카드 M 5678", "테스트서점", "23,000", "일시불", "5555555555"]
    r = ocr._parse_card_row_generic(tokens, issuer="hyundai")
    assert r is not None
    assert r["card_last3"] == "5678"


def test_parse_generic_lotte_card():
    tokens = ["2025.03.01", "2025.04.14", "롯데카드 7890", "테스트마트 지점", "89,000", "일시불", "6666666666"]
    r = ocr._parse_card_row_generic(tokens, issuer="lotte")
    assert r is not None
    assert r["card_last3"] == "7890"


def test_parse_generic_bc_card():
    tokens = ["2025.03.03", "2025.04.14", "BC 3456", "테스트편의점", "4,200", "체크", "7777777777"]
    r = ocr._parse_card_row_generic(tokens, issuer="bc")
    assert r is not None
    assert r["card_last3"] == "3456"


def test_parse_generic_fallback_when_issuer_unknown():
    """발급사 감지 실패해도 다른 발급사 패턴으로 fallback."""
    tokens = ["2025.03.05", "2025.04.14", "KB국민 9012", "테스트편의점", "3,200", "일시불", "8888888888"]
    r = ocr._parse_card_row_generic(tokens, issuer="generic")
    assert r is not None
    assert r["card_last3"] == "9012"


def test_parse_card_refund_negative_generic():
    tokens = ["2025.02.14", "2025.03.14", "KB국민 9012", "테스트샵 환불", "-5,500", "일시불", "1010101010"]
    r = ocr._parse_card_row_generic(tokens, issuer="kb")
    assert r is not None
    assert r["amount"] == "-5500"


def test_card_parsers_registry_dispatch():
    """_parse_card_row는 CARD_PARSERS에 등록된 발급사 우선 사용."""
    assert "shinhan" in ocr.CARD_PARSERS
    tokens = ["2025.01.01", "2025.02.14", "복수 100", "가상식당", "6,500", "일시불", "1234567890"]
    r = ocr._parse_card_row(tokens, issuer="shinhan")
    assert r is not None
    assert r["card_last3"] == "100"
