# hdfs

Hilfsmittel rund um ACL-Metadaten (xattr `user.acl`) auf Ordnern im HDFS.

## ACL-Format

```
<besitzer>|<eintrag>=<recht>,<eintrag>=<recht>,...
```

- `besitzer` / `eintrag`: `@clientid`, User (ohne Präfix) oder Gruppe. Eine
  Gruppe beginnt mit `.` und hat einen vollen DN:
  `.cn=ops,ou=groups,dc=firma=3` ist ein Eintrag, Gruppe
  `.cn=ops,ou=groups,dc=firma` mit Recht `3`. Die Kommas im DN trennen keine
  Einträge. Das `#` von früher ist kein Gruppenpräfix mehr, `#ops` zählt als User.
- LDAP-Teile **ohne** `.` davor werden ignoriert: Ein DN wie
  `cn=doc,ou=hell,dc=firma=3` zerfällt am `,` in `cn=doc`, `ou=hell`,
  `dc=firma=3`. Erkannt werden Teile, die mit `cn=`, `ou=`, `dc=`, `o=`, `c=`,
  `l=`, `st=`, `street=`, `uid=` oder `sn=` beginnen (Groß-/Kleinschreibung
  egal), gefolgt von einem Wert, der nicht nur aus Ziffern besteht. Sie geben
  keine Warnung, stehen nicht in `entry` und bleiben beim Ändern Wort für Wort
  erhalten.
