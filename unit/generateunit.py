#!/usr/bin/env python3
"""
generateunit.py

Erstellt interaktiv eine systemd User Unit (systemctl --user) fuer eine
Java- (JAR) oder Python-Anwendung:
  - laesst per Nummer eine mit 'search_env.py' gefundene (oder zuvor
    gespeicherte) Java-/Python-Installation waehlen, alternativ einen
    eigenen Pfad angeben (der optional fuer kuenftige Laeufe gespeichert
    wird, siehe envstore.py)
  - fragt nach der JAR-Datei bzw. dem Python-Skript
  - fragt nach zusaetzlichen Interpreter- und Programm-Parametern
  - fragt nach einer optionalen EnvironmentFile
  - baut daraus eine .service-Datei unter ~/.config/systemd/user/
  - bietet an, "systemctl --user daemon-reload" und "enable --now"
    direkt auszufuehren

Kompatibel zu Python 3.9 (kein match-Statement, keine X | Y Type-Hints).
"""

import sys
from pathlib import Path
from typing import Optional

from envstore import add_entry, get_version, load_environments, save_environments
from helper import die, error, run_systemctl

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def ask(prompt: str, default: Optional[str] = None, required: bool = False) -> str:
    suffix = " [{0}]".format(default) if default is not None else ""
    while True:
        value = input("{0}{1}: ".format(prompt, suffix)).strip()
        if not value:
            if default is not None:
                return default
            if not required:
                return ""
            error("Eingabe darf nicht leer sein.")
            continue
        return value


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    hint = "J/n" if default else "j/N"
    while True:
        value = input("{0} [{1}]: ".format(prompt, hint)).strip().lower()
        if not value:
            return default
        if value in ("j", "ja", "y", "yes"):
            return True
        if value in ("n", "nein", "no"):
            return False
        error("Bitte mit j oder n antworten.")


def ask_choice(prompt: str, choices: dict, default: str) -> str:
    print(prompt)
    for key, label in choices.items():
        marker = " (Standard)" if key == default else ""
        print("  [{0}] {1}{2}".format(key, label, marker))
    while True:
        value = input("Auswahl: ").strip() or default
        if value in choices:
            return value
        error("Ungueltige Auswahl '{0}'.".format(value))


def ask_existing_path(prompt: str, must_be_file: bool = True) -> Path:
    while True:
        raw = ask(prompt, required=True)
        path = Path(raw).expanduser()
        if must_be_file and not path.is_file():
            error("Datei '{0}' existiert nicht.".format(path))
            continue
        if not must_be_file and not path.exists():
            error("Pfad '{0}' existiert nicht.".format(path))
            continue
        return path.resolve()


def ask_jar_path(prompt: str = "Pfad zur JAR-Datei oder zum Ordner") -> Path:
    """Fragt nach einer JAR-Datei oder einem Ordner. Bei einem Ordner werden
    die enthaltenen .jar-Dateien aufgelistet; gibt es nur eine, wird sie
    automatisch ausgewaehlt."""
    while True:
        raw = ask(prompt, required=True)
        path = Path(raw).expanduser()

        if path.is_dir():
            jars = sorted(path.glob("*.jar"))
            if not jars:
                error("Im Ordner '{0}' wurden keine .jar-Dateien gefunden.".format(path))
                continue
            if len(jars) == 1:
                print("Gefunden: {0}".format(jars[0].name))
                return jars[0].resolve()
            print()
            print("Gefundene JAR-Dateien in '{0}':".format(path))
            for i, jar in enumerate(jars, start=1):
                print("  [{0}] {1}".format(i, jar.name))
            while True:
                choice = ask("Auswahl", default="1", required=True)
                if choice.isdigit() and 1 <= int(choice) <= len(jars):
                    return jars[int(choice) - 1].resolve()
                error("Ungueltige Auswahl '{0}'.".format(choice))

        if path.is_file():
            return path.resolve()

        error("Pfad '{0}' existiert nicht.".format(path))


def resolve_binary_path(raw: str, binary_name: str) -> Optional[Path]:
    path = Path(raw).expanduser()
    if path.is_dir():
        candidate = path / "bin" / binary_name
        if candidate.is_file():
            return candidate.resolve()
        error("In '{0}' wurde kein 'bin/{1}' gefunden.".format(path, binary_name))
        return None
    if path.is_file():
        return path.resolve()
    error("'{0}' ist weder ein gueltiges Verzeichnis noch eine Datei.".format(path))
    return None


def ask_custom_environment(kind_label: str, env_key: str, binary_name: str, store: dict) -> str:
    """Fragt nach *_HOME oder direkt nach dem Interpreter-Pfad und bietet an,
    den Pfad fuer kuenftige Laeufe in environments.json zu speichern."""
    while True:
        raw = ask(
            "{0} (Verzeichnis oder Pfad zur '{1}'-Binary)".format(kind_label, binary_name),
            required=True,
        )
        resolved = resolve_binary_path(raw, binary_name)
        if resolved is not None:
            break

    path_str = str(resolved)
    if ask_yes_no("Diesen Pfad fuer kuenftige Laeufe speichern?", default=True):
        version = get_version(path_str, env_key)
        add_entry(store, env_key, path_str, version, source="custom")
        save_environments(store)
        print("Gespeichert: {0} ({1})".format(path_str, version))
    return path_str


