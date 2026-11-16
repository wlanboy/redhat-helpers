# add: Anwendungsfälle und Sonderfälle

`add QUELLE NEU` ist Phase 1 einer Migration. NEU bekommt in jeder ACL, die
QUELLE selbst nennt, mindestens die Rechte von QUELLE. QUELLE bleibt
unverändert, beide clientids greifen nebeneinander. Erst `replace` oder
`remove` nimmt QUELLE heraus (siehe [hdfs.md](hdfs.md#ändern)).

```bash
uv run main.py add @alt @neu                     # Dry-Run, nur Container
uv run main.py add @alt @neu --apply             # schreiben
uv run main.py add @alt @neu --accounts --apply  # auch die Accounts
```

## Rechte in diesem Dokument

Welche Maske welches Recht bedeutet, steht in `permissions.json`
([Rechte-Tabelle](hdfs.md#rechte-tabelle)). Der Text nennt Rechte deshalb nur
beim Namen:

- `owner` ist das Recht, das der Besitzer einer ACL immer hat.
- `admin` ist das Verwaltungsrecht, das `add` NEU gibt, wo QUELLE Besitzer
  ist. In der mitgelieferten Tabelle sind `owner` und `admin` dasselbe Recht,
  nämlich `ADMIN`.
- `READ`, `READ_WRITE` und `ADMIN` sind die Namen aus `permissions`.

Die Beispiel-ACLs enthalten Masken, weil das ACL-Format sie verlangt. Sie
sind mit der mitgelieferten `permissions.json` gerechnet. Mit einer anderen
Tabelle ändern sich die Zahlen, die Regeln bleiben gleich.

## Zwei Modi

| Modus | Aufruf | geänderte Pfade |
|---|---|---|
| nur Container (Default) | `add @alt @neu` | alle Ebenen ab der zweiten unter `--base-dir` |
| mit Accounts | `add @alt @neu --accounts` | zusätzlich die erste Ebene, die Accounts |

`--account NAME` beschränkt beide Modi auf einzelne Accounts. Die Option
lässt sich mehrfach angeben und nimmt einen Namen (`account3`) oder einen
Pfad (`/fs/account3`). Der Account selbst wird auch dann nur mit `--accounts`
geändert.

Im Default-Modus bekommt NEU nur dort Rechte, wo eine Container-ACL QUELLE
nennt. NEU fehlt danach an diesen Stellen:

- in den Accounts, auch wenn QUELLE dort Besitzer ist oder einen Eintrag hat,
- in Containern ohne eigene ACL, weil sie vom Account erben und dort weiter
  nur QUELLE steht,
- in Containern mit eigener ACL, die QUELLE nicht nennt und in denen QUELLE
  ihr Recht aus dem Account bezieht.

Wer die Anwendung vollständig auf NEU umstellen will, braucht deshalb
`--accounts`. Der Default-Modus passt, wenn die Accounts getrennt oder von
Hand migriert werden.

## Ablauf

1. Das Tool liest den Stand ein, per Default aus dem neuesten fsimage neben
   der DB, mit `--live` direkt aus HDFS. Dabei schreibt es die DB neu.
2. Es wählt die Pfade des Modus aus und plant je ACL nach der Regel unten.
3. Es liest die Pfade aus dem Plan live nach und plant auf den aktuellen
   Werten ein zweites Mal. Gelöschte Pfade fallen aus dem Plan. Jeder andere
   Lesefehler bricht den Lauf ab.
4. Es zeigt je Pfad die alte und die neue ACL.
5. Mit `--apply` schreibt es erst das Backup, dann die ACLs. Danach liest es
   jeden geänderten Pfad zurück, vergleicht ihn mit dem Plan und schreibt die
   DB fort. Mit `--generate DATEI` schreibt es das Backup und ein Shell-Skript,
   HDFS und DB bleiben unverändert. Ohne beide Optionen endet der Lauf als
   Dry-Run.

## Regel je ACL

Das Tool betrachtet jede ACL für sich und rechnet nichts Geerbtes ein.

1. Es bestimmt das Recht von QUELLE in dieser ACL. Dazu verodert es die Bits
   aller gültigen Einträge von QUELLE. Ist QUELLE Besitzer, kommt `admin`
   dazu.
2. Nennt die ACL QUELLE weder als Besitzer noch mit einem gültigen Eintrag,
   bleibt sie unverändert.
3. Es bestimmt das Recht von NEU auf dieselbe Weise. Ist NEU Besitzer, zählt
   `owner` mit.
4. Hat NEU schon alle Bits von QUELLE, bleibt die ACL unverändert.
5. Sonst schreibt das Tool genau einen Eintrag für NEU. Er enthält die Bits,
   die NEU schon als Eintrag hatte, und die Bits von QUELLE. Stand NEU schon
   in der ACL, bleibt der Eintrag an der ersten Stelle, sonst hängt das Tool
   ihn hinten an.

`add` entfernt kein Bit, ändert keinen Besitzer und lässt die Einträge von
QUELLE, von Usern, Gruppen und anderen clientids Wort für Wort stehen. In
einer geänderten ACL fallen nur Leerzeichen um Besitzer und Einträge und
leere Einträge weg.

## Sonderfälle je ACL

### QUELLE ist Besitzer

Eine ACL hat nur einen Besitzer. QUELLE bleibt es, und NEU bekommt
stattdessen einen Eintrag mit dem Verwaltungsrecht `admin`, mit der
mitgelieferten Tabelle also `ADMIN`. Ist QUELLE zusätzlich gelistet, kommen
die Bits dieser Einträge dazu. Mit dem Eintrag kann NEU die ACL verwalten wie
der Besitzer. Erst `replace` macht NEU zum Besitzer.

Das Tool addiert dabei keine Rechte, es verodert Bits. Technisch verodert es
`owner` und `admin`. Beide sind in der mitgelieferten Tabelle gleich, das
Ergebnis ist `ADMIN`. Nur wenn eine eigene Tabelle `owner` und `admin`
verschieden setzt, enthält der Eintrag die Bits beider.

| Fall | vorher | nachher | Recht von NEU |
|---|---|---|---|
| QUELLE ist Besitzer | `@alt\|@t1=1,@t2=2` | `@alt\|@t1=1,@t2=2,@neu=19` | `admin` |
| QUELLE ist nur Besitzer, keine Einträge | `@alt\|` | `@alt\|@neu=19` | `admin` |
| Besitzer zusätzlich mit `READ` gelistet | `@alt\|@alt=1,@t=1` | `@alt\|@alt=1,@t=1,@neu=19` | `admin`, `READ` steckt darin |
| Besitzer zusätzlich mit einem Bit außerhalb von `admin` | `@alt\|@alt=8` | `@alt\|@alt=8,@neu=27` | `admin` und dieses Bit |
| NEU hat dort schon weniger | `@alt\|@neu=1,@x=3` | `@alt\|@neu=19,@x=3` | ergänzt an derselben Stelle |
| NEU hat dort schon alles | `@alt\|@neu=19` | unverändert | |
| ACL ohne `\|`, nur der Besitzer | `@alt` | `@alt\|@neu=19` | das fehlende `\|` kommt dazu |

### QUELLE hat einen Eintrag

NEU bekommt denselben Eintrag ein zweites Mal unter eigenem Namen. Der
Eintrag von QUELLE bleibt stehen.

| Fall | vorher | nachher |
|---|---|---|
| ein Eintrag | `hans\|@alt=3,.cn=ops,ou=g,dc=f=3` | `hans\|@alt=3,.cn=ops,ou=g,dc=f=3,@neu=3` |
| QUELLE steht mehrfach | `hans\|@alt=5,@x=1,@alt=16` | `hans\|@alt=5,@x=1,@alt=16,@neu=21` |
| leerer Besitzer | `\|@alt=1` | `\|@alt=1,@neu=1` |
| Container schränkt QUELLE ein | `@y\|@alt=1` | `@y\|@alt=1,@neu=1` |

Steht QUELLE mehrfach in der ACL, bekommt NEU einen einzigen Eintrag mit
allen Bits. Die doppelten Einträge von QUELLE fasst `add` nicht zusammen.

### NEU steht schon in der ACL

| Fall | vorher | nachher |
|---|---|---|
| NEU hat schon alle Bits | `@o\|@alt=1,@neu=3` | unverändert |
| NEU fehlen Bits | `@o\|@neu=4,@alt=1` | `@o\|@neu=5,@alt=1` |
| NEU steht mehrfach | `hans\|@neu=4,@alt=1,@neu=8` | `hans\|@neu=13,@alt=1` |
| NEU ist Besitzer, QUELLE hat nur Bits aus `owner` | `@neu\|@alt=1` | unverändert |
| NEU ist Besitzer, QUELLE hat ein Bit außerhalb von `owner` | `@neu\|@alt=8` | `@neu\|@alt=8,@neu=8` |
| NEU ist Besitzer und gelistet | `@neu\|@neu=4,@alt=8` | `@neu\|@neu=12,@alt=8` |

Mehrere Einträge von NEU fasst `add` zu einem zusammen, sobald es die ACL
ohnehin ändert. Ist NEU Besitzer, schreibt `add` trotzdem einen Eintrag, wenn
QUELLE Bits hat, die `owner` nicht enthält.

### Ungültige Einträge

Ein ungültiger Eintrag gibt keine Rechte. `collect` meldet ihn als Warnung,
`add` meldet ihn nicht.

| Fall | vorher | nachher |
|---|---|---|
| QUELLE mit Maske 0 | `hans\|@alt=0` | unverändert, QUELLE gilt als nicht genannt |
| QUELLE ohne Recht | `hans\|@alt,@x=1` | unverändert |
| QUELLE mit Leerzeichen um `=`, daneben gültig | `hans\| @alt = 1,@alt=2` | `hans\|@alt = 1,@alt=2,@neu=2` |
| QUELLE ist Besitzer, ihr Eintrag ist ungültig | `@alt\|@alt=abc` | `@alt\|@alt=abc,@neu=19` |
| ACL ohne `\|`, mit Recht | `@alt=3` | unverändert, `@alt=3` ist kein gültiger Besitzer |
| NEU ohne Recht | `hans\|@neu,@alt=1` | `hans\|@neu=1,@alt=1` |
| NEU mit Maske 0 | `hans\|@neu=0,@alt=1` | `hans\|@neu=1,@alt=1` |
| NEU mit Maske über 2147483647 | `hans\|@neu=2147483648,@alt=1` | `hans\|@neu=1,@alt=1` |
| NEU mit Text als Recht | `hans\|@neu=abc,@alt=1` | `hans\|@neu=abc,@alt=1,@neu=1` |

Einen ungültigen Eintrag von NEU ersetzt `add` durch den gültigen, wenn er
die Form `@neu` oder `@neu=<zahl>` hat. `@neu=abc` erkennt das Tool nicht als
Eintrag von NEU und lässt ihn stehen.

### Einträge, die add nicht anfasst

| Fall | vorher | nachher |
|---|---|---|
| Leerzeichen und leere Einträge | `@alt \| @b=1, ,@c=2` | `@alt\|@b=1,@c=2,@neu=19` |
| LDAP-Teile ohne `.` | `@alt\|cn=doc,ou=hell,dc=firma=3,cn=testgroup` | `@alt\|cn=doc,ou=hell,dc=firma=3,cn=testgroup,@neu=19` |
| Gruppe ohne Recht am Ende | `@alt\|.cn=ops,ou=groups` | `@alt\|.cn=ops,ou=groups,@neu=19` |
| User mit demselben Namen | `alt\|alt=3` | unverändert, `alt` ist ein User |
| leeres Attribut | (leer) | unverändert |
| Pfad ohne ACL | (kein Attribut) | unverändert |

## `--permission RECHT`

Mit `--permission` zählt in Schritt 1 der Regel das angegebene Recht statt
der Bits von QUELLE, auch wo QUELLE Besitzer ist. `admin` kommt dann nicht
dazu. Alles andere bleibt gleich. Bits, die NEU in der ACL schon
hat, bleiben und werden mit RECHT verodert. NEU hat danach also mindestens
RECHT, nicht in jedem Fall genau RECHT.

`RECHT` ist ein Name aus `permissions.json` (`READ_WRITE`), mehrere Namen
(`READ+READ_WRITE`) oder eine Maske. Die Beispiele nutzen
`--permission READ_WRITE`.

| Fall | vorher | nachher |
|---|---|---|
| QUELLE ist Besitzer | `@alt\|@t1=1` | `@alt\|@t1=1,@neu=3` |
| QUELLE hat mehr als RECHT | `hans\|@alt=5,@alt=16` | `hans\|@alt=5,@alt=16,@neu=3` |
| NEU hat andere Bits | `hans\|@neu=4,@alt=1` | `hans\|@neu=7,@alt=1` |
| NEU hat schon mehr | `@alt\|@neu=19` | unverändert |
| NEU ist Besitzer | `@neu\|@alt=8` | unverändert, `owner` enthält RECHT |

## Sonderfälle über mehrere Pfade

### Meldungen und Exit-Codes

| Situation | Ausgabe | Exit-Code |
|---|---|---|
| QUELLE steht in keiner ACL des Modus | „`@alt` steht in keiner ACL eines Containers (Accounts mit --accounts).“ | 0 |
| QUELLE steht da, NEU hat überall schon alles | „Keine Änderungen nötig.“ | 0 |
| Dry-Run mit Änderungen | Plan, „Dry-Run: nichts geschrieben.“ | 0 |
| `--apply`, alles geschrieben und geprüft | „OK: n Pfade geändert und geprüft.“ | 0 |
| `--apply`, ein Aufruf schlug fehl oder ein Wert stimmt nicht | je Pfad eine Fehlerzeile | 1 |
| QUELLE und NEU sind gleich | Fehler | 1 |
| ungültige clientid, unbekanntes Recht, unbekannter Account | Fehler | 1 |
| Lesefehler beim Nachlesen (Berechtigung, Kerberos, NameNode) | Fehler, nichts geschrieben | 1 |

Ein fehlendes `@` ergänzt das Tool, `add alt neu` meint `@alt` und `@neu`.

### Veralteter Stand

Das fsimage ist so alt wie der letzte Checkpoint. Das Nachlesen gleicht das
nur für die Pfade aus, die der erste Plan ändern würde.

| Was seit dem Checkpoint passiert ist | Verhalten |
|---|---|
| Pfad aus dem Plan wurde geändert | Das Tool plant auf dem aktuellen Wert. |
| Pfad aus dem Plan wurde gelöscht | Der Pfad fällt aus dem Plan. |
| Pfad hat QUELLE erst danach bekommen | Der Pfad fehlt im Plan. |
| Pfad brauchte laut fsimage keine Änderung, NEU hat dort inzwischen Bits verloren | Der Pfad fehlt im Plan. |
| Ordner wurde danach angelegt | Der Pfad fehlt im Plan. |

Die drei letzten Fälle behebt ein zweiter Lauf nach dem nächsten Checkpoint
oder ein Lauf mit `--live`. `add` lässt sich beliebig oft wiederholen.

### Tiefe

Das Tool liest nur die Ebenen bis `--depth` unter `--base-dir` (Default 2,
Accounts und Container). ACLs auf tieferen Ordnern sieht `add` erst mit
größerem `--depth`.

### Vererbung

Das Tool rechnet nicht nach, wie die Anwendung Account- und Container-ACL
zusammenführt ([hdfs.md](hdfs.md#nur-die-acl-einträge-selbst)). Daraus
folgen zwei Fälle, die der Plan nicht zeigt.

Im Default-Modus hat NEU in Containern, die QUELLE nicht nennen, kein Recht,
obwohl QUELLE dort über den Account eins hat (siehe [Zwei Modi](#zwei-modi)).

Mit `--accounts` kann NEU in einzelnen Containern mehr Rechte bekommen als
QUELLE. Ist QUELLE Besitzer eines Accounts, bekommt NEU dort einen Eintrag
mit `admin`. Ein Container mit eigenem Besitzer ersetzt den
Besitzer des Accounts, QUELLE hat dort also kein Besitzer-Recht. Den Eintrag
von NEU erbt der Container aber, solange seine ACL NEU nicht nennt. Diese
Aussage folgt aus den Regeln der Anwendung, wie hdfs.md sie beschreibt. Das
Tool prüft sie nicht. Die Container eines Accounts mit eigenem Besitzer
zeigt diese Abfrage auf der DB:

```bash
sqlite3 hdfs-acl.db "SELECT path, acl FROM node
  WHERE acl IS NOT NULL AND path LIKE '/fs/account1/%'
    AND acl NOT LIKE '@alt|%' AND acl NOT LIKE '|%'"
```

## Grenzen des Dry-Run

Der Dry-Run ist jeder Aufruf ohne `--apply` und ohne `--generate`. Eine
eigene Option `--dry-run` gibt es nicht.

- Der Dry-Run arbeitet nicht offline. Er braucht den Hadoop-Client, Java, die
  `core-site.xml` und ein fsimage, und er liest die betroffenen Pfade mit
  einem NameNode-RPC je Pfad live nach. Ohne HDFS plant nur
  `plan add` ([hdfs.md](hdfs.md#trockenlauf-mit-plan)).
- Er schreibt die lokale DB neu, mit dem Stand des fsimage. Die live
  nachgelesenen Werte kommen im Dry-Run nicht in die DB.
- Er schreibt kein Backup und keine Plan-Datei. Den Plan als CSV liefert
  `plan add`.
- Er zeigt den Stand zum Zeitpunkt des Laufs. `--apply` liest danach neu ein
  und plant neu. Ändert jemand HDFS dazwischen, weicht der geschriebene Plan
  vom gezeigten ab. Was wirklich geschrieben wurde, steht im Backup.
- Er hat dieselben blinden Stellen wie der echte Lauf (siehe
  [Veralteter Stand](#veralteter-stand) und [Tiefe](#tiefe)).
- Er prüft keine Schreibrechte. Ob `setfattr` an jedem Pfad gelingt, zeigt
  erst `--apply`, das jeden Pfad zurückliest.
- Er zeigt nur ACLs, die sich ändern. Pfade außerhalb des Modus, Pfade ohne
  ACL und die Folgen der Vererbung fehlen in der Ausgabe.
- Er meldet keine Formatfehler. Ein ungültiger Eintrag von QUELLE führt
  still dazu, dass die ACL unverändert bleibt. Die Warnungen zeigt `collect`.
- Er endet mit Exit-Code 0, auch wenn Änderungen anstehen. Ein Skript erkennt
  offene Änderungen nur an der Ausgabe.
