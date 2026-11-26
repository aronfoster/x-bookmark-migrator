"""Synthetic offline accounting at the CLI and both paid-request reservation boundaries."""

import json
import os
import sys
from datetime import UTC, datetime

import pytest

from x_bookmark_migrator import billing
from x_bookmark_migrator.cli import main
from x_bookmark_migrator.core import Budget, PilotError, Store
from x_bookmark_migrator.worker import OPERATIONS, CumulativeBudget


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "synthetic")
    store.write(billing.LEDGER, {"2000-01": "13.000", "2000-02": "6.99"})
    february = datetime(2000, 2, 15, tzinfo=UTC).timestamp()
    os.utime(store.path(billing.LEDGER), (february, february))
    store.write(billing.OBSERVATION, {"actual_usd": "1", "observed_at": "2000-03-01T00:00Z"})
    return store


def cli(store, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["xbm", "--state-dir", str(store.root), *args])
    return main()


def settlement_args(digest):
    return [
        "billing-settle",
        "--expected-cutoff",
        digest,
        "--provider-cumulative-usd",
        "19.98",
        "--settled-through",
        "2000-03-02T00:00Z",
        "--evidence-sha256",
        "1" * 64,
        "--attest-full-coverage",
    ]


def settle(store, monkeypatch):
    return cli(store, monkeypatch, *settlement_args(billing.cutoff(store)["cutoff_sha256"]))


def manual(store):
    return Budget(store, ".01", ".25", "1")


def worker(store):
    return CumulativeBudget(store, dict.fromkeys(OPERATIONS, ".01"), ".25", 10)


def test_workflow(store, monkeypatch, capsys):
    ledger = store.path(billing.LEDGER).read_bytes()
    observation = store.path(billing.OBSERVATION).read_bytes()
    assert cli(store, monkeypatch, "billing-cutoff") == 0
    snapshot = json.loads(capsys.readouterr().out)
    args = settlement_args(snapshot["cutoff_sha256"])
    with store.locked():
        assert cli(store, monkeypatch, *args) == 1
    for budget in (manual, worker):
        with pytest.raises(PilotError, match="cap"):
            budget(store).reserve()
    assert cli(store, monkeypatch, *args) == 0
    assert cli(store, monkeypatch, *args) == 1
    assert store.path(billing.LEDGER).read_bytes() == ledger
    assert store.path(billing.OBSERVATION).read_bytes() == observation
    manual(store).reserve()
    worker(store).reserve()
    for budget in (manual, worker):
        with pytest.raises(PilotError, match="cap"):
            budget(store).reserve()
    assert store.read(billing.LEDGER) == {
        **snapshot["reserved_by_month_usd"],
        datetime.now(UTC).strftime("%Y-%m"): "0.02",
    }


@pytest.mark.parametrize("change", ["ledger", "observation", "partial", "earlier", "lower"])
def test_refuse_invalid_coverage(store, monkeypatch, change):
    args = settlement_args(billing.cutoff(store)["cutoff_sha256"])
    if change == "ledger":
        store.write(billing.LEDGER, {"2000-01": "13.001", "2000-02": "6.989"})
    elif change == "observation":
        store.write(billing.OBSERVATION, {"actual_usd": "2", "observed_at": "2000-03-01T00:00Z"})
    elif change == "partial":
        args[2] = billing.fingerprint({"2000-01": "13.000"})
    elif change == "earlier":
        args[6] = "1999-01-01T00:00Z"
    else:
        args[4] = ".99"
    assert cli(store, monkeypatch, *args) == 1
    assert not store.path(billing.SETTLEMENT).exists()
    assert not store.path(billing.HIGH_WATER).exists()


@pytest.mark.parametrize("through", ["2000-01-16T00:00Z", "2000-02-14T00:00Z"])
def test_refuse_through_before_last_reservation(store, monkeypatch, through):
    store.write(billing.OBSERVATION, {"actual_usd": "1", "observed_at": "2000-01-15T00:00Z"})
    args = settlement_args(billing.cutoff(store)["cutoff_sha256"])
    args[4], args[6] = "1", through
    assert cli(store, monkeypatch, *args) == 1
    assert not store.path(billing.SETTLEMENT).exists()
    assert not store.path(billing.HIGH_WATER).exists()


@pytest.mark.parametrize("budget", [manual, worker])
def test_post_cutoff_month_regression_even_above_cutoff(store, monkeypatch, budget):
    assert settle(store, monkeypatch) == 0
    manual(store).reserve()
    worker(store).reserve()
    ledger = store.read(billing.LEDGER)
    ledger[datetime.now(UTC).strftime("%Y-%m")] = ".01"
    ledger["2000-01"] = "13.01"
    store.write(billing.LEDGER, ledger)
    with pytest.raises(PilotError, match="regressed"):
        budget(store).reserve()


def test_interrupted_write_ahead_blocks_both_paths(store, monkeypatch):
    assert settle(store, monkeypatch) == 0
    write = store.write

    def interrupt(name, value):
        if name == billing.LEDGER:
            raise OSError("synthetic interruption")
        write(name, value)

    monkeypatch.setattr(store, "write", interrupt)
    with pytest.raises(OSError):
        manual(store).reserve()
    for budget in (manual, worker):
        with pytest.raises(PilotError, match="regressed"):
            budget(store).reserve()


def test_denied_higher_observation_cannot_be_reset_by_lower_balance(store, monkeypatch):
    assert settle(store, monkeypatch) == 0
    bill = store.read(billing.OBSERVATION)
    store.write(billing.OBSERVATION, {**bill, "actual_usd": "20"})
    with pytest.raises(PilotError, match="cap"):
        manual(store).reserve()
    store.write(billing.OBSERVATION, {**bill, "actual_usd": "0.10"})
    with pytest.raises(PilotError, match="cap"):
        worker(store).reserve()


@pytest.mark.parametrize("missing", [billing.SETTLEMENT, billing.HIGH_WATER])
def test_incomplete_settlement_cannot_fall_back_or_replay(store, monkeypatch, missing):
    assert settle(store, monkeypatch) == 0
    store.path(missing).unlink()
    for budget in (manual, worker):
        with pytest.raises(PilotError, match="Incomplete"):
            budget(store).reserve()
    assert settle(store, monkeypatch) == 1
