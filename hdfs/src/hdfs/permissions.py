"""Bedeutung der Rechte-Bits: Namen je Maske, Recht des Besitzers, Verwaltungsrecht.

Die Tabelle entspricht der PermissionFactory der Anwendung und ist
konfigurierbar (JSON-Datei, siehe hdfs.md und permissions.json):

    {
      "permissions": {"READ": 1, "WRITE": 2, "CREATE": 4, "DELETE": 8, "ADMINISTRATION": 16},
      "owner": "READ+WRITE",
      "admin": "ADMINISTRATION"
    }

- permissions: Name -> Maske. Einzelne Bits oder Kombinationen ("VOLL": 3).
- owner: Recht, das der Besitzer immer hat (Maske oder Namen).
- admin: Bit(s), mit denen andere die ACL verwalten dürfen wie der Besitzer;
  add gibt sie NEU zusätzlich, wo ALT Besitzer ist.

Ohne Datei gelten die Werte von Spring Security (BasePermission) mit
owner = 3 und admin = 16. Aktiv ist immer genau eine Tabelle (active()),
main() setzt sie beim Start mit activate().
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from hdfs.common import UsageError

# Spring speichert die Maske als Java-int, höchstes nutzbares Bit ist 1 << 30
MAX_PERM = (1 << 31) - 1

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# Trenner zwischen mehreren Namen/Zahlen: READ+WRITE, READ|WRITE, READ,WRITE
SEPARATORS = re.compile(r"\s*[+|,]\s*")

# Datei, die ohne --permissions/$HDFS_ACL_PERMISSIONS neben der DB gesucht wird
DEFAULT_FILE = "permissions.json"


@dataclass(frozen=True)
class Permissions:
    # Name -> Maske, in der Reihenfolge der Konfiguration
    names: dict = field(default_factory=dict)
    owner: int = 3
    admin: int = 16
    # Herkunft für Meldungen: Dateipfad oder "eingebaut"
    origin: str = "eingebaut"

    def parse(self, value: Union[str, int]) -> int:
        """'3', 'READ', 'read+write' oder 'READ|16' -> Maske (Namen ohne Groß/klein)."""
        if isinstance(value, int):
            return value
        lookup = {name.upper(): mask for name, mask in self.names.items()}
        mask = 0
        for part in SEPARATORS.split(value.strip()):
            if part.isdigit():
                mask |= int(part)
            elif part.upper() in lookup:
                mask |= lookup[part.upper()]
            else:
                known = ", ".join(self.names) or "keine"
                raise UsageError(f"Unbekanntes Recht '{part}' (bekannt: {known}; Tabelle: {self.origin}).")
        if not 0 < mask <= MAX_PERM:
            raise UsageError(f"Recht '{value}' außerhalb 1..{MAX_PERM}.")
        return mask

    def describe(self, mask: int) -> str:
        """Maske -> Namen, z. B. 19 -> 'READ+WRITE+ADMINISTRATION'.

        Passt ein Name genau, gilt er. Sonst werden Namen mit mehr Bits zuerst
        genommen (ein "VOLL": 3 schlägt READ+WRITE), übrige Bits ohne Namen
        bleiben als Zahl stehen: 35 -> 'READ+WRITE+32'. Ohne Tabelle ''.
        """
        if not self.names:
            return ""
        for name, value in self.names.items():
            if value == mask:
                return name
        parts = []
        rest = mask
        # mehr Bits zuerst, bei Gleichstand die größere Maske
        for name, value in sorted(self.names.items(), key=lambda i: (-bin(i[1]).count("1"), -i[1])):
            if rest & value == value:
                parts.append((value, name))
                rest &= ~value
        parts.sort()  # Ausgabe aufsteigend nach Maske: READ vor WRITE
        return "+".join([name for _, name in parts] + ([str(rest)] if rest else []))


DEFAULT = Permissions(
    names={"READ": 1, "WRITE": 2, "CREATE": 4, "DELETE": 8, "ADMINISTRATION": 16},
    owner=3, admin=16,
)

_active = DEFAULT


def active() -> Permissions:
    """Die gerade gültige Tabelle (eingebaut, bis activate() eine andere setzt)."""
    return _active


def activate(permissions: Permissions) -> None:
    global _active
    _active = permissions


def from_dict(data: dict, origin: str) -> Permissions:
    """Prüft eine Konfiguration (geparstes JSON) und baut daraus die Tabelle."""
    if not isinstance(data, dict):
        raise UsageError(f"{origin}: erwartet ein JSON-Objekt.")
    unknown = set(data) - {"permissions", "owner", "admin"}
    if unknown:
        raise UsageError(f"{origin}: unbekannte Schlüssel {', '.join(sorted(unknown))}.")
    names = data.get("permissions", {})
    if not isinstance(names, dict):
        raise UsageError(f"{origin}: 'permissions' muss ein Objekt Name -> Maske sein.")
    for name, mask in names.items():
        if not NAME.fullmatch(name):
            raise UsageError(f"{origin}: ungültiger Name '{name}' (Buchstaben, Ziffern, _ und -).")
        # bool ist in Python auch ein int, true wäre sonst Maske 1
        if isinstance(mask, bool) or not isinstance(mask, int) or not 0 < mask <= MAX_PERM:
            raise UsageError(f"{origin}: Maske von '{name}' muss eine Zahl in 1..{MAX_PERM} sein.")
    if len({name.upper() for name in names}) != len(names):
        raise UsageError(f"{origin}: Namen unterscheiden sich nur in Groß-/Kleinschreibung.")
    table = Permissions(names=dict(names), origin=origin)

    def mask_of(key: str, default: int) -> int:
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise UsageError(f"{origin}: '{key}' muss eine Maske oder Namen sein.")
        try:
            return table.parse(value)
        except UsageError as error:
            raise UsageError(f"{origin}: '{key}': {error}") from None

    owner = mask_of("owner", DEFAULT.owner)
    admin = mask_of("admin", DEFAULT.admin)
    if not 0 < owner <= MAX_PERM or not 0 < admin <= MAX_PERM:
        raise UsageError(f"{origin}: 'owner' und 'admin' müssen in 1..{MAX_PERM} liegen.")
    return Permissions(names=dict(names), owner=owner, admin=admin, origin=origin)


def load(path: Path) -> Permissions:
    try:
        data = json.loads(path.read_text())
    except OSError as error:
        raise UsageError(f"Rechte-Tabelle {path} nicht lesbar: {error.strerror}.") from None
    except json.JSONDecodeError as error:
        raise UsageError(f"Rechte-Tabelle {path}: kein gültiges JSON ({error}).") from None
    return from_dict(data, str(path))


def find(option: Optional[str], db: Optional[Path]) -> Permissions:
    """Tabelle nach Vorrang: --permissions, $HDFS_ACL_PERMISSIONS,
    permissions.json neben der DB, sonst eingebaut (Spring-Standard)."""
    explicit = option or os.environ.get("HDFS_ACL_PERMISSIONS")
    if explicit:
        return load(Path(explicit))
    if db is not None:
        candidate = db.resolve().parent / DEFAULT_FILE
        if candidate.is_file():
            return load(candidate)
    return DEFAULT