- `recht`: Bitmaske. Genutzt werden `1` (lesen), `3` (lesen + schreiben) und
  `19` (verwalten), siehe [Rechte-Tabelle](#rechte-tabelle). Erlaubt ist jede
  Maske von 1 bis 2147483647 (Java-`int`), auch Bits ohne Namen.
- Steht eine clientid mehrfach in einer ACL, gelten alle ihre Bits zusammen
  (bitweises ODER): `@a=5,@a=16` ergibt `21`.
- Es gibt nur **einen Besitzer**. Er hat immer mindestens das Besitzer-Recht
  (`owner` in der Rechte-Tabelle, Default `19`). Ist er zusätzlich gelistet,
  kommen die Bits seiner Einträge dazu: Besitzer mit `=8` ergibt `27`.
- Ausgewertet werden nur `@clientid`s, User und Gruppen werden ignoriert.

Beispiel: `@client1|@client2=3,svc_backup=1,@client3=1,.cn=ops,ou=groups,dc=firma=3`
ergibt `@client1=19`, `@client2=3`, `@client3=1`.

### Nur die ACL-Einträge selbst

Das Tool wertet nur aus, was an einem Pfad selbst im xattr steht: Besitzer
und Einträge. Geerbte Rechte zeigt es nicht an und rechnet es nicht nach.
Ein Pfad ohne ACL hat in der DB keine Rechte, `search` findet nur ACLs, die
die clientid nennen.

Zur Einordnung, so wertet die Anwendung die ACLs aus:

- Ein Pfad ohne ACL erbt alles vom Elternordner.
- Eine Container-ACL **erweitert oder schränkt** die des Accounts **ein**.
  Nennt sie eine clientid, gilt dort deren Wert, sonst der aus dem Account.
- Container können einen eigenen Besitzer haben, er ersetzt den des
  Accounts. Ein leerer Besitzer (`|@a=1`) übernimmt den des Accounts.

### Bezug zu Spring Security ACL

Nachgelesen im Quelltext von Spring Security (`acl/…/domain`, Branch `main`):

- `BasePermission`: READ `1`, WRITE `2`, CREATE `4`, DELETE `8`,
  ADMINISTRATION `16`. `3` = READ + WRITE, `19` = READ + WRITE +
  ADMINISTRATION.
- Der Besitzer hat in Spring **keine Datenrechte**: Die Prüfung eines Rechts
  (`DefaultPermissionGrantingStrategy.isGranted`) geht nur die Einträge
  durch. „Der Besitzer hat immer `19`“ ist eine Regel der Anwendung.
- Der Besitzer darf die **ACL verwalten**
  (`AclAuthorizationStrategyImpl.securityCheck`). Alle anderen brauchen dafür
  einen Eintrag mit ADMINISTRATION (`16`, enthalten in `19`) oder eine
  passende Rolle.
- Standard-Spring vergleicht die Maske eines Eintrags exakt (`==`), nicht
  bitweise. Kombinierte Masken wie `3`, `19` oder `21` setzen eine eigene,
  bitweise Auswertung in der Anwendung voraus, wie sie dieses Tool annimmt.
  Das Zusammenführen von Account- und Container-ACL ist ebenfalls eine Regel
  der Anwendung.

Deshalb bekommt NEU bei `add` dort, wo ALT Besitzer ist, `19`.

### Rechte-Tabelle

Welcher Wert was bedeutet, ist konfigurierbar, passend zur
`PermissionFactory` der Anwendung. Ohne Konfiguration gelten die drei
genutzten Werte, wie in `permissions.json`:

```json
{
  "permissions": {"READ": 1, "READ_WRITE": 3, "ADMIN": 19},
  "owner": "ADMIN",
  "admin": "ADMIN"
}
```

| Schlüssel     | Bedeutung                                                         |
|---------------|-------------------------------------------------------------------|
| `permissions` | Name → Maske; einzelne Bits oder Kombinationen (`"VOLL": 3`)      |
| `owner`       | Recht, das der Besitzer immer hat (Default `19`)                  |
| `admin`       | Verwaltungsrecht, das `add` NEU zusätzlich gibt, wo ALT Besitzer ist (Default `19`) |

`owner` und `admin` gehen als Zahl oder als Namen (`"READ+READ_WRITE"`, auch mit
`|` oder `,` getrennt). Fehlende Schlüssel bekommen den Default. Namen sind
bei der Eingabe unabhängig von Groß-/Kleinschreibung.

Gesucht wird in dieser Reihenfolge: `--permissions DATEI`,
`$HDFS_ACL_PERMISSIONS`, `permissions.json` neben der DB, sonst eingebaut.
Gilt für alle Kommandos und für `migrate_check.py`.

Wirkung:

- `search` hat eine Spalte `rights` mit den Namen (`19` → `ADMIN`), `show`
  zeigt sie in Klammern (`@client3=19 [ADMIN]`). Bits ohne Namen bleiben als
  Zahl stehen (`27` → `ADMIN+8`, `5` → `READ+4`), ein Name mit mehr Bits
  gewinnt.
- `search --perm` und `--has-bits` nehmen auch Namen: `--has-bits ADMIN`.
- `owner` steckt in den Rechten der DB (Tabelle `rights`). Wird die Tabelle nach
  `collect` mit anderem `owner` gewechselt, warnen die lesenden Kommandos:
  dann `collect` erneut ausführen.
- `export` bleibt numerisch (Vergleich mit `testdata/expected.csv`).

## hdfs-acl – ACLs einsammeln, suchen, migrieren

Paket `src/hdfs/`, nur Standardbibliothek, läuft ab Python 3.9 (RHEL 9).
Direkt aufrufbar oder über uv:

```bash
python3 src/hdfs/main.py <kommando> ...
uv run main.py <kommando> ...
uv run hdfs-acl <kommando> ...
```

| Datei            | Inhalt                                                             |
|------------------|--------------------------------------------------------------------|
| `main.py`        | Einstieg: Argumente (argparse), Fehlerbehandlung                   |
| `commands.py`    | ein `cmd_*` je Kommando, gemeinsamer Ablauf `execute()` fürs Ändern |
| `acl.py`         | ACL-Format parsen/prüfen (inkl. Gruppen-DN), Rechte je ACL         |
| `permissions.py` | Rechte-Tabelle: Namen der Bits, Besitzer-/Verwaltungsrecht (JSON) |
| `changes.py`     | `Change` und `plan_*`: was `add`/`replace`/`remove`/`restore` ändern |
| `fsimage.py`     | XML von `hdfs oiv` als Stream lesen, Pfade zusammensetzen          |
| `hdfs_client.py` | `hdfs oiv`, `hdfs dfs -getfattr/-setfattr` aufrufen, Java suchen   |
| `store.py`       | SQLite-Schema, DB schreiben/öffnen, Backup-CSV                     |
| `output.py`      | Ausgabe als Tabelle, CSV oder JSON                                 |
| `common.py`      | Fehlerklassen, Meldungen auf stderr, natürliche Sortierung         |

`acl.py`, `changes.py` und `fsimage.py` arbeiten nur auf Strings bzw.
Streams und lassen sich ohne HDFS testen.

Unit-Tests (`tests/`, nur `unittest` aus der Standardbibliothek, kein HDFS
nötig; die Kommandos laufen dort gegen einen Fake-Client):

```bash
uv run python -m unittest discover -s tests
PYTHONPATH=src python3 -m unittest discover -s tests   # ohne uv
uv run pyright
```

`test_commands.py` prüft außerdem, dass `export` aus `testdata/acls.csv`
genau `testdata/expected.csv` ergibt.

Für den HDFS-Client gelten dieselben Variablen wie bei `test.sh`
(`HADOOP_REPO`, `HADOOP_VERSION`, `HADOOP_HOME`, `HADOOP_CONF_DIR`,
`HDFS_JAVA_HOME`), siehe unten. `HADOOP_VERSION` gibt es auch als Option
`--hadoop-version`. Fehlt `$HADOOP_CONF_DIR/core-site.xml`, bricht das Tool
ab: ohne die Datei liest der Client das lokale Dateisystem (`file:///`).
Wer `HADOOP_HOME` setzt, muss `HADOOP_CONF_DIR` meist ebenfalls setzen, der
Default bleibt `$HADOOP_REPO/clientconfig`.

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
- `oiv` schreibt das XML in eine Named Pipe, nicht nach stdout. Logzeilen
  von Hadoop (z. B. wenn `log4j.properties` in `$HADOOP_CONF_DIR` fehlt) oder
  GC-Logging landen sonst mitten im XML. Abschnitte nach
  `<INodeDirectorySection>` liest das Tool nicht. `oiv -p DetectCorruption`
  prüft nur den Namensraum, nicht das XML. Meldet das Tool trotzdem
  **Ausgabe nicht lesbar** (`mismatched tag`, `unerwartetes Element`), zeigt
  es die Zeilen um die Stelle; nachstellen mit
  `hdfs oiv -p XML -i fsimage_<txid> -o out.xml`.

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
| `node`      | jeder Pfad mit Roh-ACL und Warnungen                            |
| `entry`     | Einträge der ACLs (client/user/group, ohne LDAP) inkl. Besitzer |
| `rights`    | Recht je `@clientid` und ACL: Einträge verodert, Besitzer mit `19`; nichts Geerbtes |
| `meta`      | Basisordner, Attribut, Zeitpunkt und Quelle (fsimage/live)      |

Die DB wird bei jedem `collect` komplett neu geschrieben, Abfragen gehen auch
direkt mit `sqlite3 hdfs-acl.db`. ACLs mit Formatfehlern (kein `|`,
Eintrag ohne `=<zahl>`, Gruppe ohne Recht, Maske 0 oder größer als
2147483647) meldet `collect` als Warnung. Ein leerer Besitzer ist kein
Fehler. Eine DB aus einer älteren Version (Tabelle `effective`) muss neu
eingesammelt werden.

### Suchen

```bash
hdfs-acl search @client3                 # alle ACLs, die @client3 nennen, mit Roh-ACL
hdfs-acl search client3                 # dasselbe, @ wird ergänzt
hdfs-acl search '@*' --format csv       # alle ACL-Einträge pro clientid
hdfs-acl search '@client*' --perm 3     # Glob, nur genau Maske 3
hdfs-acl search '@*' --has-bits 16      # alle, deren Maske Bit 16 enthält
hdfs-acl search '@*' --has-bits ADMIN   # alle mit allen Bits von 19
hdfs-acl search '@*' --perm read_write  # Namen aus der Rechte-Tabelle
hdfs-acl search @client3 --format csv   # oder json
hdfs-acl clients                         # alle clientids mit Anzahl ACLs je Maske ("3:40 19:2")
hdfs-acl count                           # Anzahl Pfade, ACLs, Einträge, Rechte, clientids in der DB
hdfs-acl show /fs/account3/container10   # ACL eines Pfads und die Rechte daraus
hdfs-acl export -o export.csv            # Format wie testdata/expected.csv
```

Ausgabe sortiert nach clientid, dann Pfad, mit der Roh-ACL des Pfads in der
letzten Spalte. Steht eine clientid mehrfach in einer ACL oder ist sie
zusätzlich Besitzer, gibt es eine Zeile mit allen Bits. Das `@` an der clientid kann
man weglassen. Suchen arbeiten auf dem Stand
des letzten `collect`, Zeitpunkt und Quelle stehen auf stderr.

### Ändern

`add`, `replace`, `remove` und `restore` laufen so ab:

1. Einlesen wie bei `collect` (fsimage, mit `--live` direkt) und planen.
2. Die Pfade, die der Plan ändern würde, **live nachlesen** und auf deren
   aktuellen Werten neu planen. Das fsimage kann eine Stunde alt sein:
   Pfade, die sich seitdem geändert haben, werden so richtig behandelt,
   gelöschte übersprungen. Jeder andere Lesefehler bricht ab (keine
   Berechtigung, Kerberos-Ticket abgelaufen, NameNode nicht erreichbar),
   damit ein nicht lesbarer Pfad nicht still aus dem Plan fällt.
   `restore` liest die Pfade aus dem Backup immer
   live, das fsimage kennt die zurückzudrehenden Änderungen meist noch nicht.
3. Für jeden betroffenen Pfad alt/neu zeigen und **erst mit `--apply`**
   schreiben. Vorher entsteht neben der DB ein Backup
   `hdfs-acl-backup-<zeit>-<aktion>.csv` (`path;old;new`).
4. Die geänderten Pfade erneut lesen, jeden Wert prüfen und die DB mit dem
   Ergebnis fortschreiben (Quelle z. B. `fsimage_… + replace-client3-newapp`).

Statt `--apply` erzeugt `--generate DATEI` ein Shell-Skript mit einem
`hadoop fs -setfattr` je Pfad (bzw. `-setfattr -x` zum Entfernen), z. B. für
ein Change-Ticket oder wenn ein anderer Benutzer schreiben muss. HDFS bleibt
dabei unverändert, die Schritte 1–3 laufen aber genauso, und das Backup wird
ebenfalls geschrieben, damit `restore` nach dem Skript funktioniert. Im
Skript steht über jedem Aufruf der alte Wert als Kommentar. Der Client lässt
sich mit `HADOOP=/pfad/zu/bin/hadoop ./skript.sh` wählen. Fehlgeschlagene
Aufrufe werden gezählt, das Skript läuft weiter und endet dann mit Exit-Code 1.
Das Nachprüfen aus Schritt 4 entfällt: Wiederholt man das Kommando danach
im Dry-Run, muss es „Keine Änderungen nötig.“ melden.

Grenze des fsimage: Ein Pfad, der erst **nach** dem Checkpoint die clientid
bekommen hat, fehlt im Plan. Nach dem nächsten Checkpoint erneut ausführen
(alle Kommandos sind wiederholbar) oder bei wenigen Pfaden `--live` nehmen.

Geändert werden nur Pfade mit eigener ACL. Container ohne ACL ändern sich
über ihren Account automatisch mit. Einträge für User, Gruppen und andere
Client-IDs bleiben Wort für Wort erhalten.

`add` und `replace` übertragen nur **explizite** Rechte: was ALT in einer
ACL selbst stehen hat, als Besitzer oder Eintrag. Geerbte Rechte lösen nichts
aus, ACLs ohne ALT bleiben unverändert.

`add` und `replace` ändern per Default nur **Container**, also alles ab der
zweiten Ebene unter `--base-dir`. Die Accounts (erste Ebene) bleiben
unverändert, auch wenn ALT dort steht. Mit `--accounts` werden sie
mitmigriert. Ohne `--accounts` bekommt NEU in Containern ohne eigene ACL
nichts, denn sie erben vom Account, und dort steht weiter nur ALT. `remove`
und `restore` kennen die Option nicht: `remove` ändert immer alle Ebenen,
`restore` genau die Pfade aus dem Backup.

```bash
# Migration: @newapp bekommt überall dieselben Rechte wie @client3
hdfs-acl add @client3 @newapp            # Plan ansehen
hdfs-acl add @client3 @newapp --apply
hdfs-acl add @client3 @newapp --generate add-newapp.sh   # oder: Skript statt schreiben
hdfs-acl add @client3 @newapp --accounts --apply         # Accounts mitmigrieren
```

- Ist `@client3` Besitzer, bleibt es Besitzer, denn eine ACL hat nur
  einen. `@newapp` bekommt dort einen Eintrag mit dem Recht des Besitzers,
  also `@newapp=19`. Aus `@client3|@test1=1,@test2=2` wird
  `@client3|@test1=1,@test2=2,@newapp=19`. Erst `replace` macht `@newapp`
  zum Besitzer.
- Hat `@newapp` schon alle Bits von `@client3`, passiert nichts. Fehlen
  welche, werden sie im vorhandenen Eintrag ergänzt (`@newapp=4` und
  `@client3=1` ergibt `@newapp=5`). Entfernt wird nie ein Bit.
- Schränkt eine Container-ACL `@client3` ein (`@client3=1`), bekommt
  `@newapp` dort denselben Eintrag. Container, die `@client3` nicht nennen,
  bleiben unverändert.
- Mehrfach ausführen ist unschädlich.

```bash
# ALT direkt durch NEU ersetzen, auch als Besitzer
hdfs-acl replace @client3 @newapp
hdfs-acl replace @client3 @newapp --apply
```

- Besitzer `@client3` wird Besitzer `@newapp`, Einträge `@client3=x` werden
  an derselben Stelle zu `@newapp=x`.
- Hatte `@newapp` schon einen Eintrag, bleibt einer mit den Bits beider,
  auch neben dem Besitzer `@newapp`.
- `@newapp` hat danach in jeder ACL genau die Rechte, die `@client3` dort
  hatte (plus seine eigenen), `@client3` steht in keiner ACL mehr.

```bash
# Client-ID überall entfernen
hdfs-acl remove @client3
hdfs-acl remove @client3 --apply
hdfs-acl remove @client3 --new-owner '.cn=admins,ou=groups,dc=firma' --apply
```

Pfade, an denen die clientid **Besitzer** ist, werden ohne `--new-owner`
übersprungen und aufgelistet (Exit-Code 2), weil eine ACL einen Besitzer
braucht. Bleibt nach dem Entfernen nur der Besitzer übrig, wird die ACL zu
`<besitzer>|`. Mit `--new-owner` werden
mehrere Einträge des neuen Besitzers zu einem zusammengefasst.

`--new-owner` nimmt eine clientid oder eine Gruppe (`.` und voller DN ohne
Recht, z. B. `.cn=admins,ou=groups,dc=firma`). Ohne Präfix
wird wie bei allen clientids ein `@` ergänzt: `--new-owner newapp` meint
`@newapp`, nicht den User `newapp`. Ein User als neuer Besitzer geht nicht,
`#gruppe` wird mit Hinweis abgelehnt. Die clientid selbst als neuer
Besitzer (`remove @a --new-owner @a`) wird abgelehnt, ihre Bits über `19`
gingen sonst verloren.

Eine clientid wird in zwei Phasen migriert. In Phase 1 greifen beide, und
die Anwendung kann umgestellt werden. Phase 2 ersetzt die alte clientid
vollständig:

```bash
hdfs-acl add @client3 @newapp --apply      # Phase 1: @newapp=19, wo @client3 Besitzer bleibt
# ... Anwendung auf @newapp umstellen ...
hdfs-acl replace @client3 @newapp --apply  # Phase 2: @newapp ersetzt @client3, auch als Besitzer
```

Statt `replace` geht in Phase 2 auch `remove @client3 --new-owner @newapp`.
Das Ergebnis hat dieselben Rechte, nur die Reihenfolge der Einträge kann
abweichen. Das gilt nur, wenn beide Phasen mit `--accounts` liefen: `remove`
nimmt `@client3` auch aus den Accounts.

Wo `@client3` Besitzer war, bleibt nach Phase 2 der Eintrag aus Phase 1
neben dem neuen Besitzer stehen: Aus `@client3|@test1=1,@test2=2` wird
erst `@client3|@test1=1,@test2=2,@newapp=19`, dann
`@newapp|@test1=1,@test2=2,@newapp=19`. `replace` kann den Eintrag aus
Phase 1 nicht von einem bewusst gesetzten unterscheiden und lässt ihn
deshalb stehen. Ein direktes `replace @client3 @newapp` ohne Phase 1 setzt
keinen solchen Eintrag.

```bash
# zurückdrehen
hdfs-acl restore hdfs-acl-backup-20261015-152310-add-newapp.csv --apply
```

`restore` setzt nur Pfade zurück, deren aktueller Wert noch dem `new` aus
dem Backup entspricht. Pfade, die seitdem jemand anders geändert hat, werden
übersprungen und gemeldet (Exit-Code 2). `--force` setzt sie trotzdem zurück.
Pfade, die schon den alten Wert haben, lässt `restore` stillschweigend aus.
Ein zweites `restore` desselben Backups ist deshalb unschädlich, ebenso ein
`restore` nach einem Teilfehler beim Schreiben.

Gemeinsame Optionen: `--db` (`$HDFS_ACL_DB`, Default `./hdfs-acl.db`),
`--base-dir` (`$BASE_DIR`), `--attr` (`$ACL_ATTR`), `--depth` (`$DEPTH`,
Default 2), `--fsimage`
(`$HDFS_FSIMAGE`), `--live`, `--parallel` (`$PARALLEL`, Default 8),
`--hadoop-version` (`$HADOOP_VERSION`, Default 3.4.2).

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
enthalten. Pfade ohne ACL kosten beim Ändern nichts.

### Gegen die Testdaten prüfen

Die Testdaten haben drei Ebenen (Unterordner `sub`), deshalb `DEPTH=3`:

```bash
./test.sh --clean
export DEPTH=3
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

Legt `/fs/account1..10` mit je `container1..52` an, unter drei Containern
je Account einen Unterordner `sub`, dazu `/fs/invalid` mit fehlerhaften
ACLs (569 Ordner). Setzt an 456 davon `user.acl` und vergleicht das Ergebnis
per `getfattr -R` mit den erwarteten Werten. `hdfs-acl` läuft in den Tests
mit `DEPTH=3`.

```bash
./test.sh --clean        # /fs löschen, neu anlegen, ACLs setzen, prüfen (~3 min)
./test.sh                # anlegen/überschreiben ohne vorher zu löschen
./test.sh --verify-only  # nur HDFS gegen testdata/acls.csv prüfen
./test.sh --dry-run      # nur testdata/*.csv erzeugen, HDFS nicht anfassen
./test.sh --docker       # eigener NameNode per Docker, dazu hdfs-acl-Tests (~4 min)
```

`--docker` braucht keinen Testcluster. Das Skript startet mit `docker run`
einen frischen NameNode aus `apache/hadoop:$HADOOP_VERSION` (`DOCKER_IMAGE`),
ohne DataNodes, weil es nur um Metadaten geht. Die Konfiguration liegt in
`namenode/` und gilt für den NameNode im Container und den Client auf dem Host
(`localhost:19000`, Web-UI `localhost:19870`), `HADOOP_CONF_DIR` wird dabei
ignoriert. Nach dem Anlegen und Prüfen der Testdaten testet das Skript
`hdfs-acl` durchgängig: `collect` aus dem fsimage und `--live` gegen
`expected.csv`, Abbruch bei Lesefehlern beim Nachlesen und ohne
`core-site.xml`, `--new-owner`, `remove`/`add` mit jeweils zwei `restore`.
Am Ende muss HDFS wieder genau `acls.csv` entsprechen. Container und
Arbeitsordner werden danach gelöscht, mit `--keep` bleiben sie stehen.

Basisordner und Attributname sind per Umgebungsvariable einstellbar:

```bash
BASE_DIR=/acltest ACL_ATTR=user.rights ./test.sh --clean
```

| Variable          | Default                                         |
|-------------------|-------------------------------------------------|
| `BASE_DIR`        | `/fs`                                           |
| `ACL_ATTR`        | `user.acl`                                      |
| `PARALLEL`        | `8` (parallele `setfattr`-Aufrufe)              |
| `OUT_DIR`         | `hdfs/testdata`                                 |
| `HADOOP_REPO`     | `~/git/hadoop`                                  |
| `HADOOP_VERSION`  | `3.4.2`                                         |
| `HADOOP_HOME`     | `$HADOOP_REPO/downloads/hadoop-$HADOOP_VERSION` |
| `HADOOP_CONF_DIR` | `$HADOOP_REPO/clientconfig`                     |
| `HDFS_JAVA_HOME`  | gesucht, siehe unten                            |
| `DOCKER_IMAGE`    | `apache/hadoop:$HADOOP_VERSION` (nur `--docker`) |

Java-Suche, Hadoop-Client und Start des Docker-NameNodes stehen in
`lib.sh`, das `test.sh` und `migrate.sh` einbinden.

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
- `testdata/expected.csv` – `path;clientid;perm`: Recht je `@clientid`
  aus der ACL des Pfads, nichts Geerbtes. Pfade ohne ACL oder ohne
  `@clientid` tauchen nicht auf.

Die Dateien hängen nur von `BASE_DIR` ab und sind bei gleichen Variablen
immer identisch.

### Schema

`n` = Account-Nummer, `cl(k)` = `@client<k>`, zyklisch über 1..10.
`OPS`, `ADMINS`, `READERS` = Gruppen `.cn=<name>,ou=groups,dc=firma`.

| Pfad                 | ACL                                               | Testet                               |
|----------------------|---------------------------------------------------|--------------------------------------|
| `account<n>`         | `cl(n)\|cl(n+1)=3,svc_backup=1,cl(n+2)=1,OPS=3`   | Grundfall, User/Gruppe gemischt      |
| `account3`           | Besitzer `hans`                                   | Besitzer ist User                    |
| `account7`           | Besitzer `ADMINS`                                 | Besitzer ist Gruppe                  |
| `account10`          | keine                                             | Account ohne ACL                     |
| `container<c>`, c%5=0 | keine                                            | Container ohne ACL                   |
| c%5=1                | `cl(n)\|cl(n+3)=3,dev_user=3,cl(n+4)=1`           | eigener Besitzer = Account-Besitzer  |
| c%5=2                | `cl(n+5)\|READERS=1,cl(n+5)=1,cl(n)=1,cl(n+1)=1`  | Besitzer zusätzlich `=1` → bleibt 19; `cl(n+1)` schränkt Account ein |
| c%5=3                | `maria\|cl(n+6)=3,cl(n+7)=1,OPS=1`                | Clients nur über Einträge            |
| c%5=4                | `ADMINS\|peter=3,OPS=1`                           | keine Client-ID → keine Rechte       |
| `container48`        | `cl(n+9)\|cl(n+1)=5,OPS=16,cl(n+1)=16,cl(n+9)=8,cn=doc,ou=hell,dc=firma=3,cn=testgroup` | Masken: ODER (21), Besitzer 19\|8=27; LDAP ohne `.` ignoriert |
| `container49`        | `cl(n+8)\|`                                       | nur Besitzer, keine Einträge         |
| `container50`        | `\|cl(n+3)=1,cl(n+2)=3`                           | leerer Besitzer: nur Einträge        |
| `container51`        | `cl(n+1)\|cl(n)=1,OPS=1`                          | Migration `cl(n)` → `cl(n+1)`: NEU schon Besitzer |
| `container52`        | `cl(n)\|cl(n+1)=1`                                | Migration: ALT Besitzer, NEU schon Eintrag |
| `container1/sub`     | keine                                             | 3. Ebene ohne ACL                    |
| `container5/sub`     | keine                                             | 3. Ebene ohne ACL                    |
| `container10/sub`    | `cl(n+2)\|cl(n+3)=1,OPS=3`                        | ACL in der 3. Ebene                  |

Unter `/fs/invalid` (selbst ohne ACL) liegen fehlerhafte ACLs. `collect`
meldet sie als Warnung. Ungültige Teile geben keine Rechte, der Rest der
ACL gilt. Die erwarteten Rechte stehen in `test.sh` von Hand (`acl_invalid`).

| Pfad                 | ACL                                               | Rechte                               |
|----------------------|---------------------------------------------------|--------------------------------------|
| `nopipe`             | `@inv1=3`                                         | keine: Besitzer `@inv1=3` ist keine clientid |
| `noowner`            | `\|@inv2=3,OPS=1`                                 | `@inv2=3` (leerer Besitzer)          |
| `noowner/sub`        | keine                                             | keine (keine ACL)                    |
| `badentry`           | `@inv3\|@inv4,@inv5=abc,=2,@inv6=1`               | `@inv3=19`, `@inv6=1`                |
| `range`              | `@inv3\|@inv4=0,@inv5=2147483648,@inv6=2147483647` | `@inv3=19`, `@inv6=2147483647`      |
| `spaces`             | `@inv3\| @inv4=1 , @inv5 = 1`                     | `@inv3=19`, `@inv4=1` (Leerzeichen um `=` ungültig) |
| `ldap`               | `@inv3\|uid=hans,CN=Doc,O=firma=1,cn=3,@inv4=1`   | `@inv3=19`, `@inv4=1` (`cn=3` ist User `cn`) |
| `group`              | `@inv3\|.cn=ops,ou=groups,@inv4=1`                | `@inv3=19`, `@inv4=1` (Gruppe ohne Recht ungültig) |

## migrate.sh – Migration durchspielen

Spielt eine Migration ALT → NEU auf einem frischen Docker-NameNode durch und
prüft jede Phase. Einen Testcluster braucht das Skript nicht.

Eine clientid lässt sich nur in zwei Phasen migrieren. Erst greifen beide,
damit die Anwendung umgestellt werden kann. Eine ACL hat aber nur einen
Besitzer: In Phase 1 bleibt ALT Besitzer, und NEU bekommt dort einen Eintrag
mit dem Recht des Besitzers (`19`). Erst in Phase 2 ersetzt NEU die alte
clientid vollständig, auch als Besitzer.

```bash
./migrate.sh                     # @client3 -> @newapp in zwei Phasen (~4,5 min)
./migrate.sh @client3 @client4   # NEU hat schon Rechte: Bits beider
./migrate.sh --direct            # nur replace, ohne Phase 1
./migrate.sh --accounts          # Accounts mitmigrieren (add/replace --accounts)
./migrate.sh --keep              # Container und Arbeitsordner behalten
```

Ablauf:

1. NameNode starten wie bei `test.sh --docker`, Testdaten mit `test.sh`
   laden. Die Vergleichsdateien landen im Arbeitsordner, `testdata/` im
   Repo bleibt unberührt.
2. fsimage erzeugen und holen. `hdfs-acl` plant damit wie im echten Betrieb
   aus dem fsimage und liest nur die betroffenen Pfade live nach. Phase 2
   plant auf demselben fsimage von vor Phase 1, das Nachlesen muss die
   Einträge aus Phase 1 also bemerken.
3. **Phase 1**, `add ALT NEU` (erst Dry-Run, dann `--apply`), danach prüfen
   (`migrate_check.py --stage added`):
   - ALT hat genau die Rechte wie vorher. NEU hat in jeder ACL, die ALT
     nennt, die Bits von ALT, mit eigenen verodert, wo ALT Besitzer ist `19`.
   - Der Besitzer bleibt: War ALT Besitzer, ist es weiter ALT.
   - Außer den Einträgen von NEU ist jede ACL Wort für Wort gleich, NEU
     steht höchstens einmal da.
4. **Phase 2**, `replace ALT NEU`, danach prüfen (`--stage replaced`):
   - Die Rechte je ACL entsprechen genau `expected.csv`, nur
     heißt ALT jetzt NEU. Hatte NEU an einem Pfad schon Rechte, gelten die
     Bits beider.
   - ALT steht in keiner ACL mehr, weder als Besitzer noch als Eintrag.
   - War ALT Besitzer, ist jetzt NEU Besitzer, sonst bleibt der Besitzer.
     NEU steht höchstens einmal als Eintrag da.
   - ACLs ohne ALT sind Wort für Wort unverändert.
   Ohne `--accounts` gelten Phase 1 und 2 nur für Container, die Accounts
   müssen in beiden Phasen Wort für Wort unverändert bleiben
   (`migrate_check.py` ohne `--accounts`).
5. Beide Backups rückwärts mit `restore` zurückspielen. Danach müssen Rechte
   und ACLs genau den Testdaten entsprechen (`--stage restored`).

Bei `@client3` ist ALT unter anderem „nur Besitzer“ (`@client3|`),
„Besitzer zusätzlich mit `=1` gelistet“ und „Besitzer mit `=8`“ (NEU
bekommt dort in Phase 1 `=27`). Die Rechte allein reichen für
die Prüfung nicht: `hans|...,@newapp=19` statt `@newapp|...` gäbe
`@newapp` dieselben Rechte, deshalb wird der Besitzer eigens geprüft.
Mit `./migrate.sh @client3 @client4` kommen über `container51/52` beide
Überschneidungen vor: NEU ist schon Besitzer und ALT nur Eintrag, und
umgekehrt.

Exit-Code 0, wenn alles stimmt. Es gelten dieselben Umgebungsvariablen wie
bei `test.sh`, außer `OUT_DIR` und `HADOOP_CONF_DIR`.
