"""A remote URL is stored only after its credentials are removed (plan §3a), and only in
the allowed forms (`common_lib.repositories`, also a database CHECK). Errors never repeat
the URL: a token in it would reach the terminal or a log."""

import re
from urllib.parse import urlsplit

from common_lib.errors import InvalidValueError
from common_lib.remote_urls import REMOTE_URL_FORM, SCP_FORM

MAX_URL_LENGTH = 2048
_SCHEMES = frozenset({"https", "ssh", "git"})
_PRINTABLE_ASCII = re.compile(r"[\x21-\x7e]+")


def without_credentials(url: str) -> str:
    """`https://host/path`, `ssh://host:22/path` or `git://host/path`, rebuilt from the
    host and port alone, so any userinfo, query or fragment is dropped; or an scp-like
    `[git@]host:path`, where any other user name is dropped (it may be a token). Anything
    else, a local path included, is refused."""
    url = url.strip()
    if len(url) > MAX_URL_LENGTH or not _PRINTABLE_ASCII.fullmatch(url):
        raise InvalidValueError("a remote URL is printable ASCII, at most 2048 characters")
    cleaned = _from_url(url) if "://" in url else _from_scp(url)
    if not (re.fullmatch(REMOTE_URL_FORM, cleaned) or re.fullmatch(SCP_FORM, cleaned)):
        raise InvalidValueError("a remote URL must be https, ssh, git or scp-like git@host:path")
    return cleaned


def _from_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:  # a bad port or bracket, which may be part of a password
        raise InvalidValueError("the remote URL is malformed") from None
    if parts.scheme not in _SCHEMES or not host:
        raise InvalidValueError("a remote URL needs https, ssh or git and a host")
    if "@" in parts.query or "@" in parts.fragment:
        # `https://user:12345#rest@host` splits into host `user` and port 12345.
        raise InvalidValueError("a remote URL must not carry credentials")
    netloc = f"{host}:{port}" if port is not None else host
    return f"{parts.scheme}://{netloc}{parts.path}"


def _from_scp(url: str) -> str:
    address = re.split(r"[?#]", url, maxsplit=1)[0]
    user, at, rest = address.rpartition("@")
    if not at:
        return address
    return f"git@{rest}" if user == "git" else rest
