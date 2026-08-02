# userunits – Python-Tools für systemd User Units

Sammlung von Python-Skripten, um systemd User Units (`systemctl --user`)
für Java- und Python-Anwendungen zu erzeugen, zu verwalten und auf
Fehler zu prüfen. Laufen im normalen User-Kontext (kein root, kein
sudo). Kompatibel zu Python 3.9 (kein `match`-Statement, keine
`X | Y` Type-Hints).

## Inhalt

- **search_env.py** – Durchsucht das System nach installierten Java- und
  Python-Versionen (PATH, `JAVA_HOME`, `/usr/lib/jvm`, `/usr/lib64/jvm`,
  `/usr/java`, `/opt/*/jdk*`, `~/.sdkman/candidates/java/*`,
  `update-alternatives` für Java; PATH, `/usr/bin`, `/usr/local/bin`,
  `/opt/*/bin`, `~/.pyenv/versions/*` für Python) und speichert das
  Ergebnis nach `~/.config/generateunit/environments.json`. Manuell per
  `generateunit.py` hinzugefügte `custom`-Einträge bleiben beim erneuten
  Scannen erhalten.

- **envstore.py** – Gemeinsame Hilfsfunktionen (kein eigenständiges
  Skript) zum Laden/Speichern der erkannten bzw. per Hand hinzugefügten
  Java-/Python-Installationen in `~/.config/generateunit/environments.json`.
  Wird von `search_env.py` und `generateunit.py` verwendet.

- **generateunit.py** – Erstellt interaktiv eine systemd User Unit für
  eine Java- (JAR) oder Python-Anwendung: lässt eine per `search_env.py`
  gefundene bzw. gespeicherte Installation per Nummer wählen (oder einen
  eigenen Pfad, der optional gespeichert wird), fragt nach JAR-Datei
  bzw. Python-Skript, zusätzlichen Interpreter-/Programm-Parametern,
  optionaler `EnvironmentFile`, `WorkingDirectory` und Restart-Policy.
  Schreibt die `.service`-Datei nach `~/.config/systemd/user/` und
  bietet an, `systemctl --user daemon-reload` sowie `enable --now`
  direkt auszuführen.

- **unithelper.py** – Zeigt alle systemd User Units durchnummeriert an
  und führt einen Befehl auf einer Nummer, mehreren Nummern (Komma-
  oder Leerzeichen-getrennt) oder allen Units aus:
  - `start`, `stop`, `status`, `restart` – direkte `systemctl --user`-Aufrufe
  - `enable`, `disable`
  - `delete` – stoppt und deaktiviert die Unit, entfernt die
    `.service`-Datei aus `~/.config/systemd/user/` und führt
    `daemon-reload` aus; fragt vor dem Löschen nochmal explizit nach

  Optional kann ein Filter-Text als zweites Argument angegeben werden,
  um nur Units anzuzeigen, deren Name diesen Text enthält
  (Gross-/Kleinschreibung wird ignoriert):

  ```bash
  python3 unithelper.py status ubuntu
  ```

- **checkunits.py** – Prüft alle User Units und zeigt eine Übersicht mit
  Fehlern (failed Units bzw. Journal-Einträge mit Priorität
  err/crit/alert/emerg) und Warnungen (Journal-Einträge mit Priorität
  warning) an. Nagios-artige Exit-Codes für Monitoring-Wrapper:
  `0` = keine Fehler/Warnungen, `1` = nur Warnungen, `2` = mindestens
  ein Fehler. Braucht zusätzlich `journalctl --user`.

## Nutzung

```bash
# 1. Java-/Python-Installationen suchen und speichern
python3 search_env.py

# 2. User Unit interaktiv erstellen
python3 generateunit.py

# 3. Units verwalten (start/stop/status/restart/enable/disable/delete)
python3 unithelper.py status
python3 unithelper.py status ubuntu

# 4. Units auf Fehler/Warnungen prüfen (z.B. per Cron/Monitoring)
python3 checkunits.py
python3 checkunits.py --since "-1h" --lines 10
```

## Voraussetzungen

- Python >= 3.9 (Projekt-Setup via `uv`, siehe `pyproject.toml`)
- `systemctl --user` bzw. `journalctl --user` im PATH
- Keine root-Rechte nötig
