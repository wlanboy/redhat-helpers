import subprocess

import pytest

import helper

LIST_UNITS_OUTPUT = """\
app.service          loaded active   running My App with spaces
broken.service       loaded failed   failed  Broken Service

short.service        not-found
"""


def fake_run(returncode=0, stdout="", exc=None):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout=stdout)

    run.calls = calls
    return run


def test_list_units_parses_systemctl_output(monkeypatch):
    run = fake_run(stdout=LIST_UNITS_OUTPUT)
    monkeypatch.setattr(helper.subprocess, "run", run)

    units = helper.list_units()

    assert run.calls[0][:3] == ["systemctl", "--user", "list-units"]
    assert units == [
        {
            "name": "app.service",
            "load": "loaded",
            "active": "active",
            "sub": "running",
            "description": "My App with spaces",
        },
        {
            "name": "broken.service",
            "load": "loaded",
            "active": "failed",
            "sub": "failed",
            "description": "Broken Service",
        },
        {
            "name": "short.service",
            "load": "not-found",
            "active": "",
            "sub": "",
            "description": "",
        },
    ]


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("systemctl"),
        subprocess.CalledProcessError(1, ["systemctl"]),
    ],
)
def test_list_units_exits_with_die_code(monkeypatch, exc):
    monkeypatch.setattr(helper.subprocess, "run", fake_run(exc=exc))

    with pytest.raises(SystemExit) as info:
        helper.list_units(die_code=2)

    assert info.value.code == 2


def test_run_systemctl_success(monkeypatch):
    run = fake_run(returncode=0)
    monkeypatch.setattr(helper.subprocess, "run", run)

    assert helper.run_systemctl(["daemon-reload"]) is True
    assert run.calls == [["systemctl", "--user", "daemon-reload"]]


def test_run_systemctl_failure_reports_error(monkeypatch, capsys):
    monkeypatch.setattr(helper.subprocess, "run", fake_run(returncode=5))

    assert helper.run_systemctl(["stop", "app.service"]) is False
    assert "systemctl stop app.service fehlgeschlagen" in capsys.readouterr().err


def test_run_systemctl_without_systemctl(monkeypatch, capsys):
    monkeypatch.setattr(
        helper.subprocess, "run", fake_run(exc=FileNotFoundError("systemctl"))
    )

    assert helper.run_systemctl(["daemon-reload"]) is False
    assert "nicht gefunden" in capsys.readouterr().err
