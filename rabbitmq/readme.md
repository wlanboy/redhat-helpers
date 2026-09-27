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

- **versions.conf** – Versionen (Default: Erlang 27.3.4.18, RabbitMQ
  4.3.6), SHA256-Pins, Nexus-URL und Repo-Namen. Jeder Wert lässt sich per
  Umgebungsvariable überschreiben.
- **requirements.md** – Anforderungen an Nexus (Repos, Artefakte,
  Rechte, Firewall).
- **install-build-deps.sh** – root, Build-VM. Installiert Compiler und
  `-devel`-Pakete.
- **build-erlang.sh** – User, Build-VM. Lädt den OTP-Quellcode, baut ohne
  Java/wx/ODBC, bricht ab wenn OpenSSL fehlt, testet `crypto`/`ssl`, packt
  den Tarball und lädt ihn optional nach Nexus hoch.
- **prepare-runtime.sh** – User, Runtime-VM. Prüft Laufzeit-Bibliotheken,
  Lingering, `systemctl --user`, `/opt/local/rabbitmq` und Hostname. Gibt fehlende root-Schritte aus.
- **install-rabbitmq.sh** – User, Runtime-VM. Installation, Konfiguration,
  Erlang-Cookie, systemd User Unit.

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

Wichtig: `systemctl --user` braucht eine echte Login-Session des Users (SSH
direkt als dieser User oder `machinectl shell rabbitmq@`). Mit `su -` oder
`sudo -u` fehlt `XDG_RUNTIME_DIR` und `systemctl --user` schlägt fehl.

Verzeichnisse unter `/opt/local/rabbitmq` (änderbar per `RABBITMQ_BASE`):

```
/opt/local/rabbitmq/
├── erlang/erlang-27.3.4.18/       + current -> erlang-27.3.4.18
├── server/rabbitmq_server-4.3.6/  + current -> rabbitmq_server-4.3.6
├── etc/rabbitmq.conf              einmalig angelegt, danach eigene Pflege
├── etc/enabled_plugins            einmalig angelegt (management, prometheus)
├── etc/rabbitmq.env               bei jedem Lauf neu generiert
├── data/                          Mnesia/Khepri, Queues
├── log/
└── downloads/
```

`/opt/local/rabbitmq` legt root einmalig an und übergibt es dem User
(`prepare-runtime.sh` gibt den Befehl aus).

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
3. Ports 4369, 25672 und 35672–35682 zwischen den Nodes offen
   (firewalld, als root).

Beitritt manuell auf Node 2 und 3:

```bash
set -a; . /opt/local/rabbitmq/etc/rabbitmq.env; set +a
rabbitmqctl stop_app
rabbitmqctl join_cluster rabbit@node1
rabbitmqctl start_app
rabbitmqctl cluster_status
```

Alternativ die `cluster_formation.classic_config`-Zeilen in
`etc/rabbitmq.conf` einkommentieren, bevor die Nodes zum ersten Mal
starten.

## Upgrade

1. Neue Versionen in `versions.conf` eintragen (Kompatibilität prüfen:
   https://www.rabbitmq.com/docs/which-erlang).
2. Bei neuer Erlang-Version: `build-erlang.sh` auf der Build-VM.
3. Vor einem RabbitMQ-Minor/Major-Upgrade alle Feature Flags aktivieren:
   `rabbitmqctl enable_feature_flag all`.
4. Node für Node `install-rabbitmq.sh` ausführen. Neue Versionen werden
   neben die alten entpackt, `current` umgebogen und der Dienst neu
   gestartet. `data/` und `etc/rabbitmq.conf` bleiben erhalten.

Rollback: `current`-Symlink auf die alte Version zurücksetzen und
`systemctl --user restart rabbitmq`. Funktioniert nur, solange nach dem
Upgrade keine neuen Feature Flags aktiviert wurden.
