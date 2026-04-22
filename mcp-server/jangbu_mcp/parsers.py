"""원본 파일 → 표준 거래내역 파서.

- bank CSV (KB·신한·우리·하나·IBK + generic)
- card CSV (개인·법인 카드 공통 포맷)
- 엑셀 (수기 장부)

각 파서는 표준 스키마 dict 리스트를 반환한다.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pandas as pd


def _tx_id() -> str:
    return f"tx_{uuid.uuid4().hex[:16]}"


def _to_decimal(s: str) -> Decimal:
    """쉼표/공백/원 기호 제거 후 Decimal. 음수·괄호표기 지원."""
    if s is None:
        return Decimal("0")
    t = str(s).replace(",", "").replace(" ", "").replace("원", "").strip()
    if not t:
        return Decimal("0")
    # (1,000) → -1000
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return Decimal(t)
    except Exception:
        return Decimal("0")


def _norm_date(s: str) -> str:
    """다양한 날짜 포맷을 YYYY-MM-DD로. 실패 시 원본 반환."""
    if not s:
        return ""
    s = str(s).strip()
    # 2025-07-01 / 2025.07.01 / 2025/07/01
    m = re.match(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    # 20250701
    m = re.match(r"(20\d{2})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s[:10]


# ---- 은행별 컬럼 매핑 ----
# 각 은행의 '거래내역 조회' CSV 다운로드 기준. 실서비스별 포맷은 조금씩 달라
# 자동 탐지 fallback으로 보강한다.
BANK_COLUMN_MAPS: dict[str, dict[str, list[str]]] = {
    "kb": {  # 국민은행
        "date": ["거래일시", "거래일자", "날짜"],
        "desc": ["거래내용", "적요", "거래점", "내용"],
        "inflow": ["입금액", "입금", "받은금액"],
        "outflow": ["출금액", "출금", "보낸금액"],
        "balance": ["거래후잔액", "잔액"],
    },
    "shinhan": {  # 신한은행
        "date": ["거래일자", "거래일시"],
        "desc": ["적요", "거래내용", "받으신분/보내신분"],
        "inflow": ["입금", "입금액"],
        "outflow": ["출금", "출금액"],
        "balance": ["잔액"],
    },
    "woori": {  # 우리은행
        "date": ["거래일자", "거래일시"],
        "desc": ["내용", "적요", "거래기록사항"],
        "inflow": ["찾으신금액", "입금"],  # 우리은행은 일부 포맷에서 이 라벨을 거꾸로 쓰는 경우 존재
        "outflow": ["맡기신금액", "출금"],
        "balance": ["거래후잔액", "잔액"],
    },
    "hana": {  # 하나은행
        "date": ["거래일자", "거래일시"],
        "desc": ["적요", "거래내용", "받는분"],
        "inflow": ["입금액", "입금"],
        "outflow": ["출금액", "출금"],
        "balance": ["거래후잔액", "잔액"],
    },
    "ibk": {  # 기업은행
        "date": ["거래일자", "거래일시"],
        "desc": ["적요", "거래내용"],
        "inflow": ["입금금액", "입금"],
        "outflow": ["출금금액", "출금"],
        "balance": ["잔액"],
    },
    "generic": {
        "date": ["date", "거래일자", "거래일시", "날짜"],
        "desc": ["description", "적요", "거래내용", "내용"],
        "inflow": ["deposit", "inflow", "입금", "입금액"],
        "outflow": ["withdrawal", "outflow", "출금", "출금액"],
        "balance": ["balance", "잔액"],
    },
}


def _resolve_column(df_cols: list[str], candidates: list[str]) -> str | None:
    """DF 컬럼명 중 후보 중 하나와 일치(또는 포함)하는 실제 컬럼명 반환."""
    norm_cols = {c.strip(): c for c in df_cols}
    # 정확 일치 우선
    for cand in candidates:
        if cand in norm_cols:
            return norm_cols[cand]
    # 부분 일치
    for cand in candidates:
        for real in norm_cols:
            if cand in real:
                return norm_cols[real]
    return None


def _detect_bank_from_header(df_cols: list[str]) -> str:
    """헤더만 보고 은행 추정. 실패 시 'generic'."""
    joined = " ".join(df_cols)
    if "찾으신금액" in joined or "맡기신금액" in joined:
        return "woori"
    if "거래후잔액" in joined and "받으신분" in joined:
        return "shinhan"
    # 단순 휴리스틱 — 실전 데이터 확보되면 보강
    return "generic"


def _read_csv_with_encoding(path: Path) -> pd.DataFrame:
    """한국 은행 CSV는 EUC-KR이 많음. UTF-8 시도 후 실패하면 CP949/EUC-KR."""
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc).fillna("")
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return pd.read_csv(path, dtype=str, encoding="cp949", errors="replace").fillna("")


def parse_bank_csv(path: Path, account_id: str, bank: str = "auto") -> Iterator[dict]:
    """은행 거래내역 CSV 파싱.

    bank: auto(자동 탐지) / kb / shinhan / woori / hana / ibk / generic
    """
    df = _read_csv_with_encoding(path)
    df.columns = [str(c).strip() for c in df.columns]

    if bank == "auto":
        bank = _detect_bank_from_header(list(df.columns))

    cmap = BANK_COLUMN_MAPS.get(bank) or BANK_COLUMN_MAPS["generic"]

    col_date = _resolve_column(list(df.columns), cmap["date"])
    col_desc = _resolve_column(list(df.columns), cmap["desc"])
    col_in = _resolve_column(list(df.columns), cmap["inflow"])
    col_out = _resolve_column(list(df.columns), cmap["outflow"])

    if not col_date or not col_desc or not (col_in or col_out):
        raise ValueError(
            f"bank CSV 컬럼 탐지 실패. bank={bank} columns={list(df.columns)}. "
            "컬럼명을 확인하거나 bank='generic'으로 재시도."
        )

    for _, r in df.iterrows():
        inflow = _to_decimal(r.get(col_in, "") if col_in else "0")
        outflow = _to_decimal(r.get(col_out, "") if col_out else "0")
        if inflow == 0 and outflow == 0:
            continue
        direction = "inflow" if inflow > 0 else "outflow"
        amount = inflow if direction == "inflow" else outflow
        if amount < 0:  # 마이너스 표기 보정
            amount = abs(amount)
            direction = "inflow" if direction == "outflow" else "outflow"

        date_s = _norm_date(str(r.get(col_date, "")))
        desc = str(r.get(col_desc, "")).strip()

        yield {
            "transaction_id": _tx_id(),
            "date": date_s,
            "amount": str(amount),
            "currency": "KRW",
            "direction": direction,
            "counterparty": desc[:100],
            "description": desc,
            "source": "bank",
            "source_ref": f"{bank}:{account_id}:{date_s}:{desc}:{amount}",
            "raw_description": desc,
            "account_id": account_id,
        }


def parse_card_csv(path: Path, account_id: str) -> Iterator[dict]:
    """카드 거래내역 CSV 파싱.

    예상 컬럼: date, merchant, amount, mcc(선택), approval_no
    카드는 전부 outflow로 가정 (취소건은 amount 음수).
    한글 헤더도 지원: 이용일자·가맹점명·이용금액·승인번호
    """
    df = _read_csv_with_encoding(path)
    df.columns = [str(c).strip() for c in df.columns]

    col_date = _resolve_column(list(df.columns), ["date", "이용일자", "거래일자", "사용일자"])
    col_merchant = _resolve_column(list(df.columns), ["merchant", "가맹점명", "가맹점", "이용가맹점"])
    col_amount = _resolve_column(list(df.columns), ["amount", "이용금액", "금액", "사용금액"])
    col_approval = _resolve_column(list(df.columns), ["approval_no", "승인번호"])

    if not col_date or not col_merchant or not col_amount:
        raise ValueError(
            f"card CSV 컬럼 탐지 실패. columns={list(df.columns)}"
        )

    for _, r in df.iterrows():
        amount = _to_decimal(str(r.get(col_amount, "")))
        if amount == 0:
            continue
        direction = "outflow" if amount > 0 else "inflow"
        amount = abs(amount)

        date_s = _norm_date(str(r.get(col_date, "")))
        merchant = str(r.get(col_merchant, "")).strip()
        approval = str(r.get(col_approval, "")).strip() if col_approval else ""

        yield {
            "transaction_id": _tx_id(),
            "date": date_s,
            "amount": str(amount),
            "currency": "KRW",
            "direction": direction,
            "counterparty": merchant[:100],
            "description": merchant,
            "source": "card",
            "source_ref": approval or f"{account_id}:{date_s}:{merchant}:{amount}",
            "raw_description": merchant,
            "account_id": account_id,
        }


def parse_manual_xlsx(path: Path) -> Iterator[dict]:
    """수기 엑셀 장부 파싱.

    컬럼: date, counterparty, description, amount, direction, account_id(선택)
    한글 헤더도 지원.
    """
    df = pd.read_excel(path, sheet_name=0, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    col_date = _resolve_column(list(df.columns), ["date", "날짜", "거래일자"])
    col_cp = _resolve_column(list(df.columns), ["counterparty", "거래처", "상대방"])
    col_desc = _resolve_column(list(df.columns), ["description", "적요", "내용"])
    col_amount = _resolve_column(list(df.columns), ["amount", "금액"])
    col_dir = _resolve_column(list(df.columns), ["direction", "방향", "구분"])
    col_acct = _resolve_column(list(df.columns), ["account_id", "계좌"])

    if not col_date or not col_amount:
        raise ValueError(f"manual xlsx 필수 컬럼(date, amount) 없음. columns={list(df.columns)}")

    for _, r in df.iterrows():
        amount = _to_decimal(str(r.get(col_amount, "")))
        if amount == 0:
            continue
        if col_dir:
            direction = str(r[col_dir]).strip()
            if direction not in ("inflow", "outflow", "입금", "출금"):
                direction = "inflow" if amount > 0 else "outflow"
            elif direction in ("입금",):
                direction = "inflow"
            elif direction in ("출금",):
                direction = "outflow"
        else:
            direction = "inflow" if amount > 0 else "outflow"
        amount = abs(amount)

        date_s = _norm_date(str(r.get(col_date, "")))
        cp = str(r.get(col_cp, "")).strip() if col_cp else ""
        desc = str(r.get(col_desc, "")).strip() if col_desc else ""
        acct = str(r.get(col_acct, "")).strip() if col_acct else ""

        yield {
            "transaction_id": _tx_id(),
            "date": date_s,
            "amount": str(amount),
            "currency": "KRW",
            "direction": direction,
            "counterparty": (cp or desc)[:100],
            "description": desc or cp,
            "source": "manual",
            "source_ref": f"manual:{date_s}:{cp or desc}:{amount}",
            "raw_description": desc,
            "account_id": acct or None,
        }
