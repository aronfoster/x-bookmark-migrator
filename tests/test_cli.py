import json
import sys
import time

import httpx
import pytest

from x_bookmark_migrator import cli
from x_bookmark_migrator.core import SCOPES, Store


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    store = Store(tmp_path / "state")
    store.write(
        "tokens.private.json",
        dict(
            access_token="SECRET",
            refresh_token="SECRET",
            scope=" ".join(SCOPES),
            client_id="app",
            expires_at=time.time() + 7200,
        ),
    )
    monkeypatch.setenv("X_CLIENT_ID", "app")
    real_client = httpx.Client
    calls = []

    def run(handler, command=None):
        def handle(req):
            calls.append(req)
            return handler(req)

        monkeypatch.setattr(
            cli.httpx,
            "Client",
            lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["x-migrator", "--state-dir", str(store.root)]
            + (
                command
                if command is not None
                else [
                    "pilot",
                    "--app-owner-id",
                    "123",
                    "--per-call-reserve-usd",
                    "0.1",
                    "--console-hard-cap-confirmed",
                    "--auto-recharge-off-confirmed",
                ]
            ),
        )
        return cli.main()

    return run, store, calls


@pytest.mark.parametrize(
    "kind",
    [
        "https://api.x.com/2/problems/resource-not-found",
        "https://api.x.com/2/problems/not-authorized-for-resource",
        "PRIVATE arbitrary type",
    ],
)
def test_partial_prints_summary_without_private_values(run_cli, capsys, kind):
    run, _, calls = run_cli

    def handle(req):
        if req.url.path.endswith("me"):
            return httpx.Response(200, json={"data": {"id": "123"}})
        return httpx.Response(
            200,
            headers={"x-rate-limit-remaining": "99"},
            json={
                "data": [{"id": "456", "text": "PRIVATE content"}],
                "errors": [{"type": kind, "detail": "PRIVATE detail"}],
            },
        )

    assert run(handle) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["pilot_status"] == "partial" and result["records"] == 1
    assert result["partial_error_types"] == ([kind] if kind.startswith("https:") else ["unknown"])
    assert result["reads"][1]["rate_limits"]["x-rate-limit-remaining"] == "99"
    assert "PRIVATE" not in output.out + output.err
    assert len(calls) == 2


@pytest.mark.parametrize("data", [[], None, "PRIVATE"])
def test_malformed_identity_no_traceback(run_cli, capsys, data):
    run, _, calls = run_cli
    assert run(lambda req: httpx.Response(200, json={"data": data})) == 1
    output = capsys.readouterr()
    assert "Malformed identity" in output.err and "Traceback" not in output.err
    assert len(calls) == 1


def test_corrupted_token_no_network(run_cli, capsys):
    run, store, calls = run_cli
    store.write("tokens.private.json", ["PRIVATE"])
    assert run(lambda req: pytest.fail("network")) == 1
    assert not calls and "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("status", [400, 401])
def test_refresh_recovery(run_cli, capsys, status):
    run, store, calls = run_cli
    token = store.read("tokens.private.json")
    token["expires_at"] = 1
    store.write("tokens.private.json", token)
    assert run(lambda req: httpx.Response(status, text="PRIVATE")) == 1
    output = capsys.readouterr()
    assert "refresh rejected" in output.err and "auth-start" in output.err
    assert "PRIVATE" not in output.err and len(calls) == 1
    assert calls[0].method == "POST"


def test_console_confirmation_required(run_cli, capsys):
    run, _, calls = run_cli
    assert (
        run(
            lambda req: pytest.fail("network"),
            ["pilot", "--app-owner-id", "123", "--per-call-reserve-usd", "0.1"],
        )
        == 1
    )
    assert not calls and "Confirm Console" in capsys.readouterr().err


def test_callback_requires_terminal(run_cli, monkeypatch, capsys):
    run, _, calls = run_cli
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert run(lambda req: pytest.fail("network"), ["auth-finish"]) == 1
    assert not calls and "interactive terminal" in capsys.readouterr().err
