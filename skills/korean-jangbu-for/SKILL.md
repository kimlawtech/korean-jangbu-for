---
name: korean-jangbu-for
description: 한국 스타트업·1인 법인 대표·프리랜서·개인사업자를 위한 장부 자동 생성 진입점 스킬. 호출 시 5개 하위 스킬(jangbu-import·jangbu-tag·jangbu-tax·jangbu-dash·jangbu-jongso)을 번호·문자 메뉴로 제시하고, 입력 즉시 해당 스킬 인터뷰로 직행한다. 엑셀·5대 은행 CSV·7대 카드사 명세서 PDF·영수증·세금계산서 지원, macOS Vision/PaddleOCR 로컬 처리, Level 2 민감정보 마스킹.
---

# korean-jangbu-for

한국 장부 자동 생성 진입점 스킬.

## 최초 호출 시 출력 (1회성 인트로)

처음 사용 시 (또는 `~/.jangbu/jangbu.db` 미존재) 아래 안내를 먼저 출력.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 한국 법률 AI 허브 — SpeciAI 에서 만들었어요
 → discord.gg/wQWpEpnBfE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 korean-jangbu-for
 한국 스타트업·1인 법인·프리랜서를 위한 장부 자동화

[무엇을 할 수 있나요]
  • 카드명세서 PDF·은행 CSV·영수증을 자동 표준화
  • 계정과목 자동 분류 (룰 + 학습 + LLM 보조)
  • 손익계산서·재무상태표·월별 대시보드 생성
  • 더존·세무사랑 호환 분개 CSV (세무사 전달용)
  • 홈택스·은행·카드사 자동 수집 (CODEF API 연동)
  • 5월 종합소득세 준비 체크리스트 + 자동 생성 서류 패키지

[보안 모델 · Level 2]
  • 원본 파일은 ~/.jangbu/raw/ 에만 저장
  • 사업자번호·주민번호·카드번호·계좌번호 자동 토큰화
  • LLM에는 마스킹 뷰만, 리포트 집계는 서버 내부에서
  • OCR은 macOS Vision / PaddleOCR 로컬 실행
  • 모든 호출 ~/.jangbu/audit.log 기록

[자동 수집 (BYOK — 사용자 본인 키 사용)]
  • CODEF developer.codef.io 무료 가입 후 키 발급
  • /jangbu-connect 스킬에서 로컬 저장 (Keychain)
  • 외부 서버 전송 없음, 사용자 본인 계정 비용으로 호출

[SpeciAI — 한국 법률·세무 AI 허브]
  창업자·개발자·변호사·세무사·회계사가 모여
  계약·노동·투자·지재권·세무 이슈를 AI로 푸는 커뮤니티.

  여기서 물어볼 수 있어요:
    • 이 스킬 사용 중 궁금한 점 / 버그 리포트 / 기능 요청
    • 종소세·부가세·법인세 신고 전 실무 질문
    • CODEF·홈택스·세무사랑 연동 관련 이슈
    • 계약서·처리방침·장부 작성 팁
    • "이런 스킬 있으면 좋겠는데" 아이디어 제안

  신규 스킬 업데이트 공지·실전 사례·Q&A 채널 운영 중.

  → https://discord.gg/wQWpEpnBfE

[면책]
  생성 문서는 참고용 초안입니다.
  법인세·종소세 신고 전 세무사 검토 필수,
  외감 대상(자산 120억↑)은 공인회계사 감사 필수.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## 동작

호출 즉시 아래 번호 메뉴를 출력하고 사용자 입력을 대기한다.

```
무엇을 하시겠습니까?

━ 자동 수집 ━
[S] CODEF API 설정 (CODEF 키 발급·등록, 1회)
    → jangbu-connect
[F] 홈택스·은행·카드 자동 수집 실행
    codef_fetch_hometax / codef_fetch_bank / codef_fetch_card

━ 단계별 실행 ━
[1] 원본 데이터 표준화 (엑셀·은행CSV·카드명세서·영수증·세금계산서)
    → jangbu-import
[2] 계정과목 매핑 (룰 분류 + 학습 + 사용자 확인)
    → jangbu-tag
[3] 세무용 재무제표 생성 (재무상태표·손익계산서)
    → jangbu-tax
[4] 경영 리포트 생성 (월별 손익·현금흐름·cash burn·카드별 분석)
    → jangbu-dash

━ 원클릭 시나리오 ━
[M] 월마감 (지난달 데이터 정리 → 분류 → PL·현금흐름 → HTML 대시보드)
[Q] 분기마감 (분기 데이터 정리 → 분류 → BS·PL·분기 대시보드)
[T] 세무사 전달용 (연간 분개 CSV + PL CSV 생성, 더존 호환)
[X] 종소세 준비 (체크리스트 + 자동 생성 서류 묶음)
    → jangbu-jongso
[C] 카드별 사용 분석 (여러 장 카드 이용액·계정 분포·월별 추이)
[A] 전체 파이프라인 순차 실행 (1 → 2 → 3 → 4)

번호 또는 문자를 입력하세요.
```

## 원클릭 시나리오 상세

**[S] 자동 수집 설정** — jangbu-connect 직행. 첫 CODEF 등록 5분, 이후 재실행 1분.

