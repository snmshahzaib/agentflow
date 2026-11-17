"""URL normalisation and safety validation for retrieved sources.

Two separate jobs that both operate on a URL, kept in one module because they
run together on every retrieved result:

**Normalisation** decides whether two URLs are the same source. Without it,
``http://Example.com/a?utm_source=x`` and ``https://example.com/a`` are two
rows, two citations and two entries in the source list for one page.

**Safety validation** decides whether a URL is fit to store and show. AgentFlow
does not fetch pages — the provider supplies the snippet — so this is not SSRF
mitigation; the SSRF surface is absent by design. It is defence in depth for a
URL that will be persisted, rendered as a link, and may one day be fetched by
something else.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.errors import UnsafeUrlError

#: Only these schemes may be stored. Anything else -- javascript:, data:,
#: file:, ftp: -- is rejected rather than normalised.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: A URL longer than this is refused rather than truncated: a truncated URL is
#: a different URL, and silently storing one would misattribute a citation.
MAX_URL_LENGTH = 2048

#: Query parameters that identify a campaign or a click, not a document.
#: Stripping them is what makes deduplication work across referrers.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_source_platform",
        "gclid",
        "gclsrc",
        "dclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref",
        "ref_src",
        "spm",
        "_ga",
        "yclid",
        "twclid",
        "si",
    }
)

#: Hostnames that name the local machine or a cloud metadata service.
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: A bare label with no dot is not a public host: "intranet", "router".
_HAS_DOT = re.compile(r"\.")


def normalise_url(raw: str) -> str:
    """Return a canonical form of ``raw`` for comparison and storage.

    Deliberately conservative. Lower-casing the path or sorting query
    parameters would merge URLs that are genuinely different documents on
    case-sensitive or order-sensitive servers, so neither is done.
    """
    url = raw.strip()
    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")

    # Drop a default port: ``https://a.test:443/x`` is ``https://a.test/x``.
    port = parts.port
    netloc = host
    if port is not None and _DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"

    # Credentials are never part of a source's identity, and keeping them
    # would store someone's token in a citation.
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(kept)

    # Fragments address a position within a document, not a document.
    return urlunsplit((scheme, netloc, path, query, ""))


def url_digest(raw: str) -> str:
    """Stable digest of the **normalised** URL, used for deduplication."""
    return hashlib.sha256(normalise_url(raw).encode()).hexdigest()


def validate_url(raw: str) -> str:
    """Return the normalised URL, or raise :class:`UnsafeUrlError`.

    Rejects rather than sanitises. A URL we cannot vouch for is dropped from
    the result set, which costs one source; quietly rewriting it into
    something that looks acceptable would misattribute a citation.
    """
    if not raw or not raw.strip():
        raise UnsafeUrlError("The source URL is empty.")

    if len(raw) > MAX_URL_LENGTH:
        raise UnsafeUrlError(
            f"The source URL exceeds {MAX_URL_LENGTH} characters.", length=len(raw)
        )

    try:
        parts = urlsplit(raw.strip())
    except ValueError as exc:
        raise UnsafeUrlError("The source URL could not be parsed.") from exc

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"Scheme {scheme or '(none)'!r} is not allowed for a source URL.",
            scheme=scheme,
        )

    if parts.username or parts.password:
        raise UnsafeUrlError("A source URL must not embed credentials.")

    try:
        host = parts.hostname
    except ValueError as exc:
        raise UnsafeUrlError("The source URL has an invalid host.") from exc

    if not host:
        raise UnsafeUrlError("The source URL has no host.")

    host = host.lower().rstrip(".")
    if host in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"Host {host!r} names the local machine.", host=host)

    # A literal IP is checked directly; a name is only rejected for shape,
    # because resolving it here would be both slow and a DNS-rebinding trap.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _HAS_DOT.search(host):
            raise UnsafeUrlError(f"Host {host!r} is not a public hostname.", host=host) from None
    else:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise UnsafeUrlError(f"Host {host} is not a public address.", host=str(address))

    try:
        # Accessing .port validates it; urlsplit defers the check until now.
        _ = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("The source URL has an invalid port.") from exc

    return normalise_url(raw)
