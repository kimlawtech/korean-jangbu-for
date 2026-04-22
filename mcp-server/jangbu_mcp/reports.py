"""재무제표·경영리포트 생성 모듈.

- BS (재무상태표) / PL (손익계산서) — 국세청 표준계정 기준
- 경영 리포트: 월별 손익, 현금 flow, cash burn, 비용 구조

언마스킹된 원본 데이터로 직접 집계. 결과는 로컬 파일로만 출력(LLM 미경유).
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from jangbu_mcp.storage import BASE_DIR, finance_conn


def _load_account_mappings() -> dict[str, dict]:
    """internal_account → {nts_account, statement}"""
    with finance_conn() as conn:
        rows = conn.execute(
            "SELECT internal_account, nts_account, nts_code, statement FROM account_mappings"
        ).fetchall()
    return {
        r["internal_account"]: {
            "nts_account": r["nts_account"],
            "nts_code": r["nts_code"],
            "statement": r["statement"],
        }
        for r in rows
    }


def _fetch_period(period_start: str, period_end: str) -> list[dict]:
    with finance_conn() as conn:
        rows = conn.execute(
            """
            SELECT transaction_id, date, amount, direction, counterparty,
                   description, matched_account
            FROM transactions
            WHERE date >= ? AND date <= ?
              AND matched_account IS NOT NULL
            """,
            (period_start, period_end),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- PL (손익계산서) ----

def build_pl(period_start: str, period_end: str) -> dict:
    mappings = _load_account_mappings()
    txs = _fetch_period(period_start, period_end)

    pl_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    revenue = Decimal("0")
    expense = Decimal("0")

    for t in txs:
        m = mappings.get(t["matched_account"])
        if not m or m["statement"] != "PL":
            continue
        amt = Decimal(t["amount"])
        nts = m["nts_account"]
        pl_totals[nts] += amt
        if t["direction"] == "inflow":
            revenue += amt
        else:
            expense += amt

    return {
        "period": {"start": period_start, "end": period_end},
        "totals": {k: str(v) for k, v in pl_totals.items()},
        "summary": {
            "revenue": str(revenue),
            "expense": str(expense),
            "net_income": str(revenue - expense),
        },
    }


# ---- BS (재무상태표) — 간이버전 ----

def build_bs(as_of: str) -> dict:
    """기초부터 as_of까지 누적 집계. 간이 BS.

    한계: 분개 엔진이 없으면 정식 BS 생성이 불가. 여기서는 계정별
    잔액 집계에 그친다 (가용 현금·미수금·미지급금 등 대표 항목만).
    """
    mappings = _load_account_mappings()
    with finance_conn() as conn:
        rows = conn.execute(
            """
            SELECT matched_account, direction, amount
            FROM transactions
            WHERE date <= ? AND matched_account IS NOT NULL
            """,
            (as_of,),
        ).fetchall()

    balances: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for r in rows:
        m = mappings.get(r["matched_account"])
        if not m or m["statement"] != "BS":
            continue
        amt = Decimal(r["amount"])
        if r["direction"] == "inflow":
            balances[m["nts_account"]] += amt
        else:
            balances[m["nts_account"]] -= amt

    return {
        "as_of": as_of,
        "balances": {k: str(v) for k, v in balances.items()},
    }


# ---- 경영 리포트 ----

def build_monthly_pl(year: int) -> dict:
    """월별 손익(간이)."""
    mappings = _load_account_mappings()
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    txs = _fetch_period(start, end)

    monthly: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"revenue": Decimal("0"), "expense": Decimal("0")}
    )
    for t in txs:
        m = mappings.get(t["matched_account"])
        if not m or m["statement"] != "PL":
            continue
        month = t["date"][:7]
        amt = Decimal(t["amount"])
        if t["direction"] == "inflow":
            monthly[month]["revenue"] += amt
        else:
            monthly[month]["expense"] += amt

    return {
        "year": year,
        "monthly": {
            k: {
                "revenue": str(v["revenue"]),
                "expense": str(v["expense"]),
                "net": str(v["revenue"] - v["expense"]),
            }
            for k, v in sorted(monthly.items())
        },
    }


def build_cash_flow(period_start: str, period_end: str) -> dict:
    """간이 현금흐름 — 실제 현금 입출만 집계(분개 미사용)."""
    with finance_conn() as conn:
        rows = conn.execute(
            """
            SELECT date, direction, amount
            FROM transactions
            WHERE date >= ? AND date <= ?
            """,
            (period_start, period_end),
        ).fetchall()

    inflow = Decimal("0")
    outflow = Decimal("0")
    daily: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for r in rows:
        amt = Decimal(r["amount"])
        if r["direction"] == "inflow":
            inflow += amt
            daily[r["date"]] += amt
        else:
            outflow += amt
            daily[r["date"]] -= amt

    return {
        "period": {"start": period_start, "end": period_end},
        "inflow": str(inflow),
        "outflow": str(outflow),
        "net_cash_flow": str(inflow - outflow),
        "daily_net": {k: str(v) for k, v in sorted(daily.items())},
    }


def build_burn_rate(months: int = 6) -> dict:
    """최근 N개월 평균 월별 순지출 = cash burn."""
    today = date.today()
    # 간단 계산: 최근 N개월의 outflow - inflow 평균
    with finance_conn() as conn:
        rows = conn.execute(
            """
            SELECT substr(date, 1, 7) as month, direction, SUM(CAST(amount AS REAL)) as total
            FROM transactions
            WHERE date >= date('now', ?)
            GROUP BY month, direction
            """,
            (f"-{months} months",),
        ).fetchall()

    monthly: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"inflow": Decimal("0"), "outflow": Decimal("0")}
    )
    for r in rows:
        monthly[r["month"]][r["direction"]] = Decimal(str(r["total"]))

    burns = []
    for month, v in sorted(monthly.items()):
        burns.append(v["outflow"] - v["inflow"])

    avg = sum(burns) / len(burns) if burns else Decimal("0")
    return {
        "months_analyzed": months,
        "monthly_burn": [str(b) for b in burns],
        "avg_monthly_burn": str(avg),
    }


# ---- 출력 ----

def _report_dir() -> Path:
    d = BASE_DIR / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def export_djournal_csv(period_start: str, period_end: str) -> Path:
    """더존·세무사랑 호환 분개 CSV 출력.

    컬럼: 일자 / 구분 / 계정과목 / 거래처 / 적요 / 공급가액 / 부가세 / 합계
    구분: 출금/입금/대체
    부가세는 단순 10% 추정 (정확한 과세/면세 판단은 세무사 검토).

    ※ 이 CSV는 언마스킹된 원본으로 생성. 로컬 파일에만 저장.
    """
    mappings = _load_account_mappings()
    with finance_conn() as conn:
        rows = conn.execute(
            """SELECT date, direction, amount, counterparty, description,
                      matched_account
               FROM transactions
               WHERE date >= ? AND date <= ?
                 AND matched_account IS NOT NULL
               ORDER BY date""",
            (period_start, period_end),
        ).fetchall()

    path = _report_dir() / f"journal_{period_start}_{period_end}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["일자", "구분", "계정과목", "NTS코드", "거래처", "적요", "공급가액", "부가세", "합계"])
        for r in rows:
            m = mappings.get(r["matched_account"], {})
            nts_account = m.get("nts_account", r["matched_account"])
            nts_code = m.get("nts_code", "")
            total = Decimal(r["amount"])
            # 간이 부가세: 부가세 포함 가정, 합계 / 11 = VAT, 합계 - VAT = 공급가액
            # 면세 계정은 VAT 0
            vat_exempt_accounts = {"급여", "상여금", "퇴직급여", "세금과공과", "이자비용", "이자수익", "법인세비용", "보험료"}
            if nts_account in vat_exempt_accounts:
                vat = Decimal("0")
                supply = total
            else:
                vat = (total / Decimal("11")).quantize(Decimal("1"))
                supply = total - vat

            gubun = "출금" if r["direction"] == "outflow" else "입금"
            w.writerow([
                r["date"], gubun, nts_account, nts_code,
                r["counterparty"], r["description"],
                int(supply), int(vat), int(total),
            ])
    return path


def export(report_type: str, report_data: dict, fmt: str = "json") -> Path:
    """리포트를 로컬 파일로 저장.

    fmt: json / csv / html
    파일은 ~/.jangbu/reports/ 에 저장.
    """
    ts = date.today().isoformat()
    path = _report_dir() / f"{report_type}_{ts}.{fmt}"

    if fmt == "json":
        path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "csv":
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["key", "value"])
            def _flat(prefix: str, obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        _flat(f"{prefix}.{k}" if prefix else k, v)
                else:
                    w.writerow([prefix, obj])
            _flat("", report_data)
    elif fmt == "html":
        path.write_text(_render_html(report_type, report_data), encoding="utf-8")
    else:
        raise ValueError(f"unsupported format: {fmt}")

    return path


def build_card_analysis(period_start: str, period_end: str) -> dict:
    """카드별 이용 분석.

    여러 장의 카드(신한039·신한122·KB1234 등)를 사용 중일 때
    카드별 이용액·계정 분포·월별 추이를 한 번에 제공.
    """
    with finance_conn() as conn:
        rows = conn.execute(
            """SELECT account_id, matched_account, amount, direction, date
               FROM transactions
               WHERE date >= ? AND date <= ?
                 AND source = 'ocr'
                 AND account_id IS NOT NULL""",
            (period_start, period_end),
        ).fetchall()

    # 카드별 집계
    cards: dict[str, dict] = {}
    for r in rows:
        card = r["account_id"]
        if card not in cards:
            cards[card] = {
                "account_id": card,
                "count": 0,
                "total_outflow": Decimal("0"),
                "total_inflow": Decimal("0"),
                "by_account": defaultdict(lambda: Decimal("0")),
                "by_month": defaultdict(lambda: Decimal("0")),
            }
        amt = Decimal(str(r["amount"]))
        cards[card]["count"] += 1
        if r["direction"] == "outflow":
            cards[card]["total_outflow"] += amt
        else:
            cards[card]["total_inflow"] += amt
        if r["matched_account"]:
            cards[card]["by_account"][r["matched_account"]] += amt
        month = r["date"][:7]
        cards[card]["by_month"][month] += amt if r["direction"] == "outflow" else -amt

    # 정렬용 요약
    summary = []
    for card, data in cards.items():
        summary.append({
            "account_id": card,
            "count": data["count"],
            "total_outflow": str(data["total_outflow"]),
            "total_inflow": str(data["total_inflow"]),
            "net_spending": str(data["total_outflow"] - data["total_inflow"]),
            "by_account": {k: str(v) for k, v in sorted(
                data["by_account"].items(), key=lambda x: -x[1]
            )[:10]},
            "by_month": {k: str(v) for k, v in sorted(data["by_month"].items())},
        })
    summary.sort(key=lambda x: -Decimal(x["total_outflow"]))

    return {
        "period": {"start": period_start, "end": period_end},
        "card_count": len(cards),
        "cards": summary,
    }


def build_dashboard(year: int) -> dict:
    """경영 대시보드용 종합 데이터.

    연간 PL + 월별 추이 + 현금흐름 + 계정별 비용 Top 10 + 최근 cash burn.
    """
    mappings = _load_account_mappings()
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    pl = build_pl(start, end)
    monthly = build_monthly_pl(year)
    cash = build_cash_flow(start, end)
    burn = build_burn_rate(6)
    cards = build_card_analysis(start, end)

    # 계정별 비용 Top 10
    with finance_conn() as conn:
        rows = conn.execute(
            """SELECT matched_account, SUM(CAST(amount AS REAL)) as total, COUNT(*) as cnt
               FROM transactions
               WHERE date >= ? AND date <= ?
                 AND direction = 'outflow'
                 AND matched_account IS NOT NULL
               GROUP BY matched_account
               ORDER BY total DESC
               LIMIT 10""",
            (start, end),
        ).fetchall()
    top_expenses = [
        {"account": r["matched_account"], "total": r["total"], "count": r["cnt"]}
        for r in rows
    ]

    return {
        "year": year,
        "pl_summary": pl.get("summary", {}),
        "monthly": monthly.get("monthly", {}),
        "cash_flow": {
            "inflow": cash.get("inflow"),
            "outflow": cash.get("outflow"),
            "net": cash.get("net_cash_flow"),
        },
        "burn": {
            "avg_monthly_burn": burn.get("avg_monthly_burn"),
            "monthly_burn": burn.get("monthly_burn"),
        },
        "top_expenses": top_expenses,
        "card_analysis": cards,
    }


def _render_html(report_type: str, data: dict) -> str:
    """HTML 대시보드 렌더링. 외부 라이브러리 없이 순수 HTML+CSS+SVG."""
    if report_type == "dashboard":
        return _render_dashboard_html(data)
    # 기본: 간이 표 렌더링
    return _render_generic_html(report_type, data)


def _render_generic_html(title: str, data: dict) -> str:
    rows = _flatten_for_html("", data)
    body_rows = "".join(f"<tr><td>{k}</td><td class='v'>{v}</td></tr>" for k, v in rows)
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:-apple-system,'Pretendard',sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#1a1a1a}}
h1{{border-bottom:2px solid #000;padding-bottom:10px}}
table{{width:100%;border-collapse:collapse;margin-top:20px}}
td{{padding:8px 12px;border-bottom:1px solid #e5e5e5}}
td.v{{text-align:right;font-variant-numeric:tabular-nums}}
</style></head><body>
<h1>{title}</h1>
<table>{body_rows}</table>
<p style='color:#888;margin-top:40px;font-size:12px'>생성: korean-jangbu-for · 참고용 초안 (세무사 검토 필수)</p>
</body></html>"""


