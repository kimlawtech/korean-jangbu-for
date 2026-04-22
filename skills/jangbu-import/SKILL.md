---
name: jangbu-import
description: 원본 재무 데이터(엑셀·은행 CSV·카드 내역 CSV·영수증 이미지·세금계산서 PDF·카드명세서 PDF)를 표준 거래내역 13개 필드로 변환하는 스킬. PaddleOCR 로컬 처리로 영수증·세금계산서·카드명세서에서 거래 자동 추출, Level 2 민감정보 마스킹 적용.
---

# jangbu-import

원본 파일 → 표준 거래내역 변환 스킬.

## 입력 지원

| 소스 | 포맷 | MCP 도구 |
|------|------|---------|
| 은행 거래내역 | CSV | `ingest_raw(source_type="bank")` |
| 카드 거래내역 | CSV | `ingest_raw(source_type="card")` |
| 수기 장부 | 엑셀 | `ingest_raw(source_type="manual")` |
| 영수증 | JPG/PNG/PDF | `ocr_document(doc_type="receipt")` |
| 세금계산서 | PDF | `ocr_document(doc_type="tax_invoice")` |
| 통장 스캔 | JPG/PDF | `ocr_document(doc_type="bank_statement_scan")` |
| 카드사 회원이용내역서 | PDF | `ocr_document(doc_type="card_statement_scan")` |

**카드명세서 지원 현황**:
- 신한카드 회원이용내역서 — 완전 지원 (표 파서 구현)
- 국민·삼성·현대·롯데·우리·하나·BC카드 — 발급사 자동 탐지만 지원, 파서는 LLM fallback

## 표준 스키마 (13개 필드)

필수 9 + 확장 4.

- **필수**: `transaction_id`, `date`, `amount`, `currency`, `direction`, `counterparty`, `description`, `source`, `source_ref`
- **확장**: `raw_description`, `account_id`, `matched_account`, `confidence`

## 인터뷰 플로우

### Step 1. 입력 소스 확인

```
어떤 데이터를 추가하시겠습니까?

[1] 은행 거래내역 (CSV)
[2] 카드 거래내역 (CSV)
[3] 엑셀 장부 (xlsx)
[4] 영수증 이미지/PDF (PaddleOCR 처리)
[5] 세금계산서 PDF (PaddleOCR 처리)
[6] 카드사 회원이용내역서 PDF (PaddleOCR + 표 파서)
[7] 여러 파일 일괄 업로드

번호를 입력하세요.
```

**[6] 카드명세서 PDF 선택 시 경고**:
```
⚠ 카드사 회원이용내역서에는 주민번호·사업자번호 등 민감정보가
포함되어 있습니다. 처리 전 확인:

1. 본인 소유 문서가 맞습니까? [Y/n]
2. 파일은 ~/.jangbu/raw/ocr/ 에만 저장되며
   LLM에는 마스킹 뷰와 요약만 전달됩니다.
```

### Step 2. 파일 경로·메타데이터 수집

- 파일 경로 (절대경로 권장)
- `account_id` (내부 계좌/카드 ID, 예: `acct_shinhan_001`, `card_bc_002`)
- 은행이면 은행명 (generic / kb / shinhan / woori / hana / ibk)

### Step 3. MCP 서버 호출

**CSV/엑셀 (선택지 1·2·3):**
```python
ingest_raw(
    file_path="/절대/경로/파일.csv",
    source_type="bank",  # or card / manual
    account_id="acct_shinhan_001",
    bank="shinhan"  # bank일 때만
)
```

**OCR (선택지 4·5·6·7):**
```python
ocr_document(
    file_path="/절대/경로/영수증.jpg",
    doc_type="receipt",  # receipt / tax_invoice / bank_statement_scan / card_statement_scan
    account_id="card_bc_002",  # 카드명세서면 base prefix로 사용, 카드별 last3 자동 suffix
    auto_ingest=True  # 영수증·카드명세서면 자동 거래 등록
)
```

**카드명세서 응답 예시**:
```json
{
  "doc_type": "card_statement_scan",
  "parsed_rows": 287,
  "ingested": 285,
  "duplicates_skipped": 2,
  "issuer": "shinhan",
  "period": {"start": "2025-07-01", "end": "2025-12-31"},
  "card_last3_list": ["039", "122"],
  "unparsed_count": 3,
  "needs_llm_fallback": false,
  "structured": {
    "rows_sample": [
      {"use_date": "2025-07-01", "merchant": "가상식당 테스트점",
       "amount": "6500", "biz_id": "TK_BIZ_ID_a1b2", "card_last3": "122"}
    ]
  }
}
```

### Step 4. 결과 요약

- 파싱된 거래 건수
- 등록된 건수 (중복 제거 후)
- OCR 케이스는 `needs_llm_fallback=true` 건 별도 표시
- 분류되지 않은 항목(matched_account=NULL) 수

### Step 5. 다음 단계 안내

```
표준화 완료:
- 파싱: N건
- 등록: M건 (중복 K건 제외)
- 분류 미완료: P건

다음:
- [2] 계정과목 매핑 (jangbu-tag)
```

## OCR 처리 흐름

1. PaddleOCR (PP-OCRv4 한국어) — 로컬 실행, 외부 전송 없음
2. 룰 기반 구조화 — 정규식으로 사업자번호·합계·공급가액 추출
3. 룰 실패 시 LLM fallback — 마스킹된 텍스트만 전달
4. 영수증이면 `receipt_to_transaction`으로 자동 거래 등록

## 보안

- 원본 파일은 `~/.jangbu/raw/imports/` 또는 `~/.jangbu/raw/ocr/`에만 저장
- OCR 결과 응답에서 사업자번호·카드번호 등 자동 마스킹
- 모든 `ingest_raw`·`ocr_document` 호출은 audit.log 기록

## 처리 후 데이터 검증

중복 제거는 `UNIQUE(source, source_ref)` 제약으로 자동 처리.
`source_ref` 생성 규칙:
- bank: `{account_id}:{date}:{description}:{amount}`
- card: 승인번호 우선, 없으면 `{account_id}:{date}:{merchant}:{amount}`
- manual: `manual:{date}:{counterparty}:{amount}`
- ocr(receipt): `receipt:{biz_id}:{date}:{total}`
- ocr(card_statement_scan): `card_stmt:{issuer}:{date}:{merchant}:{amount}:{seq}` — 같은 날 같은 가맹점·금액 반복 거래(편의점 여러 건)를 seq 번호로 구분

## 실패 대응

- OCR 정확도 낮음 → 원본 이미지 해상도 확인, 200 DPI 이상 권장
- CSV 컬럼 불일치 → 은행별 포맷 매퍼 확장 요청 안내
- 중복 대량 발생 → source_ref 생성 규칙 검토 후 재적재
