"""Local dev CA: one CA and one server certificate per TLS service of the stack (ADR-0036).

    python tools/server_tool.py python -m devtools.dev_ca             # create or renew
    python tools/server_tool.py python -m devtools.dev_ca --rotate-ca # new CA, re-trust

Writes into deploy/certs/ (Git ignores it). The CA key stays in deploy/certs/ca.key and
is never mounted into a container; each service directory holds only that service's
certificate and key plus the CA certificate. The CA carries name constraints: even a
leaked key can issue only for the stack's names and loopback, so never add the CA to an
OS trust store.
"""

import argparse
import ipaddress
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "deploy" / "certs"
# Services that terminate TLS (the edge proxy, the backend and the stores).
SERVICES = ("edge", "backend", "postgres", "redis", "keystore", "rabbitmq")
# Every leaf also answers on these names: healthchecks and tests connect over loopback.
LOOPBACK_DNS = "localhost"
LOOPBACK_IP = ipaddress.ip_address("127.0.0.1")
CA_DAYS = 730
LEAF_DAYS = 90
# A leaf is renewed when fewer days than this are left; the CA must outlive a new leaf.
RENEW_DAYS = 30
CA_NAME = "XYPE IDE local dev CA"


class DevCaError(Exception):
    """The CA cannot issue certificates as asked (expiring, unreadable)."""


@dataclass(frozen=True, slots=True)
class Authority:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _clear_stray_dir(path: Path) -> None:
    """Docker Desktop creates an empty directory for a missing file bind source (for
    example `ca.crt`, when compose ran before this tool); a file can then take its place."""
    if not path.is_dir():
        return
    try:
        path.rmdir()
    except OSError:
        raise DevCaError(f"{path} is a directory, not a file; remove it and run again") from None


