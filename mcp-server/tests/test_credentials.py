"""credentials 모듈 테스트 (envfile 경로).

keyring 사용 여부와 무관하게 envfile fallback을 명확히 검증.
"""
import os
import pytest

from jangbu_mcp import credentials


def test_save_and_load_via_envfile():
    credentials.save("CODEF_CLIENT_ID", "test_id_12345", use_keyring=False)
    credentials.save("CODEF_CLIENT_SECRET", "test_secret_abcde", use_keyring=False)

    assert credentials.load("CODEF_CLIENT_ID") == "test_id_12345"
    assert credentials.load("CODEF_CLIENT_SECRET") == "test_secret_abcde"


def test_unsupported_key_rejected():
    with pytest.raises(ValueError):
        credentials.save("UNKNOWN_KEY", "x", use_keyring=False)


def test_env_overrides_envfile(monkeypatch):
    credentials.save("CODEF_CLIENT_ID", "from_envfile", use_keyring=False)
    monkeypatch.setenv("CODEF_CLIENT_ID", "from_env")
    assert credentials.load("CODEF_CLIENT_ID") == "from_env"


def test_delete_from_envfile():
    credentials.save("CODEF_CLIENT_ID", "to_delete", use_keyring=False)
    assert credentials.load("CODEF_CLIENT_ID") == "to_delete"
    credentials.delete("CODEF_CLIENT_ID")
    # envfile에서 제거 확인
    assert credentials.load("CODEF_CLIENT_ID") is None


def test_list_keys_masking():
    credentials.save("CODEF_CLIENT_ID", "1234567890abcdef", use_keyring=False)
    keys = credentials.list_keys()
    assert keys["CODEF_CLIENT_ID"]["set"] is True
    # 마스킹된 프리뷰는 원본을 노출하지 않음
    preview = keys["CODEF_CLIENT_ID"]["preview"]
    assert preview != "1234567890abcdef"
    assert "..." in preview


def test_envfile_permission():
    credentials.save("CODEF_CLIENT_ID", "perm_test", use_keyring=False)
    mode = os.stat(credentials.CREDENTIALS_ENV).st_mode & 0o777
    assert mode == 0o600
