"""초기 데이터 주입 스크립트.

- accounts/nts_standard.json → account_mappings 테이블
- rules/seed_rules.json → classification_rules 테이블

사용법:
  python -m scripts.seed
"""
from __future__ import annotations

import json
from pathlib import Path

from jangbu_mcp import rules, storage


ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_JSON = ROOT / "accounts" / "nts_standard.json"
RULES_JSON = ROOT / "rules" / "seed_rules.json"


def seed_accounts() -> int:
    data = json.loads(ACCOUNTS_JSON.read_text(encoding="utf-8"))
    inserted = 0
    with storage.finance_conn() as conn:
        for m in data:
            exists = conn.execute(
                "SELECT 1 FROM account_mappings WHERE internal_account=?",
                (m["internal_account"],),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO account_mappings(internal_account, nts_account, nts_code, statement) VALUES(?,?,?,?)",
                (m["internal_account"], m["nts_account"], m.get("nts_code"), m["statement"]),
            )
            inserted += 1
    return inserted


def main() -> None:
    storage.ensure_layout()
    n_accounts = seed_accounts()
    n_rules = rules.load_seed_rules(RULES_JSON)
    print(f"accounts inserted: {n_accounts}")
    print(f"rules inserted: {n_rules}")


if __name__ == "__main__":
    main()
