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

Paket `src/hdfs/`, nur Standardbibliothek, läuft ab Python 3.9 (RHEL 9).
Direkt aufrufbar oder über uv:

```bash
python3 src/hdfs/main.py <kommando> ...
uv run hdfs-acl <kommando> ...
```

| Datei            | Inhalt                                                             |
|------------------|--------------------------------------------------------------------|
| `main.py`        | Einstieg: Argumente (argparse), Fehlerbehandlung                   |
| `commands.py`    | ein `cmd_*` je Kommando, gemeinsamer Ablauf `execute()` fürs Ändern |
| `acl.py`         | ACL-Format parsen/prüfen, effektive Rechte, Vererbung              |
| `changes.py`     | `Change` und `plan_*`: was `add`/`replace`/`remove`/`restore` ändern |
| `fsimage.py`     | XML von `hdfs oiv` als Stream lesen, Pfade zusammensetzen          |
| `hdfs_client.py` | `hdfs oiv`, `hdfs dfs -getfattr/-setfattr` aufrufen, Java suchen   |
| `store.py`       | SQLite-Schema, DB schreiben/öffnen, Backup-CSV                     |
| `output.py`      | Ausgabe als Tabelle, CSV oder JSON                                 |
| `common.py`      | Fehlerklassen, Meldungen auf stderr, natürliche Sortierung         |

`acl.py`, `changes.py` und `fsimage.py` arbeiten nur auf Strings bzw.
Streams und lassen sich ohne HDFS testen.

Für den HDFS-Client gelten dieselben Variablen wie bei `test.sh`
(`HADOOP_REPO`, `HADOOP_HOME`, `HADOOP_CONF_DIR`, `HDFS_JAVA_HOME`), siehe
unten.

### fsimage besorgen

Eingelesen wird aus dem **fsimage**, dem Checkpoint des NameNodes. Das Tool
wertet es lokal mit `hdfs oiv` aus und stellt dabei **keine einzige Anfrage
an den NameNode**. Es erwartet das fsimage im selben Ordner wie die DB
(Default: aktueller Ordner) und nimmt dort das neueste `fsimage_<txid>`
(höchste txid). Ältere Images kann man löschen.

```bash
cd /var/tmp/hdfs-acl              # Ordner mit hdfs-acl.db
hdfs dfsadmin -fetchImage .       # legt fsimage_<txid> hier ab
hdfs-acl collect
```

- `hdfs dfsadmin -fetchImage` lädt den letzten Checkpoint per HTTP vom
  NameNode herunter. Das ist eine reine Dateiübertragung (so groß wie das
  fsimage, oft mehrere GB) ohne Sperre auf dem Namensraum. In Clustern mit
  Kerberos braucht es in der Regel HDFS-Adminrechte.
- Ganz ohne den NameNode: auf dem **Standby-NameNode** das neueste
  `fsimage_<txid>` aus `<dfs.namenode.name.dir>/current/` kopieren
  (z. B. per `scp`) und neben die DB legen. Die `.md5`-Datei braucht das Tool
  nicht.
- Das fsimage ist so alt wie der letzte Checkpoint. Standby- bzw.
  Secondary-NameNode schreiben ihn spätestens nach
  `dfs.namenode.checkpoint.period` (Default 3600 s) oder nach
  `dfs.namenode.checkpoint.txns` (Default 1.000.000) Änderungen.
- **Nicht in Produktion:** `hdfs dfsadmin -saveNamespace` erzwingt einen
  Checkpoint, verlangt aber den Safemode, und der sperrt alle Schreibzugriffe.
  Nur für den lokalen Test (siehe unten).
- Bricht `oiv` bei sehr großen Images mit `OutOfMemoryError` ab, hilft
  `export HADOOP_CLIENT_OPTS=-Xmx8g`.

Anderer Ort: `--fsimage DATEI` oder `$HDFS_FSIMAGE`.

### Einsammeln

```bash
hdfs-acl collect                     # /fs, user.acl, fsimage -> ./hdfs-acl.db
hdfs-acl collect --base-dir /data --attr user.rights --db /var/tmp/acl.db
hdfs-acl collect --live              # direkt aus HDFS statt aus dem fsimage
```

Aus dem fsimage kommen alle Ordner auf den Ebenen unter `--base-dir` (Default
2: Accounts und Container). Gemerkt werden nur Ordner. Dateien, meist der
Großteil des Images, kosten nur Lesezeit.

