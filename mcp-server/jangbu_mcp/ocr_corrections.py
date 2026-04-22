"""OCR 보정 레이어.

Layer 2 (이 모듈): OCR 결과에서 반복 오류·유사 패턴 자동 탐지
Layer 3 (스킬 프롬프트): LLM이 제안 검토·사용자 확인 유도

기능:
- analyze_unparsed: unparsed 행의 공통 구조·누락 컬럼 통계
- suggest_counterparty_aliases: 유사 가맹점명 통합 제안
- suggest_card_last3_aliases: 카드 식별자 오인식 감지
- apply_alias: alias 저장 + 기존 거래 일괄 갱신
- load_aliases: OCR 파싱 시 사전에 적용할 alias 맵

모든 분석 결과는 **마스킹 이전 데이터로 생성**되며,
LLM에게 전달할 때는 summarize()로 카운트·구조 요약만 보냄.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from jangbu_mcp.storage import finance_conn


# ---- 정규화 유틸 ----

def _normalize_name(s: str) -> str:
    """가맹점명 비교용 정규화. 공백·대소문자·특수문자 제거."""
    if not s:
        return ""
    t = re.sub(r"[\s\-·,.\(\)\[\]]", "", s)
    return t.lower()


def _similar(a: str, b: str) -> float:
    """정규화 후 문자열 유사도 (0~1)."""
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


# ---- Layer 2: 패턴 분석 ----

def analyze_unparsed(unparsed_rows: list[list[str]]) -> dict:
    """unparsed 행의 공통 구조 분석.

    반환:
      {
        "total": N,
        "by_token_count": {5: 30, 6: 9},  # 토큰 개수 분포
        "missing_column_estimate": {"amount": 87, "biz_id": 3, "merchant": 5},
        "samples": [...첫 5행...],
      }

    파싱 성공 행의 공통 토큰 개수와 비교해 어느 컬럼이 누락됐는지 추정.
    """
    if not unparsed_rows:
        return {"total": 0, "by_token_count": {}, "missing_column_estimate": {}, "samples": []}

    from jangbu_mcp.ocr import _BIZ_ID_10, _CARD_AMOUNT, _CARD_MARKERS

    # 토큰 개수 분포
    counts = Counter(len(row) for row in unparsed_rows)

    # 각 행에서 어떤 앵커가 있는지 체크해 누락 추정
    # 금액은 "쉼표 포함 숫자"만 인정 (사업자번호 10자리와 구별)
    amount_re = re.compile(r"-?[0-9]{1,3}(?:,[0-9]{3})+")
    missing = Counter()
    for row in unparsed_rows:
        flat = " ".join(row)
        # 사업자번호(10자리 연속)를 먼저 제거한 뒤 금액 탐색
        flat_wo_biz = _BIZ_ID_10.sub("", flat)
        has_biz = bool(_BIZ_ID_10.search(flat))
        has_amount = bool(amount_re.search(flat_wo_biz))
        has_card = any(p.search(flat) for p in _CARD_MARKERS.values())
        # 가맹점 존재: 사업자·카드마커·금액 제외 후 한글·영문 단어 있는지
        has_merchant = bool(re.search(r"[가-힣a-zA-Z]{2,}", flat_wo_biz))

        if not has_amount:
            missing["amount"] += 1
        if not has_biz:
            missing["biz_id"] += 1
        if not has_card:
            missing["card_marker"] += 1
        if not has_merchant:
            missing["merchant"] += 1

    return {
        "total": len(unparsed_rows),
        "by_token_count": dict(counts),
        "missing_column_estimate": dict(missing),
        "samples": [" | ".join(r)[:150] for r in unparsed_rows[:5]],
    }


def suggest_counterparty_aliases(min_similarity: float = 0.82,
                                 min_occurrences: int = 2) -> list[dict]:
    """유사한 가맹점명을 쌍으로 제안.

    로직:
    1. transactions 테이블의 counterparty를 빈도순 수집
    2. 빈도 낮은 쪽(의심)을 빈도 높은 쪽(정규형)으로 통합 제안
    3. 유사도 임계 이상 + 발생 건수 min_occurrences 이상만

    반환:
      [{"source": "GS 2S", "source_count": 3,
        "target": "GS25", "target_count": 19,
        "similarity": 0.89,
        "correction_type": "counterparty_alias"}]
    """
    with finance_conn() as conn:
        rows = conn.execute(
            """SELECT counterparty, COUNT(*) as cnt
               FROM transactions
               WHERE counterparty IS NOT NULL AND counterparty != ''
               GROUP BY counterparty ORDER BY cnt DESC"""
        ).fetchall()
    names = [(r["counterparty"], r["cnt"]) for r in rows]

    # 이미 저장된 alias는 제안에서 제외
    existing = _existing_sources("counterparty_alias")

    suggestions = []
    for i, (src, src_cnt) in enumerate(names):
        if src in existing:
            continue
        if src_cnt < min_occurrences:
            continue
        # 자기보다 빈도 높고 유사한 타겟 찾기
        for tgt, tgt_cnt in names[:i]:
            if tgt_cnt <= src_cnt:
                continue
            if _normalize_name(src) == _normalize_name(tgt):
                sim = 1.0
            else:
                sim = _similar(src, tgt)
            if sim >= min_similarity:
                suggestions.append({
                    "source": src,
                    "source_count": src_cnt,
                    "target": tgt,
                    "target_count": tgt_cnt,
                    "similarity": round(sim, 3),
                    "correction_type": "counterparty_alias",
                })
                break

    return suggestions


def suggest_card_last3_aliases(min_occurrences: int = 2) -> list[dict]:
    """카드 last3 중 OCR 오인식된 것 감지.

    '본인 039'가 200건인데 '본인 0S9'가 3건이면 동일 카드로 간주.
    숫자로만 된 카드 식별자 vs 문자 포함 식별자 비교.
    """
    with finance_conn() as conn:
        rows = conn.execute(
            """SELECT account_id, COUNT(*) as cnt
               FROM transactions
               WHERE account_id IS NOT NULL AND account_id LIKE 'card_%'
               GROUP BY account_id ORDER BY cnt DESC"""
        ).fetchall()

    # account_id에서 마지막 숫자·문자 3자리 추출
    # card_shinhan_039 → "039", card_shinhan_0S9 → "0S9"
    buckets: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in rows:
        acct = r["account_id"]
        m = re.search(r"_([^_]{3,4})$", acct)
        if not m:
            continue
        last = m.group(1)
        prefix = acct[:m.start() + 1]  # 끝 부분 제외
        # 정규화 (OCR 보정 적용)
        from jangbu_mcp.ocr import _fix_card_last3
        normalized = _fix_card_last3(last)
        buckets[prefix + normalized].append((acct, r["cnt"]))

    existing = _existing_sources("card_last3_alias")
    suggestions = []
    for canonical, variants in buckets.items():
        if len(variants) < 2:
            continue
        # 가장 빈도 높은 걸 target, 나머지를 source
        variants.sort(key=lambda x: -x[1])
        target_acct, target_cnt = variants[0]
        for src_acct, src_cnt in variants[1:]:
            if src_cnt < min_occurrences:
                continue
            if src_acct in existing:
                continue
            suggestions.append({
                "source": src_acct,
                "source_count": src_cnt,
                "target": target_acct,
                "target_count": target_cnt,
                "similarity": 1.0,
                "correction_type": "card_last3_alias",
            })
    return suggestions


# ---- alias 저장·적용 ----

def apply_alias(correction_type: str, source: str, target: str,
                approved_by: str = "user") -> dict:
    """alias 저장 + 기존 거래 갱신.

    correction_type:
      - counterparty_alias → transactions.counterparty 갱신
      - card_last3_alias   → transactions.account_id 갱신
      - biz_id_alias       → 향후 확장

    반환: {"saved": bool, "updated_transactions": N}
    """
    if correction_type not in ("counterparty_alias", "card_last3_alias", "biz_id_alias"):
        raise ValueError(f"unsupported correction_type: {correction_type}")

    updated = 0
    with finance_conn() as conn:
        # alias 저장 (중복이면 applied_count 증가)
        existing = conn.execute(
            """SELECT correction_id, applied_count FROM ocr_corrections
               WHERE correction_type=? AND source_pattern=?""",
            (correction_type, source),
        ).fetchone()

        if correction_type == "counterparty_alias":
            cur = conn.execute(
                "UPDATE transactions SET counterparty=? WHERE counterparty=?",
                (target, source),
            )
            updated = cur.rowcount
        elif correction_type == "card_last3_alias":
            cur = conn.execute(
                "UPDATE transactions SET account_id=? WHERE account_id=?",
                (target, source),
            )
            updated = cur.rowcount

        if existing:
            conn.execute(
                """UPDATE ocr_corrections
                   SET target_value=?, applied_count=applied_count+?, approved_by=?
                   WHERE correction_id=?""",
                (target, max(updated, 1), approved_by, existing["correction_id"]),
            )
        else:
            conn.execute(
                """INSERT INTO ocr_corrections
                   (correction_type, source_pattern, target_value, applied_count, approved_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (correction_type, source, target, updated, approved_by),
            )

    return {"saved": True, "updated_transactions": updated}


