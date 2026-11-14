import shlex
from pathlib import Path

import pytest

from generateunit import (
    build_exec_start,
    escape_specifiers,
    quote_exec_arg,
    split_params,
)


def systemd_argv(exec_start: str) -> list:
    """Zerlegt eine ExecStart-Zeile so, wie systemd es fuer die hier
    erzeugten Formen tut: doppelte Anfuehrungszeichen mit \\\\ und \\" als
    Escapes (deckt sich mit shlex), danach '%%' -> '%'."""
    return [arg.replace("%%", "%") for arg in shlex.split(exec_start)]


def test_escape_specifiers():
    assert escape_specifiers("-Dlogging.pattern=%d %m") == "-Dlogging.pattern=%%d %%m"


@pytest.mark.parametrize(
    "arg, expected",
    [
        ("-Xmx512m", "-Xmx512m"),
        ("-Dpattern=%d", "-Dpattern=%%d"),
        ("/opt/my app/app.jar", '"/opt/my app/app.jar"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("C:\\tmp", '"C:\\\\tmp"'),
        (";", '";"'),
        ("", '""'),
        ("${PORT}", "${PORT}"),
    ],
)
def test_quote_exec_arg(arg, expected):
    assert quote_exec_arg(arg) == expected


def test_quote_exec_arg_force_quotes_plain_path():
    assert quote_exec_arg("/usr/bin/java", force=True) == '"/usr/bin/java"'


def test_split_params_keeps_quoted_values_together():
    assert split_params('-Xmx512m "-Dlogging.pattern=%d %m"') == [
        "-Xmx512m",
        "-Dlogging.pattern=%d %m",
    ]


def test_split_params_rejects_unbalanced_quotes():
    with pytest.raises(ValueError):
        split_params('-Dfoo="bar')


def test_build_exec_start_with_spaces_and_percent():
    exec_start = build_exec_start(
        "/opt/java home/bin/java",
        split_params('-Xmx512m "-Dlogging.pattern=%d %-5level %msg"'),
        Path("/srv/my app/app.jar"),
        split_params("--server.port=8080 --name 'a b'"),
        is_jar=True,
    )

    assert exec_start == (
        '"/opt/java home/bin/java" -Xmx512m '
        '"-Dlogging.pattern=%%d %%-5level %%msg" '
        '-jar "/srv/my app/app.jar" --server.port=8080 --name "a b"'
    )
    assert systemd_argv(exec_start) == [
        "/opt/java home/bin/java",
        "-Xmx512m",
        "-Dlogging.pattern=%d %-5level %msg",
        "-jar",
        "/srv/my app/app.jar",
        "--server.port=8080",
        "--name",
        "a b",
    ]


def test_build_exec_start_python_without_params():
    exec_start = build_exec_start(
        "/usr/bin/python3", [], Path("/srv/app/run.py"), [], is_jar=False
    )

    assert exec_start == '"/usr/bin/python3" "/srv/app/run.py"'
