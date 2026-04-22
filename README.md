# korean-jangbu-for

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Claude Code](https://img.shields.io/badge/Claude_Code-Skill-orange)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
[![Discord](https://img.shields.io/badge/Discord-SpeciAI-5865F2)](https://discord.gg/wQWpEpnBfE)

한국 스타트업·1인 법인 대표·프리랜서·개인사업자를 위한 장부 자동 생성 Claude Code 스킬 패키지.
카드명세서 PDF·은행 CSV·영수증을 넣으면 표준 거래내역·계정과목 매핑·재무제표·세무사 전달 CSV가 자동 생성됩니다.

> 한국 법률 AI 허브 **SpeciAI** 에서 만들고 있어요.
> 계약·노동·투자·지재권·세무를 AI로 해결하는 창업자·변호사 커뮤니티에 초대합니다.
> → [discord.gg/wQWpEpnBfE](https://discord.gg/wQWpEpnBfE)

**라이선스**: Apache-2.0
**버전**: 0.2.0
**저자**: [@kimlawtech](https://github.com/kimlawtech) (SpeciAI)

## 설계 원칙

- **입력은 넓게** — 엑셀·은행 CSV·카드 내역·영수증 이미지·세금계산서 PDF
- **출력은 단계적** — 세무용(BS·PL) / 경영용(현금흐름·cash burn)
- **계정체계는 매핑** — 내부 계정(유연) ↔ 국세청 표준계정(고정)
- **보안 Level 2** — 민감정보 마스킹 + 룰 우선 분류 + LLM fallback 최소화

## 스킬 구성

진입점 1개 + 하위 스킬 5개 + MCP 서버 1개.

| 스킬 | 호출 | 용도 |
|------|------|------|
| `jangbu-for` | 진입점 | 번호 메뉴 라우팅 |
| `jangbu-for-import` | 직행 | 원본 데이터(엑셀·CSV·이미지·PDF) → 표준 거래내역 |
| `jangbu-for-tag` | 직행 | 표준 거래내역 → 계정과목 매핑 (룰 우선 + LLM fallback) |
| `jangbu-for-tax` | 직행 | 세무용 BS·PL (국세청 표준계정) |
| `jangbu-for-dash` | 직행 | 경영용 현금흐름·비용구조·월별 손익 |
| `jangbu-for-jongso` | 직행 | 종소세·법인세 신고 전 준비 서류 체크리스트 |

```
/jangbu-for      → 1·2·3·4·M·Q·T·X·C·A 번호·문자 메뉴
/jangbu-for-import       → 원본 데이터 표준화 인터뷰 (7대 카드사 명세서 포함)
/jangbu-for-tag        → 계정과목 매핑 인터뷰 (룰 + LLM + 학습)
/jangbu-for-tax      → BS·PL 생성
/jangbu-for-dash     → 경영 리포트 + 카드별 분석 + 대시보드
/jangbu-for-jongso   → 종소세 준비 체크리스트 + 자동 생성 서류
```

## 카드사 지원 현황

| 카드사 | 명세서 PDF 파싱 | 비고 |
|--------|---------------|------|
| 신한카드 | ✅ 완전 지원 | 실전 검증 완료 (313건/74% 분류) |
| KB국민카드 | ✅ 범용 파서 | 테스트 통과, 실전 샘플 대기 |
| 삼성카드 | ✅ 범용 파서 | 테스트 통과 |
| 현대카드 | ✅ 범용 파서 | 테스트 통과 (M/X 카드 포함) |
| 롯데카드 | ✅ 범용 파서 | 테스트 통과 |
| BC(비씨)카드 | ✅ 범용 파서 | 테스트 통과 |
| 우리카드 | ✅ 범용 파서 | 테스트 통과 |
| 하나카드 | ✅ 범용 파서 | 테스트 통과 |

범용 파서 구조: **발급사별 카드 마커 패턴 분기 + 공통 앵커(날짜·금액·사업자번호·상품구분) 추출**.
카드사별 특화 파서는 `CARD_PARSERS` 레지스트리에 추가 등록 가능.

## 데이터 흐름

```
[원본 파일]
  ├─ bank.csv, card.csv, books.xlsx
  └─ receipt.jpg, tax_invoice.pdf
        ↓
  [jangbu-mcp-server]
    ├─ ingest_raw        # CSV/엑셀 파서
    ├─ ocr_document      # PaddleOCR (로컬)
    └─ 마스킹 레이어      # 사업자번호·카드번호·계좌번호 토큰화
        ↓
  표준 거래내역 (13개 필드, SQLite)
        ↓
  [jangbu-for-tag]
    ├─ 룰 기반 (거래처 사전 + MCC + 정규식) → 80%+
    └─ LLM fallback (마스킹된 데이터만) → 나머지
        ↓
  분류 완료 거래내역
        ↓
  ├─ [jangbu-for-tax]  → BS·PL (국세청 표준계정 매핑)
  └─ [jangbu-for-dash] → 현금흐름·cash burn·월별 손익
```

## 표준 거래내역 스키마

필수 9 + 확장 4.

| 필드 | 타입 | 필수 | 설명 | 예시 |
|---|---|---|---|---|
| `transaction_id` | str (UUID) | 필수 | 내부 고유 ID | `tx_01HW...` |
| `date` | date | 필수 | 거래일 | `2026-04-15` |
| `amount` | decimal(18,2) | 필수 | 절대값 | `1500000.00` |
| `currency` | str (ISO 4217) | 필수 | 통화코드 | `KRW` |
| `direction` | enum | 필수 | `inflow`/`outflow` | `outflow` |
| `counterparty` | str | 필수 | 상대방명(정규화) | `구글코리아` |
| `description` | str | 필수 | 정규화 적요 | `Google Ads 광고비` |
| `source` | enum | 필수 | `bank`/`card`/`manual`/`ocr` | `card` |
| `source_ref` | str | 필수 | 원본 식별자(중복 제거) | `20260415-APPR-88421` |
| `raw_description` | str | 확장 | 원문 적요 | `GOOGLE*ADS 1234` |
| `account_id` | str | 확장 | 내부 계좌/카드 ID | `acct_shinhan_001` |
| `matched_account` | str | 확장 | 매핑된 계정과목 | `광고선전비` |
| `confidence` | float (0~1) | 확장 | 분류 신뢰도 | `0.92` |

## 보안 모델 (Level 2)

- **토큰화 대상**: 사업자번호·주민번호·카드번호·계좌번호·`account_id`
- **원본 유지**: `date`, `amount`, `currency`, `direction`, `source`
- **부분 마스킹**: `counterparty`·`description`·`raw_description` (번호류만 토큰 치환)
- **LLM 미경유 작업**: 룰 분류·BS/PL 생성·OCR 텍스트 추출
- **LLM 경유 작업**: 룰 실패한 분류 건만, 마스킹된 뷰로 전달
- **감사 로그**: 모든 MCP 도구 호출 기록(append-only)

## 저장소 레이아웃

```
~/.jangbu/
  ├── raw/
  │   ├── imports/         # 엑셀·CSV 원본
  │   └── ocr/             # 영수증·세금계산서 이미지·PDF
  ├── finance.db           # SQLite: transactions, mappings, classifications
  ├── tokens.db            # SQLite: 토큰↔원본 매핑 (권한 분리)
  └── audit.log            # append-only 감사 로그
```

## OCR 파이프라인

PaddleOCR 로컬 실행 → 룰 기반 구조화 → LLM fallback.

1. **PaddleOCR (PP-OCRv4 한국어 모델)** — 텍스트 + 좌표 추출, 외부 전송 없음
2. **문서 유형별 파서**
   - `receipt`: 상호·사업자번호·날짜·합계·공급가액·부가세
   - `tax_invoice`: 공급자·공급받는자·품목·공급가액·세액·작성일자
   - `bank_statement_scan`: 거래일·적요·금액·잔액
3. **LLM fallback** — 파서 실패 시 마스킹된 텍스트만 전달해 구조화

## 설치

```bash
# 스킬 등록
mkdir -p ~/.claude/skills
cd ~/.claude/skills
git clone https://github.com/kimlawtech/korean-jangbu-for.git
for s in jangbu-for jangbu-for-import jangbu-for-tag jangbu-for-tax jangbu-for-dash jangbu-for-jongso; do
  ln -sf ~/.claude/skills/korean-jangbu-for/skills/$s ~/.claude/skills/$s
done

# MCP 서버 설치
cd ~/.claude/skills/korean-jangbu-for/mcp-server
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Claude Code MCP 등록
claude mcp add jangbu-mcp -- ~/.claude/skills/korean-jangbu-for/mcp-server/.venv/bin/python -m jangbu_mcp
```

## 의존성

- Python 3.11+
- `paddlepaddle`, `paddleocr` (OCR)
- `pdf2image`, `pillow` (PDF → 이미지)
- `pandas`, `openpyxl` (엑셀·CSV 파싱)
- `mcp` (MCP SDK)

## 대상 사용자

- 스타트업 대표 — 기장 맡기기 전 내부 재무 가시성 확보
- 1인 법인 — 법인세 신고 연결 + 월별 현금흐름 파악
- 세무사무소 주니어 — 고객 raw 데이터 정리 반복 업무 단축

## 법적 면책

본 스킬이 생성하는 재무제표는 **참고용 초안**이며, 공인회계사 감사·세무사 검토를 대체하지 않습니다.

- 외감 대상(자산 120억 이상 등)은 **공인회계사 감사 필수**
- 법인세 신고 전 **세무사 검토 필수**
- 일반기업회계기준(K-GAAP)의 간소화 버전이며 모든 주석을 포함하지 않습니다

## 커뮤니티 — SpeciAI

한국 법률 AI 허브 **SpeciAI** 디스코드에서 만들어요.
창업자·변호사·세무사·개발자가 계약·노동·투자·지재권·세무 이슈를 AI로 해결하는 커뮤니티입니다.

- 신규 스킬 업뎃 소식·공지
- 스킬 제안·버그 리포트·기능 요청
- 한국 사업자를 위한 AI 법률·세무 정보

**초대 링크**: [discord.gg/wQWpEpnBfE](https://discord.gg/wQWpEpnBfE)

## 기여

Pull Request 환영합니다. 특히 필요한 영역:

- 카드사별 특화 파서 (KB·삼성·현대·롯데·BC·우리·하나)
- 5대 은행 CSV 포맷 실데이터 검증·보강
- 계정과목 분류 룰 확장 (업종별 특화)
- 분개 엔진 MVP (복식부기 기반 정식 BS)

## 저자

운영: **[@kimlawtech](https://github.com/kimlawtech)** — [SpeciAI](https://discord.gg/wQWpEpnBfE)

한국 법률 AI 허브 SpeciAI를 운영하며 한국 사업자를 위한 계약서·처리방침·약관·장부·세무 자동화 Claude Code 스킬을 만들고 있습니다.

**관련 스킬 패키지**
- [`korean-contracts`](https://github.com/kimlawtech/korean-contracts) — 근로계약·프리랜서·외주·연봉갱신 등 9종 계약서
- [`korean-privacy-terms`](https://github.com/kimlawtech/korean-privacy-terms) — 처리방침·이용약관 (PIPA·GDPR·CCPA)
- [`korean-patent-diagram`](https://github.com/kimlawtech/korean-patent-diagram) — 특허 도면 자동 생성
- **`korean-jangbu-for`** — 장부·재무제표·세무사 전달 (이 패키지)

## License

**Apache License 2.0** — 자세한 내용은 [LICENSE](./LICENSE) 참조.
Copyright 2026 kimlawtech (SpeciAI).

## Disclaimer

본 스킬이 생성하는 재무제표는 **참고용 초안**이며 공인회계사 감사·세무사 검토를 대체하지 않습니다.
실제 법인세·종합소득세 신고 전 반드시 세무사 검토를 받으세요.
자세한 면책 고지는 [DISCLAIMER.md](./DISCLAIMER.md) 참조.

---

Built with [Claude Code](https://claude.com/claude-code) by [@kimlawtech](https://github.com/kimlawtech).
