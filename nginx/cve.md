# nginx: Sicherheitsupdate vom 14.10.2026

Stand: 26.10.2026

Am 14.10.2026 hat nginx die Versionen 1.30.6 (stable) und 1.31.7
(mainline) veröffentlicht. Sie schließen fünf Lücken. Alle Versionen
davor sind betroffen, die Fehler greifen aber nur bei bestimmten
Konfigurationen. Ob ein Server angreifbar ist, entscheidet deshalb die
Konfiguration, nicht allein die Versionsnummer.

## Übersicht

| CVE | Problem | Voraussetzung | Betroffen | CVSS 3.1 |
|-----|---------|---------------|-----------|----------|
| CVE-2026-15176 | Buffer Overflow | `alias` + `rewrite` + `$request_filename` | 0.1.29–1.31.6 | 8.1 |
| CVE-2026-93479 | Buffer Overflow | `map` mit Regex-Captures in `root` oder Log-Format | 1.30.0–1.30.5, 1.31.2–1.31.6 | 8.1 |
| CVE-2026-97205 | Use-after-free | `aio` + `ssi` mit `include virtual` | 0.8.11–1.31.6 | 8.1 |
| CVE-2026-97629 | Session-Fälschung | `ssl_session_cache shared` ohne `ssl_session_ticket_key` | 1.23.2–1.31.6 | 6.5 |
| CVE-2026-95101 | Request-Body-Injection | HTTP/3 (`listen ... quic`) | 1.25.0–1.31.6 | 5.3 |

Behoben in **1.30.6** und **1.31.7**. Die Mainline-Version 1.31.8 vom
21.10.2026 enthält keine weiteren Sicherheitsfixes.

nginx selbst stuft alle fünf als "medium" ein. F5 vergibt in den
CVE-Einträgen für die drei Speicherfehler CVSS 3.1 8.1 und CVSS 4.0 9.2,
Red Hat bewertet CVE-2026-97205 als "Important". Der Unterschied kommt
daher, dass nginx die Konfigurationsvoraussetzung einrechnet, CVSS nicht.

## Die Lücken im Einzelnen

### CVE-2026-15176: alias mit rewrite

Ein Buffer Overflow im Worker, wenn eine `location` mit `alias` arbeitet,
die Request-URI vorher per `rewrite` geändert wurde und danach in einen
Dateipfad umgesetzt wird. Ausgelöst wird das über `$request_filename`,
etwa in `set`, `return` oder einem `log_format`.

```nginx
location /static/ {
    alias /srv/www/;
    rewrite ^/static/old/(.*)$ /static/new/$1 break;
    set $file $request_filename;            # Auswertung -> Overflow
}
```

Sicher ist ein Absturz des Workers. Laut F5 ist Codeausführung möglich.
Der Fehler steckt seit 0.1.29 im Code.

Workaround bis zum Update: `$request_filename` in `alias`-Locations nicht
verwenden, oder `alias` durch `root` ersetzen.

### CVE-2026-93479: Ausdrücke mit Variablen (map/regex)

Ein Buffer Overflow beim Auswerten von Zeichenketten mit Variablen. Das
betrifft die Berechnung von `root` und das Access-Log, in http und
stream. Konkret: Ein `map` mit Regex und unbenannten Captures (`$1`),
deren Werte in `root` oder einem `log_format` vor der Ausgabevariable des
`map` stehen.

Der Fehler ist mit dem Fix für CVE-2026-42533 (Juli 2026, nginx 1.31.3
bzw. 1.30.4) entstanden. Deshalb sind nur 1.30.0–1.30.5 und 1.31.2–1.31.6
betroffen. Wer noch auf einer älteren Version ohne den Juli-Fix sitzt,
hat stattdessen CVE-2026-42533 offen und muss ohnehin aktualisieren.

Auswirkung wie bei CVE-2026-15176: Worker-Absturz, laut F5 eventuell
Codeausführung.

Workaround: im `map` benannte Captures (`(?<name>...)`) statt `$1`
verwenden.

### CVE-2026-97205: AIO mit SSI

Use-after-free im Worker, wenn asynchrones File-I/O (`aio on` oder
`aio threads`) aktiv ist und eine SSI-Seite `<!--# include virtual=... -->`
enthält. Beides muss im selben Kontext greifen. Ein nicht
authentifizierter Client kann das von außen auslösen, sobald er eine
solche Seite abrufen kann. Betroffen seit 0.8.11.

Red Hat: "Important", CVSS 8.1
(`AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H`). Beide Einstellungen sind nicht
Standard, und ASLR erschwert unter RHEL eine zuverlässige
Codeausführung.

Workaround: `aio` oder `ssi` abschalten.

### CVE-2026-97629: TLS Session Tickets

Bei der automatischen Rotation der Session-Ticket-Schlüssel bleibt einer
der Schlüssel uninitialisiert. Damit lässt sich eine TLS-Session fälschen
und wiederaufnehmen. F5 nennt das "nicht deterministisch", der Angreifer
kann es also nicht gezielt herbeiführen.

