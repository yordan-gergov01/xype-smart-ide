from pathlib import Path

import pytest

from devtools import e2e_stack

TEMPLATE = """# comment with PASSWORD=dev-only-change-me-x
POSTGRES_DB=xype
POSTGRES_APP_PASSWORD=dev-only-change-me-app
REDIS_PASSWORD=dev-only-change-me-redis
AUTH_SIGNING_KEY_CURRENT=
AUTH_PEPPER_CURRENT=
KEK_CURRENT=
AUTH_SIGNING_KEY_PREVIOUS=
BACKEND_PORT=8000
"""


def _values(text: str) -> dict[str, str]:
    pairs = (line.partition("=") for line in text.splitlines() if not line.startswith("#"))
    return {name: value for name, _, value in pairs}


def test_fills_every_placeholder_and_key_with_fresh_values() -> None:
    first, second = _values(e2e_stack.render(TEMPLATE)), _values(e2e_stack.render(TEMPLATE))
    assert first["POSTGRES_DB"] == "xype"
    assert not any("dev-only-change-me" in value for value in first.values())
    for name in ("POSTGRES_APP_PASSWORD", "REDIS_PASSWORD"):
        assert len(first[name]) >= 32
        assert first[name] != second[name]
    assert first["POSTGRES_APP_PASSWORD"] != first["REDIS_PASSWORD"]
    assert first["AUTH_SIGNING_KEY_CURRENT"].startswith("e2e-signing-1:")
    assert first["AUTH_PEPPER_CURRENT"].startswith("e2e-pepper-1:")
    assert first["KEK_CURRENT"].startswith("e2e-kek-1:")
    assert first["AUTH_SIGNING_KEY_PREVIOUS"] == ""
    assert first["BACKEND_PORT"] == "18443"


def test_comments_are_kept_as_they_are() -> None:
    assert e2e_stack.render(TEMPLATE).splitlines()[0] == TEMPLATE.splitlines()[0]


def test_writes_the_file_into_the_given_directory(tmp_path: Path) -> None:
    assert e2e_stack.main(["--out", str(tmp_path / "e2e")]) == 0
    written = _values((tmp_path / "e2e" / "stack.vars").read_text(encoding="utf-8"))
    assert written["KEK_CURRENT"].startswith("e2e-kek-1:")
    assert written["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318"


def test_refuses_a_directory_inside_the_repository() -> None:
    with pytest.raises(SystemExit):
        e2e_stack.main(["--out", str(e2e_stack.REPOSITORY / "deploy" / "e2e")])
    assert not (e2e_stack.REPOSITORY / "deploy" / "e2e" / "stack.vars").exists()


def test_a_second_run_replaces_the_file(tmp_path: Path) -> None:
    e2e_stack.main(["--out", str(tmp_path)])
    first = (tmp_path / "stack.vars").read_text(encoding="utf-8")
    e2e_stack.main(["--out", str(tmp_path)])
    assert (tmp_path / "stack.vars").read_text(encoding="utf-8") != first
