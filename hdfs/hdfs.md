# hdfs

Hilfsmittel rund um ACL-Metadaten (xattr `user.acl`) auf Ordnern im HDFS.

## ACL-Format

```
<besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
```

- `besitzer` / `eintrag`: `@clientid`, User (ohne Präfix) oder `#gruppe`
- `recht`: `3` = lesen + schreiben + löschen, `1` = nur lesen
- Der Besitzer hat immer volle Rechte (3).
- Ausgewertet werden nur `@clientid`s, User und Gruppen werden ignoriert.
- Ein Container ohne ACL erbt die ACL seines Accounts. Ein Container mit
  eigener ACL ersetzt die des Accounts komplett.

Beispiel: `@client1|@client2=3,svc_backup=1,@client3=1,#ops=3` ergibt
`@client1=3`, `@client2=3`, `@client3=1`.

## hdfs-acl – ACLs einsammeln, suchen, migrieren

`src/hdfs/main.py`, nur Standardbibliothek, läuft ab Python 3.9 (RHEL 9).
Direkt aufrufbar oder über uv:

```bash
python3 src/hdfs/main.py <kommando> ...
uv run hdfs-acl <kommando> ...
```

Für den HDFS-Client gelten dieselben Variablen wie bei `test.sh`
(`HADOOP_REPO`, `HADOOP_HOME`, `HADOOP_CONF_DIR`, `HDFS_JAVA_HOME`), siehe
unten.

### Einsammeln

```bash
hdfs-acl collect                     # /fs, user.acl -> ./hdfs-acl.db
hdfs-acl collect --base-dir /data --attr user.rights --db /var/tmp/acl.db
```

Liest mit je einem `getfattr` pro Ebene (`/fs/*`, `/fs/*/*`) alle Accounts
und Container, nicht rekursiv. Dateien in den Containern werden also nicht
angefasst, und es dauert nur ein paar Sekunden. `--depth` ändert die Anzahl
der Ebenen. Ergebnis ist eine SQLite-Datei mit den Tabellen

| Tabelle     | Inhalt                                                          |
|-------------|-----------------------------------------------------------------|
| `node`      | jeder Pfad mit Roh-ACL, `acl_from` (wessen ACL gilt), Warnungen |
| `entry`     | eigene ACL-Einträge aller Arten (client/user/group), Besitzer   |
| `effective` | effektives Recht je `@clientid` und Pfad, Vererbung aufgelöst   |
| `meta`      | Basisordner, Attribut, Zeitpunkt der Sammlung                   |

Die DB wird bei jedem `collect` komplett neu geschrieben, Abfragen gehen auch
direkt mit `sqlite3 hdfs-acl.db`. ACLs mit Formatfehlern (kein `|`,
Eintrag ohne `=<zahl>`, Recht nicht 1/3) meldet `collect` als Warnung.

### Suchen

```bash
hdfs-acl search @client3                 # alle Pfade mit Rechten, inkl. geerbter
hdfs-acl search client3 --own           # nur wo direkt eingetragen
hdfs-acl search '@client*' --perm 3     # Glob, nur Lesen+Schreiben+Löschen
hdfs-acl search @client3 --format csv   # oder json
hdfs-acl clients                         # alle clientids mit Anzahl Pfade
hdfs-acl show /fs/account3/container10   # ACL, Herkunft und Rechte eines Pfads
hdfs-acl export -o export.csv            # Format wie testdata/expected.csv
```

Das `@` an der clientid kann man weglassen. Suchen arbeiten auf dem Stand
des letzten `collect`, der Zeitpunkt steht auf stderr.

### Ändern

`add`, `remove` und `restore` lesen HDFS vorher frisch ein, zeigen für jeden
betroffenen Pfad alt/neu und schreiben **erst mit `--apply`**. Vor dem
Schreiben legen sie neben der DB ein Backup `hdfs-acl-backup-<zeit>-<aktion>.csv`
an (`path;old;new`). Danach lesen sie HDFS erneut und prüfen jeden geänderten Wert.

Geändert werden nur Pfade mit eigener ACL. Container, die erben, ändern sich
über ihren Account automatisch mit. Einträge für User, Gruppen und andere
Client-IDs bleiben Wort für Wort erhalten.

```bash
# Migration: @newapp bekommt überall dieselben Rechte wie @client3
hdfs-acl add @client3 @newapp            # Plan ansehen
hdfs-acl add @client3 @newapp --apply
```

- Ist `@client3` Besitzer, wird `@newapp=3` als Eintrag ergänzt (der
  Besitzer bleibt einer).
- Hat `@newapp` schon gleiche oder höhere Rechte, passiert nichts. Hat es
  niedrigere, wird der Eintrag hochgestuft. Herabgestuft wird nie.
- Mehrfach ausführen ist unschädlich.

```bash
# Client-ID überall entfernen
hdfs-acl remove @client3
hdfs-acl remove @client3 --apply
hdfs-acl remove @client3 --new-owner '#admins' --apply
```

Pfade, an denen die clientid **Besitzer** ist, werden ohne `--new-owner`
übersprungen und aufgelistet (Exit-Code 2), weil eine ACL einen Besitzer
braucht. Bleibt nach dem Entfernen nur der Besitzer übrig, wird die ACL zu
`<besitzer>|`. Bei einem Container bedeutet das weiterhin: Die Account-ACL
gilt dort nicht.

