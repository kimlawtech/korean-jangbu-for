"""jangbu-mcp MCP 서버.

도구 목록:
- ingest_raw         : 엑셀/CSV 파일 적재
- ocr_document       : PaddleOCR로 영수증·세금계산서 구조화
- list_transactions  : LLM용 마스킹 뷰 반환
- get_transaction_masked : 단건 마스킹 뷰
- classify_with_rules : 룰 기반 분류 일괄 실행
- apply_classification : 분류 결과 저장
- export_report      : BS/PL/현금흐름 등 리포트 생성 (로컬 파일만)
- get_audit_log      : 감사 로그 조회

Level 2 보안 원칙:
- LLM이 접근하는 모든 뷰는 마스킹 적용
- 원본 데이터는 export_report 경로에서만 언마스킹
- 모든 도구 호출은 audit.log에 기록
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from jangbu_mcp import audit, masking, ocr, parsers, reports, rules, storage

app = Server("jangbu-mcp")


def _content(data: Any) -> list[TextContent]:
    if isinstance(data, str):
        return [TextContent(type="text", text=data)]
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ingest_raw",
            description="엑셀/CSV 파일을 표준 거래내역으로 파싱해 SQLite에 적재. LLM에 원본을 노출하지 않음.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "원본 파일 절대경로"},
                    "source_type": {"type": "string", "enum": ["bank", "card", "manual"]},
                    "account_id": {"type": "string", "description": "내부 계좌/카드 ID"},
                    "bank": {"type": "string", "default": "generic", "description": "은행 포맷 (bank일 때만)"},
                },
                "required": ["file_path", "source_type", "account_id"],
            },
        ),
        Tool(
            name="ocr_document",
            description="영수증·세금계산서·통장 스캔·카드명세서(PDF)를 PaddleOCR(로컬)로 처리. 구조화까지 수행. card_statement_scan은 신한카드 포맷 우선 지원.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "doc_type": {
                        "type": "string",
                        "enum": ["receipt", "tax_invoice", "bank_statement_scan", "card_statement_scan"],
                    },
                    "account_id": {"type": "string", "description": "카드명세서의 경우 기본 prefix. 카드별 last3가 자동 suffix로 붙음."},
                    "auto_ingest": {"type": "boolean", "default": True, "description": "영수증·카드명세서면 거래로 자동 적재"},
                },
                "required": ["file_path", "doc_type"],
            },
        ),
        Tool(
            name="list_transactions",
            description="거래내역 목록을 마스킹된 뷰로 반환. LLM 전달 전용.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "unclassified_only": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "default": 100},
                },
            },
        ),
        Tool(
            name="get_transaction_masked",
            description="단건 거래 마스킹 뷰 반환.",
            inputSchema={
                "type": "object",
                "properties": {"transaction_id": {"type": "string"}},
                "required": ["transaction_id"],
            },
        ),
        Tool(
            name="classify_with_rules",
            description="룰 기반으로 거래를 일괄 분류. 성공 건은 바로 저장. 실패 건 ID 리스트 반환.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                },
            },
        ),
        Tool(
            name="apply_classification",
            description="분류 결과 저장. source는 rule/llm/user 중 하나.",
            inputSchema={
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "internal_account": {"type": "string"},
                    "confidence": {"type": "number"},
                    "source": {"type": "string", "enum": ["rule", "llm", "user"]},
                },
                "required": ["transaction_id", "internal_account", "confidence", "source"],
            },
        ),
        Tool(
            name="export_report",
            description="BS/PL/현금흐름/cash burn 리포트를 로컬 파일로 생성. 언마스킹된 데이터 사용, 파일은 ~/.jangbu/reports/ 에만 저장.",
            inputSchema={
                "type": "object",
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": ["pl", "bs", "monthly_pl", "cash_flow", "burn_rate", "dashboard", "card_analysis"],
                    },
                    "period_start": {"type": "string"},
                    "period_end": {"type": "string"},
                    "as_of": {"type": "string"},
                    "year": {"type": "integer"},
                    "months": {"type": "integer", "default": 6},
                    "fmt": {"type": "string", "enum": ["json", "csv", "html"], "default": "json"},
                },
                "required": ["report_type"],
            },
        ),
        Tool(
            name="export_djournal",
            description="더존·세무사랑 호환 분개 CSV 출력. 세무사 전달용. 국세청 표준계정 + 부가세 간이 분리. 언마스킹 원본 기반, 로컬 파일만.",
            inputSchema={
                "type": "object",
                "properties": {
                    "period_start": {"type": "string"},
                    "period_end": {"type": "string"},
                },
                "required": ["period_start", "period_end"],
            },
        ),
        Tool(
            name="get_audit_log",
            description="감사 로그 조회. append-only.",
            inputSchema={
                "type": "object",
                "properties": {"tail": {"type": "integer", "default": 50}},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    storage.ensure_layout()

    if name == "ingest_raw":
        return await _ingest_raw(arguments)
    if name == "ocr_document":
        return await _ocr_document(arguments)
    if name == "list_transactions":
        return await _list_transactions(arguments)
    if name == "get_transaction_masked":
        return await _get_transaction_masked(arguments)
    if name == "classify_with_rules":
        return await _classify_with_rules(arguments)
    if name == "apply_classification":
        return await _apply_classification(arguments)
    if name == "export_report":
        return await _export_report(arguments)
    if name == "export_djournal":
        return await _export_djournal(arguments)
    if name == "get_audit_log":
        return await _get_audit_log(arguments)
    raise ValueError(f"unknown tool: {name}")


# ---- 구현 ----

async def _ingest_raw(args: dict) -> list[TextContent]:
    file_path = Path(args["file_path"])
    source_type = args["source_type"]
    account_id = args["account_id"]
    bank = args.get("bank", "generic")

    if source_type == "bank":
        rows = list(parsers.parse_bank_csv(file_path, account_id, bank))
    elif source_type == "card":
        rows = list(parsers.parse_card_csv(file_path, account_id))
    elif source_type == "manual":
        rows = list(parsers.parse_manual_xlsx(file_path))
    else:
        raise ValueError(f"unknown source_type: {source_type}")

    inserted = _insert_transactions(rows)
    audit.log(
        tool_name="ingest_raw",
        caller="claude",
        transaction_ids=[r["transaction_id"] for r in rows[:inserted]],
        masked=False,
        purpose=f"ingest {source_type} from {file_path.name}",
    )
    return _content({"parsed": len(rows), "inserted": inserted, "duplicates": len(rows) - inserted})


async def _ocr_document(args: dict) -> list[TextContent]:
    file_path = Path(args["file_path"])
    doc_type = args["doc_type"]
    account_id = args.get("account_id")
    auto_ingest = args.get("auto_ingest", True)

    ocr_res = ocr.run_ocr(file_path)
    structured = ocr.structure(ocr_res, doc_type)

    result: dict = {"doc_type": doc_type}

    # 자동 적재 처리
    if auto_ingest and isinstance(structured, dict):
        if doc_type == "receipt":
            tx = ocr.receipt_to_transaction(structured, account_id)
            if tx:
                inserted = _insert_transactions([tx])
                result["ingested"] = inserted
                result["ingested_transaction_id"] = tx["transaction_id"] if inserted else None

        elif doc_type == "card_statement_scan":
            txs = ocr.card_statement_to_transactions(structured, account_id)
            inserted = _insert_transactions(txs)
            result["parsed_rows"] = len(txs)
            result["ingested"] = inserted
            result["duplicates_skipped"] = len(txs) - inserted
            result["issuer"] = structured.get("issuer")
            result["period"] = {
                "start": structured.get("period_start"),
                "end": structured.get("period_end"),
            }
            result["card_last3_list"] = structured.get("card_last3_list", [])
            result["unparsed_count"] = structured.get("unparsed_count", 0)
            result["needs_llm_fallback"] = structured.get("needs_llm_fallback", False)

    # 구조화 결과에 민감정보가 포함될 수 있으므로 응답도 마스킹
    if isinstance(structured, dict):
        for k in ("raw_text", "merchant"):
            if structured.get(k):
                structured[k] = masking.mask_text(structured[k])
        # 카드명세서 행별 merchant/biz_id도 마스킹 적용된 요약만 반환
        if doc_type == "card_statement_scan" and "rows" in structured:
            # 원본 rows는 응답에서 제거 (이미 DB 적재됨). 샘플 3행만 마스킹 뷰로 노출.
            sample = structured["rows"][:3]
            masked_sample = []
            for r in sample:
                masked_sample.append({
                    "use_date": r.get("use_date"),
                    "merchant": masking.mask_text(r.get("merchant", "")),
                    "amount": r.get("amount"),
                    "biz_id": masking.tokenize(r["biz_id"], "biz_id") if r.get("biz_id") else None,
                    "card_last3": r.get("card_last3"),
                })
            structured["rows_sample"] = masked_sample
            del structured["rows"]

    # structured는 요약만 포함 (raw_text 제거로 응답 경량화)
    if isinstance(structured, dict) and doc_type == "card_statement_scan":
        structured.pop("raw_text", None)

    result["structured"] = structured

    audit.log(
        tool_name="ocr_document",
        caller="claude",
        masked=True,
        purpose=f"ocr {doc_type} from {file_path.name}",
    )
    return _content(result)


async def _list_transactions(args: dict) -> list[TextContent]:
    start = args.get("start_date")
    end = args.get("end_date")
    unclassified = args.get("unclassified_only", False)
    limit = args.get("limit", 100)

    sql = "SELECT * FROM transactions WHERE 1=1"
    params: list = []
    if start:
        sql += " AND date >= ?"; params.append(start)
    if end:
        sql += " AND date <= ?"; params.append(end)
    if unclassified:
        sql += " AND matched_account IS NULL"
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    with storage.finance_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    masked = [masking.mask_transaction_row(r) for r in rows]

    audit.log(
        tool_name="list_transactions",
        caller="claude",
        transaction_ids=[r["transaction_id"] for r in rows],
        masked=True,
        purpose=f"list {len(rows)} transactions",
    )
    return _content(masked)


async def _get_transaction_masked(args: dict) -> list[TextContent]:
    tx_id = args["transaction_id"]
    with storage.finance_conn() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE transaction_id=?", (tx_id,)).fetchone()
    if not row:
        return _content({"error": "not found"})
    masked = masking.mask_transaction_row(dict(row))
    audit.log(
        tool_name="get_transaction_masked",
        caller="claude",
        transaction_ids=[tx_id],
        masked=True,
    )
    return _content(masked)


async def _classify_with_rules(args: dict) -> list[TextContent]:
    start = args.get("start_date")
    end = args.get("end_date")

    sql = "SELECT * FROM transactions WHERE matched_account IS NULL"
    params: list = []
    if start:
        sql += " AND date >= ?"; params.append(start)
    if end:
        sql += " AND date <= ?"; params.append(end)

    with storage.finance_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    classified = []
    unclassified = []
    for r in rows:
        acct, conf = rules.classify(r)
        if acct:
            rules.apply(r["transaction_id"], acct, conf, "rule")
            classified.append(r["transaction_id"])
        else:
            unclassified.append(r["transaction_id"])

    audit.log(
        tool_name="classify_with_rules",
        caller="claude",
        transaction_ids=classified + unclassified,
        masked=False,
        purpose=f"rule-based classify: {len(classified)} ok, {len(unclassified)} need fallback",
    )
    return _content({
        "total": len(rows),
        "classified": len(classified),
        "unclassified_ids": unclassified,
    })


async def _apply_classification(args: dict) -> list[TextContent]:
    rules.apply(
        args["transaction_id"],
        args["internal_account"],
        args["confidence"],
        args["source"],
    )
    audit.log(
        tool_name="apply_classification",
        caller="claude",
        transaction_ids=[args["transaction_id"]],
        masked=False,
        purpose=f"classify via {args['source']} → {args['internal_account']}",
    )
    return _content({"ok": True})


async def _export_report(args: dict) -> list[TextContent]:
    rt = args["report_type"]
    fmt = args.get("fmt", "json")

    if rt == "pl":
        data = reports.build_pl(args["period_start"], args["period_end"])
    elif rt == "bs":
        data = reports.build_bs(args["as_of"])
    elif rt == "monthly_pl":
        data = reports.build_monthly_pl(args["year"])
    elif rt == "cash_flow":
        data = reports.build_cash_flow(args["period_start"], args["period_end"])
    elif rt == "burn_rate":
        data = reports.build_burn_rate(args.get("months", 6))
    elif rt == "dashboard":
        data = reports.build_dashboard(args.get("year") or date.today().year)
    elif rt == "card_analysis":
        data = reports.build_card_analysis(args["period_start"], args["period_end"])
    else:
        raise ValueError(f"unknown report_type: {rt}")

    path = reports.export(rt, data, fmt)

    # LLM에는 요약과 파일 경로만 반환 (전체 원본은 전달하지 않음)
    summary: dict = {"report_type": rt, "output_path": str(path)}
    if "summary" in data:
        summary["summary"] = data["summary"]
    elif "avg_monthly_burn" in data:
        summary["avg_monthly_burn"] = data["avg_monthly_burn"]
    elif "net_cash_flow" in data:
        summary["net_cash_flow"] = data["net_cash_flow"]

    audit.log(
        tool_name="export_report",
        caller="claude",
        masked=False,
        purpose=f"export {rt} → {path.name}",
    )
    return _content(summary)


async def _export_djournal(args: dict) -> list[TextContent]:
    path = reports.export_djournal_csv(args["period_start"], args["period_end"])
    audit.log(
        tool_name="export_djournal",
        caller="claude",
        masked=False,
        purpose=f"djournal export {args['period_start']} ~ {args['period_end']} → {path.name}",
    )
    return _content({
        "output_path": str(path),
        "note": "더존·세무사랑 호환. 부가세는 10% 간이 분리 — 과세/면세 정확도는 세무사 검토 필수.",
    })


async def _get_audit_log(args: dict) -> list[TextContent]:
    tail = args.get("tail", 50)
    if not storage.AUDIT_LOG.exists():
        return _content([])
    with storage.AUDIT_LOG.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = [json.loads(l) for l in lines[-tail:]]
    return _content(entries)


def _insert_transactions(rows: list[dict]) -> int:
    if not rows:
        return 0
    inserted = 0
    with storage.finance_conn() as conn:
        for r in rows:
            try:
                conn.execute(
                    """
                    INSERT INTO transactions(
                        transaction_id, date, amount, currency, direction,
                        counterparty, description, source, source_ref,
                        raw_description, account_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        r["transaction_id"], r["date"], r["amount"], r.get("currency", "KRW"),
                        r["direction"], r["counterparty"], r["description"],
                        r["source"], r["source_ref"],
                        r.get("raw_description"), r.get("account_id"),
                    ),
                )
                inserted += 1
            except Exception:
                # UNIQUE 위반(중복) 등은 스킵
                continue
    return inserted


def main() -> None:
    storage.ensure_layout()
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
