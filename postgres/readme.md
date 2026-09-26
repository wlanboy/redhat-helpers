# postgres – PostgreSQL als non-root betreiben (RHEL 9, offline)

PostgreSQL wird nicht compiliert und nicht per dnf installiert. Genutzt
werden die fertigen RPMs aus dem offiziellen PostgreSQL-Yum-Repo (PGDG).
Die Build-VM prüft einmal pro Version deren Signatur, entpackt sie mit
`rpm2archive` und legt das Ergebnis als Tarball in Nexus ab. Die
Runtime-VMs laden nur diesen Tarball, entpacken ihn ins Home-Verzeichnis
und betreiben den Server als systemd User Unit.

```
                 Nexus
   ┌──────────────────────────────────┐
   │ pgdg-yum        (proxy)          │◄── PGDG-RPMs + GPG-Key
   │ postgres-builds (raw hosted)     │◄── postgresql-<ver>-el9-<arch>.tar.gz
   └──────────────────────────────────┘
          ▲ upload          │ download
   ┌──────┴─────┐    ┌──────▼──────┐ ┌─────────────┐
   │  Build-VM  │    │ Runtime-VM1 │ │ Runtime-VM2 │
   └────────────┘    └─────────────┘ └─────────────┘
```

Das geht, weil PostgreSQL `share/` und `lib/` relativ zum eigenen Binary
sucht. Das RPM-Verzeichnis `/usr/pgsql-18` lässt sich deshalb beliebig
verschieben. Nur `libpq` hat keinen RPATH und kommt per `LD_LIBRARY_PATH`
aus dem eigenen `lib/` (steht in `etc/postgres.env`).

Der Tarball ist an RHEL 9 und die CPU-Architektur gebunden. Build- und
Runtime-VMs müssen beide RHEL 9 mit gleicher Architektur sein.

## Dateien

- **versions.conf** – Version (Default: 18.6), RPM-Release-Tag,
  Signaturprüfung an/aus, Nexus-URL und Repo-Namen. Jeder Wert lässt sich
  per Umgebungsvariable überschreiben.
- **build.sh** – als User, Build-VM. Download der RPMs, Signaturprüfung,
  Entpacken, Tarball mit `BUILD_INFO` und `.sha256`, optional Upload nach
  Nexus.
- **install.sh** – als User, Runtime-VM. Download des Tarballs,
  SHA256-Prüfung, Entpacken, `ldd`-Check, initdb, Konfiguration, systemd
  User Unit.

## Nexus

| Repository        | Typ          | Upstream                                        | Genutzt von |
|-------------------|--------------|-------------------------------------------------|-------------|
| `pgdg-yum`        | yum oder raw (proxy) | `https://download.postgresql.org/pub/repos/yum/` | Build-VM |
| `postgres-builds` | raw (hosted) | –                                               | Build-VM schreibt, Runtime-VMs lesen |

Abweichende Namen: `NEXUS_PGDG_REPO` bzw. `NEXUS_BUILDS_REPO` in
`versions.conf`.

Benötigte Pfade im Proxy `pgdg-yum`:

```
keys/PGDG-RPM-GPG-KEY-RHEL
18/redhat/rhel-9-x86_64/postgresql18-18.6-1PGDG.rhel9.8.x86_64.rpm
18/redhat/rhel-9-x86_64/postgresql18-libs-18.6-1PGDG.rhel9.8.x86_64.rpm
18/redhat/rhel-9-x86_64/postgresql18-server-18.6-1PGDG.rhel9.8.x86_64.rpm
18/redhat/rhel-9-x86_64/postgresql18-contrib-18.6-1PGDG.rhel9.8.x86_64.rpm
```

Auf aarch64 `rhel-9-aarch64` und `.aarch64.rpm`. Der Release-Teil
(`1PGDG.rhel9.8`) enthält die RHEL-Minor-Version, gegen die PGDG gebaut hat,
und ändert sich mit neuen Builds. Den passenden Wert im Repo-Listing
nachsehen und als `PG_RPM_RELEASE` eintragen.

Outbound von Nexus zu `download.postgresql.org` per HTTPS freischalten.
Ist ein Proxy nicht erlaubt: Raw-Hosted-Repo mit demselben Namen und die
Dateien unter denselben Pfaden hochladen.

`build.sh` lädt nach `postgres-builds` hoch:

```
postgresql/18.6/postgresql-18.6-el9-x86_64.tar.gz
postgresql/18.6/postgresql-18.6-el9-x86_64.tar.gz.sha256
```

Der Build-User braucht dafür Schreibrechte (`nx-repository-view-raw-postgres-builds-add`
und `-edit`), die Runtime-VMs nur Leserechte. Zugangsdaten liest `curl` aus
`~/.netrc`, falls vorhanden. Für den Upload alternativ `NEXUS_USER=<user>`
setzen, dann fragt `curl` nach dem Passwort.

## Build-VM

Braucht `curl`, `tar`, `gzip` und `rpm` (für `rpm2archive` und `rpmkeys`),
alles Standard auf RHEL 9. Kein root, keine Compiler.

```bash
postgres/build.sh                  # Upload am Ende bestätigen
UPLOAD=j postgres/build.sh         # ohne Rückfrage hochladen
```

Arbeitsverzeichnis ist `~/build/postgres` (änderbar per `WORK_DIR`), der
Tarball liegt danach in `dist/`. Ohne Nexus-Upload lässt er sich auch per
`scp` auf die Runtime-VM kopieren und dort nach `~/postgres/downloads/`
legen, `install.sh` nimmt dann die vorhandene Datei.

