# valkey – Valkey als non-root betreiben (RHEL 9, offline)

Valkey wird nicht gebaut. Genutzt wird der fertige Ubuntu-22.04-Build (jammy)
von `download.valkey.io`. Er läuft auf RHEL 9, weil er nur glibc 2.34,
OpenSSL 3 und libsystemd braucht (dazu zlib und libzstd, auf RHEL 9
Standard). jemalloc ist statisch gelinkt. `install-valkey.sh` prüft die
Bibliotheken per `ldd`.

## Dateien

- **versions.conf** – Version (Default: 9.1.2), optionaler SHA256-Pin,
  Nexus-URL und Repo-Name. Jeder Wert lässt sich per Umgebungsvariable
  überschreiben.
- **install-valkey.sh** – als User, Runtime-VM. Download aus Nexus,
  SHA256-Prüfung, Konfiguration, Passwort, systemd User Unit.

## Nexus

Ein Raw-Proxy `valkey-releases` mit Remote-URL `https://download.valkey.io/`
(abweichender Name: `NEXUS_VALKEY_REPO` in `versions.conf`). Benötigte Pfade:

```
releases/valkey-<VERSION>-jammy-x86_64.tar.gz
releases/valkey-<VERSION>-jammy-x86_64.tar.gz.sha256
```

Auf aarch64 heißt die Datei `...-jammy-arm64.tar.gz`. Outbound von Nexus zu
`download.valkey.io` per HTTPS freischalten, Maximum Component Age `-1`.
Ist ein Proxy nicht erlaubt: Raw-Hosted-Repo mit demselben Namen und die
Dateien unter denselben Pfaden hochladen.

Zugangsdaten liest `curl` aus `~/.netrc`, falls vorhanden.

## Voraussetzungen (einmalig als root)

```bash
loginctl enable-linger valkey
mkdir -p /opt/local/valkey && chown valkey: /opt/local/valkey
echo 'vm.overcommit_memory = 1' > /etc/sysctl.d/90-valkey.conf && sysctl --system
firewall-cmd --permanent --add-port=6379/tcp && firewall-cmd --reload
```

Ohne `vm.overcommit_memory = 1` können RDB-Snapshots und AOF-Rewrites
unter Last fehlschlagen.

## Installation

```bash
valkey/install-valkey.sh                         # als User "valkey"
VALKEY_PASSWORD=... ENABLE=j valkey/install-valkey.sh   # ohne Rückfragen
```

`systemctl --user` braucht eine echte Login-Session (SSH direkt als User
oder `machinectl shell valkey@`), nicht `su -` oder `sudo -u`.

Verzeichnisse unter `/opt/local/valkey` (änderbar per `VALKEY_BASE`):

```
/opt/local/valkey/
├── server/valkey-9.1.2-jammy-x86_64/  + current -> valkey-9.1.2-jammy-x86_64
├── etc/valkey.conf     einmalig angelegt, danach eigene Pflege
├── etc/auth.conf       requirepass, chmod 600, einmalig angelegt
├── etc/valkey.env      bei jedem Lauf neu generiert
├── data/               RDB + AOF
├── log/valkey.log
└── downloads/
```

Der Port (`VALKEY_PORT`, Default 6379) wird nur beim ersten Anlegen von
`valkey.conf` eingetragen.

```bash
set -a; . /opt/local/valkey/etc/valkey.env; set +a
valkey-cli --askpass ping
```

## Upgrade

Neue Version in `versions.conf` eintragen und `install-valkey.sh` erneut
ausführen. Die neue Version wird neben die alte entpackt, `current`
umgebogen und der Dienst neu gestartet. `data/` und `etc/` bleiben
erhalten. Rollback: `current` zurücksetzen und
`systemctl --user restart valkey`.