**[F] 자동 수집 실행**
```
1. codef_credentials_status 호출 — 키 등록 여부 확인
   미등록이면 → /jangbu-connect 안내 후 종료

2. 수집 유형 선택:
   [1] 홈택스 증명서류 (소득·납세·사업자등록·부가세과세표준)
   [2] 은행 거래내역 (KB·신한·우리·하나·IBK·NH·카카오·토스)
   [3] 카드 이용내역 (신한·KB·삼성·현대·롯데·BC·우리·하나·NH)
   [A] 전체 (홈택스 + 주 거래 계좌 + 주 사용 카드)

3. 각 항목별 필요 정보 입력:
   - 홈택스: 이름·주민번호(13자리)·귀속연도
   - 은행: 기관·계좌번호·계좌비번·기간
   - 카드: 카드사·로그인ID·비번·기간
   간편인증 방식 선택 (기본 카카오)

4. MCP 도구 호출 → 1단계 응답 받음
   → "카카오톡 승인 요청됨" 알림
   → 사용자가 카카오톡 앱에서 인증 완료

5. 2단계 호출 (twoway_info 포함)
   → 데이터 수신 → 표준 13필드 매핑 → DB 적재

6. 요약 표시:
   ✅ 홈택스: 소득금액증명 1건
   ✅ 신한은행: 856건 거래내역
   ✅ 신한카드(039/122): 287건
   → 다음 단계: /jangbu-tag 로 계정 분류
```

**[M] 월마감 워크플로우**
```
1. 지난달 기간 확정 (예: 2026-03-01 ~ 2026-03-31)
2. jangbu-import 실행 안내 — 사용자에게 파일 경로 요청
3. classify_with_rules(start_date, end_date) — 룰 자동 분류
4. 미분류 건 LLM fallback (마스킹 뷰) → 사용자 확인 루프
5. export_report(pl) + export_report(cash_flow)
6. export_report(dashboard, fmt=html) → 브라우저 열기 안내
7. 결과 요약 출력
```

**[Q] 분기마감 워크플로우**
```
1. 분기 기간 확정 (Q1: 1-3월 등)
2. 누락 월 있는지 데이터 검증
3. 룰 분류 + LLM fallback
4. export_report(pl) + export_report(bs) + export_report(monthly_pl)
5. export_report(dashboard, fmt=html) 분기 버전
```

**[T] 세무사 전달용**
```
1. 연도 확인
2. 미분류 거래 있으면 먼저 분류 권고
3. export_djournal(period_start, period_end) — 더존·세무사랑 CSV
4. export_report(pl, fmt=csv) + export_report(bs, fmt=csv)
5. 출력 파일 경로 안내 — 세무사 전달 방법
```

**[X] 종소세 준비 시나리오**
```
1. jangbu-jongso 스킬로 라우팅
2. 신고 주체 확인 (개인사업자·프리랜서·1인 법인·겸업)
3. 귀속 연도 확인 (기본 직전년도)
4. 유형별 체크리스트 + 자동 생성 가능 항목 표시
5. 자동 생성: export_djournal + PL·BS·dashboard
6. 수동 준비 항목 안내 (홈택스 자료·계약서 등)
7. 세무사 전달 패키지 생성 (선택)
```

**[C] 카드별 분석 시나리오**
```
1. 기간 확인
2. export_report(card_analysis, period_start, period_end)
3. 카드별 이용액·주요 계정·월별 추이 표시
4. 특정 카드 드릴다운 가능 (예: "신한 039 세부 내역")
```

## 번호/문자 입력 처리

- `S` → `jangbu-connect` 스킬 호출 (CODEF 자격증명 설정)
- `F` → 자동 수집 시나리오 (codef_credentials_status 확인 → 문서 유형 선택 → codef_fetch_*)
- `1` → `jangbu-import` 스킬 호출 안내
- `2` → `jangbu-tag` 스킬 호출 안내
- `3` → `jangbu-tax` 스킬 호출 안내
- `4` → `jangbu-dash` 스킬 호출 안내
- `M` → 월마감 시나리오 실행
- `Q` → 분기마감 시나리오 실행
- `T` → 세무사 전달 CSV 생성 (export_djournal + export_report csv)
- `X` → `jangbu-jongso` 스킬 호출
- `C` → `export_report(card_analysis)` 실행
- `A` → 순서대로 호출: import → tag → tax → dash

## 선행 조건 확인

각 번호 선택 시 다음 순서로 검증:

1. **jangbu-mcp 서버 연결 확인**
   - `get_audit_log` 호출로 서버 동작 확인
   - 연결 안되면 설치 안내

2. **데이터 존재 여부 확인** (2·3·4번 선택 시)
   - `list_transactions` 호출로 거래내역 존재 확인
   - 0건이면 "먼저 [1] 원본 데이터 표준화부터 진행하세요" 안내

## 보안 원칙 (모든 하위 스킬 공통)

- 원본 파일은 MCP 서버가 `~/.jangbu/raw/`에만 저장
- LLM이 보는 모든 뷰는 마스킹 (사업자번호·주민번호·카드번호·계좌번호 토큰화)
- 리포트 생성은 MCP 서버 내부에서 언마스킹 처리, LLM에는 요약만 반환
- 모든 도구 호출은 `~/.jangbu/audit.log`에 기록

## 법적 면책

- 생성된 재무제표는 **참고용 초안**
- 외감 대상(자산 120억 이상)은 공인회계사 감사 필수
- 법인세 신고 전 세무사 검토 필수
- 일반기업회계기준(K-GAAP) 간소화 버전, 주석 미포함