`BUILD_INFO` im Tarball enthält RPM-Release, Build-Datum und die SHA256 der
Quell-RPMs.

## Runtime-VM

### Voraussetzungen (einmalig als root)

```bash
dnf install -y libicu numactl-libs liburing
loginctl enable-linger postgres
firewall-cmd --permanent --add-port=5432/tcp && firewall-cmd --reload
```

`libicu` und `numactl-libs` kommen aus BaseOS, `liburing` aus AppStream.
Alles andere (OpenSSL, lz4, zstd, libxml2, systemd-libs, ...) ist auf RHEL 9
Standard. Fehlt eine Bibliothek, bricht `install.sh` nach dem `ldd`-Check mit
der Liste ab. Das contrib-Modul `xml2` braucht zusätzlich `libxslt`.

### Installation

```bash
postgres/install.sh                            # als User "postgres"
PG_PASSWORD=... ENABLE=j postgres/install.sh   # ohne Rückfragen
```

`systemctl --user` braucht eine echte Login-Session (SSH direkt als User
oder `machinectl shell postgres@`), nicht `su -` oder `sudo -u`.

Verzeichnisse unter `~/postgres` (änderbar per `PG_BASE`):

```
~/postgres/
├── server/postgresql-18.6/  + current -> postgresql-18.6
├── etc/postgresql.conf      einmalig angelegt, danach eigene Pflege
├── etc/pg_hba.conf          einmalig angelegt, scram-sha-256 für alle
├── etc/pg_ident.conf        einmalig angelegt (leer)
├── etc/postgres.env         bei jedem Lauf neu generiert
├── data/18/                 Cluster (initdb), pro Major-Version
├── log/                     postgresql-<Wochentag>.log, 7 Tage rotierend
├── run/                     Unix-Socket
└── downloads/
```

`initdb` läuft nur, wenn `data/<MAJOR>` noch keinen Cluster enthält:
Encoding UTF8, Locale-Provider `builtin` mit `C.UTF-8` (Sortierung bleibt
bei glibc- und ICU-Updates stabil), Data-Checksums an. Der Superuser heißt
`postgres` (änderbar per `PG_SUPERUSER`). Sein Passwort kommt aus
`PG_PASSWORD`, wird abgefragt oder zufällig erzeugt und steht danach in
`~/.pgpass` (für Socket und `localhost`).

`data/18/postgresql.conf` ist die Datei von initdb und bindet am Ende
`etc/postgresql.conf` ein. Eigene Einstellungen gehören nach `etc/`. Port
(`PG_PORT`, Default 5432) und Pfade werden nur beim ersten Anlegen
eingetragen. `pg_stat_statements` ist vorgeladen.

```bash
set -a; . ~/postgres/etc/postgres.env; set +a
psql -c 'select version()'
systemctl --user reload postgres     # nach Änderungen an pg_hba.conf
```

Ist `/home` mit `noexec` gemountet, `PG_BASE` auf ein ausführbares
Dateisystem legen (z.B. `/opt/local/postgres`, einmalig von root anlegen
und dem User übergeben).

## Minor-Upgrade (z.B. 18.6 → 18.7)

Neue Version und Release-Tag in `versions.conf` eintragen, auf der
Build-VM `build.sh` ausführen, dann auf jeder Runtime-VM `install.sh`. Die
neue Version wird neben die alte entpackt, `current` umgebogen und der Dienst neu gestartet. `data/`
und `etc/` bleiben erhalten. Rollback: `current` zurücksetzen und
`systemctl --user restart postgres`.

## Major-Upgrade (z.B. 18 → 19)

Tarball wie beim Minor-Upgrade mit `build.sh` erzeugen. `install.sh`
erkennt einen Cluster einer anderen Major-Version, entpackt die neue
Version und bricht dann ab. Das Upgrade selbst läuft mit
`pg_upgrade` von Hand. Im Beispiel 17.11 → 18.6 (in einer frischen Shell,
ohne `postgres.env`):

```bash
OLD=~/postgres/server/postgresql-17.11
NEW=~/postgres/server/postgresql-18.6
export LD_LIBRARY_PATH=$NEW/lib
read -rsp "Passwort postgres: " PGPASSWORD; export PGPASSWORD; echo

# Neuer Cluster mit denselben Optionen wie im Skript
$NEW/bin/initdb -D ~/postgres/data/18 -U postgres --auth=trust \
    --data-checksums -E UTF8 --locale-provider=builtin --locale=C.UTF-8

systemctl --user stop postgres
mkdir -p ~/postgres/upgrade && cd ~/postgres/upgrade
$NEW/bin/pg_upgrade -U postgres -b $OLD/bin -B $NEW/bin \
    -d ~/postgres/data/17 -D ~/postgres/data/18 --check
$NEW/bin/pg_upgrade -U postgres -b $OLD/bin -B $NEW/bin \
    -d ~/postgres/data/17 -D ~/postgres/data/18
```

Danach `install.sh` erneut ausführen. Es findet den Cluster in
`data/18`, bindet `etc/postgresql.conf` ein, entfernt die `pg_hba.conf`
von initdb, schreibt die Unit auf `data/18` um und startet den Dienst.
Anschließend:

```bash
set -a; . ~/postgres/etc/postgres.env; set +a
cd ~/postgres/upgrade
[ -f update_extensions.sql ] && psql -f update_extensions.sql
vacuumdb --all --analyze-in-stages --missing-stats-only
./delete_old_cluster.sh              # erst wenn alles läuft
```

Stammt der alte Cluster nicht aus diesem Skript und hat keine
Data-Checksums, den neuen mit `--no-data-checksums` anlegen.
