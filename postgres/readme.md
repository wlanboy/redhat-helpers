# postgres – PostgreSQL als non-root betreiben (RHEL 9, offline)

PostgreSQL wird nicht compiliert und nicht per dnf installiert. Genutzt
werden die fertigen RPMs aus dem offiziellen PostgreSQL-Yum-Repo (PGDG).
Die Build-VM prüft einmal pro Version deren Signatur, entpackt sie mit
`rpm2archive` und legt das Ergebnis als Tarball in Nexus ab. Die
Runtime-VMs laden nur diesen Tarball, entpacken ihn nach
`/opt/local/postgres` und betreiben den Server als systemd User Unit.

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
verschieben. Nur `libpq.so.5` findet der Loader nicht relativ, denn die
Programme und Module, die sie brauchen (`psql`, `pg_ctl`, `pg_dump`, `postgres_fdw`,
`dblink`), haben keinen RPATH. Sie laden `libpq` per `LD_LIBRARY_PATH` aus
dem eigenen `lib/`. Für den Dienst steht das in `etc/postgres.env`, für die
Shell in `etc/postgres.sh`.

Der Tarball ist an RHEL 9 und die CPU-Architektur gebunden. Build- und
Runtime-VMs müssen beide RHEL 9 mit gleicher Architektur sein.

## Dateien

- **versions.conf** – Version (Default: 18.7), RPM-Release-Tag,
  Signaturprüfung an/aus, Nexus-URL und Repo-Namen. Jeder Wert lässt sich
  per Umgebungsvariable überschreiben.
- **build.sh** – als User, Build-VM. Download der RPMs, Signaturprüfung,
  Entpacken, Tarball mit `BUILD_INFO` und `.sha256`, optional Upload nach
  Nexus.
- **urls.txt** – Upstream-URLs der RPMs und des GPG-Keys für die
  Default-Version (z.B. für Firewall-Freigaben oder manuellen Download
  per `wget -i urls.txt`).
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
18/redhat/rhel-9-x86_64/postgresql18-18.7-1PGDG.rhel9.9.x86_64.rpm
18/redhat/rhel-9-x86_64/postgresql18-libs-18.7-1PGDG.rhel9.9.x86_64.rpm
18/redhat/rhel-9-x86_64/postgresql18-server-18.7-1PGDG.rhel9.9.x86_64.rpm
18/redhat/rhel-9-x86_64/postgresql18-contrib-18.7-1PGDG.rhel9.9.x86_64.rpm
```

Auf aarch64 `rhel-9-aarch64` und `.aarch64.rpm`. Der Release-Teil
(`1PGDG.rhel9.9`) enthält die RHEL-Minor-Version, gegen die PGDG gebaut hat,
und ändert sich mit neuen Builds. Den passenden Wert im Repo-Listing
nachsehen und als `PG_RPM_RELEASE` eintragen.

PGDG hält im Repo nur die aktuelle Minor-Version vor. Mit 18.8 verschwindet
18.7 upstream. Eine ältere Version lässt sich danach nur noch bauen, wenn
der Proxy die RPMs schon im Cache hat.

Outbound von Nexus zu `download.postgresql.org` per HTTPS freischalten.
Ist ein Proxy nicht erlaubt: Raw-Hosted-Repo mit demselben Namen und die
Dateien unter denselben Pfaden hochladen.

`build.sh` lädt nach `postgres-builds` hoch:

```
postgresql/18.7/postgresql-18.7-el9-x86_64.tar.gz
postgresql/18.7/postgresql-18.7-el9-x86_64.tar.gz.sha256
```

Der Build-User braucht dafür Schreibrechte (`nx-repository-view-raw-postgres-builds-add`
und `-edit`), die Runtime-VMs nur Leserechte. Zugangsdaten liest `curl` aus
`~/.netrc`, falls vorhanden. Für den Upload alternativ `NEXUS_USER=<user>`
setzen, dann fragt `curl` nach dem Passwort. Solange `NEXUS_URL` mit
`http://` beginnt, geht das Passwort im Klartext über das Netz. Bietet
Nexus HTTPS an, `NEXUS_URL` in `versions.conf` auf `https://` umstellen.

## Build-VM

Braucht `curl`, `tar`, `gzip` und `rpm` (für `rpm2archive` und `rpmkeys`),
alles Standard auf RHEL 9. Kein root, keine Compiler.

```bash
postgres/build.sh                  # Upload am Ende bestätigen
UPLOAD=j postgres/build.sh         # ohne Rückfrage hochladen
```

Arbeitsverzeichnis ist `~/build/postgres` (änderbar per `WORK_DIR`), der
Tarball liegt danach in `dist/`. Ohne Nexus-Upload lässt er sich auch per
`scp` auf die Runtime-VM kopieren. Tarball und `.sha256` gehören beide nach
`/opt/local/postgres/downloads/`, `install.sh` nimmt dann die vorhandenen
Dateien.

