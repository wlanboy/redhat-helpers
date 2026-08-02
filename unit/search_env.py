#!/usr/bin/env python3
"""
search_env.py

Durchsucht das System nach installierten Java- und Python-Versionen und
schreibt das Ergebnis nach ~/.config/generateunit/environments.json.
generateunit.py liest diese Datei ein, um Java Home / Python Home per
Nummer auswaehlen zu koennen, statt den Pfad jedes Mal von Hand einzugeben.

Bereits per Hand hinzugefuegte "custom"-Eintraege (aus generateunit.py)
bleiben beim erneuten Scannen erhalten.

Durchsuchte Quellen:
  Java:   PATH, JAVA_HOME, /usr/lib/jvm, /usr/lib64/jvm, /usr/java,
          /opt/*/jdk*, ~/.sdkman/candidates/java/*, update-alternatives
  Python: PATH (python, python3, python3.<x>), /usr/bin, /usr/local/bin,
          /opt/*/bin, ~/.pyenv/versions/*

Kompatibel zu Python 3.9.
"""

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

from envstore import ENV_FILE, get_version, load_environments, save_environments

PYTHON_BINARY_NAMES = ["python3.8", "python3.9", "python3.10", "python3.11",
                        "python3.12", "python3.13", "python3.14", "python3", "python"]


def existing_executables(paths: List[str]) -> List[Path]:
    result = []
    for raw in paths:
        path = Path(raw)
        if "-config" in path.name:
            continue
        if path.is_file() and os.access(path, os.X_OK):
            result.append(path)
    return result


def dedupe_by_realpath(paths: List[Path]) -> List[Path]:
    seen = set()
    result = []
    for path in paths:
        try:
            real = str(path.resolve())
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        result.append(Path(real))
    return result


def find_java_candidates() -> List[Path]:
    candidates: List[str] = []

    which_java = shutil.which("java")
    if which_java:
        candidates.append(which_java)

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(str(Path(java_home) / "bin" / "java"))

    candidates.extend(glob.glob("/usr/lib/jvm/*/bin/java"))
    candidates.extend(glob.glob("/usr/lib64/jvm/*/bin/java"))
    candidates.extend(glob.glob("/usr/java/*/bin/java"))
    candidates.extend(glob.glob("/opt/*/jdk*/bin/java"))
    candidates.extend(glob.glob(str(Path.home() / ".sdkman/candidates/java/*/bin/java")))

    try:
        proc = subprocess.run(
            ["update-alternatives", "--list", "java"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            candidates.extend(line.strip() for line in proc.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass

    return dedupe_by_realpath(existing_executables(candidates))


def find_python_candidates() -> List[Path]:
    candidates: List[str] = []

    for name in PYTHON_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            candidates.append(found)

    for bindir in ("/usr/bin", "/usr/local/bin"):
        candidates.extend(glob.glob("{0}/python3*".format(bindir)))

    candidates.extend(glob.glob("/opt/*/bin/python3*"))
    candidates.extend(glob.glob(str(Path.home() / ".pyenv/versions/*/bin/python3*")))

    return dedupe_by_realpath(existing_executables(candidates))


def merge(customs: List[dict], detected: List[dict]) -> List[dict]:
    merged = list(customs)
    custom_paths = {entry["path"] for entry in customs}
    for entry in detected:
        if entry["path"] not in custom_paths:
            merged.append(entry)
    return merged


def print_summary(label: str, entries: List[dict]) -> None:
    print()
    print("{0}:".format(label))
    if not entries:
        print("  (nichts gefunden)")
        return
    for i, entry in enumerate(entries, start=1):
        tag = "custom" if entry.get("source") == "custom" else "gefunden"
        print("  [{0}] {1}  ({2}, {3})".format(i, entry["path"], entry["version"], tag))


def main() -> None:
    print("== Suche nach installierten Java- und Python-Versionen ==")

    store = load_environments()

    java_customs = [e for e in store.get("java", []) if e.get("source") == "custom"]
    python_customs = [e for e in store.get("python", []) if e.get("source") == "custom"]

    java_detected = [
        {"path": str(p), "version": get_version(str(p), "java"), "source": "detected"}
        for p in find_java_candidates()
    ]
    python_detected = [
        {"path": str(p), "version": get_version(str(p), "python"), "source": "detected"}
        for p in find_python_candidates()
    ]

    store["java"] = merge(java_customs, java_detected)
    store["python"] = merge(python_customs, python_detected)

    save_environments(store)

    print_summary("Java-Versionen", store["java"])
    print_summary("Python-Versionen", store["python"])

    print()
    print("Gespeichert nach: {0}".format(ENV_FILE))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(1)
