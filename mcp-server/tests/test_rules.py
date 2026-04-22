import json
import tempfile
from pathlib import Path

from jangbu_mcp import rules, storage


def _seed_rules(rules_data):
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps(rules_data), encoding="utf-8")
    return tmp


def test_classify_counterparty_exact_wins():
    seed = _seed_rules([
        {"pattern_type": "counterparty_exact", "pattern": "GS25", "internal_account": "복리후생비", "priority": 10},
        {"pattern_type": "counterparty_regex", "pattern": "GS", "internal_account": "소모품비", "priority": 100},
    ])
    rules.load_seed_rules(seed)

    acct, conf = rules.classify({"counterparty": "GS25", "description": "편의점", "amount": "3000", "direction": "outflow"})
    assert acct == "복리후생비"
    assert conf == 1.00


def test_classify_amount_min_branches():
    seed = _seed_rules([
        {"pattern_type": "counterparty_regex", "pattern": "배민", "internal_account": "복리후생비", "priority": 10, "amount_max": 50000},
        {"pattern_type": "counterparty_regex", "pattern": "배민", "internal_account": "접대비", "priority": 9, "amount_min": 50000},
    ])
    rules.load_seed_rules(seed)

    acct_small, _ = rules.classify({"counterparty": "배민", "description": "점심", "amount": "15000", "direction": "outflow"})
    acct_big, _ = rules.classify({"counterparty": "배민", "description": "점심", "amount": "80000", "direction": "outflow"})
    assert acct_small == "복리후생비"
    assert acct_big == "접대비"


def test_classify_direction_filter():
    seed = _seed_rules([
        {"pattern_type": "description_regex", "pattern": "이자", "internal_account": "이자수익", "priority": 10, "direction": "inflow"},
        {"pattern_type": "description_regex", "pattern": "이자", "internal_account": "이자비용", "priority": 11, "direction": "outflow"},
    ])
    rules.load_seed_rules(seed)

    acct_in, _ = rules.classify({"counterparty": "신한은행", "description": "이자입금", "amount": "500", "direction": "inflow"})
    acct_out, _ = rules.classify({"counterparty": "신한은행", "description": "대출이자", "amount": "12000", "direction": "outflow"})
    assert acct_in == "이자수익"
    assert acct_out == "이자비용"


def test_apply_learns_user_rule():
    # 거래 하나 먼저 만들어둠
    with storage.finance_conn() as conn:
        conn.execute("""INSERT INTO transactions(
            transaction_id, date, amount, currency, direction,
            counterparty, description, source, source_ref
        ) VALUES('tx_test_1', '2025-10-01', '15000', 'KRW', 'outflow',
                 '테스트식당', '테스트식당 점심', 'card', 'ref1')""")

    result = rules.apply("tx_test_1", "복리후생비", 1.0, "user")
    assert result["learned_rule"] is True
    assert result["counterparty"] == "테스트식당"

    # 재분류 시 학습된 룰로 매칭
    acct, conf = rules.classify({"counterparty": "테스트식당", "description": "다른 날", "amount": "8000", "direction": "outflow"})
    assert acct == "복리후생비"
    assert conf == 1.00


def test_apply_no_learn_for_rule_source():
    with storage.finance_conn() as conn:
        conn.execute("""INSERT INTO transactions(
            transaction_id, date, amount, currency, direction,
            counterparty, description, source, source_ref
        ) VALUES('tx_test_2', '2025-10-01', '15000', 'KRW', 'outflow',
                 '신규거래처XYZ', '---', 'card', 'ref2')""")

    result = rules.apply("tx_test_2", "복리후생비", 0.9, "rule")
    assert result["learned_rule"] is False
