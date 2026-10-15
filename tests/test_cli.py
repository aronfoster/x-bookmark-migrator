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


def test_inventory_cli_partial_and_offline_export(run_cli, capsys, monkeypatch):
    run, store, calls = run_cli

    def handle(req):
        if req.url.path.endswith("me"):
            return httpx.Response(200, json={"data": {"id": "123"}})
        return httpx.Response(200, json={"data": [{"id": "10", "name": "PRIVATE folder"}]})

    assert (
        run(
            handle,
            [
                "inventory",
                "--app-owner-id",
                "123",
                "--max-calls",
                "2",
                "--per-call-reserve-usd",
                ".1",
                "--console-hard-cap-confirmed",
                "--auto-recharge-off-confirmed",
            ],
        )
        == 1
    )
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert "PRIVATE" not in output.out + output.err
    assert result["inventory_status"] == "partial" and result["folders"] == 1
    assert len(calls) == 2
    monkeypatch.delenv("X_CLIENT_ID")
    assert (
        run(lambda req: pytest.fail("offline export made network call"), ["inventory-export"]) == 0
    )
    exported = json.loads(capsys.readouterr().out)
    assert exported["private_export"] != result["private_export"]
    assert len(calls) == 2 and (store.root / exported["private_export"]).exists()


def test_inventory_cli_requires_console_confirmation(run_cli, capsys):
    run, _, calls = run_cli
    assert (
        run(
            lambda req: pytest.fail("network"),
            ["inventory", "--app-owner-id", "123", "--per-call-reserve-usd", ".1"],
        )
        == 1
    )
    assert not calls and "Confirm Console" in capsys.readouterr().err


def read_args(command="inventory"):
    return [
        command,
        "--per-call-reserve-usd",
        ".1",
        "--console-hard-cap-confirmed",
        "--auto-recharge-off-confirmed",
    ]


@pytest.mark.parametrize("command", ["pilot", "inventory"])
@pytest.mark.parametrize("owner", [None, "", "not-a-user-id", "12345678901234567890"])
def test_owner_configuration_errors_are_clear_and_send_no_requests(
    run_cli, monkeypatch, capsys, command, owner
):
    run, _, calls = run_cli
    monkeypatch.delenv("X_APP_OWNER_ID", raising=False)
    if owner is not None:
        monkeypatch.setenv("X_APP_OWNER_ID", owner)
    assert run(lambda req: pytest.fail("network"), read_args(command)) == 1
    output = capsys.readouterr()
    assert "X_APP_OWNER_ID" in output.err and "numeric X user ID" in output.err
    assert "JSON number" not in output.err
    assert not calls


@pytest.mark.parametrize("explicit", [None, "123", ""])
def test_owner_override_precedence(run_cli, monkeypatch, capsys, explicit):
    run, _, calls = run_cli
    monkeypatch.setenv("X_APP_OWNER_ID", "123" if explicit is None else "999")
    args = read_args("pilot") + ([] if explicit is None else ["--app-owner-id", explicit])

    def handle(req):
        return httpx.Response(
            200,
            json={"data": {"id": "123"}}
            if req.url.path.endswith("me")
            else {"data": [], "meta": {"result_count": 0}},
        )

    assert run(handle, args) == (1 if explicit == "" else 0)
    if explicit == "":
        assert not calls and "--app-owner-id" in capsys.readouterr().err
    else:
        assert len(calls) == 2 and calls[-1].url.path == "/2/users/123/bookmarks"


@pytest.mark.parametrize("option,value", [("--max-calls", "0"), ("--hydrate-batch-size", "101")])
def test_invalid_run_limit_does_not_replace_active_checkpoint(run_cli, option, value):
    from x_bookmark_migrator.inventory import Inventory

    run, store, calls = run_cli
    inv = Inventory(store, "123")
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    assert (
        run(
            lambda req: pytest.fail("network"),
            read_args() + ["--app-owner-id", "123", option, value],
        )
        == 1
    )
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before
    assert store.read("inventory-active.private.json")["pass_id"] == inv.state["pass_id"]
    assert not calls


def test_real_uv_env_file_inventory_workflow(tmp_path):
    """Execute uv and the installed CLI; all HTTP in the child is intercepted."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    uv = shutil.which("uv")
    assert uv, "uv is required to validate the documented workflow"
    store = Store(tmp_path / "state")
    store.write(
        "tokens.private.json",
        dict(
            access_token="synthetic",
            refresh_token="synthetic",
            scope=" ".join(SCOPES),
            client_id="synthetic-app",
            expires_at=time.time() + 7200,
        ),
    )
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        "X_CLIENT_ID=synthetic-app\nX_APP_OWNER_ID=123\n"
        "X_PER_CALL_RESERVE_USD=.2\nX_RUN_CAP_USD=.60\nX_MONTH_CAP_USD=1.00\n"
    )
    (tmp_path / "sitecustomize.py").write_text("""
import httpx
original = httpx.Client

def handle(req):
    assert req.method == "GET" and req.url.host == "api.x.com"
    if req.url.path == "/2/users/me":
        body = {"data": {"id": "123"}}
    elif req.url.path.endswith("/folders/10"):
        body = {"data": [{"id": str(i)} for i in range(100, 120)]}
    elif req.url.path.endswith("/folders"):
        body = {"data": [{"id": "10", "name": "synthetic"}]}
    elif req.url.path == "/2/users/123/bookmarks":
        body = {"data": [{"id": "100", "text": "synthetic"}],
                "meta": {"result_count": 1, "next_token": "next"}}
    else:
        assert req.url.path == "/2/tweets"
        body = {"data": [{"id": i, "text": "synthetic"} for i in req.url.params["ids"].split(",")]}
    return httpx.Response(200, json=body)

httpx.Client = lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
""")
    env = {k: v for k, v in os.environ.items() if not k.startswith("X_")}
    env["PYTHONPATH"] = (
        str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[1] / "src")
    )
    result = subprocess.run(
        [
            uv,
            "run",
            "--no-sync",
            "--env-file",
            str(env_file),
            "x-migrator",
            "--state-dir",
            str(store.root),
            *read_args(),
            "--max-calls",
            "6",
            "--page-size",
            "1",
            "--hydrate-batch-size",
            "1",
            "--max-pages-per-stream",
            "1",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr
    summary = json.loads(result.stdout)
    assert summary["records"] == summary["membership_counts"]["filed"] == 20
    assert summary["get_attempts_this_run"] == 6 and summary["lookup_requests_this_run"] == 2
    assert summary["hydration_counts"] == {"read": 3, "pending": 17}
    assert summary["estimated_reserved_usd"] == "0.6"
    assert "synthetic" not in result.stdout


def test_budget_environment_defaults(run_cli, monkeypatch, capsys):
    run, _, calls = run_cli
    monkeypatch.setenv("X_APP_OWNER_ID", "123")
    monkeypatch.setenv("X_PER_CALL_RESERVE_USD", ".2")
    monkeypatch.setenv("X_RUN_CAP_USD", ".4")
    monkeypatch.setenv("X_MONTH_CAP_USD", ".4")

    def handle(req):
        return httpx.Response(
            200,
            json={"data": {"id": "123"}}
            if req.url.path.endswith("me")
            else {"meta": {"result_count": 0}},
        )

    assert (
        run(handle, ["pilot", "--console-hard-cap-confirmed", "--auto-recharge-off-confirmed"]) == 0
    )
    assert len(calls) == 2
    assert json.loads(capsys.readouterr().out)["estimated_reserved_usd"] == "0.4"
