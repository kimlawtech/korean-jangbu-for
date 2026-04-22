---
name: jangbu-for-dash
description: 스타트업·1인 법인 대표용 경영 리포트 자동 생성 스킬. 월별 손익 추이, 현금흐름(일별 net), cash burn rate(최근 N개월 평균 순지출), 비용 구조 분석. 공식 재무제표와 별개로 의사결정용 가공 리포트 제공.
---

# jangbu-for-dash

경영 의사결정용 리포트 생성 스킬.

## 생성 가능한 리포트

| 리포트 | 도구 | 용도 |
|------|------|------|
| 월별 손익 | `export_report(report_type="monthly_pl")` | 연간 매출·비용 추이 |
| 현금흐름 | `export_report(report_type="cash_flow")` | 기간 inflow/outflow, 일별 net |
| Cash burn rate | `export_report(report_type="burn_rate")` | 최근 N개월 평균 월별 순지출 |

## 인터뷰 플로우

### Step 1. 리포트 유형 선택

```
어떤 경영 리포트를 생성하시겠습니까?

[1] 월별 손익 추이 (연간)
[2] 현금흐름 (기간 지정)
[3] Cash burn rate (최근 6개월 기본)
[4] 비용 구조 분석
[A] 전체 (1~4 일괄)

번호를 입력하세요.
```

### Step 2. 파라미터 입력

**[1] 월별 손익:**
```
연도를 입력하세요. 기본값: 2026.
```

**[2] 현금흐름:**
```
기간을 지정하세요.
- 직전 월 / 올해 누적 / 직접 입력
```

**[3] Cash burn:**
```
분석 기간(개월)을 입력하세요. 기본 6.
```

**[4] 비용 구조:**
```
월별 비용을 계정별로 stacked 집계.
(내부적으로는 monthly_pl + 계정별 breakdown 사용)
```

### Step 3. 리포트 생성

```python
# 월별 손익
export_report(report_type="monthly_pl", year=2026)

# 현금흐름
export_report(
    report_type="cash_flow",
    period_start="2026-01-01",
    period_end="2026-04-30"
)

# Cash burn
export_report(report_type="burn_rate", months=6)
```

### Step 4. 결과 요약 표시

**월별 손익 예시:**
```
2026년 월별 손익

          매출         비용        순이익
2026-01   12,000,000   8,500,000   3,500,000
2026-02   14,500,000   9,100,000   5,400,000
2026-03   11,800,000   9,800,000   2,000,000
2026-04   15,200,000   10,300,000  4,900,000

출력 파일: ~/.jangbu/reports/monthly_pl_2026-04-22.json
```

**Cash burn 예시:**
```
최근 6개월 Cash burn rate

평균 월 burn: -3,200,000원 (순지출)
→ 현재 예금 8천만원 기준 약 25개월 런웨이

출력 파일: ~/.jangbu/reports/burn_rate_2026-04-22.json
```

### Step 5. 추가 질문·해석

생성 후 사용자가 자연어로 후속 질문 가능:
- "광고비 비중이 큰 달은?" → monthly_pl 파일 열어 비교
- "어느 계정이 제일 많이 늘었어?" → 직전 기간 대비 증감 계산

**주의**: 후속 분석도 `list_transactions` 마스킹 뷰 또는 리포트 파일에서만 수행. 원본 거래 전체를 LLM이 직접 집계하지 않음.

## Cash burn 해석

- **양수 (+)**: 순수익 상태 (수입 > 지출)
- **음수 (-)**: burn 상태 (지출 > 수입)
- **런웨이 계산**: 현재 현금 / |평균 월 burn|

사용자가 현재 예금 금액을 알려주면 런웨이를 추가 계산해 제시.

## 공식 재무제표와의 차이

| 항목 | jangbu-for-tax | jangbu-for-dash |
|------|---------|---------|
| 기준 | 일반기업회계기준 | 현금 기준 |
| 용도 | 세무 신고·감사 | 의사결정 |
| 계정 | 국세청 표준 | 내부 유연 |
| 감가상각 | (한계 존재) | 미반영 |
| 부가세 | (간이) | 무시 (총액 기준) |

**같이 만들면 좋은 조합**:
- 월 마감: PL + monthly_pl + cash_flow
- 분기 마감: PL + BS + burn_rate
- 투자자 미팅: monthly_pl + burn_rate + 런웨이

## 보안

- 집계는 MCP 서버 내부에서 언마스킹 원본으로 수행
- LLM 응답은 요약 수치 + 파일 경로만
- 세부 거래 내역 필요 시 `list_transactions`로 마스킹 뷰만 조회
- 모든 호출은 audit.log 기록
