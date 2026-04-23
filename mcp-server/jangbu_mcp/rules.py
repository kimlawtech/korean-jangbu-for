"""룰 기반 분류 엔진.

우선순위:
1. counterparty_exact (정확히 일치)
2. counterparty_regex (정규식)
3. description_regex
4. mcc (카드 MCC 코드)

룰 실패 시 confidence=0, matched_account=None 반환.
호출자는 LLM fallback 여부를 결정.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from jangbu_mcp.storage import finance_conn


def load_seed_rules(seed_path: Path) -> int:
    """seed_rules.json을 classification_rules 테이블에 주입. 이미 있으면 스킵.

    JSON 스키마:
      {
        "pattern_type": "counterparty_regex",
        "pattern": "배달의민족",
        "internal_account": "접대비",
        "priority": 10,
        "amount_min": 50000,   // 선택
        "amount_max": null,    // 선택
        "direction": "outflow" // 선택
      }
    """
    if not seed_path.exists():
        return 0
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    inserted = 0
    with finance_conn() as conn:
        for r in data:
            exists = conn.execute(
                """SELECT 1 FROM classification_rules
                   WHERE pattern_type=? AND pattern=? AND internal_account=?
                     AND IFNULL(amount_min,-1)=? AND IFNULL(amount_max,-1)=?""",
                (r["pattern_type"], r["pattern"], r["internal_account"],
                 r.get("amount_min", -1), r.get("amount_max", -1)),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO classification_rules(
                    pattern_type, pattern, internal_account, priority,
                    amount_min, amount_max, direction
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    r["pattern_type"], r["pattern"], r["internal_account"],
                    r.get("priority", 100),
                    r.get("amount_min"), r.get("amount_max"), r.get("direction"),
                ),
            )
            inserted += 1
    return inserted


def classify(transaction: dict) -> tuple[str | None, float]:
    """거래 1건을 룰에 매칭. (internal_account, confidence) 반환.

    confidence:
      1.00 - counterparty_exact
      0.90 - counterparty_regex
      0.80 - description_regex
      0.70 - mcc
      0.00 - 매칭 없음 (LLM fallback 필요)

    복합 조건(amount_min·amount_max·direction) 있는 룰은 조건까지 만족해야 적용.
    """
    counterparty = (transaction.get("counterparty") or "").strip()
    description = (transaction.get("description") or "").strip()
    amount = float(transaction.get("amount") or 0)
    direction = transaction.get("direction")

    with finance_conn() as conn:
        rows = conn.execute(
            """SELECT pattern_type, pattern, internal_account,
                      amount_min, amount_max, direction
               FROM classification_rules
               ORDER BY priority ASC"""
        ).fetchall()

    for r in rows:
        pt = r["pattern_type"]
        pat = r["pattern"]
        acct = r["internal_account"]

        # 금액/방향 조건 필터
        if r["amount_min"] is not None and amount < r["amount_min"]:
            continue
        if r["amount_max"] is not None and amount > r["amount_max"]:
            continue
        if r["direction"] and r["direction"] != direction:
            continue

        try:
            if pt == "counterparty_exact" and counterparty == pat:
                return acct, 1.00
            if pt == "counterparty_regex" and re.search(pat, counterparty):
                return acct, 0.90
            if pt == "description_regex" and re.search(pat, description):
                return acct, 0.80
        except re.error:
            # 잘못된 정규식 룰은 스킵하고 다음으로
            continue

    return None, 0.00


def apply(transaction_id: str, account: str, confidence: float, source: str,
          learn: bool = True) -> dict:
    """분류 결과를 transactions 테이블에 반영.

    source: rule / llm / user
    learn: source='user'일 때 counterparty_exact 룰 자동 등록 여부.
           같은 counterparty 재등장 시 룰로 자동 분류됨.

    반환: {"learned_rule": bool, "counterparty": str | None}
    """
    learned = False
    counterparty = None

    with finance_conn() as conn:
        conn.execute(
            "UPDATE transactions SET matched_account=?, confidence=? WHERE transaction_id=?",
            (account, confidence, transaction_id),
        )

        # 사용자가 확정한 분류는 룰로 학습
        if learn and source == "user":
            row = conn.execute(
                "SELECT counterparty FROM transactions WHERE transaction_id=?",
                (transaction_id,),
            ).fetchone()
            if row and row["counterparty"]:
                counterparty = row["counterparty"].strip()
                # 이미 같은 룰이 있으면 스킵
                exists = conn.execute(
                    """SELECT 1 FROM classification_rules
                       WHERE pattern_type='counterparty_exact'
                         AND pattern=? AND internal_account=?""",
                    (counterparty, account),
                ).fetchone()
                if not exists:
                    conn.execute(
                        """INSERT INTO classification_rules
                           (pattern_type, pattern, internal_account, priority)
                           VALUES('counterparty_exact', ?, ?, 5)""",
                        (counterparty, account),
                    )
                    learned = True

    return {"learned_rule": learned, "counterparty": counterparty}
