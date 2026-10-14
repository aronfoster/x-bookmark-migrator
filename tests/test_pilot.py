import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from x_bookmark_migrator.core import (
    SCOPES,
    Budget,
    Client,
    PilotError,
    Store,
    auth_start,
    exact_id,
    pilot,
)

OWNER = "1234567890123456789"
POST = "1934567890123456789"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private")


def tokens(store, expires=None):
    store.write(
        "tokens.private.json",
        dict(
            access_token="SECRET",
            refresh_token="REFRESH",
            scope=" ".join(SCOPES),
            expires_at=expires or time.time() + 3600,
            client_id="app",
        ),
    )


def client(store, handler):
    return Client(store, httpx.Client(transport=httpx.MockTransport(handler)), "app")


def budget(store, cap="1"):
    return Budget(store, "0.10", cap, "2")


def test_pilot_exact_strings_and_private_full_payload(store):
    tokens(store)
    calls = []
    body = {
        "data": [
            {
                "id": POST,
                "text": "synthetic short",
                "note_post": {"text": "x" * 400},
                "article": {"title": "synthetic"},
                "author_id": OWNER,
            }
        ],
        "includes": {"media": [{"type": "video", "variants": [{"url": "https://example.org"}]}]},
        "meta": {"result_count": 1, "next_token": "opaque"},
    }

    def handle(req):
        calls.append(req)
        assert req.method == "GET" and req.url.host == "api.x.com"
        return httpx.Response(200, json={"data": {"id": OWNER}} if len(calls) == 1 else body)

    summary = pilot(client(store, handle), budget(store), OWNER)
    assert len(calls) == 2
    assert calls[1].url.params["max_results"] == "5"
    assert "note_post" in calls[1].url.params["post.fields"]
    assert summary["has_next_page"] and summary["records"] == 1
    assert store.read(summary["private_file"])["response"] == body
    assert "SECRET" not in json.dumps(summary) and "synthetic" not in json.dumps(summary)
    assert (store.root / summary["private_file"]).stat().st_mode & 0o777 == 0o600


def test_refresh_rotates_before_read(store):
    tokens(store, 1)
    calls = []

    def handle(req):
        calls.append(req)
        if req.method == "POST":
            assert req.url.path == "/2/oauth2/token"
            assert b"refresh_token=REFRESH" in req.content
            return httpx.Response(
                200,
                json=dict(
                    access_token="NEW",
                    refresh_token="ROTATED",
                    token_type="bearer",
                    expires_in=7200,
                ),
            )
        assert store.read("tokens.private.json")["refresh_token"] == "ROTATED"
        assert req.headers["authorization"] == "Bearer NEW"
        return httpx.Response(200, json={"data": {"id": OWNER}})

    client(store, handle).get("/2/users/me", {}, budget(store).reserve)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_errors_redacted_and_no_retry(store, status):
    tokens(store)
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(status, json={"detail": "SECRET PRIVATE BODY"})

    with pytest.raises(PilotError) as error:
        client(store, handle).get("/2/users/me", {}, budget(store).reserve)
    assert "SECRET" not in str(error.value)
    assert str(status) in str(error.value) and len(calls) == 1
    assert store.read("budget.private.json")


def test_cap_prevents_network_and_persists_between_runs(store):
    tokens(store)
    calls = []
    api = client(store, lambda req: calls.append(req) or httpx.Response(200, json={}))
    with pytest.raises(PilotError, match="cap"):
        api.get("/2/users/me", {}, budget(store, "0.09").reserve)
    assert not calls
    Budget(store, "0.1", "0.2", "0.1").reserve()
    with pytest.raises(PilotError, match="cap"):
        Budget(store, "0.1", "0.2", "0.1").reserve()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_bad_budget(store, value):
    with pytest.raises(PilotError):
        Budget(store, value, "1", "1")


@pytest.mark.parametrize("value", [1234567890123456789, "1e19", "", "12/3", None])
def test_exact_id_rejects_lossy_or_unsafe(value):
    with pytest.raises(PilotError):
        exact_id(value)


