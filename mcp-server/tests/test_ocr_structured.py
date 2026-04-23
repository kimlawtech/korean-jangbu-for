"""영수증·세금계산서 구조화 테스트 (가상 OCR 토큰).

실제 OCR 실행 없이 OcrResult를 직접 구성해서 구조화 함수만 검증.
"""
from jangbu_mcp import ocr


def _make_result(lines: list[tuple[str, list]]):
    """텍스트 + 박스 좌표로 OcrResult 구성."""
    r = ocr.OcrResult()
    for text, box in lines:
        r.lines.append(ocr.OcrLine(text=text, box=box, confidence=1.0))
    r.full_text = "\n".join(l.text for l in r.lines)
    return r


def _simple_box(y: float):
    """y좌표 기준 단순 박스."""
    return [[0, y], [100, y], [100, y + 20], [0, y + 20]]


# ---- 영수증 ----

def test_structure_receipt_basic():
    r = _make_result([
        ("테스트카페 강남점", _simple_box(10)),
        ("사업자번호 123-45-67890", _simple_box(30)),
        ("2025-10-15 14:22", _simple_box(50)),
        ("아메리카노", _simple_box(70)),
        ("합계 5,500", _simple_box(90)),
    ])
    doc = ocr.structure_receipt(r)
    assert doc["doc_type"] == "receipt"
    assert doc["biz_id"] == "123-45-67890"
    assert doc["date"] == "2025-10-15"
    assert doc["total"] == "5500"
    assert "테스트카페" in (doc.get("merchant") or "")


def test_structure_receipt_with_vat():
    r = _make_result([
        ("테스트마트", _simple_box(10)),
        ("사업자번호 987-65-43210", _simple_box(30)),
        ("2025.12.01", _simple_box(50)),
        ("상품1  10,000", _simple_box(70)),
        ("공급가액 10,000", _simple_box(90)),
        ("부가세 1,000", _simple_box(110)),
        ("합계 11,000", _simple_box(130)),
    ])
    doc = ocr.structure_receipt(r)
    assert doc["total"] == "11000"
    assert doc["supply_amount"] == "10000"
    assert doc["vat"] == "1000"


def test_receipt_to_transaction():
    doc = {
        "doc_type": "receipt",
        "merchant": "테스트카페",
        "biz_id": "123-45-67890",
        "date": "2025-10-15",
        "total": "5500",
        "raw_text": "테스트카페 5500",
    }
    tx = ocr.receipt_to_transaction(doc, account_id="card_001")
    assert tx is not None
    assert tx["date"] == "2025-10-15"
    assert tx["amount"] == "5500"
    assert tx["direction"] == "outflow"
    assert tx["counterparty"] == "테스트카페"
    assert tx["source"] == "ocr"


def test_receipt_to_transaction_missing_required():
    doc = {"doc_type": "receipt", "merchant": "X", "date": None, "total": None}
    assert ocr.receipt_to_transaction(doc) is None


# ---- 세금계산서 ----

def test_structure_tax_invoice():
    r = _make_result([
        ("전자세금계산서", _simple_box(10)),
        ("공급자 사업자번호 111-11-11111", _simple_box(30)),
        ("공급받는자 사업자번호 222-22-22222", _simple_box(50)),
        ("작성일자 2025-11-30", _simple_box(70)),
        ("품목 용역", _simple_box(90)),
        ("공급가액 1,000,000", _simple_box(110)),
        ("세액 100,000", _simple_box(130)),
    ])
    doc = ocr.structure_tax_invoice(r)
    assert doc["doc_type"] == "tax_invoice"
    assert doc["supplier_biz_id"] == "111-11-11111"
    assert doc["recipient_biz_id"] == "222-22-22222"
    assert doc["issue_date"] == "2025-11-30"
    assert doc["supply_amount"] == "1000000"
    assert doc["tax_amount"] == "100000"


def test_structure_tax_invoice_missing_supply():
    r = _make_result([
        ("전자세금계산서", _simple_box(10)),
        ("공급자 사업자번호 111-11-11111", _simple_box(30)),
    ])
    doc = ocr.structure_tax_invoice(r)
    assert doc["needs_llm_fallback"] is True


# ---- 발급사 탐지 범위 ----

def test_detect_card_issuers_all():
    cases = {
        "신한카드 (주) 명세서": "shinhan",
        "KB카드 이용내역": "kb",
        "삼성카드 청구서": "samsung",
        "현대카드 내역": "hyundai",
        "롯데카드 명세": "lotte",
        "BC카드 이용": "bc",
        "우리카드 이용": "woori",
        "하나카드 명세": "hana",
        "알수없는카드": "generic",
    }
    for text, expected in cases.items():
        assert ocr._detect_card_issuer(text) == expected, text


# ---- 카드명세서 기간 추출 ----

def test_card_statement_period_extraction():
    r = _make_result([
        ("신한카드 회원이용내역서", _simple_box(10)),
        ("기간: 2025/07/01 - 2025/12/31", _simple_box(30)),
        ("2025.07.01 2025.08.14 복수 100 테스트 5,000 일시불 1234567890", _simple_box(50)),
    ])
    doc = ocr.structure_card_statement(r)
    assert doc["issuer"] == "shinhan"
    assert doc["period_start"] == "2025-07-01"
    assert doc["period_end"] == "2025-12-31"


# ---- 카드명세서 메타데이터 ----

def test_card_statement_metadata():
    r = _make_result([
        ("신한카드 (주)", _simple_box(10)),
        ("발행일시 2026년03월27일 19:18", _simple_box(30)),
        ("결제일: 매월 14일", _simple_box(50)),
        ("홍*동 고객님", _simple_box(70)),
        ("기간: 2025/07/01 - 2025/12/31", _simple_box(90)),
        ("2025.07.01 2025.08.14 복수 100 테스트 5,000 일시불 1234567890", _simple_box(110)),
    ])
    doc = ocr.structure_card_statement(r)
    assert doc["meta"]["payment_day"] == 14
    assert doc["meta"]["customer_mask"] == "홍*동"
