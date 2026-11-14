import subprocess

import pytest

import helper
import unithelper

UNIT = "app.service"


@pytest.fixture
def unit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(unithelper, "UNIT_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def systemctl(monkeypatch):
    """Ersetzt subprocess.run. failing enthaelt die systemctl-Aktionen
    (z.B. "stop"), die mit Exit-Code 1 enden sollen."""
    calls = []
    failing = set()

    def run(args, **kwargs):
        calls.append(args)
        returncode = 1 if args[2] in failing else 0
        return subprocess.CompletedProcess(args, returncode)

    monkeypatch.setattr(helper.subprocess, "run", run)
    run.calls = calls
    run.failing = failing
    return run


def actions(calls):
    return [args[2] for args in calls]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a", [1, 2, 3]),
        ("alle", [1, 2, 3]),
        ("2", [2]),
        ("3,1 3", [3, 1]),
        (" 1 , 2 ", [1, 2]),
    ],
)
def test_parse_selection_valid(raw, expected):
    assert unithelper.parse_selection(raw, 3) == expected


@pytest.mark.parametrize("raw", ["x", "0", "4", "-1", "", " , "])
def test_parse_selection_invalid(raw):
    assert unithelper.parse_selection(raw, 3) is None


def test_delete_unit_success(unit_dir, systemctl):
    unit_file = unit_dir / UNIT
    unit_file.write_text("[Unit]\n")

    assert unithelper.delete_unit(UNIT) is True
    assert not unit_file.exists()
    assert actions(systemctl.calls) == ["stop", "disable", "daemon-reload"]


@pytest.mark.parametrize("failing_step", ["stop", "disable"])
def test_delete_unit_keeps_file_when_step_fails(
    unit_dir, systemctl, capsys, failing_step
):
    unit_file = unit_dir / UNIT
    unit_file.write_text("[Unit]\n")
    systemctl.failing.add(failing_step)

    assert unithelper.delete_unit(UNIT) is False
    assert unit_file.exists()
    assert "daemon-reload" not in actions(systemctl.calls)
    assert f"{failing_step} fehlgeschlagen" in capsys.readouterr().err


def test_delete_unit_stop_failure_skips_disable(unit_dir, systemctl):
    (unit_dir / UNIT).write_text("[Unit]\n")
    systemctl.failing.add("stop")

    unithelper.delete_unit(UNIT)

    assert actions(systemctl.calls) == ["stop"]


def test_delete_unit_without_file_still_reloads(unit_dir, systemctl, capsys):
    assert unithelper.delete_unit(UNIT) is True
    assert actions(systemctl.calls) == ["stop", "disable", "daemon-reload"]
    assert "nicht gefunden" in capsys.readouterr().err


def test_delete_unit_reports_failed_daemon_reload(unit_dir, systemctl):
    unit_file = unit_dir / UNIT
    unit_file.write_text("[Unit]\n")
    systemctl.failing.add("daemon-reload")

    assert unithelper.delete_unit(UNIT) is False
    assert not unit_file.exists()


def test_run_action_status_adds_no_pager(systemctl):
    assert unithelper.run_action("status", UNIT) is True
    assert systemctl.calls == [
        ["systemctl", "--user", "status", UNIT, "--no-pager"]
    ]


def test_run_action_failure(systemctl):
    systemctl.failing.add("restart")

    assert unithelper.run_action("restart", UNIT) is False
    assert systemctl.calls == [["systemctl", "--user", "restart", UNIT]]


def test_run_action_delete_delegates(unit_dir, systemctl):
    (unit_dir / UNIT).write_text("[Unit]\n")

    assert unithelper.run_action("delete", UNIT) is True
    assert not (unit_dir / UNIT).exists()
