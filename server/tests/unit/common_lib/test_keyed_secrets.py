"""`<id>:<base64url>` keys from the environment (signing keys, peppers, later the KEK)."""

import pytest
from pydantic import SecretStr, ValidationError
from support.backend import make_settings

from backend.admin_cli import main
from common_lib.settings import new_keyed_secret, parse_keyed_secret


def test_a_new_value_parses_to_32_bytes() -> None:
    key_id, key = parse_keyed_secret(SecretStr(new_keyed_secret("signing-1")))
    assert key_id == "signing-1"
    assert len(key) == 32
    assert new_keyed_secret("x") != new_keyed_secret("x")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "no-separator",
        "UPPER:" + "A" * 43,
        "id:" + "A" * 42,
        "id:" + "*" * 43,
        "id:" + "A" * 43,  # 32 equal bytes: not random
        "-starts-with-dash:" + "QUJD" * 10 + "QUI",
    ],
)
def test_refuses_malformed_or_weak_values(value: str) -> None:
    with pytest.raises(ValueError, match=r"expected|random"):
        parse_keyed_secret(SecretStr(value))


def test_settings_refuse_a_bad_key_without_repeating_it() -> None:
    with pytest.raises(ValidationError) as refused:
        make_settings(auth_signing_key_current="leaky-value-123")
    assert "leaky-value-123" not in str(refused.value)


def test_empty_previous_values_mean_none() -> None:
    settings = make_settings(auth_signing_key_previous="", auth_pepper_previous="")
    assert settings.auth_signing_key_previous is None
    assert settings.auth_pepper_previous is None


def test_cli_prints_a_new_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new-key", "pepper-2"]) == 0
    printed = capsys.readouterr().out.strip()
    assert parse_keyed_secret(SecretStr(printed))[0] == "pepper-2"


def test_cli_reports_an_invalid_key_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new-key", "Bad Id"]) == 1
    assert "error:" in capsys.readouterr().err