`build.sh` vergleicht zuerst den SHA256 des PGDG-Keys mit
`PG_GPG_KEY_SHA256` aus `versions.conf`. Danach importiert es den Key in
eine eigene rpm-Datenbank und verlangt für jedes RPM `signatures OK`. Ein unsigniertes
RPM fällt damit ebenso durch wie ein falsch signiertes.

`BUILD_INFO` im Tarball enthält RPM-Release, Build-Datum und die SHA256 der
Quell-RPMs.

## Runtime-VM

### Voraussetzungen (einmalig als root)

```bash
dnf install -y libicu numactl-libs liburing
useradd postgres && passwd postgres   # oder SSH-Key hinterlegen
loginctl enable-linger postgres
mkdir -p /opt/local/postgres && chown postgres: /opt/local/postgres
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

Verzeichnisse unter `/opt/local/postgres` (änderbar per `PG_BASE`):

```
/opt/local/postgres/
├── server/postgresql-18.7/  + current -> postgresql-18.7
├── etc/postgresql.conf      einmalig angelegt, danach eigene Pflege
├── etc/pg_hba.conf          einmalig angelegt, scram-sha-256 für alle
├── etc/pg_ident.conf        einmalig angelegt (leer)
├── etc/postgres.env         für systemd, bei jedem Lauf neu generiert
├── etc/postgres.sh          für die Shell, bei jedem Lauf neu generiert
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

`etc/postgres.sh` ergänzt `PATH` und `LD_LIBRARY_PATH` und setzt die
`PG*`-Variablen, die Zeile passt also auch in `~/.bashrc`. `postgres.env`
setzt dagegen einen festen `PATH` und ist nur für systemd gedacht.

```bash
. /opt/local/postgres/etc/postgres.sh
psql -c 'select version()'
systemctl --user reload postgres     # nach Änderungen an pg_hba.conf
```

## Minor-Upgrade (z.B. 18.7 → 18.8)

Neue Version und Release-Tag in `versions.conf` und `urls.txt` eintragen, auf der
Build-VM `build.sh` ausführen, dann auf jeder Runtime-VM `install.sh`. Die
neue Version wird neben die alte entpackt, `current` umgebogen und der Dienst neu gestartet. `data/`
und `etc/` bleiben erhalten. Rollback: `current` zurücksetzen und
`systemctl --user restart postgres`.

Der Name des Tarballs enthält nur die PostgreSQL-Version, nicht das
RPM-Release. Baut man dieselbe Version mit einem neuen `PG_RPM_RELEASE`
neu, überschreibt der Upload den alten Tarball. Dazu muss die Write-Policy
von `postgres-builds` Redeploy erlauben. Auf Runtime-VMs, die die Version
schon haben, vor `install.sh` aufräumen, sonst bleibt der alte Stand:

```bash
systemctl --user stop postgres
rm -rf /opt/local/postgres/downloads/postgresql-18.7-* /opt/local/postgres/server/postgresql-18.7
postgres/install.sh
```

## Major-Upgrade (z.B. 18 → 19)

Tarball wie beim Minor-Upgrade mit `build.sh` erzeugen. `install.sh`
erkennt einen Cluster einer anderen Major-Version, entpackt die neue
Version und bricht dann ab. Das Upgrade selbst läuft mit
`pg_upgrade` von Hand. Im Beispiel 17.11 → 18.7 (in einer frischen Shell,
ohne `postgres.env`):

```bash
OLD=/opt/local/postgres/server/postgresql-17.11
NEW=/opt/local/postgres/server/postgresql-18.7
export LD_LIBRARY_PATH=$NEW/lib
read -rsp "Passwort postgres: " PGPASSWORD; export PGPASSWORD; echo

# Neuer Cluster mit denselben Optionen wie im Skript
$NEW/bin/initdb -D /opt/local/postgres/data/18 -U postgres --auth=trust \
    --data-checksums -E UTF8 --locale-provider=builtin --locale=C.UTF-8

systemctl --user stop postgres
mkdir -p /opt/local/postgres/upgrade && cd /opt/local/postgres/upgrade
$NEW/bin/pg_upgrade -U postgres -b $OLD/bin -B $NEW/bin \
    -d /opt/local/postgres/data/17 -D /opt/local/postgres/data/18 --check
$NEW/bin/pg_upgrade -U postgres -b $OLD/bin -B $NEW/bin \
    -d /opt/local/postgres/data/17 -D /opt/local/postgres/data/18
```

Danach `install.sh` erneut ausführen. Es findet den Cluster in
`data/18`, bindet `etc/postgresql.conf` ein, entfernt die `pg_hba.conf`
von initdb, schreibt die Unit auf `data/18` um und startet den Dienst.
Anschließend:

```bash
. /opt/local/postgres/etc/postgres.sh
cd /opt/local/postgres/upgrade
[ -f update_extensions.sql ] && psql -f update_extensions.sql
vacuumdb --all --analyze-in-stages --missing-stats-only
./delete_old_cluster.sh              # erst wenn alles läuft
```

Stammt der alte Cluster nicht aus diesem Skript und hat keine
Data-Checksums, den neuen mit `--no-data-checksums` anlegen.
