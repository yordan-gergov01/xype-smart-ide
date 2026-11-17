"""The key store's client and the KEK ring, built from a service's settings; shared by
the backend's and the indexer's composition roots."""

from redis.asyncio import Redis

from common_lib.security import Kek, KekRing
from common_lib.settings import KekSettings, KeyedSecret, KeyStoreSettings, parse_keyed_secret
from common_lib.tls import MINIMUM_TLS


def connect_keystore(settings: KeyStoreSettings) -> Redis:
    """TLS with the dev CA (ADR-0036), this service's ACL user and bounded timeouts. It
    connects on first use."""
    return Redis(
        host=settings.keystore_host,
        port=settings.keystore_port,
        username=settings.keystore_user,
        password=settings.keystore_password.get_secret_value(),
        ssl=True,
        ssl_ca_certs=str(settings.tls_ca_file),
        ssl_cert_reqs="required",
        ssl_check_hostname=True,
        ssl_min_version=MINIMUM_TLS,
        socket_timeout=settings.keystore_socket_timeout_seconds,
        socket_connect_timeout=settings.keystore_socket_timeout_seconds,
    )


def _kek(value: KeyedSecret) -> Kek:
    kek_id, material = parse_keyed_secret(value)
    return Kek(kek_id, material)


def kek_ring(settings: KekSettings) -> KekRing:
    previous = settings.kek_previous
    return KekRing(_kek(settings.kek_current), _kek(previous) if previous else None)