def list_corrections(correction_type: str | None = None) -> list[dict]:
    """저장된 alias 목록 반환."""
    sql = "SELECT * FROM ocr_corrections"
    params: list = []
    if correction_type:
        sql += " WHERE correction_type=?"
        params.append(correction_type)
    sql += " ORDER BY applied_count DESC, created_at DESC"

    with finance_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def load_alias_map(correction_type: str) -> dict[str, str]:
    """OCR 파싱 단계에서 즉시 적용할 alias 맵.

    향후 ocr.py에서 import해 파싱 직후 치환 가능.
    """
    with finance_conn() as conn:
        rows = conn.execute(
            "SELECT source_pattern, target_value FROM ocr_corrections WHERE correction_type=?",
            (correction_type,),
        ).fetchall()
    return {r["source_pattern"]: r["target_value"] for r in rows}


# ---- 내부 유틸 ----

def _existing_sources(correction_type: str) -> set[str]:
    with finance_conn() as conn:
        rows = conn.execute(
            "SELECT source_pattern FROM ocr_corrections WHERE correction_type=?",
            (correction_type,),
        ).fetchall()
    return {r["source_pattern"] for r in rows}


def summarize_for_llm(analysis: dict, cp_suggestions: list[dict],
                     card_suggestions: list[dict]) -> dict:
    """LLM 전달용 요약. 실제 거래 내용·금액·사업자번호는 포함 안함."""
    return {
        "unparsed_analysis": analysis,
        "counterparty_alias_suggestions": [
            {k: v for k, v in s.items() if k != "raw"}
            for s in cp_suggestions[:10]
        ],
        "card_alias_suggestions": [
            {k: v for k, v in s.items() if k != "raw"}
            for s in card_suggestions[:10]
        ],
        "note": "제안 승인은 apply_alias()로 호출하면 DB 저장 + 기존 거래 일괄 갱신됩니다.",
    }
