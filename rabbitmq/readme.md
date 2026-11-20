# rabbitmq – Erlang bauen und RabbitMQ als non-root betreiben (RHEL 9, offline)

Eine Build-VM übersetzt Erlang/OTP einmal pro Version und legt das Ergebnis
als Tarball in Nexus ab. Die Runtime-VMs laden diesen Tarball plus den
offiziellen RabbitMQ Generic-Unix-Tarball und betreiben RabbitMQ als
systemd User Unit. Welche Repos Nexus dafür bereitstellen muss, steht in
[requirements.md](requirements.md).

```
                 Nexus
   ┌──────────────────────────────────┐
   │ rhel9-baseos / rhel9-appstream   │◄── dnf (Build-Deps, Runtime-Libs)
   │ github-releases  (raw proxy)     │◄── otp_src, rabbitmq generic-unix
   │ rabbitmq-builds  (raw hosted)    │◄── erlang-<ver>-el9-<arch>.tar.gz
   └──────────────────────────────────┘
          ▲ upload          │ download
   ┌──────┴─────┐    ┌──────▼──────┐ ┌─────────────┐ ┌─────────────┐
   │  Build-VM  │    │ Runtime-VM1 │ │ Runtime-VM2 │ │ Runtime-VM3 │
   └────────────┘    └─────────────┘ └─────────────┘ └─────────────┘
```

## Warum Erlang selbst bauen, RabbitMQ aber nicht

- **Erlang**: RHEL 9 liefert kein Erlang in BaseOS/AppStream, EPEL ist zu
  alt für aktuelle RabbitMQ-Versionen. Die RPMs von Team RabbitMQ
  installieren nach `/usr/lib64/erlang` und brauchen root. Ein eigener
  Build mit `make release` ist dagegen verschiebbar: Nach dem Entpacken
  setzt `Install -minimal <pfad>` den Installationspfad neu, der Tarball
  läuft also in jedem Home-Verzeichnis.
- **RabbitMQ**: Der Generic-Unix-Tarball enthält nur plattformunabhängigen
  BEAM-Bytecode und Shell-Skripte. Er braucht nichts außer `erl` im `PATH`
  und läuft ohne Build direkt aus dem entpackten Verzeichnis.

Der Erlang-Tarball ist an RHEL 9 und die CPU-Architektur gebunden (dynamisch
gegen `openssl-libs` 3.x, `ncurses-libs`, `libstdc++` gelinkt). Build- und
Runtime-VMs müssen beide RHEL 9 mit gleicher Architektur sein.

## Dateien

- **versions.conf** – Versionen (Default: Erlang 27.3.4.19, RabbitMQ
  4.3.6), SHA256-Pins, Nexus-URL und Repo-Namen. Jeder Wert lässt sich per
  Umgebungsvariable überschreiben.
- **requirements.md** – Anforderungen an Nexus (Repos, Artefakte,
  Rechte, Firewall).
- **install-build-deps.sh** – root, Build-VM. Installiert Compiler und
  `-devel`-Pakete.
- **build-erlang.sh** – User, Build-VM. Lädt den OTP-Quellcode (oder nimmt
  `otp_src_<OTP_VERSION>.tar.gz` aus dem Skriptordner, falls vorhanden), baut ohne
  Java/wx/ODBC, bricht ab wenn OpenSSL fehlt, testet `crypto`/`ssl`, packt
  den Tarball und lädt ihn optional nach Nexus hoch.
- **prepare-runtime.sh** – User, Runtime-VM. Prüft Laufzeit-Bibliotheken,
  Lingering, `systemctl --user`, `/opt/local/rabbitmq` und Hostname. Gibt fehlende root-Schritte aus.
- **install-rabbitmq.sh** – User, Runtime-VM. Installation, Konfiguration,
  Erlang-Cookie, systemd User Unit. Im Cluster leert es den Node vor dem
  Restart (`rabbitmq-upgrade drain`).
