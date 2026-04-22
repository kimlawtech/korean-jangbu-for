"""사용자 API 자격증명 저장·로드.

저장 위치 우선순위:
1. macOS Keychain / Linux secret-service (keyring 설치 시)
2. ~/.jangbu/credentials.env (평문, 0o600)
3. 환경변수 (일시적)

BYOK(Bring Your Own Key) 원칙:
- 스킬은 자격증명을 직접 발급·보관하지 않음
- 사용자가 직접 CODEF 등에 가입해 받은 키를 로컬에 저장
- 외부 전송 없음, 원격 동기화 없음
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from jangbu_mcp.storage import BASE_DIR

CREDENTIALS_ENV = BASE_DIR / "credentials.env"
SERVICE_NAME = "korean-jangbu-for"

# 지원하는 자격증명 키
SUPPORTED_KEYS = {
    "CODEF_CLIENT_ID",
    "CODEF_CLIENT_SECRET",
    "CODEF_PUBLIC_KEY",          # CODEF RSA 공개키 (요청 본문 암호화용)
    "CODEF_SANDBOX",              # "1" / "0" — 샌드박스 사용 여부
}


def _try_keyring():
    """keyring 사용 가능 여부 확인. 실패 시 None."""
    try:
        import keyring
        return keyring
    except Exception:
        return None


def save(key: str, value: str, use_keyring: bool = True) -> str:
    """자격증명 저장. 저장 방법을 문자열로 반환 ('keyring'/'envfile')."""
    if key not in SUPPORTED_KEYS:
        raise ValueError(f"unsupported key: {key}. Supported: {sorted(SUPPORTED_KEYS)}")

    if use_keyring:
        kr = _try_keyring()
        if kr:
            try:
                kr.set_password(SERVICE_NAME, key, value)
                return "keyring"
            except Exception:
                pass  # fallback

    _save_to_envfile(key, value)
    return "envfile"


def load(key: str) -> Optional[str]:
    """자격증명 로드. 없으면 None.

    순서:
    1. 환경변수 (일시 override 가능)
    2. keyring
    3. ~/.jangbu/credentials.env
    """
    env_val = os.environ.get(key)
    if env_val:
        return env_val

    kr = _try_keyring()
    if kr:
        try:
            val = kr.get_password(SERVICE_NAME, key)
            if val:
                return val
        except Exception:
            pass

    envfile = _load_envfile()
    return envfile.get(key)


def delete(key: str) -> bool:
    """자격증명 삭제. 실제로 삭제됐으면 True."""
    deleted = False
    kr = _try_keyring()
    if kr:
        try:
            kr.delete_password(SERVICE_NAME, key)
            deleted = True
        except Exception:
            pass

    envfile = _load_envfile()
    if key in envfile:
        del envfile[key]
        _write_envfile(envfile)
        deleted = True

    return deleted


def list_keys() -> dict:
    """저장된 키 목록 + 저장 위치 반환. 값은 마스킹해서 보여줌."""
    result = {}
    envfile = _load_envfile()
    kr = _try_keyring()

    for key in SUPPORTED_KEYS:
        val = None
        source = None

        if key in os.environ:
            val = os.environ[key]
            source = "env"
        elif kr:
            try:
                v = kr.get_password(SERVICE_NAME, key)
                if v:
                    val = v
                    source = "keyring"
            except Exception:
                pass

        if val is None and key in envfile:
            val = envfile[key]
            source = "envfile"

        result[key] = {
            "set": val is not None,
            "source": source,
            "preview": _mask(val) if val else None,
        }

    return result


def _mask(s: str) -> str:
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"


def _ensure_envfile() -> None:
    """~/.jangbu/credentials.env 파일 존재 보장 + 권한 설정."""
    CREDENTIALS_ENV.parent.mkdir(parents=True, exist_ok=True)
    if not CREDENTIALS_ENV.exists():
        CREDENTIALS_ENV.touch()
    try:
        os.chmod(CREDENTIALS_ENV, 0o600)
    except Exception:
        pass


def _load_envfile() -> dict:
    """간단한 KEY=VALUE 파싱."""
    _ensure_envfile()
    result = {}
    for line in CREDENTIALS_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _save_to_envfile(key: str, value: str) -> None:
    data = _load_envfile()
    data[key] = value
    _write_envfile(data)


def _write_envfile(data: dict) -> None:
    _ensure_envfile()
    lines = [
        "# korean-jangbu-for 자격증명 파일",
        "# 권한 0o600. git 커밋 금지.",
        "# /korean-jangbu-for → [S] 자동 수집 설정에서 입력하면 자동 저장됨.",
        "",
    ]
    for k in sorted(data.keys()):
        lines.append(f'{k}="{data[k]}"')
    CREDENTIALS_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(CREDENTIALS_ENV, 0o600)
    except Exception:
        pass