def test_pkce_callback_state_and_grants(store):
    url = auth_start(store, "app")
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == SCOPES
    assert "bookmark.write" not in url
    calls = []

    def handle(req):
        calls.append(req)
        assert b"code_verifier=" in req.content
        return httpx.Response(
            200,
            json=dict(
                access_token="A",
                refresh_token="R",
                token_type="bearer",
                expires_in=7200,
                scope=" ".join(SCOPES),
            ),
        )

    api = client(store, handle)
    with pytest.raises(PilotError, match="state mismatch"):
        api.finish("http://127.0.0.1:8765/callback?state=wrong&code=SECRET")
    assert not calls
    api.finish("http://127.0.0.1:8765/callback?code=SECRET&state=" + query["state"][0])
    assert len(calls) == 1 and not store.read("pending.private.json")
    assert store.read("tokens.private.json")["access_token"] == "A"


def test_wrong_owner_stops_before_bookmarks(store):
    tokens(store)
    calls = []
    api = client(
        store, lambda req: calls.append(req) or httpx.Response(200, json={"data": {"id": "9"}})
    )
    with pytest.raises(PilotError, match="differs"):
        pilot(api, budget(store), OWNER)
    assert len(calls) == 1


def test_partial_errors_preserved_but_not_accepted(store):
    tokens(store)
    api = client(
        store,
        lambda req: httpx.Response(
            200,
            json=(
                {"data": {"id": OWNER}}
                if req.url.path.endswith("me")
                else {"data": [], "errors": [{"detail": "private content"}]}
            ),
        ),
    )
    with pytest.raises(PilotError, match="NOT accepted"):
        pilot(api, budget(store), OWNER)
    assert len(list(store.root.glob("pilot-*"))) == 1


def test_store_rejects_git_and_permissive_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(PilotError, match="Git"):
        Store(tmp_path / "private")
    (tmp_path / ".git").rmdir()
    tmp_path.chmod(0o755)
    with pytest.raises(PilotError, match="0700"):
        Store(tmp_path)


def test_state_lock(store):
    with store.locked(), pytest.raises(PilotError, match="Another"):
        with store.locked():
            pass


def test_refresh_failure_does_not_read_or_replace_token(store):
    tokens(store, 1)
    calls = []
    api = client(store, lambda req: calls.append(req) or httpx.Response(400, json={"secret": "x"}))
    with pytest.raises(PilotError):
        api.get("/2/users/me", {}, budget(store).reserve)
    assert len(calls) == 1 and calls[0].method == "POST"
    assert store.read("tokens.private.json")["refresh_token"] == "REFRESH"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": [{"id": 123, "text": "x"}]},
        {"data": [], "meta": {"next_token": 9}},
        {"data": [], "meta": {"result_count": 2}},
    ],
)
def test_malformed_bookmark_response(store, body):
    tokens(store)
    api = client(
        store,
        lambda req: httpx.Response(
            200, json=({"data": {"id": OWNER}} if req.url.path.endswith("me") else body)
        ),
    )
    with pytest.raises(PilotError):
        pilot(api, budget(store), OWNER)


def test_legacy_profile_and_empty_page(store):
    tokens(store)

    def handle(req):
        if req.url.path.endswith("me"):
            return httpx.Response(200, json={"data": {"id": OWNER}})
        assert "note_tweet" in req.url.params["tweet.fields"]
        assert "referenced_tweets.id.attachments.media_keys" in req.url.params["expansions"]
        return httpx.Response(200, json={"meta": {"result_count": 0}})

    result = pilot(client(store, handle), budget(store), OWNER, "tweet")
    assert result["records"] == 0 and not result["has_next_page"]


def test_wrong_grant_rejected(store):
    api = client(
        store,
        lambda req: httpx.Response(
            200,
            json={
                "scope": "tweet.read bookmark.write",
                "access_token": "A",
                "refresh_token": "R",
                "token_type": "bearer",
                "expires_in": 7200,
            },
        ),
    )
    with pytest.raises(PilotError, match="exactly"):
        api.token({"grant_type": "authorization_code"})
    assert not store.read("tokens.private.json")
