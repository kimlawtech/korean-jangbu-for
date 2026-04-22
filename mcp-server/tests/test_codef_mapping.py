"""CODEF 응답 → 표준 13필드 매핑 테스트.

실제 API 호출은 안 하고, CODEF 응답 포맷 샘플로 매퍼만 검증.
"""
from jangbu_mcp.connectors import codef


def test_map_bank_tx_outflow():
    raw = {
        "resAccountTrDate": "20250715",
        "resAccountTrTime": "143022",
        "resAccountDesc1": "스타벅스",
        "resAccountDesc2": "강남점",
        "resAccountOut": "5500",
        "resAccountIn": "0",
        "resAfterTranBalance": "1234567",
        "resAccountTrNo": "TRN202507150001",
    }
    tx = codef.map_bank_tx(raw, account_id="acct_shinhan_001")
    assert tx["date"] == "2025-07-15"
    assert tx["amount"] == "5500"
    assert tx["direction"] == "outflow"
    assert tx["source"] == "bank"
    assert tx["account_id"] == "acct_shinhan_001"
    assert "스타벅스" in tx["counterparty"]
    assert tx["source_ref"].startswith("codef:")


def test_map_bank_tx_inflow():
    raw = {
        "resAccountTrDate": "20250801",
        "resAccountDesc1": "급여입금",
        "resAccountDesc2": "",
        "resAccountOut": "0",
        "resAccountIn": "3000000",
    }
    tx = codef.map_bank_tx(raw, account_id="acct_001")
    assert tx["direction"] == "inflow"
    assert tx["amount"] == "3000000"


def test_map_card_tx():
    raw = {
        "resUsedDate": "20250720",
        "resUsedAmount": "12500",
        "resMemberStoreName": "테스트카페",
        "resMemberStoreRegNo": "1234567890",
        "resCardApprovalNo": "APP20250720-001",
    }
    tx = codef.map_card_tx(raw, account_id="card_shinhan_039")
    assert tx["date"] == "2025-07-20"
    assert tx["amount"] == "12500"
    assert tx["direction"] == "outflow"
    assert tx["source"] == "card"
    assert tx["counterparty"] == "테스트카페"
    assert "1234567890" in tx["description"]


def test_map_tax_invoice_sales():
    raw = {
        "resIssueDate": "20250615",
        "resTotalAmount": "1100000",
        "resSupplyAmount": "1000000",
        "resTaxAmount": "100000",
        "resInvoiceType": "매출",
        "resSupplierName": "내 회사",
        "resBuyerName": "고객사A",
        "resAuthorizationNo": "AUTH-12345",
    }
    tx = codef.map_tax_invoice(raw)
    assert tx["date"] == "2025-06-15"
    assert tx["amount"] == "1100000"
    assert tx["direction"] == "inflow"
    assert tx["counterparty"] == "고객사A"


def test_map_tax_invoice_purchase():
    raw = {
        "resIssueDate": "20250616",
        "resTotalAmount": "550000",
        "resInvoiceType": "매입",
        "resSupplierName": "공급사B",
        "resBuyerName": "내 회사",
        "resSupplyAmount": "500000",
        "resTaxAmount": "50000",
    }
    tx = codef.map_tax_invoice(raw)
    assert tx["direction"] == "outflow"
    assert tx["counterparty"] == "공급사B"


def test_load_client_raises_without_credentials(monkeypatch):
    # 모든 저장소 비운 상태
    from jangbu_mcp import credentials as creds
    # env·envfile 모두 비움
    for key in ("CODEF_CLIENT_ID", "CODEF_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
        creds.delete(key)

    import pytest
    with pytest.raises(codef.CodefError, match="자격증명 미설정"):
        codef.load_client()


def test_load_client_with_credentials():
    from jangbu_mcp import credentials as creds
    creds.save("CODEF_CLIENT_ID", "cid_test", use_keyring=False)
    creds.save("CODEF_CLIENT_SECRET", "secret_test", use_keyring=False)
    creds.save("CODEF_SANDBOX", "1", use_keyring=False)

    client = codef.load_client()
    assert client.client_id == "cid_test"
    assert client.client_secret == "secret_test"
    assert client.sandbox is True
    assert client.api_base.endswith("sandbox.codef.io")