`--live` liest stattdessen mit je einem `getfattr` pro Ebene (`/fs/*`,
`/fs/*/*`), nicht rekursiv. Das ist aktuell, kostet aber einen NameNode-RPC je
Pfad, nacheinander. Gut für kleine Bestände und den Test. Ein Pfad, dessen
Attribute nicht lesbar sind (keine Berechtigung, inzwischen gelöscht), wird
mit Warnung übersprungen. Ist ein Ordner nicht listbar, bricht `collect` ab,
weil das Ergebnis sonst Lücken hätte. Anders als das fsimage liefert `--live`
auch Dateien, die direkt auf diesen Ebenen liegen.

Ergebnis ist eine SQLite-Datei mit den Tabellen

| Tabelle     | Inhalt                                                          |
|-------------|-----------------------------------------------------------------|
| `node`      | jeder Pfad mit Roh-ACL, `acl_from` (wessen ACL gilt), Warnungen |
| `entry`     | nur **aktiv gesetzte** Einträge aller Arten (client/user/group) inkl. Besitzer |
| `effective` | effektives Recht je `@clientid` und Pfad, Vererbung aufgelöst (berechnet) |
| `meta`      | Basisordner, Attribut, Zeitpunkt und Quelle (fsimage/live)      |

Die DB wird bei jedem `collect` komplett neu geschrieben, Abfragen gehen auch
direkt mit `sqlite3 hdfs-acl.db`. ACLs mit Formatfehlern (kein `|`,
Eintrag ohne `=<zahl>`, Recht nicht 1/3) meldet `collect` als Warnung.

### Suchen

```bash
hdfs-acl search @client3                 # alle Pfade mit Rechten, inkl. geerbter
hdfs-acl search client3 --own           # nur aktiv gesetzte, mit Roh-ACL
hdfs-acl search '@*' --own --format csv # alle aktiv gesetzten ACLs pro clientid
hdfs-acl search '@client*' --perm 3     # Glob, nur Lesen+Schreiben+Löschen
hdfs-acl search @client3 --format csv   # oder json
hdfs-acl clients                         # alle clientids mit Anzahl Pfade
hdfs-acl show /fs/account3/container10   # ACL, Herkunft und Rechte eines Pfads
hdfs-acl export -o export.csv            # Format wie testdata/expected.csv
```

Ausgabe sortiert nach clientid, dann Pfad. `--own` liest aus `entry` und
zeigt statt `acl_from` die Roh-ACL des Pfads. Das `@` an der clientid kann
man weglassen. Suchen arbeiten auf dem Stand
des letzten `collect`, Zeitpunkt und Quelle stehen auf stderr.

### Ändern

`add`, `replace`, `remove` und `restore` laufen so ab:

1. Einlesen wie bei `collect` (fsimage, mit `--live` direkt) und planen.
2. Die Pfade, die der Plan ändern würde, **live nachlesen** und auf deren
   aktuellen Werten neu planen. Das fsimage kann eine Stunde alt sein:
   Pfade, die sich seitdem geändert haben, werden so richtig behandelt,
   gelöschte übersprungen. `restore` liest die Pfade aus dem Backup immer
   live, das fsimage kennt die zurückzudrehenden Änderungen meist noch nicht.
3. Für jeden betroffenen Pfad alt/neu zeigen und **erst mit `--apply`**
   schreiben. Vorher entsteht neben der DB ein Backup
   `hdfs-acl-backup-<zeit>-<aktion>.csv` (`path;old;new`).
4. Die geänderten Pfade erneut lesen, jeden Wert prüfen und die DB mit dem
   Ergebnis fortschreiben (Quelle z. B. `fsimage_… + replace-client3-newapp`).

