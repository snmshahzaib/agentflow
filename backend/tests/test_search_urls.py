"""URL normalisation and safety validation.

Normalisation decides whether two URLs are the same source, so getting it
wrong means duplicate citations and a padded source list. Validation decides
what is fit to persist and render.
"""

from __future__ import annotations

import pytest

from app.adapters.search.urls import (
    MAX_URL_LENGTH,
    normalise_url,
    url_digest,
    validate_url,
)
from app.domain.errors import UnsafeUrlError


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Scheme and host case carry no meaning.
            ("HTTPS://Example.COM/a", "https://example.com/a"),
            # Default ports are implied.
            ("https://example.com:443/a", "https://example.com/a"),
            ("http://example.com:80/a", "http://example.com/a"),
            # A non-default port does matter.
            ("https://example.com:8443/a", "https://example.com:8443/a"),
            # A fragment addresses a position, not a document.
            ("https://example.com/a#section-3", "https://example.com/a"),
            # A trailing slash on a path is the same document.
            ("https://example.com/a/", "https://example.com/a"),
            # The root path is preserved.
            ("https://example.com", "https://example.com/"),
            ("https://example.com/", "https://example.com/"),
            # A trailing dot on the host is the same host.
            ("https://example.com./a", "https://example.com/a"),
        ],
    )
    def test_equivalent_urls_normalise_together(self, raw: str, expected: str) -> None:
        assert normalise_url(raw) == expected

    @pytest.mark.parametrize(
        "tracking",
        [
            "utm_source=newsletter",
            "utm_medium=email&utm_campaign=q4",
            "fbclid=abc123",
            "gclid=xyz",
            "mc_eid=99",
            "igshid=zz",
        ],
    )
    def test_campaign_parameters_are_stripped(self, tracking: str) -> None:
        """They identify a click, not a document. Keeping them means one page
        becomes several sources."""
        assert normalise_url(f"https://example.com/a?{tracking}") == "https://example.com/a"

    def test_meaningful_query_parameters_are_kept(self) -> None:
        """Stripping these would merge genuinely different documents."""
        assert (
            normalise_url("https://example.com/search?q=airflow&page=2")
            == "https://example.com/search?q=airflow&page=2"
        )

    def test_tracking_is_removed_without_disturbing_real_parameters(self) -> None:
        assert (
            normalise_url("https://example.com/a?id=7&utm_source=x&page=2")
            == "https://example.com/a?id=7&page=2"
        )

    def test_parameter_order_is_preserved(self) -> None:
        """Sorting would merge URLs that order-sensitive servers treat as
        different documents."""
        assert normalise_url("https://e.com/a?b=2&a=1") == "https://e.com/a?b=2&a=1"

    def test_path_case_is_preserved(self) -> None:
        """Paths are case-sensitive on most servers; lower-casing would merge
        two different documents."""
        assert normalise_url("https://e.com/Guide") == "https://e.com/Guide"


class TestDigest:
    def test_equivalent_urls_share_a_digest(self) -> None:
        """This is what makes deduplication work across referrers."""
        a = url_digest("https://example.com/a?utm_source=twitter")
        b = url_digest("HTTPS://Example.com/a/#top")
        assert a == b

    def test_different_documents_do_not_collide(self) -> None:
        assert url_digest("https://e.com/a") != url_digest("https://e.com/b")

    def test_a_digest_is_a_full_sha256(self) -> None:
        assert len(url_digest("https://e.com/a")) == 64


class TestSafety:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/a",
            "http://sub.example.co.uk/path?q=1",
            "https://example.com:8443/a",
        ],
    )
    def test_ordinary_public_urls_are_accepted(self, url: str) -> None:
        assert validate_url(url).startswith(("http://", "https://"))

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>x</script>",
            "file:///etc/passwd",
            "ftp://example.com/a",
            "//example.com/a",
            "example.com/a",
        ],
    )
    def test_non_http_schemes_are_refused(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/a",
            "http://127.0.0.1/a",
            "http://[::1]/a",
            "http://10.0.0.5/a",
            "http://192.168.1.1/a",
            "http://172.16.0.1/a",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://0.0.0.0/a",
        ],
    )
    def test_private_loopback_and_metadata_hosts_are_refused(self, url: str) -> None:
        """AgentFlow does not fetch pages, so this is not SSRF mitigation —
        the surface is absent by design. It is defence in depth for a URL
        that gets persisted, rendered as a link, and may one day be fetched
        by something else."""
        with pytest.raises(UnsafeUrlError):
            validate_url(url)

    def test_embedded_credentials_are_refused(self) -> None:
        """Accepting these would store someone's token inside a citation."""
        with pytest.raises(UnsafeUrlError, match="credentials"):
            validate_url("https://user:secret@example.com/a")

    def test_a_bare_hostname_is_refused(self) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_url("http://intranet/a")

    def test_an_overlong_url_is_refused_not_truncated(self) -> None:
        """A truncated URL is a different URL; storing one would misattribute
        a citation."""
        with pytest.raises(UnsafeUrlError, match="exceeds"):
            validate_url("https://example.com/" + "x" * (MAX_URL_LENGTH + 1))

    @pytest.mark.parametrize("url", ["", "   ", "https://"])
    def test_empty_or_hostless_urls_are_refused(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError):
            validate_url(url)

    def test_validation_returns_the_normalised_form(self) -> None:
        """So a caller cannot accidentally store the raw URL after validating."""
        assert validate_url("HTTPS://Example.com/a/?utm_source=x#frag") == "https://example.com/a"
