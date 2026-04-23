"""CODEF API 커넥터 (BYOK — 사용자 본인 키 사용).

CODEF: https://developer.codef.io
- 한국 공공기관·금융기관 데이터 통합 프록시
- 사용자가 직접 가입해 Client ID/Secret 발급
- OAuth 2.0 토큰 + RSA 암호화 본문 + 간편인증

이 모듈은:
- OAuth 토큰 발급·캐시
- 요청 본문 RSA 암호화 (CODEF 공개키)
- 간편인증(카카오·PASS 등) 2단계 처리
- 응답 → 표준 13필드 매핑
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from jangbu_mcp import credentials
from jangbu_mcp.storage import BASE_DIR

# CODEF 엔드포인트
CODEF_OAUTH_URL_PROD = "https://oauth.codef.io/oauth/token"
CODEF_OAUTH_URL_SANDBOX = "https://sandbox.codef.io/oauth/token"
CODEF_API_BASE_PROD = "https://api.codef.io"
CODEF_API_BASE_SANDBOX = "https://sandbox.codef.io"

TOKEN_CACHE = BASE_DIR / "codef_token.json"


class CodefError(RuntimeError):
    """CODEF API 호출 실패."""


@dataclass
class CodefClient:
    client_id: str
    client_secret: str
    sandbox: bool = True
    public_key: Optional[str] = None

    @property
    def oauth_url(self) -> str:
        return CODEF_OAUTH_URL_SANDBOX if self.sandbox else CODEF_OAUTH_URL_PROD

    @property
    def api_base(self) -> str:
        return CODEF_API_BASE_SANDBOX if self.sandbox else CODEF_API_BASE_PROD


def load_client() -> CodefClient:
    """credentials에서 CODEF 클라이언트 구성. 미설정 시 예외."""
    cid = credentials.load("CODEF_CLIENT_ID")
    secret = credentials.load("CODEF_CLIENT_SECRET")
    sandbox = credentials.load("CODEF_SANDBOX") != "0"  # 기본 샌드박스
    pubkey = credentials.load("CODEF_PUBLIC_KEY")

    if not cid or not secret:
        raise CodefError(
            "CODEF 자격증명 미설정. "
            "/korean-jangbu-for → [S] 자동 수집 설정 에서 먼저 등록하세요."
        )
    return CodefClient(cid, secret, sandbox, pubkey)


def get_access_token(client: CodefClient, force: bool = False) -> str:
    """OAuth 토큰 발급·캐시. 만료 전까지 재사용."""
    if not force and TOKEN_CACHE.exists():
        try:
            cache = json.loads(TOKEN_CACHE.read_text())
            if cache.get("expires_at", 0) > time.time() + 60:
                if cache.get("client_id") == client.client_id:
                    return cache["access_token"]
        except Exception:
            pass

    auth = base64.b64encode(
        f"{client.client_id}:{client.client_secret}".encode()
    ).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "read",
    }).encode()

    req = urllib.request.Request(
        client.oauth_url,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise CodefError(f"CODEF OAuth 실패 ({e.code}): {e.read().decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise CodefError(
            f"CODEF OAuth 네트워크 오류: {e.reason}. "
            "샌드박스 도메인(sandbox.codef.io) 접근 가능한지 확인하세요."
        ) from e
    except Exception as e:
        raise CodefError(f"CODEF OAuth 알 수 없는 오류: {type(e).__name__}: {e}") from e

    token = body.get("access_token")
    if not token:
        raise CodefError(f"CODEF OAuth 응답에 access_token 없음: {body}")

    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({
        "access_token": token,
        "expires_at": time.time() + int(body.get("expires_in", 3600)),
        "client_id": client.client_id,
    }))
    try:
        import os as _os
        _os.chmod(TOKEN_CACHE, 0o600)
    except Exception:
        pass
    return token


def _encrypt_password(plain: str, public_key_pem: Optional[str]) -> str:
    """CODEF 요청 본문 비밀번호 RSA 암호화.

    공개키가 설정되지 않았으면 원문 반환 (샌드박스·일부 API는 평문 허용).
    프로덕션에서는 반드시 RSA-PKCS1 암호화 필요.
    """
    if not public_key_pem:
        return plain
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        pub = serialization.load_pem_public_key(public_key_pem.encode())
        cipher = pub.encrypt(plain.encode(), padding.PKCS1v15())
        return base64.b64encode(cipher).decode()
    except ImportError:
        return plain  # cryptography 미설치 시 평문 (샌드박스 전용)


def call_api(path: str, body: dict, client: Optional[CodefClient] = None,
             timeout: int = 30) -> dict:
    """CODEF REST API 호출.

    body는 JSON 인코딩 후 URL-encoding (CODEF 규약).
    """
    client = client or load_client()
    token = get_access_token(client)
    url = client.api_base + path

    # CODEF 규약: body 전체를 JSON → URL 인코딩해서 전송
    encoded = urllib.parse.quote(json.dumps(body, ensure_ascii=False))
    data = encoded.encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise CodefError(f"CODEF API {path} 실패 ({e.code}): {e.read().decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise CodefError(f"CODEF API 네트워크 오류 ({path}): {e.reason}") from e
    except Exception as e:
        raise CodefError(f"CODEF API 알 수 없는 오류 ({path}): {type(e).__name__}: {e}") from e

    # 응답도 URL-decoded JSON
    decoded = urllib.parse.unquote(raw)
    try:
        return json.loads(decoded)
    except json.JSONDecodeError:
        raise CodefError(f"CODEF API {path} 응답 JSON 파싱 실패: {decoded[:200]}")


# ---- 간편인증 2단계 처리 ----

def request_simple_auth(organization: str, login_type: str,
                        user_name: str, identity: str,
                        login_type_extra: Optional[dict] = None) -> dict:
    """간편인증 요청 단계.

    organization: 홈택스=0001, 건강보험=0002 등 CODEF 기관코드
    login_type: "6"=카카오, "1"=공동인증서, "2"=금융인증서 등
    identity: 주민번호 또는 사업자번호

    반환: CODEF가 simpleAuth 대기 상태로 돌려준 응답. twoWayInfo 포함.
    """
    body = {
        "organization": organization,
        "loginType": login_type,
        "userName": user_name,
        "identity": identity,
        "loginTypeLevel": "1",
        "simpleAuth": "1",
    }
    if login_type_extra:
        body.update(login_type_extra)
    return call_api("/v1/kr/public/ef/hometax/tax-certificate", body)


def complete_simple_auth(twoway_info: dict, organization: str,
                         login_type: str, user_name: str,
                         identity: str, original_body: dict) -> dict:
    """간편인증 완료 단계. 사용자가 카카오톡에서 승인한 후 호출."""
    body = dict(original_body)
    body.update({
        "organization": organization,
        "loginType": login_type,
        "userName": user_name,
        "identity": identity,
        "simpleAuth": "1",
        "is2Way": True,
        "twoWayInfo": twoway_info,
    })
    return call_api("/v1/kr/public/ef/hometax/tax-certificate", body)


# ---- 표준 13필드 매핑 ----

def map_bank_tx(tx: dict, account_id: str) -> dict:
    """CODEF 은행 거래내역 1건 → 표준 13필드."""
    from decimal import Decimal
    from uuid import uuid4

    # CODEF 은행 거래내역 공통 필드:
    # resAccountTrDate, resAccountTrTime, resAccountDesc1, resAccountDesc2,
    # resAccountOut, resAccountIn, resAfterTranBalance, resAccountTrNo
    date_s = tx.get("resAccountTrDate", "")
    if len(date_s) == 8:
        date_s = f"{date_s[:4]}-{date_s[4:6]}-{date_s[6:8]}"

    outflow = Decimal(str(tx.get("resAccountOut", "0") or "0"))
    inflow = Decimal(str(tx.get("resAccountIn", "0") or "0"))
    if outflow > 0:
        direction = "outflow"
        amount = outflow
    else:
        direction = "inflow"
        amount = inflow

    desc = " ".join(filter(None, [tx.get("resAccountDesc1"), tx.get("resAccountDesc2")])).strip()
    source_ref = tx.get("resAccountTrNo") or f"{account_id}:{date_s}:{desc}:{amount}"

    return {
        "transaction_id": f"tx_{uuid4().hex[:16]}",
        "date": date_s,
        "amount": str(amount),
        "currency": "KRW",
        "direction": direction,
        "counterparty": desc[:100] or "거래처 미상",
        "description": desc,
        "source": "bank",
        "source_ref": f"codef:{source_ref}",
        "raw_description": desc,
        "account_id": account_id,
    }


def map_card_tx(tx: dict, account_id: str) -> dict:
    """CODEF 카드 이용내역 1건 → 표준 13필드."""
    from decimal import Decimal
    from uuid import uuid4

    date_s = tx.get("resUsedDate") or tx.get("resCardApprovalDate") or ""
    if len(date_s) == 8:
        date_s = f"{date_s[:4]}-{date_s[4:6]}-{date_s[6:8]}"

    amount = Decimal(str(tx.get("resUsedAmount", "0") or "0"))
    merchant = tx.get("resMemberStoreName") or tx.get("resMemberStore") or ""
    biz_id = tx.get("resMemberStoreRegNo") or ""
    approval = tx.get("resCardApprovalNo") or ""

    direction = "outflow" if amount > 0 else "inflow"
    amount = abs(amount)

    return {
        "transaction_id": f"tx_{uuid4().hex[:16]}",
        "date": date_s,
        "amount": str(amount),
        "currency": "KRW",
        "direction": direction,
        "counterparty": merchant[:100] or "가맹점 미상",
        "description": f"{merchant}" + (f" (사업자 {biz_id})" if biz_id else ""),
        "source": "card",
        "source_ref": f"codef:{approval or account_id + ':' + date_s + ':' + merchant + ':' + str(amount)}",
        "raw_description": merchant,
        "account_id": account_id,
    }


def map_tax_invoice(inv: dict) -> dict:
    """CODEF 세금계산서 1건 → 표준 13필드.

    매출/매입 구분은 inv['resInvoiceType']로.
    """
    from decimal import Decimal
    from uuid import uuid4

    issue_date = inv.get("resIssueDate") or inv.get("resAuthorizationDate") or ""
    if len(issue_date) == 8:
        issue_date = f"{issue_date[:4]}-{issue_date[4:6]}-{issue_date[6:8]}"

    total = Decimal(str(inv.get("resTotalAmount", "0") or "0"))
    supply = Decimal(str(inv.get("resSupplyAmount", "0") or "0"))
    vat = Decimal(str(inv.get("resTaxAmount", "0") or "0"))

    invoice_type = (inv.get("resInvoiceType") or "").upper()  # "매출" / "매입"
    direction = "inflow" if "매출" in invoice_type else "outflow"

    supplier = inv.get("resSupplierName") or ""
    buyer = inv.get("resBuyerName") or ""
    counterparty = buyer if direction == "inflow" else supplier

    return {
        "transaction_id": f"tx_{uuid4().hex[:16]}",
        "date": issue_date,
        "amount": str(total),
        "currency": "KRW",
        "direction": direction,
        "counterparty": counterparty[:100],
        "description": f"세금계산서 {invoice_type} (공급가액 {supply}, VAT {vat})",
        "source": "manual",
        "source_ref": f"codef:invoice:{inv.get('resAuthorizationNo', '')}:{issue_date}:{total}",
        "raw_description": json.dumps({
            "supplier": supplier, "buyer": buyer,
            "supply": str(supply), "vat": str(vat),
        }, ensure_ascii=False),
        "account_id": None,
    }
