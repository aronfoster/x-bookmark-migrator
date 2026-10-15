"""Bounded reads, PKCE, and private local state for the Linux pilot."""

import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

SCOPES = frozenset("tweet.read users.read bookmark.read offline.access".split())
CALLBACK = "http://127.0.0.1:8765/callback"
API = "https://api.x.com"


class PilotError(Exception):
    """Safe to show to the operator: never contains remote bodies or secrets."""

    def __init__(self, message, summary=None, code="read_failed"):
        super().__init__(message)
        self.summary = summary
        self.code = code


def exact_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
        raise PilotError("API ID must be a decimal string, never a JSON number.")
    return value


class Store:
    def __init__(self, root: Path):
        self.root = root.expanduser().absolute()
        if self.root.is_symlink():
            raise PilotError("Private directory must not be a symlink.")
        self.root = self.root.resolve()
        if any((p / ".git").exists() for p in [self.root, *self.root.parents]):
            raise PilotError("Private directory must be outside a Git checkout.")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        st = self.root.stat()
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise PilotError("Private directory must be owned by this user with mode 0700.")

    def read(self, name):
        path = self.root / name
        if path.is_symlink():
            raise PilotError("Private file must not be a symlink.")
        if not path.exists():
            return None
        if path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
            raise PilotError("Private file must be owner-only.")
        return json.loads(path.read_text())

    def write(self, name, value):
        fd, temporary = tempfile.mkstemp(dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / name)
            directory = os.open(self.root, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def locked(self):
        fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PilotError("Another command is using this state directory.") from None
            yield
        finally:
            os.close(fd)


def auth_start(store, client_id):
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    store.write(
        "pending.private.json",
        dict(verifier=verifier, state=state, client_id=client_id, created=time.time()),
    )
    return "https://x.com/i/oauth2/authorize?" + urlencode(
        dict(
            response_type="code",
            client_id=client_id,
            redirect_uri=CALLBACK,
            scope=" ".join(sorted(SCOPES)),
            state=state,
            code_challenge=challenge.decode().rstrip("="),
            code_challenge_method="S256",
        )
    )


def decode(response):
    if response.status_code != 200:
        hints = {
            401: "Reauthorize OAuth.",
            403: "Check entitlement and scopes; stopped.",
            429: "Rate limited; wait before a new manual run.",
        }
        raise PilotError(
            f"X HTTP {response.status_code}. "
            + hints.get(response.status_code, "Stopped without retry; inspect Console."),
            code=f"http_{response.status_code}",
        )
    try:
        body = response.json()
    except ValueError:
        raise PilotError("X returned invalid JSON.") from None
    if not isinstance(body, dict):
        raise PilotError("X returned an unexpected response shape.")
    return body


class Client:
    def __init__(self, store, http, client_id, client_secret=None):
        self.observations = []
        self.store, self.http = store, http
        self.client_id, self.client_secret = client_id, client_secret

    def token(self, data, prior=None):
        auth = (self.client_id, self.client_secret) if self.client_secret else None
        response = self.http.post(
            API + "/2/oauth2/token",
            data={**data, "client_id": self.client_id},
            auth=auth,
        )
        if response.status_code in (400, 401):
            operation = "refresh" if data.get("grant_type") == "refresh_token" else "exchange"
            raise PilotError(
                f"OAuth token {operation} rejected (HTTP {response.status_code}); "
                "verify client configuration, then run auth-start and auth-finish again."
            )
        body = decode(response)
        scopes = body.get("scope", prior.get("scope") if prior else None)
        if not isinstance(scopes, str) or set(scopes.split()) != SCOPES:
            raise PilotError("OAuth grant must contain exactly the requested read scopes.")
        for field in ("access_token", "refresh_token"):
            if not isinstance(body.get(field), str) or not body[field]:
                raise PilotError("OAuth response missing access or rotated refresh token.")
        lifetime = body.get("expires_in")
        if type(lifetime) is not int or lifetime <= 0:
            raise PilotError("OAuth response missing valid expiry.")
        if str(body.get("token_type", "")).lower() != "bearer":
            raise PilotError("Unexpected OAuth token type.")
        saved = {key: body[key] for key in ("access_token", "refresh_token")}
        saved.update(scope=scopes, expires_at=time.time() + lifetime, client_id=self.client_id)
        self.store.write("tokens.private.json", saved)
        return saved

    def finish(self, redirect):
        pending = self.store.read("pending.private.json")
        if (
            not pending
            or time.time() - pending["created"] > 600
            or pending["client_id"] != self.client_id
        ):
            raise PilotError("Authorization expired or client changed; start again.")
        url = urlsplit(redirect)
        if (url.scheme, url.netloc, url.path) != ("http", "127.0.0.1:8765", "/callback"):
            raise PilotError("Unexpected callback URL.")
        query = parse_qs(url.query)
        if (
            len(query.get("state", [])) != 1
            or not secrets.compare_digest(query["state"][0], pending["state"])
            or len(query.get("code", [])) != 1
            or "error" in query
        ):
            raise PilotError("OAuth callback denied, malformed, or state mismatch.")
        # Consume before exchange: an uncertain exchange requires new consent, not replay.
        (self.store.root / "pending.private.json").unlink()
        self.token(
            dict(
                grant_type="authorization_code",
                code=query["code"][0],
                code_verifier=pending["verifier"],
                redirect_uri=CALLBACK,
            )
        )

    def get(self, path, params, reserve):
        token = self.store.read("tokens.private.json")
        if not isinstance(token, dict) or token.get("client_id") != self.client_id:
            raise PilotError("Run auth-start and auth-finish for this client first.")
        if set(token.get("scope", "").split()) != SCOPES:
            raise PilotError("Stored grant does not match read-only scopes.")
        if token["expires_at"] <= time.time() + 60:
            token = self.token(
                dict(grant_type="refresh_token", refresh_token=token["refresh_token"]), prior=token
            )
        reserve()
        # Never retry a potentially billable read (including 401/429).
        response = self.http.get(
            API + path,
            params=params,
            headers={
                "Authorization": "Bearer " + token["access_token"],
            },
        )
        # Persist every completed GET before HTTP/JSON/schema validation. Never token responses.
        name = "read-" + secrets.token_hex(12) + ".private.json"
        rate_limits = {}
        for header in ("x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset"):
            value = response.headers.get(header)
            if value is not None and re.fullmatch(r"[0-9]{1,20}", value):
                rate_limits[header] = value
        try:
            payload = {"response": response.json()}
        except ValueError:
            payload = {"response_text": response.text}
        observation = {
            "status": response.status_code,
            "rate_limits": rate_limits,
            "private_file": name,
        }
        self.store.write(
            name,
            {
                **payload,
                **observation,
                "endpoint": path,
                "request": params,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "not_an_archive": True,
            },
        )
        self.observations.append(observation)
        try:
            return decode(response)
        except PilotError as exc:
            raise PilotError(
                str(exc) + " Read evidence: " + json.dumps(observation), code=exc.code
            ) from None


class Budget:
    """Reservations are estimates, retained even on failures; Console is the hard cap."""

    def __init__(self, store, per_call, run_cap, month_cap):
        self.store = store
        self.per_call, self.run_cap, self.month_cap = map(Decimal, (per_call, run_cap, month_cap))
        if any(not v.is_finite() or v <= 0 for v in (self.per_call, self.run_cap, self.month_cap)):
            raise PilotError("Budget values must be finite and positive.")
        self.used = Decimal(0)

    def reserve(self):
        month = datetime.now(UTC).strftime("%Y-%m")
        ledger = self.store.read("budget.private.json") or {}
        used = Decimal(ledger.get(month, "0"))
        if self.used + self.per_call > self.run_cap or used + self.per_call > self.month_cap:
            raise PilotError("Estimated spend cap reached; no read sent.", code="spend_cap")
        ledger[month] = str(used + self.per_call)
        self.store.write("budget.private.json", ledger)
        self.used += self.per_call


def pilot(client, budget, owner_id, dialect="post"):
    owner_id = exact_id(owner_id)
    me = client.get("/2/users/me", {}, budget.reserve)
    identity = me.get("data")
    if not isinstance(identity, dict):
        raise PilotError(
            "Malformed identity response; private response saved, stopped before bookmarks."
        )
    if me.get("errors") or exact_id(identity.get("id")) != owner_id:
        raise PilotError("Authenticated user differs from configured developer-app owner.")
    params = {"max_results": "5", **post_fields(dialect)}
    body = client.get(f"/2/users/{owner_id}/bookmarks", params, budget.reserve)
    meta = body.get("meta", {})
    if not isinstance(meta, dict):
        raise PilotError("Malformed pagination metadata; private response saved.")
    if "data" not in body and meta.get("result_count") != 0:
        if not body.get("errors"):
            raise PilotError("Missing bookmark data and empty-page metadata.")
    data = body.get("data", [])
    if not isinstance(data, list) or len(data) > 5:
        raise PilotError("Unexpected bookmark page size or shape.")
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise PilotError("Malformed bookmark record.")
        exact_id(item.get("id"))
        if "author_id" in item:
            exact_id(item["author_id"])
    if "next_token" in meta and (not isinstance(meta["next_token"], str) or not meta["next_token"]):
        raise PilotError("Malformed pagination metadata.")
    if "result_count" in meta and (
        type(meta["result_count"]) is not int or meta["result_count"] != len(data)
    ):
        raise PilotError("Result count does not match records.")
    includes = body.get("includes", {})
    if not isinstance(includes, dict):
        raise PilotError("Malformed includes; private response saved.")
    media = includes.get("media", [])
    if not isinstance(media, list) or any(not isinstance(item, dict) for item in media):
        raise PilotError("Malformed media expansion; private response saved.")
    errors = body.get("errors", [])
    if not isinstance(errors, list):
        raise PilotError("Malformed errors array; private response saved.")
    # Never print arbitrary remote strings, even when supplied in the type field.
    known_types = {
        f"https://{host}/2/problems/{kind}"
        for host in ("api.x.com", "api.twitter.com")
        for kind in ("resource-not-found", "not-authorized-for-resource")
    }
    error_types = sorted(
        {
            error["type"]
            if isinstance(error, dict)
            and isinstance(error.get("type"), str)
            and error["type"] in known_types
            else "unknown"
            for error in errors
        }
    )
    summary = {
        "partial_error_types": error_types,
        "pilot_status": "partial" if errors else "read_complete",
        "records": len(data),
        "has_next_page": bool(meta.get("next_token")),
        "partial_errors": bool(body.get("errors")),
        "field_dialect": dialect,
        "estimated_reserved_usd": str(budget.used),
        "observed_charge": "unverified",
        "private_file": client.observations[-1]["private_file"],
        "reads": client.observations,
        "includes": sorted(includes),
        "media_fields": sorted({key for item in media for key in item}),
        "fields": sorted({k for item in data for k in item}),
    }
    if body.get("errors"):
        raise PilotError("Partial API errors; diagnostics available, pilot NOT accepted.", summary)
    return summary


def post_fields(dialect="post"):
    """Shared documented field profiles for bookmarks and folder-only lookup."""
    params = {
        "media.fields": "type,url,variants,preview_image_url,alt_text,duration_ms,width,height",
    }
    if dialect == "tweet":
        params.update(
            {
                "tweet.fields": ",".join(
                    (
                        "created_at",
                        "author_id",
                        "attachments",
                        "referenced_tweets",
                        "entities",
                        "note_tweet",
                        "article",
                    )
                ),
                "expansions": "author_id,attachments.media_keys,referenced_tweets.id,"
                "referenced_tweets.id.attachments.media_keys",
            }
        )
    elif dialect == "post":
        params.update(
            {
                "post.fields": "id,text,created_at,attachments,entities,note_post,article",
                "expansions": "author_id,attachments.media_keys,referenced_posts",
            }
        )
    else:
        raise PilotError("Unknown field dialect.")
    return params
