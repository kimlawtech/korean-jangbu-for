---
name: jangbu-connect
description: 홈택스·은행·카드사 데이터를 자동 수집하기 위한 CODEF API 자격증명 발급·설정 가이드 스킬. 사용자가 직접 CODEF(developer.codef.io)에 무료 가입해 받은 Client ID/Secret을 로컬(macOS Keychain 또는 ~/.jangbu/credentials.env)에 저장. BYOK(Bring Your Own Key) 방식, 외부 서버 전송 없음. 2026년 4월 기준.
---

# jangbu-connect

CODEF 자격증명 설정 가이드 스킬.

## 왜 CODEF인가

한국에서 홈택스·은행·카드·4대보험 데이터를 **합법적·합리적 비용**으로 자동 수집할 수 있는 유일한 프록시 API.

- 한국 공공기관·금융기관 통합 API
- 간편인증(카카오·PASS·공동인증서) 지원
- **개인 개발자 샌드박스 무료** (월 1,000건까지)
- 사업자등록 없어도 가입 가능

## BYOK 보안 원칙

- **스킬 제작자는 자격증명 취급 안함** — 사용자 본인 계정의 키만 사용
- 키는 **로컬에만 저장** (macOS Keychain 기본, 실패 시 `~/.jangbu/credentials.env`)
- **외부 서버 동기화 없음**
- 모든 API 호출은 사용자 자신의 비용으로 실행

## 인터뷰 플로우

### Step 1. CODEF 계정 발급 (5분)

```
CODEF 자동 수집을 쓰려면 개인 개발자 키가 필요합니다.

[1] 브라우저에서 https://developer.codef.io 접속
[2] 우측 상단 "회원가입" → 이메일 인증 (사업자번호 불필요)
[3] 로그인 후 상단 "My Page" → "서비스 관리" → "신규 서비스 등록"
     - 서비스 명: 예) korean-jangbu-for
     - 서비스 구분: "개발용" 선택
     - 이용 서비스: "대한민국" · "공공" · "금융" 체크
[4] 등록 직후 표시되는
     - Client ID
     - Client Secret
     두 값을 복사해 두세요.
[5] (선택) "My Page" → "RSA Public Key" 에서 공개키 PEM도 복사해 두면
     프로덕션 API에서 비밀번호 암호화 전송이 가능해요 (샌드박스는 불필요).

준비 끝나면 Enter 누르세요.
```

### Step 2. 자격증명 등록

```
아래 값을 순서대로 입력받습니다. 값은 로컬에만 저장됩니다.

Client ID:         (CODEF My Page에서 복사한 값)
Client Secret:     (CODEF My Page에서 복사한 값)
RSA Public Key:    (선택, 프로덕션 전환 시만 필요)
샌드박스 사용:      Y/n (기본 Y — 개인 체험·테스트)
Keychain 저장:     Y/n (기본 Y — 실패 시 envfile로 fallback)
```

입력이 끝나면 내부적으로 MCP 도구 `codef_credentials_save`를 호출:

```python
codef_credentials_save(
    client_id="...",
    client_secret="...",
    public_key_pem="...",    # 선택
    sandbox=True,
    use_keyring=True,
)
```

### Step 3. 등록 확인

```
codef_credentials_status
```

응답 예시:
```json
{
  "CODEF_CLIENT_ID":     {"set": true, "source": "keyring", "preview": "abcd...xyz1"},
  "CODEF_CLIENT_SECRET": {"set": true, "source": "keyring", "preview": "****...****"},
  "CODEF_PUBLIC_KEY":    {"set": false, "source": null, "preview": null},
  "CODEF_SANDBOX":       {"set": true, "source": "keyring", "preview": "1"}
}
```

### Step 4. 연결 테스트

```
간편인증 테스트를 할 수 있는 메뉴로 이동합니다.

[1] 홈택스 사업자등록증명 1건 발급 테스트 → /korean-jangbu-for → [F]
[2] 지금은 건너뛰기
```

## 사용자가 직접 받는 자격증명 4종

| 키 | 필수 | 용도 |
|----|------|------|
| `CODEF_CLIENT_ID` | 필수 | OAuth 클라이언트 ID |
| `CODEF_CLIENT_SECRET` | 필수 | OAuth 시크릿 |
| `CODEF_PUBLIC_KEY` | 선택 | 프로덕션 비밀번호 RSA 암호화용 |
| `CODEF_SANDBOX` | 자동 | "1"=샌드박스, "0"=운영 |

## 저장 위치 우선순위

1. **환경변수** — 일회성 override (`export CODEF_CLIENT_ID=...`)
2. **macOS Keychain** / Linux secret-service (`keyring` 패키지 설치 시)
3. **`~/.jangbu/credentials.env`** — 평문, 파일 권한 0o600

저장 방법은 `codef_credentials_save(use_keyring=True/False)`로 선택 가능.

## 키 변경·삭제

재입력하면 덮어쓰기. 완전 삭제하려면:

```bash
# Keychain (macOS)
security delete-generic-password -s korean-jangbu-for -a CODEF_CLIENT_ID

# envfile
rm ~/.jangbu/credentials.env
```

또는 향후 MCP 도구로 제공 예정.

## 비용 가이드 (2026년 4월 기준)

| 플랜 | 월 한도 | 비용 |
|------|---------|------|
| 개인 개발자(샌드박스) | 1,000건 | **무료** |
| 개인 개발자(운영) | 3,000건 | 월 1만원 |
| 스타트업 | 10,000건 | 월 5만원~ |
| 법인 | 협의 | 견적 |

일반 스타트업·1인 법인의 월 사용량은 보통 **100~500건** 수준 → 무료 플랜으로 충분.

## 법적 면책

- 본 스킬이 생성하는 자동 수집 요청은 **사용자 본인 계정 기반** 호출입니다
- CODEF와의 이용약관은 **사용자와 CODEF 사이 직접 계약** — 스킬 제작자 책임 아님
- 수집된 개인정보·금융정보는 사용자 본인 책임하에 로컬 관리
- 프로덕션 환경 전환 시 **개인정보처리방침 별도 작성 필수** (korean-privacy-terms 스킬 활용 가능)

## 대안

CODEF 사용이 어려운 경우 **수동 다운로드 + jangbu-import** 플로우로 동일한 기능 실행 가능.
`jangbu-jongso` 스킬이 홈택스 각 메뉴의 다운로드 경로를 상세히 안내합니다.
