# eMudhra / Sectigo: kompromittierte Intermediate-CAs sperren

Stand: 23.10.2026

Acht Intermediate-CAs von eMudhra haben Schlüssel, die sich aus dem
Zertifikat selbst zurückrechnen lassen. Wer sie hat, kann für jede Domain
ein TLS-Zertifikat ausstellen, dem ein ungepatchtes JDK vertraut. Dieses
Verzeichnis enthält ein Playbook, das die betroffenen Zertifikate in allen
JDKs und JREs unter `/usr/java` und `/opt/local/test/jvm` per
`blocked.certs` bzw. `blacklisted.certs` sperrt,
und ein kleines Java-Programm zum Prüfen.

## Dateien

- **blocked-certs.yml** – Playbook, trägt die 48 Fingerprints ein
- **CheckEmudhra.java** – testet ein JDK gegen die 14 Testhosts des Trackers
- **CheckEmudhraOffline.java** – derselbe Test ohne Netz, plus alle 48
  Kodierungen gegen `blocked.certs`
- **files/emudhra-blocked.certs** – die 48 Einträge zum manuellen
  Anhängen, für Hosts ohne Ansible
- **files/emudhra-rejected.pem** – die 16 Intermediates als OpenSSL-Reject-Einträge
- **files/emudhra-block.cjs** – Preload-Modul für Node.js
- **files/\*.sha256** – Prüfsummen zu allen Dateien in `files/`
- **testdata/** – Zertifikate für den Offline-Test, siehe unten

## CVE-2026-111943

Red Hat fasst den Vorfall im Security Bulletin
[RHSB-2026-012](https://access.redhat.com/security/vulnerabilities/RHSB-2026-012)
zusammen. Dort laufen vier CVE-Nummern für dasselbe Problem:

| CVE | Bereich |
|-----|---------|
| CVE-2026-111943 | Go `crypto/x509`, upstream |
| CVE-2026-91403 | Java, upstream |
| CVE-2026-114406 | RHEL `ca-certificates`, downstream |
| CVE-2026-113812 | Perl `Mozilla::CA`, upstream |

CVE-2026-111943 ist also formal die Go-CVE. Go 1.26.10 und 1.27.3 haben
eine Denylist mit den SPKI-Hashes der acht Schlüssel eingebaut. Für Java
ist CVE-2026-91403 die passende Nummer. In Tickets und Scannern taucht
trotzdem meist CVE-2026-111943 auf, weil sie zuerst veröffentlicht wurde.

Red Hat bewertet CVE-2026-111943 mit "Important", CVSS 3.1 Basiswert 6.8
(`AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:H/A:N`). AC:H, weil ein Angreifer sich
für einen Man-in-the-Middle-Angriff in den Netzwerkpfad bringen muss.

### Was passiert ist

eMudhra hat im April 2025 acht Intermediates erzeugt, sechs für TLS und
zwei für Code Signing. Die Schlüssel wurden fehlerhaft generiert, der
private Schlüssel lässt sich aus dem öffentlichen Zertifikat berechnen.
Jedes Intermediate gibt es zweimal:

- ausgestellt von der eigenen eMudhra-Root (emSign Root TLS CA - G3 bzw.
  emSign Root CS CA - G3), ECDSA-signiert
- als Cross-Sign von Sectigos **AAA Certificate Services**, RSA-signiert

Das ergibt 16 Zertifikate. Der Cross-Sign ist der Grund, warum der
Ordnername auch Sectigo nennt. Wer nur die emSign-Roots aus dem
Truststore entfernt, bleibt über die AAA-Kette angreifbar.

| Datum | Ereignis |
|-------|----------|
| April 2025 | Intermediates erzeugt |
| 17.10.2026 | Meldung an die Root-Programme, Firefox und Chrome sperren |
| 18.10.2026 | eMudhra widerruft die emSign-Ausstellungen |
| 19.10.2026 | Sectigo widerruft die Cross-Signs, Weitergabe an Downstream-Truststores |
| 19.–20.10.2026 | Go 1.26.10 / 1.27.3 |
| 20.10.2026 | Microsoft sperrt |
| 21.10.2026 | RHSB-2026-012 veröffentlicht |
| 22.10.2026 | Apple sperrt, Tracker ergänzt No-CT-Testketten |

Browser waren nach aktuellem Stand nicht gefährdet. Sie erzwingen
Certificate Transparency, und für diese Intermediates gibt es seit April
2025 keine CT-Einträge. Java prüft CT nicht. Java prüft auch keine
Sperrlisten, solange `com.sun.net.ssl.checkRevocation` bzw. OCSP nicht
explizit eingeschaltet ist. Der Widerruf durch eMudhra und Sectigo
schützt ein JDK deshalb nicht. Ob die Schlüssel tatsächlich missbraucht
wurden, weiß niemand.

### Betroffene Zertifikate

SHA-256 über das DER, so wie es ausgestellt wurde. Die vollständige Liste
mit allen Varianten steht im Playbook.

| Intermediate | Aussteller | SHA-256 |
|--------------|------------|---------|
| EM DV TLS CA - G3B | emSign Root TLS CA - G3 | `42C4ACCD12C4F59597925E1A08A7A90102608543809308D50EC427E118BA1493` |
| EM DV TLS CA - G3B | AAA Certificate Services | `45A883F349AEDFD5F3A649B5498FFB121712BA8B448F3F2A91DBC5A766FCDDBE` |
| EM OV TLS CA - G3B | emSign Root TLS CA - G3 | `98B896625C0C230ADEEC3EB2CF6AAD5001ECF9156A68644E82374CE5833BC25B` |
| EM OV TLS CA - G3B | AAA Certificate Services | `5F1DED4DD590AF74631290CF073B122A2AAC6A1FA479778A0BD43579BD832699` |
| EM EV TLS CA - G3B | emSign Root TLS CA - G3 | `E7568FE6E12331F4FD7DE61DA4C7559D0F8EB192A218D4741A3BA75184333838` |
| EM EV TLS CA - G3B | AAA Certificate Services | `579480AEA166290BD3C643589E795FC0D0E54E86505D180F1DFF99EFD3199512` |
| EM DV TLS CA - G3C | emSign Root TLS CA - G3 | `0164D7CEAE5BB909C272AF151FF44B1177AE9B47D9424B94F1D9558C30934D19` |
| EM DV TLS CA - G3C | AAA Certificate Services | `62B7F9868263F72F6911FC78CEAF72C1979158B7368BF648683BB2AA39B02E72` |
| EM OV TLS CA - G3C | emSign Root TLS CA - G3 | `CB6E15165AC802E9B3F993F16C25D41F4E81C21EBDE71AE1B012AC00BF9C7C3F` |
| EM OV TLS CA - G3C | AAA Certificate Services | `7485B51C0ED192878FA1D2F948FB529AE1842CC5086C4B67F7BE5B2039DE9489` |
| EM EV TLS CA - G3C | emSign Root TLS CA - G3 | `432EC25F0D28DE9006D97BF752F17BBB2CD41F669933FCB072D9A1148D0261F3` |
| EM EV TLS CA - G3C | AAA Certificate Services | `FD9D5C2DBBD4995AB69B8D50C439D9527B00529199C2FD01B2174EB58F07021F` |
| EM OV CS CA - G3B | emSign Root CS CA - G3 | `9E39DE2881273ACC225A149C72BA3E0F51424963669AB633EE1DC7DF5DB067B4` |
| EM OV CS CA - G3B | AAA Certificate Services | `A496E23AC0CAB8155FC9B117B97EAE368EFCBB9041AEC8804D5A308D2D01C2D8` |
| EM OV CS CA - G3C | emSign Root CS CA - G3 | `4B2460A466F95FD40113BCDCCA2CEB29B3B093921DE45474DA7DF48874C630B1` |
| EM OV CS CA - G3C | AAA Certificate Services | `84112972B8E59D14560AE7E7119654474AAD119990CF3722BFC1E3B91615E1FD` |

## emudhra.we-have-a-problem.com

Die Seite gehört nicht zu eMudhra. Dennis Jackson betreibt sie als
Community-Tracker für den Vorfall, der Quellcode liegt auf GitHub. Der
Name klingt nach Phishing, die Seite ist aber die Referenz, auf die
Distributionen und Hersteller verlinken. Sie bietet:

- [impacted-intermediates.json](https://emudhra.we-have-a-problem.com/impacted-intermediates.json)
  mit allen 16 Zertifikaten als PEM, Fingerprints, SPKI-Hashes und
  Widerrufsdaten. Die Hashes im Playbook sind daraus berechnet.
- [roots.json](https://emudhra.we-have-a-problem.com/roots.json) mit den
  Roots, an denen die Ketten hängen
- einen Browser-Check und 14 Testhosts nach dem Muster
  `<dv|ov|ev>-<g3b|g3c>[-aaa][-noct]-emudhra.we-have-a-problem.com`.
  Jeder Host liefert ein Leaf-Zertifikat über eines der kompromittierten
  Intermediates. `-aaa` nimmt die Sectigo-Kette, `-noct` ein Leaf ohne
  CT-Eintrag. Kommt eine Verbindung zustande, vertraut der Client den
  Intermediates noch.
- `chains.zip` mit allen Ketten als PEM für Offline-Tests mit OpenSSL
- eine Liste gepatchter Software und Statistiken, welche User-Agents sich
  noch erfolgreich verbinden

### Gepatchte JDKs

Laut Tracker sperren diese Versionen die Zertifikate bereits selbst,
OpenJDK und Oracle JDK:

| Java | Version |
|------|---------|
| 27 | 27.0.1.0.2 |
| 25 | 25.0.5.0.2 |
| 21 | 21.0.13.0.2 |
| 17 | 17.0.21.0.2 |
| 11 | 11.0.33.0.2 |
| 8 | 8u511 |

Das Update ist die eigentliche Lösung. Das Playbook ist für die Zeit bis
dahin gedacht und für JDKs, die sich nicht so schnell tauschen lassen.
Auf einem gepatchten JDK schadet es nicht, doppelte Einträge in
`blocked.certs` sind egal.

## Warum blocked.certs

Jedes JDK bringt eine eigene Sperrliste mit. Der Validator lehnt jede
Kette ab, in der ein Zertifikat mit einem dieser Fingerprints vorkommt,
egal welcher Root es vertraut. Die Alternative wäre, AAA Certificate
Services aus `cacerts` zu löschen. Das empfiehlt auch Red Hat für den
System-Truststore. Es bricht aber jede ältere Sectigo-Kette, die noch an
AAA hängt. Die Sperrliste trifft genau die 16 Zertifikate und sonst
nichts.

Format und Ort:

| JDK | Datei |
|-----|-------|
| 8 | `<JAVA_HOME>/jre/lib/security/blacklisted.certs` |
| 11 bis 11.0.13 | `<JAVA_HOME>/lib/security/blacklisted.certs` |
| ab 11.0.14, 17, 21, 25, ... | `<JAVA_HOME>/lib/security/blocked.certs` |

Die Umbenennung kam mit JDK-8253866. Bei Red Hats java-11-openjdk ist
sie seit 11.0.14 drin, andere Hersteller können abweichen. Das Playbook
sucht deshalb nach beiden Namen.

Das JDK liest die Datei per `Properties.load`. Die erste Nutzzeile muss
`Algorithm=SHA-256` sein, danach ein Fingerprint pro Zeile in
Großbuchstaben ohne Doppelpunkte. Zeilen mit `#` sind Kommentare.

### Warum 48 Einträge statt 16

Java bildet den Fingerprint über das DER, das der Server schickt. Zwei
Felder eines Zertifikats lassen sich ändern, ohne dass die Signatur
ungültig wird:

- Der `signatureAlgorithm` außerhalb des signierten Teils kann einen
  NULL-Parameter haben oder nicht. Java akzeptiert beides.
- Eine ECDSA-Signatur `(r, s)` ist mit `(r, n-s)` genauso gültig.

Ein Angreifer könnte also eine umkodierte Variante schicken, deren
Fingerprint nicht auf der Liste steht. OpenJDKs `BlockedCertsConverter`
trägt deshalb alle Varianten ein. Für die acht RSA-signierten
AAA-Cross-Signs sind das je zwei, für die acht ECDSA-signierten
emSign-Ausstellungen je vier, zusammen 48. Einige Anleitungen im Netz
listen nur die 16 Basis-Hashes, das reicht nicht.

Ich habe alle 48 Werte aus den PEMs in `impacted-intermediates.json`
selbst berechnet. Sie stimmen mit der Liste aus
[gominimal/pkgs#949](https://github.com/gominimal/pkgs/pull/949) überein.

## Playbook

```bash
ansible-playbook -i ../inventory/hosts.ini blocked-certs.yml --check --diff
ansible-playbook -i ../inventory/hosts.ini blocked-certs.yml
```

Das Playbook braucht root (`become: true`), weil die JDKs unter
`/usr/java` root gehören. Pro Host macht es Folgendes:

1. Sucht unter `/usr/java` und `/opt/local/test/jvm` bis Tiefe 5 nach
   `blocked.certs` und `blacklisted.certs` und behält nur Treffer unter
   `lib/security` bzw. `jre/lib/security`. Symlinks wie `/usr/java/latest`
   oder `/usr/java/default` folgt es nicht, jedes JDK kommt nur einmal
   dran. Fehlt eines der Verzeichnisse auf einem Host, gibt es nur eine
   Warnung.
2. Meldet JDKs, die ein `cacerts` haben, aber keine Sperrdatei. Diese
   ändert das Playbook nicht, sie müssen von Hand geprüft werden.
3. Prüft die `Algorithm`-Zeile. Steht dort etwas anderes als SHA-256,
   bricht es ab. Fehlt sie, schreibt es `Algorithm=SHA-256` an den Anfang.
4. Hängt die 48 Fingerprints als Block zwischen
   `# BEGIN/END CVE-2026-111943 eMudhra (ansible)` an. Vorher legt es eine
   Sicherung `blocked.certs.<pid>.<datum>~` daneben, die das JDK ignoriert.
5. Liest die Datei zurück und bricht ab, falls ein Fingerprint fehlt.
6. Listet laufende `java`-Prozesse, deren Binary aus einem geänderten JDK
   stammt. Die müssen neu gestartet werden.

Ein zweiter Lauf ändert nichts. Zum Entfernen:

```bash
ansible-playbook -i ../inventory/hosts.ini blocked-certs.yml -e emudhra_state=absent
```

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `emudhra_hosts` | `all` | Host-Gruppe |
| `emudhra_state` | `present` | `absent` entfernt den Block wieder |
| `emudhra_java_dirs` | `[/usr/java, /opt/local/test/jvm]` | Suchpfade, z. B. zusätzlich `/usr/lib/jvm` für die RHEL-Pakete |
| `emudhra_backup` | `true` | Sicherung vor jeder Änderung |

### Ohne Ansible

[files/emudhra-blocked.certs](files/emudhra-blocked.certs) enthält
dieselben 48 Einträge wie das Playbook, mit `Algorithm=SHA-256` am Anfang.
Die Datei wird an die vorhandene Sperrdatei angehängt. Nicht ersetzen,
das JDK bringt eigene Einträge mit, Temurin 21.0.8 zum Beispiel 38 Stück.
Die zweite `Algorithm`-Zeile stört nicht.

Nach dem Kopieren auf den Zielhost zuerst die Prüfsumme kontrollieren:

```
SHA-256 14cc75cddbb0d5b60f0a645ff428b5166f23fa4d8ebf3bfa2373272885ec73f0  emudhra-blocked.certs
```

```bash
sha256sum -c emudhra-blocked.certs.sha256
# oder ohne die .sha256-Datei
echo "14cc75cddbb0d5b60f0a645ff428b5166f23fa4d8ebf3bfa2373272885ec73f0  emudhra-blocked.certs" | sha256sum -c
```

Wer die Datei ändert, muss die Prüfsumme neu erzeugen
(`sha256sum emudhra-blocked.certs > emudhra-blocked.certs.sha256`) und
hier nachtragen.

```bash
for f in /usr/java/*/lib/security/blocked.certs \
         /usr/java/*/lib/security/blacklisted.certs \
         /usr/java/*/jre/lib/security/blacklisted.certs \
         /opt/local/test/jvm/*/lib/security/blocked.certs \
         /opt/local/test/jvm/*/lib/security/blacklisted.certs \
         /opt/local/test/jvm/*/jre/lib/security/blacklisted.certs; do
  [ -f "$f" ] || continue
  f=$(readlink -f "$f")
  grep -q 42C4ACCD12C4F59597925E1A08A7A90102608543809308D50EC427E118BA1493 "$f" && continue
  cp -p "$f" "$f.bak-cve-2026-111943"
  { echo; cat emudhra-blocked.certs; } >> "$f"
  echo "ergänzt: $f"
done
```

Die Schleife löst Symlinks wie `/usr/java/latest` auf und überspringt
Dateien, die den ersten Hash schon enthalten. So wird jedes JDK nur
einmal ergänzt, auch bei mehrfachem Aufruf. Das `echo` davor sorgt für einen
Zeilenumbruch, falls der letzte Eintrag der Originaldatei keinen hat.
Sonst würde der erste Kommentar an den letzten Hash geklebt und diesen
Eintrag kaputt machen.

### Was das Playbook nicht abdeckt

- **Laufende JVMs.** Das JDK liest die Sperrliste einmal pro Prozess.
  Ohne Neustart bleibt eine laufende Anwendung angreifbar.
- **JDK-Updates.** Ein neues JDK-RPM bringt ein eigenes `blocked.certs`
  mit. Ab den oben genannten Versionen sind die Einträge schon drin,
  ältere Versionen brauchen einen neuen Playbook-Lauf.
- **Container-Images.** Images mit eigenem JDK haben ihre Sperrliste beim
  Build eingefroren. Hier hilft nur ein neues Basis-Image oder ein
  Rebuild mit gepatchtem JDK.
- **GraalVM Native Images.** Sie haben die Trust-Konfiguration zur
  Build-Zeit eingebaut und müssen neu gebaut werden.
- **RHEL-OpenJDK aus `/usr/lib/jvm`.** Nur mit `emudhra_java_dirs`
  erfasst. Diese Pakete nutzen `/etc/pki/java/cacerts` aus dem
  System-Truststore. Red Hat empfiehlt dort zusätzlich die Blocklist für
  "Comodo AAA Services root" und "emSign ECC Root CA - G3", siehe
  RHSB-2026-012 und KB 7149312, bis das gepatchte `ca-certificates`
  kommt.
- **Eigene Truststores.** Anwendungen mit
  `-Djavax.net.ssl.trustStore=...` nutzen trotzdem `blocked.certs` des
  JDKs, das Playbook greift also auch dort.

## Prüfen

`CheckEmudhra.java` verbindet sich mit allen 14 Testhosts. Es braucht
ausgehendes HTTPS ins Internet, bei Bedarf mit
`-Dhttps.proxyHost=... -Dhttps.proxyPort=...`.

```bash
/usr/java/jdk-21/bin/java CheckEmudhra.java
# JDK 8 kann keine Quelldateien direkt starten
/usr/java/jdk1.8.0_401/bin/javac -d /tmp CheckEmudhra.java
/usr/java/jdk1.8.0_401/bin/java -cp /tmp CheckEmudhra
```

Ergebnis nach dem Playbook:

```
OK         dv-g3b-emudhra.we-have-a-problem.com: PKIX path validation failed: java.security.cert.CertPathValidatorException: Untrusted certificate: CN=EM DV TLS CA - G3B, ...
OK         dv-g3b-aaa-emudhra.we-have-a-problem.com: PKIX path validation failed: ... Untrusted certificate: ...
```

`Untrusted certificate` heißt, die Sperrliste hat gegriffen.
`unable to find valid certification path` bei den `-aaa`-Hosts heißt nur,
dass AAA Certificate Services nicht im `cacerts` dieses JDKs steht.
Temurin hat sie zum Beispiel nicht, Oracle JDK schon. Exit-Code 0 heißt
alles abgelehnt, 1 mindestens ein Host vertraut, 2 mindestens ein Host
nicht erreichbar.

Getestet mit Temurin 21.0.8+9, also ohne den Fix im JDK. Vor dem Playbook
kam die Verbindung über die emSign-Kette zustande, nach Import der
AAA-Root auch über die Sectigo-Kette. Nach dem Playbook lehnte das JDK
alle 14 Hosts mit `Untrusted certificate` ab.

### Offline prüfen

Für Hosts ohne Internetzugang. `CheckEmudhraOffline.java` baut keine
Verbindung auf, sondern lässt den PKIX-Validator des JDKs die Zertifikate
aus `testdata/` prüfen. Das ist derselbe Code, den auch der TLS-Handshake
nutzt, inklusive `blocked.certs`. Sperrlisten-Abfragen per OCSP/CRL sind
abgeschaltet.

```bash
cd eMudhra-Sectigo
/usr/java/jdk-21/bin/java CheckEmudhraOffline.java
# anderer Pfad zu den Testdaten
/usr/java/jdk-21/bin/java CheckEmudhraOffline.java /opt/check/testdata
# JDK 8
/usr/java/jdk1.8.0_401/bin/javac -d /tmp CheckEmudhraOffline.java
/usr/java/jdk1.8.0_401/bin/java -cp /tmp CheckEmudhraOffline testdata
```

Auf die Zielhosts müssen nur `CheckEmudhraOffline.java` und `testdata/`.
Der Test läuft in zwei Teilen.

**Teil 1: blocked.certs.** Jedes der 16 Intermediates wird in allen
Kodierungen erzeugt, also 48 Zertifikate, und gegen seine direkte Root
aus `testdata/roots.pem` validiert. Das Ergebnis hängt nur an
`blocked.certs`, nicht daran, welche Roots im `cacerts` stehen. Pro Zeile
steht der Fingerprint dabei, so sieht man genau, welcher Eintrag fehlt.

```
OK           42C4ACCD...1493 EM DV TLS CA - G3B / emSign Root TLS CA - G3 [DER]
OK           CD740652...6978 EM DV TLS CA - G3B / emSign Root TLS CA - G3 [NULL]
OK           37E86658...EFA8 EM DV TLS CA - G3B / emSign Root TLS CA - G3 [s']
NICHT GESP.  ...
```

`[DER]` ist das Zertifikat wie ausgestellt, `[NULL]` mit umgeschaltetem
NULL-Parameter, `[s']` und `[NULL+s']` mit malleabler ECDSA-Signatur.

**Teil 2: Ketten.** Die 14 Ketten der Testhosts aus `testdata/chains`
werden gegen den Truststore des JDKs validiert. Das ist `cacerts`,
`jssecacerts` falls vorhanden, oder was in `-Djavax.net.ssl.trustStore`
steht, Passwort aus `-Djavax.net.ssl.trustStorePassword`, Default
`changeit`. Mögliche Ergebnisse:

| Ausgabe | Bedeutung |
|---------|-----------|
| `OK gesperrt` | Kette endet an einer vertrauten Root, `blocked.certs` lehnt sie ab |
| `OK keine Root` | die Root der Kette steht nicht im Truststore, z. B. AAA bei Temurin |
| `VERWUNDBAR` | das JDK vertraut der Kette |

Die Leaf-Zertifikate laufen am 3. bzw. 5. Mai 2027 ab. Danach prüft das
Programm zum Zeitpunkt einen Tag nach Ausstellung des Leafs, damit der
Test weiter funktioniert.

Exit-Code 0 heißt alles gesperrt, 1 mindestens ein Zertifikat nicht
gesperrt oder eine Kette vertraut, 2 Fehler in den Testdaten.

Getestet ohne Netzwerk (`unshare -rn`) mit Temurin 21.0.8+9 und als
Java-8-Bytecode (`javac --release 8`):

| Zustand | Teil 1 | Teil 2 |
|---------|--------|--------|
| Original `blocked.certs`, AAA-Root importiert | 48× NICHT GESP. | 14× VERWUNDBAR |
| nach dem Playbook, AAA-Root importiert | 48× OK | 14× OK gesperrt |
| nach dem Playbook, ohne AAA-Root | 48× OK | 6× keine Root, 8× gesperrt |

Die erste Zeile zeigt auch, dass das JDK alle umkodierten Varianten
akzeptiert. Die 16 Basis-Hashes allein reichen also wirklich nicht.

### testdata/

| Datei | Inhalt | Quelle |
|-------|--------|--------|
| `intermediates.pem` | die 16 Intermediates | `impacted-intermediates.json` |
| `roots.pem` | emSign Root TLS CA - G3, emSign Root CS CA - G3, AAA Certificate Services | `roots.json` |
| `chains/*.pem` | die Ketten der 14 Testhosts, Leaf zuerst | `chains.zip` vom 20./22.10.2026 |

Alles öffentliche Zertifikate von emudhra.we-have-a-problem.com, keine
privaten Schlüssel.

## Andere Clients: OpenSSL, Netty, Node.js

`blocked.certs` wirkt nur in Java. Alles, was über OpenSSL oder einen
eigenen Root-Store prüft, braucht eine eigene Maßnahme. Getestet am
23.10.2026 gegen die Testhosts des Trackers, die OpenSSL-Varianten
zusätzlich offline mit `testdata/chains`.

| Client | Truststore | Maßnahme |
|--------|------------|----------|
| OpenSSL, curl, Python `ssl` | System (`/etc/pki/ca-trust`) | `ca-certificates`-Update, bis dahin Roots sperren oder Reject-Bundle |
| Netty | JDK (`cacerts` + `blocked.certs`) | nichts extra, Playbook reicht |
| Node.js | eigener, in Node einkompiliert | Update, bis dahin Preload-Modul |

### OpenSSL

OpenSSL kennt keine Sperrliste für Fingerprints. Es gibt zwei Wege.

**Systemweit: Roots sperren.** Das empfehlen Red Hat (RHSB-2026-012,
KB 7149312) und der Tracker, bis Red Hat ein neues `ca-certificates`
ausliefert (CVE-2026-114406, `rpm -q ca-certificates`). Als root:

```bash
d=/etc/pki/ca-trust/source/blocklist; [ -d $d ] || d=/etc/pki/ca-trust/source/blacklist
trust extract --overwrite --format=pem-bundle \
  --filter='pkcs11:object=Comodo%20AAA%20Services%20root;type=cert' \
  $d/Comodo_AAA_Services_root.pem &&
trust extract --overwrite --format=pem-bundle \
  --filter='pkcs11:object=emSign%20ECC%20Root%20CA%20-%20G3;type=cert' \
  $d/emSign_ECC_Root_CA_-_G3.pem &&
update-ca-trust
```

Das trifft alles, was den System-Truststore nutzt: OpenSSL, curl, Python,
Node mit `--use-openssl-ca` (nicht `--use-system-ca`, siehe Node.js) und das RHEL-OpenJDK über
`/etc/pki/java/cacerts`. Nachteil: Jedes Zertifikat unter diesen beiden
Roots ist danach ungültig, auch legitime ältere Sectigo-Ketten über AAA
und alle emSign-Zertifikate. Vorher prüfen, ob eigene Gegenstellen daran
hängen. Rückgängig machen durch Löschen der beiden Dateien und erneutes
`update-ca-trust`.

**Gezielt: Reject-Bundle.**
[files/emudhra-rejected.pem](files/emudhra-rejected.pem) enthält die 16
Intermediates im OpenSSL-Format `TRUSTED CERTIFICATE` mit
`reject anyExtendedKeyUsage`. Steht diese Datei im CA-Bundle, nimmt
OpenSSL beim Kettenbau die Kopie aus dem Bundle und lehnt sie ab:
`certificate rejected (28)`. Die Roots bleiben gültig, nur die 16
Zertifikate fallen raus. Umkodierungen spielen keine Rolle, OpenSSL
findet das Intermediate über Name und Schlüssel.

```bash
sha256sum -c emudhra-rejected.pem.sha256
cat /etc/pki/tls/certs/ca-bundle.crt emudhra-rejected.pem > /etc/pki/tls/emudhra-ca-bundle.pem

SSL_CERT_FILE=/etc/pki/tls/emudhra-ca-bundle.pem openssl s_client -connect host:443
curl --cacert /etc/pki/tls/emudhra-ca-bundle.pem https://host/
```

`/etc/pki/tls/certs/ca-bundle.crt` selbst nicht ändern, `update-ca-trust`
überschreibt die Datei. Das kombinierte Bundle nach jedem
`ca-certificates`-Update neu bauen. Der Weg wirkt nur für Programme, die
man auf das Bundle zeigen lassen kann (`SSL_CERT_FILE`, `--cacert`,
`CAfile` in der Konfiguration). Für alles andere bleibt die
Root-Sperre.

Getestet mit OpenSSL 3.5.5:

| CA-Bundle | `openssl verify` (14 Ketten) | curl / s_client |
|-----------|------------------------------|-----------------|
| nur die Roots | OK, also verwundbar | 200 |
| Roots + `emudhra-rejected.pem` | 14× certificate rejected | certificate rejected (28) |
| Systembundle + `emudhra-rejected.pem` | – | example.com 200, Testhost rejected |

Nur `-addreject serverAuth` statt `anyExtendedKeyUsage` reicht nicht.
Dann lehnt `openssl verify` ohne `-purpose` nicht mit „rejected“ ab,
sondern scheitert nur zufällig an etwas anderem.

### Netty

Netty braucht nichts Eigenes. Ohne expliziten TrustManager nimmt
`SslContextBuilder.forClient()` den Standard-TrustManager des JDKs, und
der prüft `blocked.certs`. Das gilt auch für `SslProvider.OPENSSL`
(netty-tcnative/BoringSSL): Den Handshake macht BoringSSL, die
Kettenprüfung ruft Netty trotzdem im Java-TrustManager auf. Auch mit
eigener CA-Datei per `trustManager(File)` greift die Sperre, weil Netty
daraus einen normalen PKIX-TrustManager baut.

Getestet mit Netty 4.1.118, netty-tcnative-boringssl-static 2.0.70 und
Temurin 21.0.8 mit importierter AAA-Root:

| `blocked.certs` | JDK-Provider | OPENSSL-Provider |
|-----------------|--------------|------------------|
| original | verwundbar | verwundbar |
| mit den 48 Einträgen | `Untrusted certificate` | `Untrusted certificate` |
| mit den 48 Einträgen, `trustManager(roots.pem)` | `Untrusted certificate` | `Untrusted certificate` |

Also reichen Playbook und Neustart der Anwendung. Das gilt genauso für
Reactor Netty bzw. Spring WebClient, solange kein eigener TrustManager
gesetzt ist. Nicht abgedeckt:

- `InsecureTrustManagerFactory` oder eigene TrustManager ohne PKIX. Die
  prüfen gar nicht bzw. selbst, `blocked.certs` spielt dann keine Rolle.
- Netty in einem GraalVM Native Image, siehe oben.

### Spring-Boot-Webserver und mTLS

Bisher ging es um ausgehende Verbindungen. Ein Server mit Client-Zertifikat
(mTLS, `server.ssl.client-auth=need` oder `want`) prüft dagegen
eingehende Zertifikate. Ohne eigenen Truststore läuft das über `cacerts`.
Wer einen der kompromittierten Schlüssel hat, kann sich damit ein
Client-Zertifikat unter einem der Intermediates ausstellen. Deshalb gilt
die Sperre auch hier, und das habe ich getestet.

Da die echten eMudhra-Schlüssel dafür nötig wären, lief der Test mit einer
selbst gebauten CA: Root, zwei Intermediates (eines gesperrt, eines
nicht), je ein Client-Zertifikat darunter. Der Fingerprint des gesperrten
Intermediate kam in `blocked.certs`, der Truststore des Servers enthielt
nur die Test-Root. Dann eine Spring-Boot-App mit mTLS auf jedem
eingebetteten Server, Aufruf per `curl --cert`.

Getestet mit Spring Boot 3.3.5 und Temurin 21.0.8:

| Server | gesperrtes Client-Cert | gültiges Client-Cert |
|--------|------------------------|----------------------|
| Tomcat (Standard) | abgelehnt | verbunden |
| Jetty | abgelehnt | verbunden |
| Undertow | abgelehnt | verbunden |
| Reactor Netty | abgelehnt | verbunden |
| pures JSSE `SSLServerSocket` | abgelehnt (`Untrusted certificate`) | verbunden |

Ohne den Eintrag in `blocked.certs` nehmen alle beide Zertifikate an. Die
vier eingebetteten Server prüfen eingehende Zertifikate also über den
Java-TrustManager, und der beachtet `blocked.certs`. Playbook und Neustart
reichen. Nicht abgedeckt:

- **Tomcat mit nativem OpenSSL** (`sslImplementationName` =
  `OpenSSLImplementation`, über tcnative/APR oder die Panama-FFM-Variante).
  Dann validiert OpenSSL die Kette, nicht der Java-TrustManager, und
  OpenSSL kennt `blocked.certs` nicht. In der Testumgebung ließ sich das
  nicht nachstellen, die Implementierungsklasse steckt nicht in
  `tomcat-embed-core`. Das ist aber ein bewusstes Opt-in mit
  Zusatz-Abhängigkeiten, kein Standard. Wer es nutzt, muss den
  OpenSSL-Weg absichern (Reject-Bundle, siehe OpenSSL-Abschnitt).
- **Eigener TrustManager** in der Anwendung, der nicht über PKIX prüft.

### Node.js

Node bringt einen eigenen, einkompilierten Root-Store mit. Im
eingebauten Store fehlt AAA Certificate Services, aber
emSign ECC Root CA - G3 ist drin. Node 24.21.0 hat sich im Test über die
emSign-Kette ohne Fehler verbunden, mit `https` und mit `fetch`.

Behoben in Node.js 22.23.4, 24.22.1 und 26.12.1. Das Update ist der
richtige Weg.

**`--use-system-ca` hilft nicht.** Die Option nimmt die System-Roots
*zusätzlich* zu den eingebauten. emSign ECC Root CA - G3 kommt dann
weiter aus dem eingebauten Store, auch wenn der System-Truststore schon
gepatcht ist. `NODE_USE_SYSTEM_CA=1` und
`--use-bundled-ca --use-system-ca` verhalten sich genauso. Nur
`--use-openssl-ca` ersetzt den eingebauten Store durch den des Systems.

Getestet mit Node 24.21.0 auf Ubuntu 26.04 mit gepatchtem
`ca-certificates` 20260816ubuntu0.26.04.1, dort ist emSign ECC Root CA -
G3 deaktiviert. 14 Testhosts, davon 7 über die emSign-Kette:

| Aufruf | geladene Roots | emSign ECC Root CA - G3 | verwundbar |
|--------|----------------|-------------------------|------------|
| Standard | 121 | ja | 7 von 14 |
| `--use-system-ca` | 489 | ja | 7 von 14 |
| `NODE_USE_SYSTEM_CA=1` | – | – | 7 von 14 |
| `--use-openssl-ca` | System | nein | 0 von 14 |
| Preload `tls.setDefaultCACertificates(tls.getCACertificates('system'))` | 123 | nein | 0 von 14 |

Die übrigen 7 sind die AAA-Ketten, die scheitern schon an der fehlenden
Root. example.com war in allen Fällen erreichbar.

`--use-openssl-ca` oder der Preload mit `setDefaultCACertificates` sind
damit eine Alternative zum Modul unten, aber nur, wenn der
System-Truststore gepatcht ist bzw. die Roots dort gesperrt sind (siehe
OpenSSL). Auf RHEL heißt das: erst `ca-certificates`-Update oder
Root-Sperre, dann Node umstellen. Ob die eigene Node-Version
`setDefaultCACertificates` hat (24.21.0 hat es), zeigt
`node -p "typeof require('tls').setDefaultCACertificates"`, sonst bleibt
`--use-openssl-ca`.

Bis dahin hilft [files/emudhra-block.cjs](files/emudhra-block.cjs). Das
Modul ersetzt `tls.checkServerIdentity` und lehnt jede Verbindung ab,
deren geprüfte Kette einen der acht kompromittierten Schlüssel enthält.
Es vergleicht den SHA-256 über den öffentlichen Schlüssel (SPKI), wie
Go. Acht Werte decken damit emSign-Ausstellung, Sectigo-Cross-Sign und
alle Umkodierungen ab.

```bash
sha256sum -c emudhra-block.cjs.sha256
install -m 644 emudhra-block.cjs /opt/emudhra/emudhra-block.cjs

NODE_OPTIONS="--require /opt/emudhra/emudhra-block.cjs" node app.js
```

Für eine systemd-Unit:

```ini
[Service]
Environment=NODE_OPTIONS=--require /opt/emudhra/emudhra-block.cjs
```

Ein vorhandenes `NODE_OPTIONS` ergänzen, nicht überschreiben. Das Modul
funktioniert auch bei ESM-Anwendungen.

Getestet mit Node 24.21.0:

| Aufruf | emSign-Kette | AAA-Kette | example.com |
|--------|--------------|-----------|-------------|
| ohne Modul | verwundbar | keine Root | 200 |
| mit Modul | `ERR_TLS_CERT_EMUDHRA_BLOCKED` | keine Root | 200 |
| mit Modul, AAA per `NODE_EXTRA_CA_CERTS` | `ERR_TLS_CERT_EMUDHRA_BLOCKED` | `ERR_TLS_CERT_EMUDHRA_BLOCKED` | 200 |
| `--use-openssl-ca`, `SSL_CERT_FILE` = Reject-Bundle | `INVALID_PURPOSE` | `INVALID_PURPOSE` | – |

Nicht abgedeckt sind Aufrufe mit eigener `checkServerIdentity`-Option und
natürlich `rejectUnauthorized: false`. Die letzte Zeile zeigt den
anderen Weg: Mit `--use-openssl-ca` greift das OpenSSL-Reject-Bundle aus
dem vorigen Abschnitt auch in Node.

## Quellen

- [emudhra.we-have-a-problem.com](https://emudhra.we-have-a-problem.com/), Community-Tracker
- [RHSB-2026-012](https://access.redhat.com/security/vulnerabilities/RHSB-2026-012), Red Hat Security Bulletin
- [CVE-2026-111943](https://access.redhat.com/security/cve/cve-2026-111943), Red Hat CVE-Seite
- [The eMudhra CA Compromise: Why Your JVM Is Vulnerable](https://glaforge.dev/posts/2026/10/21/the-emudhra-ca-compromise-why-your-jvm-is-vulnerable-and-how-to-fix-it/), Guillaume Laforge
- [gominimal/pkgs#949](https://github.com/gominimal/pkgs/pull/949), 48er-Liste mit Herleitung
- [JDK-8253866](https://bugs.openjdk.org/browse/JDK-8253866), Umbenennung blacklisted.certs → blocked.certs