- **urls.txt** – Upstream-URLs von OTP-Quellcode und RabbitMQ-Tarball
  (inkl. `.asc`) für die Default-Versionen (z.B. für Firewall-Freigaben
  oder manuellen Download per `wget -i urls.txt`).

## Ablauf

### Build-VM

```bash
sudo rabbitmq/install-build-deps.sh      # einmalig
rabbitmq/build-erlang.sh                 # als Build-User, Upload am Ende bestätigen
```

Beim ersten Lauf gibt das Skript den SHA256 des OTP-Quellcodes aus. Den
Wert in `versions.conf` als `OTP_SHA256` eintragen, dann wird er ab dem
nächsten Build geprüft.

### Runtime-VMs (je VM)

```bash
rabbitmq/prepare-runtime.sh                  # als User "rabbitmq", nur Prüfung
rabbitmq/install-rabbitmq.sh                 # als User "rabbitmq"
```

Analog zum OTP-Quellcode gibt `install-rabbitmq.sh` beim ersten Lauf den
SHA256 des RabbitMQ-Tarballs aus. Den Wert in `versions.conf` als
`RABBITMQ_SHA256` eintragen.

`systemctl --user` braucht die systemd-User-Instanz des Users und deren
`XDG_RUNTIME_DIR`. Bei SSH direkt als dieser User oder mit
`machinectl shell rabbitmq@` ist beides gesetzt. Mit `su -` oder `sudo -u`
fehlt `XDG_RUNTIME_DIR`. Weil Lingering die User-Instanz dauerhaft laufen
lässt, reicht es dann, die Variable selbst zu setzen:

```bash
sudo -iu rabbitmq
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user status rabbitmq
```

Verzeichnisse unter `/opt/local/rabbitmq` (änderbar per `RABBITMQ_BASE`):

```
/opt/local/rabbitmq/
├── erlang/erlang-27.3.4.19/       + current -> erlang-27.3.4.19
├── server/rabbitmq_server-4.3.6/  + current -> rabbitmq_server-4.3.6
├── etc/rabbitmq.conf              einmalig angelegt, danach eigene Pflege
├── etc/enabled_plugins            einmalig angelegt (management, prometheus)
├── etc/conf.d/                    optionale *.conf, nach rabbitmq.conf geladen
├── etc/advanced.config            optional, wird gelesen, wenn vorhanden
├── etc/rabbitmq.env               bei jedem Lauf neu generiert
├── data/                          Mnesia/Khepri, Queues
├── log/
└── downloads/
```

`/opt/local/rabbitmq` legt root einmalig an und übergibt es dem User
(`prepare-runtime.sh` gibt den Befehl aus).

Die gesamte Konfiguration liegt unter `etc/`, nicht im entpackten
RabbitMQ-Verzeichnis. Ohne die Variablen in `rabbitmq.env` würde RabbitMQ
`conf.d/` und `advanced.config` unter
`server/rabbitmq_server-<version>/etc/rabbitmq/` suchen, und sie wären nach
einem Upgrade ohne Fehlermeldung weg.

Der Default-User `guest` darf sich nur von localhost anmelden. Für den
Zugriff von außen einen eigenen Admin anlegen:

```bash
set -a; . /opt/local/rabbitmq/etc/rabbitmq.env; set +a
rabbitmqctl add_user admin '<passwort>'
rabbitmqctl set_user_tags admin administrator
rabbitmqctl set_permissions -p / admin '.*' '.*' '.*'
rabbitmqctl delete_user guest
```

## Cluster

Voraussetzungen auf allen Nodes:

1. Identischer Erlang-Cookie: auf Node 1 `cat ~/.erlang.cookie`, auf den
   weiteren Nodes vor der Installation `ERLANG_COOKIE=<wert>` setzen:
   `ERLANG_COOKIE=... rabbitmq/install-rabbitmq.sh`
2. Kurznamen der Nodes auflösbar (DNS oder `/etc/hosts`), der Node-Name ist
   `rabbit@<hostname -s>`.
