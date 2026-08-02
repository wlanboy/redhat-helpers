"""
envstore.py

Gemeinsame Hilfsfunktionen fuer search_env.py und generateunit.py:
Laden/Speichern der erkannten bzw. per Hand hinzugefuegten Java- und
Python-Installationen unter ~/.config/generateunit/environments.json.

Kompatibel zu Python 3.9.
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, List

ENV_FILE = Path.home() / ".config" / "generateunit" / "environments.json"

KNOWN_KINDS = ("java", "python")


def empty_store() -> Dict[str, List[dict]]:
    return {"java": [], "python": []}


def load_environments() -> Dict[str, List[dict]]:
    if not ENV_FILE.is_file():
        return empty_store()
    try:
        data = json.loads(ENV_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_store()
    if not isinstance(data, dict):
        return empty_store()
    for kind in KNOWN_KINDS:
        data.setdefault(kind, [])
    return data


def save_environments(data: Dict[str, List[dict]]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def add_entry(
    data: Dict[str, List[dict]], kind: str, path: str, version: str, source: str = "custom"
) -> None:
    entries = data.setdefault(kind, [])
    for entry in entries:
        if entry.get("path") == path:
            entry["version"] = version
            entry["source"] = source
            return
    entries.append({"path": path, "version": version, "source": source})


def get_version(binary: str, kind: str) -> str:
    """Ermittelt eine kurze Versionsbeschreibung fuer eine Java-/Python-Binary."""
    try:
        if kind == "java":
            proc = subprocess.run(
                [binary, "-version"], capture_output=True, text=True, timeout=5
            )
            output = (proc.stderr or proc.stdout or "").strip()
        else:
            proc = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=5
            )
            output = (proc.stdout or proc.stderr or "").strip()
        first_line = output.splitlines()[0] if output else ""
        return first_line or "unbekannt"
    except (OSError, subprocess.SubprocessError):
        return "unbekannt"