```bash
# zurückdrehen
hdfs-acl restore hdfs-acl-backup-20261015-152310-add-newapp.csv --apply
```

`restore` setzt nur Pfade zurück, deren aktueller Wert noch dem `new` aus
dem Backup entspricht. Pfade, die seitdem jemand anders geändert hat, werden
übersprungen und gemeldet (Exit-Code 2). `--force` setzt sie trotzdem zurück.

Gemeinsame Optionen: `--db` (`$HDFS_ACL_DB`, Default `./hdfs-acl.db`),
`--base-dir` (`$BASE_DIR`), `--attr` (`$ACL_ATTR`), `--parallel`
(`$PARALLEL`, Default 8).

### Gegen die Testdaten prüfen

```bash
./test.sh --clean
python3 src/hdfs/main.py collect
python3 src/hdfs/main.py export | diff - testdata/expected.csv && echo OK
```

## test.sh – Testdaten anlegen

Legt `/fs/account1..10` mit je `container1..50` an (510 Ordner), setzt an
409 davon `user.acl` und vergleicht das Ergebnis per `getfattr -R` mit den
erwarteten Werten.

```bash
./test.sh --clean        # /fs löschen, neu anlegen, ACLs setzen, prüfen (~3 min)
./test.sh                # anlegen/überschreiben ohne vorher zu löschen
./test.sh --verify-only  # nur HDFS gegen testdata/acls.csv prüfen
./test.sh --dry-run      # nur testdata/*.csv erzeugen, HDFS nicht anfassen
```

Basisordner und Attributname sind per Umgebungsvariable einstellbar:

```bash
BASE_DIR=/acltest ACL_ATTR=user.rights ./test.sh --clean
```

| Variable          | Default                                 |
|-------------------|-----------------------------------------|
| `BASE_DIR`        | `/fs`                                   |
| `ACL_ATTR`        | `user.acl`                              |
| `PARALLEL`        | `8` (parallele `setfattr`-Aufrufe)      |
| `OUT_DIR`         | `hdfs/testdata`                         |
| `HADOOP_REPO`     | `~/git/hadoop`                          |
| `HADOOP_HOME`     | `$HADOOP_REPO/downloads/hadoop-3.4.2`   |
| `HADOOP_CONF_DIR` | `$HADOOP_REPO/clientconfig`             |
| `HDFS_JAVA_HOME`  | gesucht, siehe unten                    |

### Java

Der Hadoop-3.4.2-Client läuft nicht mit Java ≥ 23 (`getSubject is not
supported`). Ohne `HDFS_JAVA_HOME` nimmt das Skript das erste Java < 23 aus
`$HADOOP_REPO/downloads/jdk-11`, sdkman (`~/.sdkman/candidates/java/11*`,
`17*`), `/usr/lib/jvm/{jre,java}-{11,17}*` oder `$JAVA_HOME`. Ein
sdkman-`current` auf Java 25 stört also nicht.

Auf RHEL 9 genügt:

```bash
sudo dnf install python3 java-17-openjdk-headless   # optional: sqlite diffutils
```

Der Hadoop-Client selbst kommt als Tarball (`HADOOP_HOME`, `HADOOP_CONF_DIR`).

### Vergleichsdateien

Beide mit `;` getrennt, weil die ACL-Werte selbst `,` und `|` enthalten.

- `testdata/acls.csv` – `path;acl`: der gesetzte Rohwert je Pfad, leer =
  kein Attribut. Grundlage für `--verify-only`.
- `testdata/expected.csv` – `path;clientid;perm;acl_from`: effektives Recht
  je `@clientid` und Pfad, inklusive Vererbung. `acl_from` ist der Pfad,
  dessen ACL gilt (bei geerbten Containern der Account). Pfade, an denen
  keine `@clientid` Rechte hat, tauchen nicht auf.

Die Dateien hängen nur von `BASE_DIR` ab und sind bei gleichen Variablen
immer identisch.

### Schema

`n` = Account-Nummer, `cl(k)` = `@client<k>`, zyklisch über 1..10.

| Pfad                 | ACL                                               | Testet                               |
|----------------------|---------------------------------------------------|--------------------------------------|
| `account<n>`         | `cl(n)\|cl(n+1)=3,svc_backup=1,cl(n+2)=1,#ops=3`  | Grundfall, User/Gruppe gemischt      |
| `account3`           | Besitzer `hans`                                   | Besitzer ist User                    |
| `account7`           | Besitzer `#admins`                                | Besitzer ist Gruppe                  |
| `account10`          | keine                                             | Account ohne ACL                     |
| `container<c>`, c%5=0 | keine                                            | Vererbung vom Account                |
| c%5=1                | `cl(n)\|cl(n+3)=3,dev_user=3,cl(n+4)=1`           | Ersetzen der Account-ACL             |
| c%5=2                | `cl(n+5)\|#readers=1,cl(n+5)=1,cl(n)=1`           | Besitzer zusätzlich `=1` → bleibt 3  |
| c%5=3                | `maria\|cl(n+6)=3,cl(n+7)=1,#ops=1`               | Clients nur über Einträge            |
| c%5=4                | `#admins\|peter=3,#ops=1`                         | keine Client-ID → niemand            |
| `container49`        | `cl(n+8)\|`                                       | nur Besitzer, keine Einträge         |