3. Ports 4369, 25672 und 35672–35682 zwischen den Nodes erreichbar.

Beitritt manuell auf Node 2 und 3:

```bash
set -a; . /opt/local/rabbitmq/etc/rabbitmq.env; set +a
rabbitmqctl stop_app
rabbitmqctl join_cluster rabbit@node1
rabbitmqctl start_app
rabbitmqctl cluster_status
```

Alternativ bildet sich der Cluster beim ersten Start von selbst
(`cluster_formation.classic_config`). Peer Discovery läuft aber nur beim
allerersten Start eines Nodes. Deshalb auf allen Nodes ohne Start
installieren, die Config anpassen und erst dann starten:

```bash
ERLANG_COOKIE=... ENABLE=n rabbitmq/install-rabbitmq.sh
# in etc/rabbitmq.conf die cluster_formation-Zeilen einkommentieren
systemctl --user enable --now rabbitmq
```

Ist ein Node schon einmal ohne diese Zeilen gestartet, hat er einen
eigenen Ein-Node-Cluster gebildet. Dann nur noch per `join_cluster`
beitreten (siehe oben).

## Upgrade

1. Neue Versionen in `versions.conf` und `urls.txt` eintragen (Kompatibilität prüfen:
   https://www.rabbitmq.com/docs/which-erlang).
2. Bei neuer Erlang-Version: `build-erlang.sh` auf der Build-VM.
3. Vor einem RabbitMQ-Minor/Major-Upgrade alle Feature Flags aktivieren:
   `rabbitmqctl enable_feature_flag all`.
4. Vor dem Upgrade auf jedem Node `data/` sichern (siehe Rollback).
5. Node für Node `install-rabbitmq.sh` ausführen. Neue Versionen werden
   neben die alten entpackt, `current` umgebogen und der Dienst neu
   gestartet. `data/` und `etc/` bleiben erhalten. In einem Cluster
   wartet das Skript vor dem Restart mit
   `rabbitmq-upgrade await_online_quorum_plus_one`, bis die Quorum
   Queues ohne diesen Node auskommen, und leert ihn mit
   `rabbitmq-upgrade drain` (Clients werden getrennt, Queue-Leader
   wandern auf andere Nodes). Nach dem Restart ist der Node von selbst
   wieder aktiv. Läuft die Wartezeit ab, bricht das Skript ohne Restart
   ab. `DRAIN=n` schaltet den Schritt ab.
6. Erst zum nächsten Node wechseln, wenn der aktuelle wieder läuft
   (`rabbitmq-diagnostics check_running`, `rabbitmqctl cluster_status`).
7. Sind alle Nodes aktualisiert, die neuen Feature Flags aktivieren:
   `rabbitmqctl enable_feature_flag all`.

Rollback: RabbitMQ unterstützt kein Downgrade, die neue Version darf das
Format in `data/` beim Start ändern. Ein Rollback heißt deshalb: Dienst
stoppen, `current`-Symlinks auf die alten Versionen zurücksetzen, `data/`
aus der Sicherung von vor dem Upgrade zurückspielen und den Dienst
starten. Nachrichten, die seit der Sicherung eingegangen sind, gehen
dabei verloren. Im Cluster gilt das für alle Nodes gemeinsam.

Sicherung von `data/` bei gestopptem Dienst. Im Cluster erst alle Nodes
stoppen, dann sichern und wieder starten, sonst passen die Stände der
Nodes nicht zusammen. Das bedeutet eine kurze Downtime.

```bash
systemctl --user stop rabbitmq
tar -C /opt/local/rabbitmq -czf ~/rabbitmq-data-$(date +%F).tar.gz data
systemctl --user start rabbitmq
```

Ohne Downtime lässt sich nur die Topologie sichern (User, vhosts, Queues,
Exchanges, Bindings, Policies), nicht die Nachrichten:
`rabbitmqctl export_definitions ~/definitions-$(date +%F).json`.
Zurückspielen in einen neu aufgesetzten Cluster mit `import_definitions`.