def _flatten_for_html(prefix: str, obj, out=None) -> list:
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_for_html(f"{prefix}.{k}" if prefix else k, v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten_for_html(f"{prefix}[{i}]", v, out)
    else:
        out.append((prefix, obj))
    return out


def _render_dashboard_html(data: dict) -> str:
    year = data["year"]
    pl = data.get("pl_summary", {})
    monthly = data.get("monthly", {})
    top_exp = data.get("top_expenses", [])
    burn = data.get("burn", {})

    # 월별 차트 (SVG 바 차트)
    max_val = max(
        (float(v.get("revenue", 0)) for v in monthly.values()),
        default=1,
    )
    max_val = max(max_val, max(
        (float(v.get("expense", 0)) for v in monthly.values()),
        default=1,
    ), 1)

    bars = ""
    months = sorted(monthly.keys())
    bar_w = 40
    gap = 20
    chart_h = 240
    x = 60
    for m in months:
        rev = float(monthly[m].get("revenue", 0))
        exp = float(monthly[m].get("expense", 0))
        rh = (rev / max_val) * (chart_h - 40) if max_val > 0 else 0
        eh = (exp / max_val) * (chart_h - 40) if max_val > 0 else 0
        bars += f"""
  <rect x="{x}" y="{chart_h - 20 - rh}" width="{bar_w/2-2}" height="{rh}" fill="#10b981"/>
  <rect x="{x + bar_w/2}" y="{chart_h - 20 - eh}" width="{bar_w/2-2}" height="{eh}" fill="#ef4444"/>
  <text x="{x + bar_w/2}" y="{chart_h - 5}" text-anchor="middle" font-size="11" fill="#666">{m[5:]}</text>
"""
        x += bar_w + gap

    chart_w = max(x + 60, 800)

    # Top expenses 바
    exp_rows = ""
    max_exp = max((e["total"] for e in top_exp), default=1)
    for e in top_exp:
        pct = (e["total"] / max_exp) * 100 if max_exp > 0 else 0
        exp_rows += f"""
<tr>
  <td>{e['account']}</td>
  <td class='v'>{e['count']}건</td>
  <td class='v'>{_fmt_krw(e['total'])}</td>
  <td><div class='bar' style='width:{pct:.1f}%'></div></td>
</tr>"""

    # 카드별 분석
    card_analysis = data.get("card_analysis", {})
    card_rows = ""
    max_card_total = 1
    for c in card_analysis.get("cards", []):
        try:
            max_card_total = max(max_card_total, float(c["total_outflow"]))
        except Exception:
            pass
    for c in card_analysis.get("cards", []):
        try:
            outflow = float(c["total_outflow"])
        except Exception:
            outflow = 0
        pct = (outflow / max_card_total) * 100 if max_card_total > 0 else 0
        top_acc = list(c.get("by_account", {}).items())[:3]
        top_acc_str = ", ".join(f"{k}({_fmt_krw(v)})" for k, v in top_acc) or "-"
        card_rows += f"""
<tr>
  <td>{c['account_id']}</td>
  <td class='v'>{c['count']}건</td>
  <td class='v'>{_fmt_krw(c['total_outflow'])}</td>
  <td>{top_acc_str}</td>
  <td><div class='bar' style='width:{pct:.1f}%;background:#f59e0b'></div></td>
</tr>"""

    card_section = ""
    if card_analysis.get("card_count", 0) > 0:
        card_section = f"""
<h2>카드별 사용 분석 ({card_analysis.get('card_count', 0)}장)</h2>
<table>
  <thead><tr><th>카드</th><th class="v">건수</th><th class="v">사용액</th><th>주요 계정 Top3</th><th>비중</th></tr></thead>
  <tbody>{card_rows}</tbody>
</table>"""

    # 월별 상세 표
    monthly_rows = ""
    for m in months:
        v = monthly[m]
        monthly_rows += f"""
<tr>
  <td>{m}</td>
  <td class='v'>{_fmt_krw(v.get('revenue',0))}</td>
  <td class='v'>{_fmt_krw(v.get('expense',0))}</td>
  <td class='v {'pos' if float(v.get('net',0))>=0 else 'neg'}'>{_fmt_krw(v.get('net',0))}</td>
</tr>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>{year} 경영 대시보드</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:-apple-system,'Pretendard',sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#111;background:#fff}}
  h1{{font-size:28px;margin:0 0 8px;border-bottom:3px solid #000;padding-bottom:12px}}
  h2{{font-size:18px;margin:40px 0 12px;color:#000}}
  .meta{{color:#666;font-size:13px;margin-bottom:24px}}
  .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}}
  .card{{padding:16px;background:#f6f7f9;border-radius:8px}}
  .card .label{{font-size:12px;color:#666;margin-bottom:4px}}
  .card .value{{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}}
  .pos{{color:#059669}}
  .neg{{color:#dc2626}}
  table{{width:100%;border-collapse:collapse;margin-top:8px}}
  th,td{{padding:10px;border-bottom:1px solid #e5e5e5;text-align:left;font-size:14px}}
  th{{background:#fafafa;font-weight:600;color:#555}}
  td.v{{text-align:right;font-variant-numeric:tabular-nums}}
  .bar{{background:#2563eb;height:10px;border-radius:3px}}
  svg{{display:block;margin:12px 0;max-width:100%;height:auto}}
  .legend{{display:flex;gap:16px;font-size:12px;color:#555;margin-top:4px}}
  .swatch{{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:middle;border-radius:2px}}
  .disclaimer{{color:#888;margin-top:48px;font-size:12px;border-top:1px solid #e5e5e5;padding-top:16px}}
</style></head><body>

<h1>{year} 경영 대시보드</h1>
<p class="meta">생성일: {date.today().isoformat()} · korean-jangbu-for</p>

<div class="cards">
  <div class="card">
    <div class="label">총 매출</div>
    <div class="value pos">{_fmt_krw(pl.get('revenue', 0))}</div>
  </div>
  <div class="card">
    <div class="label">총 비용</div>
    <div class="value neg">{_fmt_krw(pl.get('expense', 0))}</div>
  </div>
  <div class="card">
    <div class="label">순이익</div>
    <div class="value {'pos' if float(pl.get('net_income', 0)) >= 0 else 'neg'}">{_fmt_krw(pl.get('net_income', 0))}</div>
  </div>
  <div class="card">
    <div class="label">월평균 Cash Burn</div>
    <div class="value neg">{_fmt_krw(burn.get('avg_monthly_burn', 0))}</div>
  </div>
</div>

<h2>월별 손익</h2>
<svg viewBox="0 0 {chart_w} {chart_h}" xmlns="http://www.w3.org/2000/svg">
  <line x1="50" y1="{chart_h-20}" x2="{chart_w-20}" y2="{chart_h-20}" stroke="#ccc"/>
  {bars}
</svg>
<div class="legend">
  <span><span class="swatch" style="background:#10b981"></span>수익</span>
  <span><span class="swatch" style="background:#ef4444"></span>비용</span>
</div>

<table>
  <thead><tr><th>월</th><th class="v">수익</th><th class="v">비용</th><th class="v">순이익</th></tr></thead>
  <tbody>{monthly_rows}</tbody>
</table>

<h2>비용 Top 10 계정</h2>
<table>
  <thead><tr><th>계정</th><th class="v">건수</th><th class="v">금액</th><th>비중</th></tr></thead>
  <tbody>{exp_rows}</tbody>
</table>

{card_section}

<p class="disclaimer">
  본 리포트는 korean-jangbu-for로 자동 생성된 참고용 초안입니다.<br>
  법인세 신고 전 세무사 검토 필수 · 외감 대상(자산 120억 이상)은 공인회계사 감사 필수<br>
  일반기업회계기준(K-GAAP) 간소화 버전, 주석·감가상각·부가세 상계 미반영
</p>
</body></html>"""


def _fmt_krw(v) -> str:
    try:
        n = float(v)
        return f"{n:,.0f}원"
    except Exception:
        return str(v)