Grenze des fsimage: Ein Pfad, der erst **nach** dem Checkpoint die clientid
bekommen hat, fehlt im Plan. Nach dem nächsten Checkpoint erneut ausführen
(alle Kommandos sind wiederholbar) oder bei wenigen Pfaden `--live` nehmen.

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
# ALT direkt durch NEU ersetzen, auch als Besitzer
hdfs-acl replace @client3 @newapp
hdfs-acl replace @client3 @newapp --apply
```

- Besitzer `@client3` wird Besitzer `@newapp`, Einträge `@client3=x` werden
  an derselben Stelle zu `@newapp=x`.
- Hatte `@newapp` schon einen Eintrag, bleibt einer mit dem höheren Recht.
  Ist `@newapp` danach Besitzer, entfallen seine Einträge (Besitzer hat 3).
- `@newapp` hat danach überall genau die Rechte, die `@client3` hatte (plus
  seine eigenen), `@client3` nirgends mehr.

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
gilt dort nicht. Mit `--new-owner` entfallen die Einträge des neuen
Besitzers, weil er als Besitzer ohnehin 3 hat.

Migration in zwei Schritten (erst ergänzen, Anwendung umstellen, dann
aufräumen):

```bash
hdfs-acl add @client3 @newapp --apply                          # @newapp=3 auch, wo @client3 Besitzer ist
# ... Anwendung auf @newapp umstellen ...
hdfs-acl remove @client3 --new-owner @newapp --apply           # @newapp wird dort Besitzer
```

Die Rechte sind danach dieselben wie bei `replace @client3 @newapp`. Nur die
Reihenfolge der Einträge kann abweichen, weil `add` neue Einträge hinten
anhängt.

```bash
# zurückdrehen
hdfs-acl restore hdfs-acl-backup-20261015-152310-add-newapp.csv --apply
```

`restore` setzt nur Pfade zurück, deren aktueller Wert noch dem `new` aus
dem Backup entspricht. Pfade, die seitdem jemand anders geändert hat, werden
übersprungen und gemeldet (Exit-Code 2). `--force` setzt sie trotzdem zurück.

Gemeinsame Optionen: `--db` (`$HDFS_ACL_DB`, Default `./hdfs-acl.db`),
`--base-dir` (`$BASE_DIR`), `--attr` (`$ACL_ATTR`), `--fsimage`
(`$HDFS_FSIMAGE`), `--live`, `--parallel` (`$PARALLEL`, Default 8).

### Viele Container

`getfattr`/`setfattr` nehmen nur einen Pfad, jeder Aufruf startet aber eine
JVM (1-2 s). Das Tool fasst deshalb viele Pfade zu einem Glob
`/{fs/a1/c1,fs/a1/c2,...}` zusammen (bis 100 KB je Aufruf, rund 2000 Pfade).
Hadoop löst solche Globs in einzelne Pfade auf, ohne Ordner zu listen. Beim
Schreiben gehen alle Pfade mit demselben neuen Wert in einen Aufruf.
`--parallel` Aufrufe laufen gleichzeitig, jeder schickt seine RPCs
nacheinander. Ein kleineres `--parallel` drosselt die Last auf dem NameNode.

| Schritt                      | NameNode-Last                      | gemessen                                     |
|------------------------------|------------------------------------|----------------------------------------------|
| Einlesen aus fsimage         | keine                              | 5 Mio. Inodes (1 Mio. Container): 76 s, 0,6 GB RAM |
| DB schreiben                 | keine                              | 1 Mio. Container: ca. 30 s, 1,3 GB DB        |
| Nachlesen/Schreiben/Prüfen   | 1-2 RPCs je **betroffenem** Pfad   | 4000 Pfade schreiben + lesen: 6 s            |
| Einlesen `--live`            | 1 RPC je Pfad, nacheinander        | geschätzt 5-20 min je Mio. Pfade             |

Gemessen lokal mit einem einzelnen NameNode bzw. synthetischem oiv-XML. Die
Laufzeit von `oiv` selbst kommt beim fsimage dazu. Der RAM-Bedarf wächst mit
der Zahl der Ordner im ganzen Image (rund 0,3 GB je Million), nicht mit der
Zahl der Dateien. Betroffen sind nur Pfade mit eigener ACL, die die clientid
enthalten. Container, die erben, kosten beim Ändern nichts.

### Gegen die Testdaten prüfen

```bash
./test.sh --clean
python3 src/hdfs/main.py collect --live
python3 src/hdfs/main.py export | diff - testdata/expected.csv && echo OK
```

Mit fsimage braucht der Testcluster erst einen Checkpoint, der die
Testdaten enthält. **Nur im Test**, `saveNamespace` sperrt per Safemode alle
Schreibzugriffe:

```bash
hdfs dfsadmin -safemode enter && hdfs dfsadmin -saveNamespace && hdfs dfsadmin -safemode leave
hdfs dfsadmin -fetchImage .
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
