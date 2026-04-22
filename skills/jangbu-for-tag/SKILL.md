---
name: jangbu-for-tag
description: 표준 거래내역에 계정과목을 매핑하는 스킬. 룰 기반 분류 우선(80%+), 실패 건만 LLM fallback(마스킹 뷰), 사용자 확인 루프. 한국 거래처 100개 시드 룰 포함, 내부 계정 ↔ 국세청 표준계정 이중 구조.
---

# jangbu-for-tag

거래내역 → 계정과목 매핑 스킬.

## 분류 전략

1. **룰 기반 (LLM 미경유)** — `classify_with_rules` 호출로 일괄 처리
2. **LLM fallback** — 룰 실패 건에 한해 마스킹된 뷰만 LLM에 전달
3. **사용자 확인** — 낮은 confidence 건은 사용자 검증 후 `apply_classification(source="user")`

## 계정 구조 (이중)

- **내부 계정**: 유연. 예) `광고선전비_구글`, `광고선전비_메타`, `SaaS_노션`
- **국세청 계정**: 고정. `accounts/nts_standard.json` 매핑 테이블 경유
- 세무 리포트 생성 시 내부 → 국세청 자동 변환

## 인터뷰 플로우

### Step 1. 대상 기간 확인

```
분류할 거래 기간을 선택하세요.

[1] 이번 달
[2] 전월
[3] 올해 전체
[4] 특정 기간 입력

미분류 거래만 처리합니다.
```

### Step 2. 룰 기반 일괄 분류

```python
classify_with_rules(start_date="2026-04-01", end_date="2026-04-30")
```

결과 예시:
```
룰 분류 완료: 87건
분류 실패(LLM fallback 필요): 13건
```

### Step 3. LLM fallback (분류 실패 건)

**핵심**: `list_transactions(unclassified_only=True)` 응답은 **이미 마스킹된 뷰**.

#### LLM 분류 프롬프트 템플릿

```
다음 거래를 한국 일반기업회계기준 계정과목으로 분류하세요.
거래처명·적요에 토큰(TK_*)이 있으면 그대로 두고 문맥으로만 판단.

거래 목록:
{
  "tx_abc": {"date": "2025-09-07", "amount": 142025, "direction": "outflow", 
             "counterparty": "CLAUDE.AI SUBSCRIPTION", "description": "..."},
  "tx_def": {...}
}

사용 가능 계정과목 (이 목록 외 사용 금지):
- 복리후생비: 직원 복리·회식·식대·간식·건강검진
- 여비교통비: 택시·KTX·주유·출장
- 접대비: 고객 접대 (5만원 이상 식사가 유력 신호)
- 통신비: 휴대폰·인터넷·유선전화
- 수도광열비/전력비: 수도·전기·가스
- 임차료: 월세·사무실 임대
- 지급수수료: SaaS 구독·은행 수수료·법무·회계·세무 용역비
- 광고선전비: 페이스북·구글·네이버 광고
- 도서인쇄비: 책·출판물·인쇄
- 소모품비: 사무용품·다이소·IKEA
- 세금과공과: 4대보험·세금·지방세
- 수선비·보험료·차량유지비·운반비·교육훈련비·회의비
- 급여·상여금·퇴직급여·잡급
- 매출액/이자수익 (inflow일 때)

각 거래에 대해 JSON으로 반환:
{
  "tx_abc": {"internal_account": "지급수수료", "confidence": 0.92, 
             "reason": "CLAUDE.AI는 AI SaaS 구독"},
  ...
}

confidence < 0.7인 건은 "사용자 확인 필요"로 표시.
```

#### 적용 절차

1. `list_transactions(unclassified_only=True, limit=50)` — 50건씩 배치
2. 위 프롬프트로 LLM 호출 → JSON 결과
3. confidence ≥ 0.7: `apply_classification(source="llm")` 자동 저장
4. confidence < 0.7: Step 4로 사용자 확인

LLM 분류 시 반드시 한국 일반기업회계기준의 표준 계정 사용.

### Step 4. 낮은 confidence 건 사용자 확인

```
다음 거래의 계정을 확인해주세요.

거래: TK_ACCOUNT_ID_xxxx | 2026-04-15 | 500,000원 | outflow
적요: (마스킹된 내용)
추정 계정: 지급수수료 (confidence=0.65)

[1] 지급수수료 유지
[2] 다른 계정 지정
[3] 스킵 (미분류 유지)
```

사용자 입력 시:
```python
apply_classification(
    transaction_id="tx_xxx",
    internal_account="지급수수료",
    confidence=1.0,
    source="user"
)
```

### Step 5. 결과 요약

```
분류 완료:
- 룰: 87건
- LLM: 10건 (평균 confidence 0.78)
- 사용자: 3건
- 최종 미분류: 0건

다음:
- [3] 세무 리포트 생성 (jangbu-for-tax)
- [4] 경영 리포트 생성 (jangbu-for-dash)
```

## 시드 룰 적용

최초 실행 시 `rules/seed_rules.json` 을 `classification_rules` 테이블에 주입.
이미 존재하는 룰은 중복 추가 안함. 사용자가 `apply_classification(source="user")` 할 때 학습용 룰로 자동 등록할지는 사용자에게 확인 후 결정.

## 신뢰도 기준

| Confidence | 출처 | 처리 |
|---|---|---|
| 1.00 | counterparty_exact / user 확정 | 자동 적용 |
| 0.90 | counterparty_regex | 자동 적용 |
| 0.80 | description_regex | 자동 적용 |
| 0.70~0.79 | LLM 추정 | 사용자 확인 권장 |
| 0.00 | 매칭 실패 | LLM fallback |

## 보안

- 룰 분류는 LLM 미경유 — 민감정보 외부 노출 없음
- LLM fallback 시에도 `list_transactions`의 마스킹 뷰만 사용
- 사업자번호·계좌번호·카드번호는 토큰으로 치환된 상태로만 노출
- `apply_classification` 호출마다 audit.log 기록

## 엣지 케이스

- 동일 거래처가 여러 용도(예: 쿠팡 = 사무용품 or 복리후생) — 적요 텍스트 우선순위로 추가 룰 작성
- 분개가 필요한 거래(계정 간 대체) — MVP 범위 외, 향후 분개 엔진에서 처리
- 계정 신규 생성 — `accounts/nts_standard.json`에 없으면 "지급수수료" 기본 매핑 후 사용자에게 확인
