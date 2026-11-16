# replace: Anwendungsfälle und Sonderfälle

`replace ALT NEU` ist Phase 2 einer Migration. In jeder ACL, die ALT selbst
nennt, tritt NEU an die Stelle von ALT, als Besitzer und als Eintrag. ALT
steht danach in diesen ACLs nicht mehr. Phase 1 ist `add`
(siehe [add-usecases.md](add-usecases.md)). `replace` läuft auch ohne
Phase 1, dann gibt es keine Zeit, in der beide clientids greifen.

```bash
uv run main.py replace @alt @neu                              # Dry-Run: Plan zeigen, nur Container
uv run main.py replace @alt @neu --generate replace-neu.sh    # Normalfall: Backup und Shell-Skript schreiben
./replace-neu.sh                                              # das Skript ändert HDFS
uv run main.py replace @alt @neu --apply                      # oder: das Tool ändert HDFS selbst
uv run main.py replace @alt @neu --accounts --generate replace-neu.sh   # auch die Accounts
```

Der Normalfall ist `--generate`. Das Tool ändert HDFS dabei nicht, es
schreibt ein Skript mit allen Aufrufen. Das Skript lässt sich prüfen, an ein
Change-Ticket hängen und von dem Benutzer ausführen, der in HDFS schreiben
darf. `--apply` schreibt dieselben Werte direkt. Was in beiden Fällen wohin
geschrieben wird, steht unter [Was der Lauf schreibt](#was-der-lauf-schreibt).

## Unterschiede zu add

| | `add QUELLE NEU` | `replace ALT NEU` |
|---|---|---|
| alte clientid | bleibt Wort für Wort stehen | verschwindet aus jeder geänderten ACL |
| Besitzer | bleibt QUELLE | wird NEU |
| NEU, wo die alte clientid Besitzer ist | Eintrag mit `admin` | Besitzer, kein zusätzlicher Eintrag |
| `--permission` | ja | nein |
| zweiter Lauf danach | „Keine Änderungen nötig.“ | „`@alt` steht in keiner ACL …“ |
| ungültiger Eintrag der alten clientid | zählt nicht, ACL bleibt | wird mit umbenannt, siehe [Ungültige Einträge](#ungültige-einträge) |

## Rechte in diesem Dokument

Welche Maske welches Recht bedeutet, steht in `permissions.json`
([Rechte-Tabelle](hdfs.md#rechte-tabelle)). `owner` ist das Recht, das der
Besitzer einer ACL immer hat, in der mitgelieferten Tabelle `ADMIN`.

`replace` rechnet nicht mit Rechten. Es benennt den Besitzer und die Einträge
um und verodert Masken nur, wenn NEU danach mehrfach in der ACL stünde. Die
Rechte-Tabelle ändert das Ergebnis deshalb nicht. Die Masken in den
Beispielen stammen aus der mitgelieferten `permissions.json`.

## Zwei Modi

| Modus | Aufruf | geänderte Pfade |
|---|---|---|
| nur Container (Default) | `replace @alt @neu` | alle Ebenen ab der zweiten unter `--base-dir` |
| mit Accounts | `replace @alt @neu --accounts` | zusätzlich die erste Ebene, die Accounts |

`--account NAME` beschränkt beide Modi auf einzelne Accounts. Die Option
lässt sich mehrfach angeben und nimmt einen Namen (`account3`) oder einen
Pfad (`/fs/account3`). Der Account selbst wird auch dann nur mit `--accounts`
geändert.

Im Default-Modus bleibt ALT in den Accounts stehen, als Besitzer und als
Eintrag. ALT ist danach also nicht entfernt. Die Folgen für die Rechte stehen
unter [Vererbung](#vererbung).

`replace` läuft im selben Modus wie vorher `add`. Lief Phase 1 mit
`--accounts`, braucht Phase 2 die Option auch, sonst bleiben in den Accounts
beide clientids stehen.

## Ablauf

1. Das Tool liest den Stand ein, per Default aus dem neuesten fsimage neben
   der DB, mit `--live` direkt aus HDFS. Dabei schreibt es die DB neu.
2. Es wählt die Pfade des Modus aus und plant je ACL nach der Regel unten.
3. Es liest die Pfade aus dem Plan live nach und plant auf den aktuellen
   Werten ein zweites Mal. Gelöschte Pfade fallen aus dem Plan. Jeder andere
   Lesefehler bricht den Lauf ab.
4. Es zeigt je Pfad die alte und die neue ACL.
5. Ohne weitere Option endet der Lauf hier als Dry-Run. Mit
   `--generate DATEI` schreibt das Tool das Backup und ein Shell-Skript, mit
   `--apply` schreibt es das Backup und ändert HDFS selbst.

Schritt 3 ist nach Phase 1 nötig. Ist das fsimage älter als der Lauf von
`add`, fehlen darin die Einträge von NEU. Das Nachlesen holt sie, und der
zweite Plan behält sie.

## Was der Lauf schreibt

| Ziel | Dry-Run | `--generate DATEI` | `--apply` |
|---|---|---|---|
| lokale DB `hdfs-acl.db` | neu geschrieben, Stand des fsimage | neu geschrieben, Stand des fsimage | neu geschrieben, danach mit den zurückgelesenen Werten fortgeschrieben |
| Backup `hdfs-acl-backup-<zeit>-replace-<alt>-<neu>.csv` neben der DB | nein | ja | ja, vor dem ersten Schreibzugriff |
| Shell-Skript `DATEI` | nein | ja, ausführbar | nein |
| xattr `user.acl` in HDFS | nein | nein, erst das Skript | ja |

In HDFS ändert sich nur der Wert des xattr `user.acl` (bzw. `--attr`) an den
Pfaden aus dem Plan. Jeder Pfad bekommt die vollständige neue ACL als einen
Wert. Dateien, HDFS-Rechte (`chmod`, `chown`), HDFS-eigene ACLs (`setfacl`)
und andere xattrs fasst weder das Tool noch das Skript an. Pfade, die nicht
im Plan stehen, bleiben unberührt.

### Backup

Das Backup hat eine Zeile je geändertem Pfad mit dem alten und dem neuen
Wert. `restore` setzt damit jeden Pfad zurück, der noch den neuen Wert hat.

```
path;old;new;empty
/fs/account1/container1;@alt|@client1=3,@client2=1;@neu|@client1=3,@client2=1;
/fs/account1/container3;maria|@alt=3;maria|@neu=3;
```

Das Backup ist der einzige Weg zurück. `replace @neu @alt` stellt den alten
Stand nicht her. Hatte NEU schon eigene Einträge, sind sie mit denen von ALT
zu einem verschmolzen, und ACLs, in denen NEU schon vorher Besitzer war,
bekämen ALT als Besitzer.

### Mit `--generate`: das Skript

Das Skript enthält einen Aufruf `hadoop fs -setfattr` je Pfad. Über jedem
Aufruf steht die alte ACL als Kommentar, im Kopf stehen die Zahl der Pfade,
das fsimage des Plans und das Kommando zum Zurückdrehen.

```bash
#!/usr/bin/env bash
# hdfs-acl replace-alt-neu: 2 Pfade, erzeugt 2026-11-16T18:30:00
# geplant auf: fsimage_0000000000000001397 (Datei vom 2026-11-11T09:55:12)
# zurückdrehen: uv run main.py restore hdfs-acl-backup-20261116-183000-replace-alt-neu.csv --apply

# Hadoop-Client: per HADOOP überschreibbar, z. B. HADOOP=/opt/hadoop/bin/hadoop
HADOOP="${HADOOP:-hadoop}"
failed=0

# alt: @alt|@client1=3,@client2=1
"$HADOOP" fs -setfattr -n user.acl -v '"@neu|@client1=3,@client2=1"' /fs/account1/container1 || failed=$((failed + 1))
# alt: maria|@alt=3
"$HADOOP" fs -setfattr -n user.acl -v '"maria|@neu=3"' /fs/account1/container3 || failed=$((failed + 1))

if [ "$failed" -ne 0 ]; then
    echo "$failed von 2 Aufrufen fehlgeschlagen" >&2
    exit 1
fi
echo "OK: 2 Pfade geschrieben"
```

Für das Skript gilt:

- Es braucht nur einen Hadoop-Client und Schreibrecht auf die Pfade, weder
  das Tool noch die DB. Ein anderer Client lässt sich mit
  `HADOOP=/pfad/zu/bin/hadoop ./replace-neu.sh` wählen.
- Es läuft nach einem Fehler weiter, zählt die fehlgeschlagenen Aufrufe und
  endet dann mit Exit-Code 1.
- Es startet je Pfad einen eigenen Hadoop-Client, nacheinander. Bei vielen
  Pfaden dauert es deshalb länger als `--apply`, das Pfade mit demselben
  neuen Wert bündelt und parallel schreibt.
- Es schreibt die Werte, die beim Erzeugen geplant waren. Ändert jemand einen
  Pfad zwischen Erzeugen und Ausführen, überschreibt das Skript diese
  Änderung. Der Kommentar `# alt:` zeigt, von welchem Wert der Plan ausging.
- Es liest nichts zurück, und das Tool erfährt nichts vom Lauf. Zur Kontrolle
  wiederholt man danach den Dry-Run. Er muss melden, dass `@alt` in keiner
  ACL steht. „Keine Änderungen nötig.“ meldet `replace` nie.
- Zurückdrehen lässt sich der Lauf mit dem Backup, das beim Erzeugen
  entstanden ist. Das Kommando steht im Kopf des Skripts.

### Mit `--apply`: das Tool schreibt selbst

1. Das Tool schreibt das Backup.
2. Es setzt die neuen Werte mit `hdfs dfs -setfattr`. Pfade mit demselben
   neuen Wert gehen gebündelt in einen Aufruf, `--parallel` Aufrufe laufen
   gleichzeitig. Ein Fehler an einem Pfad bricht die übrigen nicht ab.
3. Es liest jeden Pfad aus dem Plan zurück und vergleicht ihn mit dem
   geplanten Wert. Weicht ein Wert ab oder ist ein Pfad nicht lesbar, meldet
   es den Pfad und endet mit Exit-Code 1.
4. Es schreibt die zurückgelesenen Werte in die DB. Die Quelle in der DB
   heißt dann z. B. `fsimage_… + replace-alt-neu`.

Nach einem Teilfehler lässt sich `replace` wiederholen. Der zweite Lauf plant
nur noch die Pfade, in denen ALT noch steht.

## Regel je ACL

Das Tool betrachtet jede ACL für sich und rechnet nichts Geerbtes ein.

1. Eine ACL nennt ALT, wenn ALT ihr Besitzer ist oder wenn sie einen Eintrag
   der Form `@alt` oder `@alt=<zahl>` hat. Alle anderen ACLs bleiben
   unverändert.
2. Ist ALT Besitzer, wird NEU Besitzer. Ein anderer Besitzer bleibt.
3. Jeder Eintrag `@alt=<zahl>` wird an seiner Stelle zu `@neu=<zahl>`. Die
   Zahl bleibt, auch eine ungültige.
4. Steht NEU danach mit mindestens einer gültigen Maske in der ACL, bleibt
   genau ein Eintrag für NEU an der ersten dieser Stellen. Er enthält die
   veroderten Bits aller gültigen Einträge. Weitere Einträge von NEU
   entfallen.

`replace` ändert keinen anderen Besitzer und lässt die Einträge von Usern,
Gruppen und anderen clientids Wort für Wort stehen. In einer geänderten ACL
fallen nur Leerzeichen um Besitzer und Einträge und leere Einträge weg.

NEU hat danach in jeder geänderten ACL die Rechte, die ALT dort hatte, und
dazu die Bits, die NEU dort schon hatte. `replace` entfernt kein Bit.

## Sonderfälle je ACL

### ALT ist Besitzer

NEU wird Besitzer und hat damit `owner`. Einen Eintrag für NEU schreibt
`replace` dabei nicht. Hier liegt der Unterschied zu `add`, das NEU einen
Eintrag mit `admin` gibt.

| Fall | vorher | nachher |
|---|---|---|
| ALT ist Besitzer | `@alt\|@t1=1,@t2=2` | `@neu\|@t1=1,@t2=2` |
| ALT ist nur Besitzer, keine Einträge | `@alt\|` | `@neu\|` |
| Besitzer zusätzlich gelistet | `@alt\|@alt=1,@t=1` | `@neu\|@neu=1,@t=1` |
| Besitzer zusätzlich mit einem Bit außerhalb von `owner` | `@alt\|@alt=8` | `@neu\|@neu=8` |
| NEU hat dort schon einen Eintrag | `@alt\|@neu=1,@x=3` | `@neu\|@neu=1,@x=3` |
| beide sind gelistet | `@alt\|@alt=3,@neu=4` | `@neu\|@neu=7` |
| ACL ohne `\|`, nur der Besitzer | `@alt` | `@neu\|` |

Einen Eintrag des Besitzers lässt `replace` stehen, auch wenn seine Bits in
`owner` schon enthalten sind (`@neu|@neu=1`). Ein Container mit eigenem
Besitzer erbt vom Account die Einträge, nicht das Besitzer-Recht.

### Nach Phase 1

`add` hat NEU dort, wo ALT Besitzer ist, einen Eintrag mit `admin` gegeben.
`replace` kann diesen Eintrag nicht von einem bewusst gesetzten unterscheiden
und lässt ihn neben dem neuen Besitzer stehen.

| Schritt | ACL |
|---|---|
| Ausgangsstand | `@alt\|@t1=1,@t2=2` |
| nach `add @alt @neu` | `@alt\|@t1=1,@t2=2,@neu=19` |
| nach `replace @alt @neu` | `@neu\|@t1=1,@t2=2,@neu=19` |
| direktes `replace` ohne Phase 1 | `@neu\|@t1=1,@t2=2` |

Wo ALT nur einen Eintrag hatte, stehen nach Phase 1 beide Einträge in der
ACL, und `replace` fasst sie zu einem zusammen. Aus `hans|@alt=3,@neu=3` wird
`hans|@neu=3`. Das Ergebnis ist dasselbe wie beim direkten `replace`.

### ALT hat einen Eintrag

| Fall | vorher | nachher |
|---|---|---|
| ein Eintrag | `hans\|@alt=3,.cn=ops,ou=g,dc=f=3` | `hans\|@neu=3,.cn=ops,ou=g,dc=f=3` |
| ALT steht mehrfach | `hans\|@alt=5,@x=1,@alt=16` | `hans\|@neu=21,@x=1` |
| ALT steht doppelt mit demselben Recht | `hans\|@alt=1,@alt=1` | `hans\|@neu=1` |
| leerer Besitzer | `\|@alt=1` | `\|@neu=1` |
| Container schränkt ALT ein | `@y\|@alt=1` | `@y\|@neu=1` |

Mehrere Einträge von ALT werden zu einem Eintrag von NEU an der Stelle des
ersten.

### NEU steht schon in der ACL

Der gemeinsame Eintrag steht an der Stelle, an der zuerst ALT oder NEU stand.

| Fall | vorher | nachher |
|---|---|---|
| NEU hat schon alle Bits von ALT | `@o\|@alt=1,@neu=3` | `@o\|@neu=3` |
| NEU steht vor ALT | `@o\|@neu=4,@alt=1` | `@o\|@neu=5` |
| NEU steht hinter ALT | `@o\|@alt=1,@x=2,@neu=4` | `@o\|@neu=5,@x=2` |
| NEU steht mehrfach | `hans\|@neu=4,@alt=1,@neu=8` | `hans\|@neu=13` |
| NEU ist Besitzer, ALT hat nur Bits aus `owner` | `@neu\|@alt=1` | `@neu\|@neu=1` |
| NEU ist Besitzer, ALT hat ein Bit außerhalb von `owner` | `@neu\|@alt=8` | `@neu\|@neu=8` |
| NEU ist Besitzer und gelistet | `@neu\|@neu=4,@alt=8` | `@neu\|@neu=12` |
| ACL nennt nur NEU | `@neu\|@x=1` | unverändert |

Anders als `add` lässt `replace` eine ACL nie unverändert, wenn sie ALT
nennt. Auch wo NEU schon alle Bits hat, muss der Eintrag von ALT heraus.

### Ungültige Einträge

Ein ungültiger Eintrag gibt keine Rechte. `collect` meldet ihn als Warnung,
`replace` meldet ihn nicht. `replace` prüft die Maske nicht, bevor es den
Eintrag umbenennt.

| Fall | vorher | nachher |
|---|---|---|
| ALT mit Maske 0 | `hans\|@alt=0` | `hans\|@neu=0`, weiter ungültig |
| ALT mit Maske über 2147483647 | `hans\|@alt=2147483648` | `hans\|@neu=2147483648`, weiter ungültig |
| ALT mit Maske 0, daneben gültig | `hans\|@alt=0,@alt=1` | `hans\|@neu=1` |
| ALT mit Maske 0, NEU gültig | `hans\|@alt=0,@neu=1` | `hans\|@neu=1` |
| ALT ohne Recht | `hans\|@alt,@x=1` | `hans\|@alt,@x=1`, ALT bleibt stehen |
| ALT ohne Recht, daneben gültig | `hans\|@alt,@alt=1` | `hans\|@alt,@neu=1` |
| ALT mit Leerzeichen um `=`, daneben gültig | `hans\| @alt = 1,@alt=2` | `hans\|@alt = 1,@neu=2` |
| ALT nur mit Leerzeichen um `=` | `hans\| @alt = 1` | unverändert, nicht im Plan |
| ALT mit Text als Recht | `hans\|@alt=abc` | unverändert, nicht im Plan |
| ALT ist Besitzer, sein Eintrag hat Text als Recht | `@alt\|@alt=abc` | `@neu\|@alt=abc` |
| ACL ohne `\|`, mit Recht | `@alt=3` | unverändert, `@alt=3` ist kein Besitzer `@alt` |
| NEU ohne Recht | `hans\|@neu,@alt=1` | `hans\|@neu=1` |
| NEU mit Maske 0 | `hans\|@neu=0,@alt=1` | `hans\|@neu=1` |
| NEU mit Text als Recht | `hans\|@neu=abc,@alt=1` | `hans\|@neu=abc,@neu=1` |

Daraus folgen drei Fälle, in denen der Name von ALT nach `replace` noch in
einer ACL steht:

- `@alt` ohne Recht benennt `replace` nicht um. Die ACL gilt trotzdem als
  eine, die ALT nennt. Sie steht deshalb bei jedem Lauf wieder im Plan, auch
  wenn alter und neuer Wert gleich sind, und die Meldung „`@alt` steht in
  keiner ACL“ erscheint nie. Den Eintrag muss man von Hand entfernen.
- `@alt=abc` und `@alt = 1` erkennt das Tool nicht als Einträge von ALT. Sie
  bleiben stehen und lösen keinen Plan aus.
- `search @alt` findet alle drei Fälle nicht, weil die Einträge keine Rechte
  geben. Die Warnungen von `collect` nennen die Pfade.

### Einträge, die replace nicht anfasst

| Fall | vorher | nachher |
|---|---|---|
| Leerzeichen und leere Einträge | `@alt \| @b=1, ,@c=2` | `@neu\|@b=1,@c=2` |
| LDAP-Teile ohne `.` | `@alt\|cn=doc,ou=hell,dc=firma=3,cn=testgroup` | `@neu\|cn=doc,ou=hell,dc=firma=3,cn=testgroup` |
| Gruppe ohne Recht am Ende | `@alt\|.cn=ops,ou=groups` | `@neu\|.cn=ops,ou=groups` |
| User mit demselben Namen | `alt\|alt=3` | unverändert, `alt` ist ein User |
| clientid mit demselben Anfang | `hans\|@alt2=3` | unverändert |
| leeres Attribut | (leer) | unverändert |
| Pfad ohne ACL | (kein Attribut) | unverändert |

## Sonderfälle über mehrere Pfade

### Meldungen und Exit-Codes

| Situation | Ausgabe | Exit-Code |
|---|---|---|
| ALT steht in keiner ACL des Modus | „`@alt` steht in keiner ACL eines Containers (Accounts mit --accounts).“ | 0 |
| zweiter Lauf nach einem vollständigen `replace` | dieselbe Meldung | 0 |
| Dry-Run mit Änderungen | Plan, „Dry-Run: nichts geschrieben.“ | 0 |
| `--apply`, alles geschrieben und geprüft | „OK: n Pfade geändert und geprüft.“ | 0 |
| `--apply`, ein Aufruf schlug fehl oder ein Wert stimmt nicht | je Pfad eine Fehlerzeile | 1 |
| ALT und NEU sind gleich | Fehler | 1 |
| ungültige clientid, unbekannter Account | Fehler | 1 |
| Lesefehler beim Nachlesen (Berechtigung, Kerberos, NameNode) | Fehler, nichts geschrieben | 1 |

Ein fehlendes `@` ergänzt das Tool, `replace alt neu` meint `@alt` und
`@neu`. Die Meldung „steht in keiner ACL“ gilt nur für die Pfade des Modus.
Im Default-Modus kann ALT weiter in den Accounts stehen.

### Kontrolle danach

Nach dem nächsten Checkpoint zeigt die DB, wo ALT noch steht:

```bash
uv run main.py collect
uv run main.py search @alt      # alle Ebenen bis --depth, auch die Accounts
```

`search` kennt die Modi nicht und zeigt auch Accounts, die ein Lauf ohne
`--accounts` ausgelassen hat.

### Veralteter Stand

Das fsimage ist so alt wie der letzte Checkpoint. Das Nachlesen gleicht das
nur für die Pfade aus, die der erste Plan ändern würde.

| Was seit dem Checkpoint passiert ist | Verhalten |
|---|---|
| Pfad aus dem Plan wurde geändert, z. B. durch `add` | Das Tool plant auf dem aktuellen Wert. |
| Pfad aus dem Plan wurde gelöscht | Der Pfad fällt aus dem Plan. |
| Pfad hat ALT erst danach bekommen | Der Pfad fehlt im Plan, ALT bleibt dort stehen. |
| Ordner wurde danach angelegt | Der Pfad fehlt im Plan. |

Die beiden letzten Fälle behebt ein zweiter Lauf nach dem nächsten Checkpoint
oder ein Lauf mit `--live`. `replace` lässt sich beliebig oft wiederholen.

### Tiefe

Das Tool liest nur die Ebenen bis `--depth` unter `--base-dir` (Default 2,
Accounts und Container). ACLs auf tieferen Ordnern sieht `replace` erst mit
größerem `--depth`. Bis dahin steht ALT dort weiter.

### Vererbung

Das Tool rechnet nicht nach, wie die Anwendung Account- und Container-ACL
zusammenführt ([hdfs.md](hdfs.md#nur-die-acl-einträge-selbst)). Die
folgenden Aussagen folgen aus den Regeln der Anwendung, wie hdfs.md sie
beschreibt. Das Tool prüft sie nicht, und der Plan zeigt sie nicht.

Im Default-Modus steht ALT weiter in den Accounts:

- In Containern ohne eigene ACL und in Containern, deren ACL ALT nie genannt
  hat, greift weiter ALT über den Account. NEU hat dort kein Recht.
- In Containern, deren ACL ALT genannt hat, steht jetzt NEU. Die ACL nennt
  ALT nicht mehr, für ALT gilt dort also wieder der Wert aus dem Account.
  Hat ein Container ALT eingeschränkt (`@y|@alt=1`) und gibt der Account ALT
  mehr, hat ALT nach `replace` in diesem Container mehr Rechte als vorher.

Wer ALT ablösen will, braucht deshalb `--accounts`. Der Default-Modus passt,
wenn die Accounts getrennt oder von Hand migriert werden.

Mit `--accounts` kann NEU in einzelnen Containern andere Rechte haben, als
ALT dort hatte:

- Ein Container nennt NEU schon, aber nicht ALT. Seine ACL bleibt
  unverändert, und für NEU gilt dort weiter der Wert des Containers. Gab der
  Account ALT mehr, hat NEU in diesem Container weniger, als ALT hatte.
- Nach Phase 1 steht im Account neben dem Besitzer NEU der Eintrag
  `@neu=19`. Container mit eigenem Besitzer erben diesen Eintrag, solange
  ihre ACL NEU nicht nennt. ALT hatte dort kein Besitzer-Recht. Ein direktes
  `replace` ohne Phase 1 setzt den Eintrag nicht.

Die betroffenen Container eines Accounts zeigen diese Abfragen auf der DB:

```bash
# nennen NEU, aber nicht ALT
sqlite3 hdfs-acl.db "SELECT path, acl FROM node
  WHERE path LIKE '/fs/account1/%' AND acl LIKE '%@neu%' AND acl NOT LIKE '%@alt%'"

# haben einen eigenen Besitzer
sqlite3 hdfs-acl.db "SELECT path, acl FROM node
  WHERE acl IS NOT NULL AND path LIKE '/fs/account1/%'
    AND acl NOT LIKE '@alt|%' AND acl NOT LIKE '|%'"
```

Das `LIKE` trifft auch clientids, die mit `@alt` oder `@neu` anfangen.

## replace oder remove

In Phase 2 geht statt `replace` auch `remove @alt --new-owner @neu`.

| | `replace @alt @neu` | `remove @alt --new-owner @neu` |
|---|---|---|
| geänderte Ebenen | Container, mit `--accounts` auch Accounts | immer alle |
| `--account` | ja | nein |
| Einträge von ALT | werden zu Einträgen von NEU | entfallen |
| Voraussetzung | keine | Phase 1 mit `--accounts`, sonst fehlen NEU die Rechte aus den Einträgen von ALT |

Nach einer Phase 1 mit `--accounts` haben beide Wege dieselben Rechte zum
Ergebnis. Die Reihenfolge der Einträge kann abweichen.

## Grenzen des Dry-Run

Der Dry-Run ist jeder Aufruf ohne `--apply` und ohne `--generate`. Eine
eigene Option `--dry-run` gibt es nicht.

- Der Dry-Run arbeitet nicht offline. Er braucht den Hadoop-Client, Java, die
  `core-site.xml` und ein fsimage, und er liest die betroffenen Pfade mit
  einem NameNode-RPC je Pfad live nach. Ohne HDFS plant nur
  `plan replace` ([hdfs.md](hdfs.md#trockenlauf-mit-plan)).
- Er schreibt die lokale DB neu, mit dem Stand des fsimage. Die live
  nachgelesenen Werte kommen im Dry-Run nicht in die DB.
- Er schreibt kein Backup und keine Plan-Datei. Den Plan als CSV liefert
  `plan replace`.
- Er zeigt den Stand zum Zeitpunkt des Laufs. `--apply` liest danach neu ein
  und plant neu. Ändert jemand HDFS dazwischen, weicht der geschriebene Plan
  vom gezeigten ab. Was wirklich geschrieben wurde, steht im Backup.
- Er hat dieselben blinden Stellen wie der echte Lauf (siehe
  [Veralteter Stand](#veralteter-stand) und [Tiefe](#tiefe)).
- Er prüft keine Schreibrechte. Ob `setfattr` an jedem Pfad gelingt, zeigt
  erst `--apply`, das jeden Pfad zurückliest.
- Er zeigt nur ACLs, die ALT nennen. Pfade außerhalb des Modus, Pfade ohne
  ACL und die Folgen der Vererbung fehlen in der Ausgabe.
- Er meldet keine Formatfehler. Eine ungültige Maske von ALT wandert still
  zu NEU. Die Warnungen zeigt `collect`.
- Er endet mit Exit-Code 0, auch wenn Änderungen anstehen. Ein Skript erkennt
  offene Änderungen nur an der Ausgabe.
