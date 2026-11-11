"""The dev CA generator: layout, renewal, rotation and the limits it puts on the CA."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from devtools.dev_ca import (
    CA_DAYS,
    LEAF_DAYS,
    RENEW_DAYS,
    SERVICES,
    DevCaError,
    _load_ca,
    generate,
    main,
)

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_issues_one_leaf_per_service_signed_by_the_ca(tmp_path: Path) -> None:
    assert generate(tmp_path, now=NOW) == list(SERVICES)
    ca = load(tmp_path / "ca.crt")
    for service in SERVICES:
        leaf = load(tmp_path / service / "tls.crt")
        leaf.verify_directly_issued_by(ca)
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert set(names.get_values_for_type(x509.DNSName)) == {service, "localhost"}
        assert (tmp_path / service / "ca.crt").read_bytes() == (tmp_path / "ca.crt").read_bytes()
        # Only the service's own key: never the CA key.
        assert sorted(p.name for p in (tmp_path / service).iterdir()) == [
            "ca.crt",
            "tls.crt",
            "tls.key",
        ]


def test_ca_may_issue_only_for_the_stack(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    ca = load(tmp_path / "ca.crt")
    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert constraints.critical
    permitted = constraints.value.permitted_subtrees or []
    dns = {n.value for n in permitted if isinstance(n, x509.DNSName)}
    assert dns == {*SERVICES, "localhost"}
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 0


def test_a_second_run_keeps_current_certificates(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    before = (tmp_path / "redis" / "tls.crt").read_bytes()
    assert generate(tmp_path, now=NOW + timedelta(days=1)) == []
    assert (tmp_path / "redis" / "tls.crt").read_bytes() == before


def test_renews_leaves_close_to_expiry_but_keeps_the_ca(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    ca = (tmp_path / "ca.crt").read_bytes()
    later = NOW + timedelta(days=LEAF_DAYS - RENEW_DAYS + 1)
    assert generate(tmp_path, now=later) == list(SERVICES)
    assert (tmp_path / "ca.crt").read_bytes() == ca


def test_reissues_a_leaf_from_another_ca_or_with_a_missing_key(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    (tmp_path / "edge" / "tls.key").unlink()
    other = tmp_path / "other"
    generate(other, now=NOW)
    (tmp_path / "redis" / "tls.crt").write_bytes((other / "redis" / "tls.crt").read_bytes())
    assert generate(tmp_path, now=NOW) == ["edge", "redis"]


def test_reissues_an_unreadable_leaf_or_a_missing_ca_copy(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    (tmp_path / "postgres" / "tls.crt").write_text("not a certificate")
    (tmp_path / "rabbitmq" / "ca.crt").unlink()
    assert generate(tmp_path, now=NOW) == ["postgres", "rabbitmq"]


def test_reissues_a_leaf_whose_key_or_names_do_not_fit(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    other = tmp_path / "other"
    generate(other, now=NOW)
    # An interrupted run: a new key next to the old, still valid certificate.
    (tmp_path / "edge" / "tls.key").write_bytes((other / "edge" / "tls.key").read_bytes())
    ca = _load_ca(tmp_path)
    assert ca is not None
    no_san = (
        x509.CertificateBuilder()
        .subject_name(ca.cert.subject)
        .issuer_name(ca.cert.subject)
        .public_key(ca.key.public_key())
        .serial_number(1)
        .not_valid_before(NOW)
        .not_valid_after(NOW + timedelta(days=LEAF_DAYS))
        .sign(ca.key, hashes.SHA256())
    )
    (tmp_path / "redis" / "tls.crt").write_bytes(no_san.public_bytes(serialization.Encoding.PEM))
    assert generate(tmp_path, now=NOW) == ["edge", "redis"]


def test_refuses_a_ca_key_that_does_not_match_its_certificate(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    other = tmp_path / "other"
    generate(other, now=NOW)
    (tmp_path / "ca.key").write_bytes((other / "ca.key").read_bytes())
    with pytest.raises(DevCaError, match="does not match"):
        generate(tmp_path, now=NOW)


def test_replaces_an_empty_directory_docker_left_for_a_file(tmp_path: Path) -> None:
    """Docker Desktop creates a directory for a missing file bind source (ca.crt)."""
    (tmp_path / "ca.crt").mkdir(parents=True)
    assert generate(tmp_path, now=NOW) == list(SERVICES)
    assert (tmp_path / "ca.crt").is_file()


def test_refuses_a_non_empty_directory_in_place_of_a_file(tmp_path: Path) -> None:
    (tmp_path / "ca.crt").mkdir(parents=True)
    (tmp_path / "ca.crt" / "stray").write_text("x")
    with pytest.raises(DevCaError, match="is a directory"):
        generate(tmp_path, now=NOW)


def test_refuses_a_service_the_ca_does_not_cover(tmp_path: Path) -> None:
    generate(tmp_path, ("redis",), now=NOW)
    with pytest.raises(DevCaError, match="does not cover keystore"):
        generate(tmp_path, ("redis", "keystore"), now=NOW)
    assert generate(tmp_path, ("redis", "keystore"), rotate_ca=True, now=NOW) == [
        "redis",
        "keystore",
    ]


def test_rotation_replaces_the_ca_and_every_leaf(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    old = load(tmp_path / "ca.crt")
    assert generate(tmp_path, rotate_ca=True, now=NOW) == list(SERVICES)
    assert load(tmp_path / "ca.crt").serial_number != old.serial_number


def test_refuses_to_issue_past_the_ca_lifetime(tmp_path: Path) -> None:
    generate(tmp_path, now=NOW)
    with pytest.raises(DevCaError, match="rotate-ca"):
        generate(tmp_path, now=NOW + timedelta(days=CA_DAYS - LEAF_DAYS + 1))


def test_cli_reports_and_fails_cleanly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--out", str(tmp_path)]) == 0
    assert "issued: edge" in capsys.readouterr().out
    (tmp_path / "ca.key").write_text("not a key")
    assert main(["--out", str(tmp_path)]) == 1
    assert "--rotate-ca" in capsys.readouterr().err