def ask_environment(kind_label: str, env_key: str, binary_name: str) -> str:
    """Laesst per Nummer eine von search_env.py gefundene bzw. zuvor
    gespeicherte Installation waehlen, alternativ einen eigenen Pfad."""
    store = load_environments()
    entries = store.get(env_key, [])

    if not entries:
        print(
            "Keine gespeicherten {0}-Installationen gefunden (vorher "
            "'search_env.py' ausfuehren, um automatisch zu suchen).".format(kind_label)
        )
        return ask_custom_environment(kind_label, env_key, binary_name, store)

    print()
    print("Gefundene {0}-Installationen:".format(kind_label))
    for i, entry in enumerate(entries, start=1):
        tag = "custom" if entry.get("source") == "custom" else "gefunden"
        print("  [{0}] {1}  ({2}, {3})".format(i, entry["path"], entry["version"], tag))
    print("  [0] Eigenen Pfad angeben")

    while True:
        choice = ask("Auswahl", default="1", required=True)
        if choice == "0":
            return ask_custom_environment(kind_label, env_key, binary_name, store)
        if choice.isdigit() and 1 <= int(choice) <= len(entries):
            return entries[int(choice) - 1]["path"]
        error("Ungueltige Auswahl '{0}'.".format(choice))


UNIT_NAME_RE_MSG = "Erlaubt sind Buchstaben, Ziffern, '_', '-', '.', '@'."


def ask_unit_name(default: str) -> str:
    import re

    pattern = re.compile(r"^[A-Za-z0-9_.@-]+$")
    while True:
        name = ask("Unit-Name (ohne .service)", default=default, required=True)
        if pattern.match(name):
            return name
        error("Ungueltiger Unit-Name '{0}'. {1}".format(name, UNIT_NAME_RE_MSG))


def build_exec_start(
    interpreter: str,
    interpreter_params: str,
    target: Path,
    app_params: str,
    is_jar: bool,
) -> str:
    parts = [interpreter]
    if interpreter_params:
        parts.append(interpreter_params)
    if is_jar:
        parts.append("-jar")
    parts.append(str(target))
    if app_params:
        parts.append(app_params)
    return " ".join(parts)


def main() -> None:
    print("== systemd User Unit Generator ==")
    print()

    kind = ask_choice(
        "Art der Anwendung:",
        {"1": "Java (JAR)", "2": "Python-Skript"},
        default="1",
    )
    is_java = kind == "1"

    if is_java:
        interpreter = ask_environment("Java", "java", "java")
        target = ask_jar_path("Pfad zur JAR-Datei oder zum Ordner")
        interpreter_params = ask(
            "Zusaetzliche JVM-Parameter (z.B. -Xmx512m -Dspring.profiles.active=prod)"
        )
    else:
        interpreter = ask_environment("Python", "python", "python3")
        target = ask_existing_path("Pfad zum Python-Skript")
        interpreter_params = ask("Zusaetzliche Interpreter-Parameter (z.B. -O)")

    app_params = ask("Programm-Argumente (Argumente fuer die Anwendung selbst)")

    env_file = ""
    if ask_yes_no("Environment-File verwenden (EnvironmentFile=)?", default=False):
        env_path = ask_existing_path("Pfad zum Environment-File", must_be_file=True)
        env_file = str(env_path)

    default_workdir = str(target.parent)
    workdir = ask("WorkingDirectory", default=default_workdir, required=True)
    workdir_path = Path(workdir).expanduser()
    if not workdir_path.is_dir():
        die("WorkingDirectory '{0}' existiert nicht.".format(workdir_path))

    restart_policy = ask_choice(
        "Restart-Policy:",
        {"1": "on-failure", "2": "always", "3": "no"},
        default="1",
    )
    restart_map = {"1": "on-failure", "2": "always", "3": "no"}
    restart = restart_map[restart_policy]

    default_name = target.stem
    unit_name = ask_unit_name(default_name)

    default_description = "{0} ({1})".format(
        target.name, "Java" if is_java else "Python"
    )
    description = ask("Beschreibung", default=default_description, required=True)

    exec_start = build_exec_start(
        interpreter, interpreter_params, target, app_params, is_java
    )

    lines = [
        "[Unit]",
        "Description={0}".format(description),
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        "WorkingDirectory={0}".format(workdir_path.resolve()),
    ]
    if env_file:
        lines.append("EnvironmentFile={0}".format(env_file))
    lines.extend(
        [
            "ExecStart={0}".format(exec_start),
            "Restart={0}".format(restart),
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    unit_content = "\n".join(lines)

    print()
    print("-- Vorschau: {0}.service --".format(unit_name))
    print(unit_content)

    unit_path = UNIT_DIR / "{0}.service".format(unit_name)
    if unit_path.exists():
        if not ask_yes_no(
            "'{0}' existiert bereits. Ueberschreiben?".format(unit_path), default=False
        ):
            print("Abgebrochen.")
            sys.exit(0)

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_content, encoding="utf-8")
    print()
    print("Unit geschrieben nach: {0}".format(unit_path))

    if ask_yes_no("'systemctl --user daemon-reload' jetzt ausfuehren?", default=True):
        run_systemctl(["daemon-reload"])

    if ask_yes_no(
        "Unit '{0}' jetzt aktivieren und starten (enable --now)?".format(unit_name),
        default=False,
    ):
        run_systemctl(["enable", "--now", "{0}.service".format(unit_name)])

    print()
    print("Fertig.")
    print("  Status pruefen: systemctl --user status {0}.service".format(unit_name))
    print("  Logs ansehen:   journalctl --user -u {0}.service -f".format(unit_name))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        die("Abgebrochen.")