Die automatische Rotation läuft, wenn `ssl_session_cache shared:...`
gesetzt ist und kein `ssl_session_ticket_key`. Das ist eine verbreitete
Konfiguration.

Gefährlich wird es bei mTLS (`ssl_verify_client on`): Eine gefälschte
Session kann die Prüfung des Client-Zertifikats umgehen. Ohne
Client-Zertifikate ist der Schaden deutlich kleiner.

Red Hat: CVSS 6.5 (`AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:L/A:N`).

Workaround, einer von beiden:

```nginx
ssl_session_tickets off;
```

```nginx
ssl_session_ticket_key /etc/nginx/ticket.key;   # 80 Byte, z. B. openssl rand 80
```

Nach dem Update die Ticket-Schlüssel trotzdem wechseln. Vorher
gefälschte Tickets bleiben sonst gültig. Automatisch erzeugte Schlüssel
liegen im Shared-Memory-Cache, der ein `reload` überlebt, also nginx
komplett neu starten. Mit `ssl_session_ticket_key` eine neue
Schlüsseldatei einspielen.

### CVE-2026-95101: HTTP/3 Request-Body-Injection

Bei HTTP/3 kann ein Teil eines unbekannten Frames als Request-Body
behandelt werden. Folgen: `client_max_body_size` lässt sich umgehen, und
ein Client kann Daten in den Request einschleusen, den nginx an das
Backend weiterreicht. Das greift mit `proxy_request_buffering on`, dem
Standard. Betroffen seit 1.25.0, also seit HTTP/3 im Mainline-Code ist.

Workaround: `quic`-Listener vorübergehend entfernen. Clients fallen dann
auf HTTP/2 zurück.

## Prüfen

Version:

```bash
nginx -v
```

Die geladene Konfiguration komplett ausgeben, inklusive aller
`include`-Dateien. Ein `grep` über `/etc/nginx` allein übersieht
generierte oder eingebundene Dateien.

```bash
nginx -T 2>/dev/null > /tmp/nginx-full.conf

# CVE-2026-15176: alias und $request_filename in derselben location?
grep -nE '^\s*(location|alias|rewrite)\b|\$request_filename' /tmp/nginx-full.conf

# CVE-2026-93479: map mit Regex und unbenannten Captures
grep -nE -A10 '^\s*map\s' /tmp/nginx-full.conf | grep -E '~|\$[0-9]'

# CVE-2026-97205: aio und ssi
grep -nE '^\s*(aio|ssi)\s' /tmp/nginx-full.conf

# CVE-2026-97629: Session-Cache ohne Ticket-Key, mTLS
grep -nE 'ssl_session_(cache|tickets|ticket_key)|ssl_verify_client' /tmp/nginx-full.conf

# CVE-2026-95101: HTTP/3
grep -nE 'quic|http3' /tmp/nginx-full.conf
```

Die Treffer muss man von Hand bewerten. Für CVE-2026-15176 und
CVE-2026-97205 zählt, ob die Direktiven im selben `server`- bzw.
`location`-Block wirken.

## Red Hat

Red Hat liefert nginx als AppStream-Modul bzw. -Paket aus und portiert
Fixes zurück, die Versionsnummer bleibt dabei meist alt. `nginx -v` sagt
dort also nichts über den Patchstand. Maßgeblich sind die Errata:

```bash
dnf updateinfo list --cve CVE-2026-97205
rpm -q --changelog nginx | grep -E 'CVE-2026-(15176|93479|95101|97629|97205)'
```

Den aktuellen Status je RHEL-Version zeigen die CVE-Seiten, z. B.
<https://access.redhat.com/security/cve/CVE-2026-97205>.

## Vorher: CVE-2026-90439 (15.09.2026)

Einen Monat davor kam 1.30.5/1.31.6 mit einem Fix für einen Heap
Overflow in `ngx_http_v3_module` (CVE-2026-90439). Betroffen sind
1.29.2–1.31.5, nur mit HTTP/3 und OpenSSL 3.5.0 oder älter. Wer jetzt
auf 1.30.6 bzw. 1.31.7 geht, hat diesen Fix mit drin.

## Quellen

- [nginx security advisories](https://nginx.org/en/security_advisories.html)
- [nginx CHANGES](https://nginx.org/en/CHANGES)
- [nginx news 2026](https://nginx.org/2026.html)
- [F5 K000163408: Out-of-band Security Notification (14.10.2026)](https://my.f5.com/manage/s/article/K000163408)
- F5-Advisories je CVE:
  [K000162416](https://my.f5.com/manage/s/article/K000162416) (15176),
  [K000162607](https://my.f5.com/manage/s/article/K000162607) (93479),
  [K000162613](https://my.f5.com/manage/s/article/K000162613) (95101),
  [K000162606](https://my.f5.com/manage/s/article/K000162606) (97629),
  [K000162608](https://my.f5.com/manage/s/article/K000162608) (97205)
- Red Hat: [CVE-2026-97205](https://access.redhat.com/security/cve/CVE-2026-97205),
  [CVE-2026-97629](https://access.redhat.com/security/cve/CVE-2026-97629)
