"""Synthetic identities only; exercise the parser shared with FOS-166."""

import pytest

from x_bookmark_migrator.catalog import post_id as legacy_post_id
from x_bookmark_migrator.core import PilotError
from x_bookmark_migrator.source import X_HOSTS, post_id, raindrop_post_id, source_url


@pytest.mark.parametrize(
    "host", [p + h for h in ("x.com", "twitter.com") for p in ("", "www.", "mobile.", "m.")]
)
@pytest.mark.parametrize("path", ["old_name/status", "renamed/status", "i/status", "i/web/status"])
@pytest.mark.parametrize("suffix", ["", "/", "/photo/1", "/video/12", "/photo/2/?s=20#fragment"])
def test_aliases(host, path, suffix):
    pid = "9007199254740993"
    url = f"https://{host}/{path}/{pid}{suffix}"
    assert post_id(url) == legacy_post_id(url) == raindrop_post_id(url) == pid
    assert source_url(url) == f"https://x.com/i/status/{pid}"


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/home",
        "https://mobile.twitter.com/search?q=101",
        "https://x.com/a/status/101/extra",
        "https://x.com/a/status/101/photo/0",
        "https://x.com:443/i/status/101",
        "https://user@x.com/i/status/101",
        "https://x.com/a/status/1010/video/x",
        "https://x.com/a/status/１０１",
        "ftp://x.com/a/status/101",
        "https://x.com/a/status/101\n",
        "https://x.com/?post=101",
        "https://x.com/#101",
        "https://x.com/?",
        "https://x.com/#",
        "https://x.com//",
        "https://x.com/%2F",
        "https://x.com:443/",
        "https://user@x.com/",
        "https://x.com/\n",
        "\x00https://x.com/",
        "ftp://x.com/",
        "https://x.com/synthetic_profile",
        "https://x.com/i/lists/101",
        "https://x.com/i/spaces/synthetic",
    ],
)
def test_unsupported_x_link_blocks_dedupe(url):
    with pytest.raises(PilotError):
        raindrop_post_id(url)


@pytest.mark.parametrize("value", [101, 101.0, True, None, "1e2", "x101"])
def test_ids_never_coerced(value):
    with pytest.raises(PilotError):
        post_id(value)


def test_foreign_domains_and_exact_ids():
    assert legacy_post_id is post_id
    for url in ("https://x.com.evil.invalid/a/status/101", "https://example.invalid/?x=101"):
        assert raindrop_post_id(url) is None
        with pytest.raises(PilotError):
            post_id(url)
    with pytest.raises(PilotError):
        post_id("00101")
    assert post_id("101") != post_id("1010")


@pytest.mark.parametrize("host", sorted(X_HOSTS))
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("suffix", ["", "/", "./"])
def test_bare_homepages_are_non_post_inventory_entries(host, scheme, suffix):
    link = f"{scheme}://{host}{suffix}"
    assert raindrop_post_id(link) is None
    with pytest.raises(PilotError):
        post_id(link)


@pytest.mark.parametrize(
    "value",
    ["0", "00", "0101", "https://x.com/a/status/0101", "https://twitter.com./i/status/0101"],
)
def test_zero_padded_identity_is_blocked(value):
    with pytest.raises(PilotError):
        post_id(value)
    if "://" in value:
        with pytest.raises(PilotError):
            raindrop_post_id(value)


@pytest.mark.parametrize("host", ["x.com.", "mobile.twitter.com.", "WWW.X.COM."])
def test_trailing_dot_is_same_x_host(host):
    assert raindrop_post_id(f"https://{host}/a/status/101") == "101"
    with pytest.raises(PilotError):
        raindrop_post_id(f"https://{host}/home")


@pytest.mark.parametrize(
    "link",
    ["mailto:a@example.invalid", "x.com/a/status/101", "https://t.co/abc", "https://t.co./abc"],
)
def test_ambiguous_primary_links_block_absence(link):
    with pytest.raises(PilotError):
        raindrop_post_id(link)


@pytest.mark.parametrize(
    "overrides", [[], None, {"0101": "label"}, {"999": "label"}, {"101": None}, {"101": " "}]
)
def test_invalid_category_overrides_fail_closed(overrides):
    from x_bookmark_migrator.catalog import apply_annotations

    with pytest.raises(PilotError):
        apply_annotations([{"post_id": "101", "versions": []}], {"category_overrides": overrides})