def _write(path: Path, data: bytes, mode: int) -> None:
    """Atomic, and created with its final mode: a key is never readable by others."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _clear_stray_dir(path)
    # Same suffix as the target, so the ignore rules (*.key) cover the temporary file too.
    tmp = path.with_name(f"{path.stem}.tmp{path.suffix}")
    tmp.unlink(missing_ok=True)
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as file:
            file.write(data)
        tmp.chmod(mode)  # the umask may have narrowed it
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _permitted_names(services: tuple[str, ...]) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = [x509.DNSName(s) for s in (*services, LOOPBACK_DNS)]
    names.append(x509.IPAddress(ipaddress.ip_network("127.0.0.0/8")))
    return names


def create_ca(services: tuple[str, ...], now: datetime) -> Authority:
    key = _new_key()
    public = key.public_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(CA_NAME))
        .issuer_name(_name(CA_NAME))
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=_permitted_names(services), excluded_subtrees=None
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .sign(key, hashes.SHA256())
    )
    return Authority(cert, key)


def issue_leaf(
    ca: Authority, service: str, now: datetime
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = _new_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(service))
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=LEAF_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(_leaf_names(service)), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                ca.cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
            ),
            critical=False,
        )
        .sign(ca.key, hashes.SHA256())
    )
    return cert, key


def _leaf_names(service: str) -> list[x509.GeneralName]:
    return [x509.DNSName(service), x509.DNSName(LOOPBACK_DNS), x509.IPAddress(LOOPBACK_IP)]


def _load_ca(out_dir: Path) -> Authority | None:
    cert_path, key_path = out_dir / "ca.crt", out_dir / "ca.key"
    _clear_stray_dir(cert_path)
    if not (cert_path.exists() and key_path.exists()):
        return None
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise DevCaError(f"cannot read the CA in {out_dir}; run with --rotate-ca") from None
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise DevCaError(f"{key_path} is not an EC key; run with --rotate-ca")
    # An interrupted run can leave a new key next to the old certificate.
    if cert.public_key() != key.public_key():
        raise DevCaError(f"the CA key in {out_dir} does not match ca.crt; run with --rotate-ca")
    return Authority(cert, key)


def _key_matches(cert: x509.Certificate, key_path: Path) -> bool:
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return False
    return key.public_key() == cert.public_key()


def _leaf_is_current(service_dir: Path, service: str, ca: Authority, now: datetime) -> bool:
    """False for anything missing, unreadable, foreign, stale, with other names, or with
    a key that does not belong to the certificate."""
    cert_path, ca_path, key_path = (service_dir / n for n in ("tls.crt", "ca.crt", "tls.key"))
    if not all(p.exists() for p in (cert_path, ca_path, key_path)):
        return False
    if ca_path.read_bytes() != ca.cert.public_bytes(serialization.Encoding.PEM):
        return False
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        cert.verify_directly_issued_by(ca.cert)
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (ValueError, TypeError, InvalidSignature, x509.ExtensionNotFound):
        return False
    fresh = cert.not_valid_after_utc - now > timedelta(days=RENEW_DAYS)
    return fresh and set(names) == set(_leaf_names(service)) and _key_matches(cert, key_path)


def _ensure_ca(out_dir: Path, services: tuple[str, ...], rotate: bool, now: datetime) -> Authority:
    ca = None if rotate else _load_ca(out_dir)
    if ca is None:
        ca = create_ca(services, now)
        _write(out_dir / "ca.key", _key_pem(ca.key), 0o600)
        _write(out_dir / "ca.crt", ca.cert.public_bytes(serialization.Encoding.PEM), 0o644)
    if ca.cert.not_valid_after_utc - now < timedelta(days=LEAF_DAYS):
        raise DevCaError("the dev CA expires within a leaf's lifetime; run with --rotate-ca")
    missing = sorted(set(services) - _covered_names(ca.cert))
    if missing:
        # Its name constraints would make every client refuse a leaf for these names.
        raise DevCaError(f"the dev CA does not cover {', '.join(missing)}; run with --rotate-ca")
    return ca


def _covered_names(cert: x509.Certificate) -> set[str]:
    try:
        constraints = cert.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return set()
    return {n.value for n in constraints.permitted_subtrees or [] if isinstance(n, x509.DNSName)}


def generate(
    out_dir: Path = DEFAULT_DIR,
    services: tuple[str, ...] = SERVICES,
    *,
    rotate_ca: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Create the CA if missing and (re)issue every missing, stale or foreign leaf.

    Returns the services whose certificate was issued. Leaf keys are readable by all
    (0644): containers read them under their own user ids; deploy/certs/ itself is
    owner-only.
    """
    now = now or datetime.now(UTC)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        out_dir.chmod(0o700)
    except (NotImplementedError, OSError):
        # reason: the 0644 leaf keys rely on an owner-only folder; say so, do not hide it
        print(f"warning: could not make {out_dir} owner-only", file=sys.stderr)  # noqa: T201
    ca = _ensure_ca(out_dir, services, rotate_ca, now)
    ca_pem = ca.cert.public_bytes(serialization.Encoding.PEM)
    issued = []
    for service in services:
        service_dir = out_dir / service
        if _leaf_is_current(service_dir, service, ca, now):
            continue
        cert, key = issue_leaf(ca, service, now)
        _write(service_dir / "tls.key", _key_pem(key), 0o644)
        _write(service_dir / "tls.crt", cert.public_bytes(serialization.Encoding.PEM), 0o644)
        _write(service_dir / "ca.crt", ca_pem, 0o644)
        issued.append(service)
    return issued


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR)
    parser.add_argument(
        "--rotate-ca", action="store_true", help="replace the CA and every certificate"
    )
    args = parser.parse_args(argv)
    try:
        issued = generate(args.out, rotate_ca=args.rotate_ca)
    except DevCaError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201 - CLI output
        return 1
    print(f"dev CA in {args.out}; issued: {', '.join(issued) or 'none (all current)'}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
