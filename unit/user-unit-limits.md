# Grenzen von systemd User Units

Was in User Units (`systemctl --user`) anders ist als in System Units und
worauf man beim Schreiben achten muss. Zielsystem ist RHEL 9 mit systemd 252.
Die mit *(getestet)* markierten Punkte sind auf systemd 259 nachgestellt,
die übrigen stammen aus den Manpages und dem systemd-Quellcode (Links
unten).

Grundprinzip: Jeder User mit Linger oder aktiver Session hat einen eigenen
Service-Manager (`user@<UID>.service`), der als dieser User läuft. Er sieht
nur seine eigenen Units und hat nur die Rechte dieses Users. Fast alle
Unterschiede folgen daraus.

## Kurzübersicht

| Thema | System Unit | User Unit |
|---|---|---|
| Ablage | `/etc/systemd/system/` | `~/.config/systemd/user/`, für alle User `/etc/systemd/user/` |
| Install-Ziel | `WantedBy=multi-user.target` | `WantedBy=default.target` |
| `network.target`, `network-online.target` | vorhanden | gibt es nicht |
| Abhängigkeit auf andere System-Units | ja | nein |
| Start beim Booten | immer | nur mit Linger |
| `User=`, `Group=`, `SupplementaryGroups=`, `DynamicUser=` | ja | nein |
| Ports < 1024, Capabilities | ja | nein |
| `Nice=` negativ, Realtime-Scheduling | ja | nein, `Nice=` wird ohne Fehler auf 0 gesetzt |
| `Limit*=` erhöhen | frei | nur bis zum Hard-Limit des User-Managers |
| `MemoryMax=`, `CPUQuota=`, `TasksMax=` | ja | ja |
| `IO*=`, `AllowedCPUs=` | ja | wirkungslos ohne zusätzliche Delegation |
| `IPAddressDeny=`, `IPAccounting=`, `NFTSet=` | ja | nein |
| Sandboxing (`PrivateTmp=`, `ProtectSystem=` …) | ja | nur mit unprivilegierten User-Namespaces |
| `StateDirectory=` usw. | `/var/lib`, `/var/log`, `/run` … | `~/.local/state`, `~/.cache`, `/run/user/<UID>` … |
| Umgebung | leer, aus der Unit | Umgebung des User-Managers, kein `~/.bashrc` |
| `systemctl` per `su`/`sudo -u` | ja | nein, Session-Bus fehlt |
| Journal | `journalctl -u` | `journalctl --user -u`, nur mit persistentem Journal und UID ≥ 1000 |
| `.mount`, `.swap`, `WakeSystem=` | ja | nein |

## 1. Keine Abhängigkeiten auf System-Units

Der User-Manager kennt keine System-Units. `network.target`,
`network-online.target`, `multi-user.target`, `local-fs.target`,
`remote-fs.target` und `sysinit.target` haben dort den Status `not-found`
*(getestet)*:

```bash
systemctl --user show -p LoadState network-online.target
# LoadState=not-found
```

Was mit solchen Einträgen in einer User Unit passiert *(getestet)*:

- `After=network-online.target` und `Wants=network-online.target` werden
  ohne Meldung ignoriert. Die Unit startet sofort.
- `Requires=network-online.target` lässt den Start scheitern:
  `Unit network-online.target not found.`

Das gilt für jede System-Unit. Ein `Requires=postgresql.service` in einer
User Unit sucht eine User Unit dieses Namens, nicht den System-Dienst.
Umgekehrt kann auch keine System-Unit auf eine User Unit warten. Der
Feature-Request dafür ist offen (systemd Issue #26305).

### Was beim Booten trotzdem gilt

systemd ordnet jeden `user@<UID>.service` über das Drop-in
`user@.service.d/10-login-barrier.conf` nach `systemd-user-sessions.service`
ein. Das gilt auch für systemd 252. `systemd-user-sessions.service` läuft
selbst erst nach `network.target` und `remote-fs.target`. Daraus folgt:

- Beim Start der User Units ist der Netzwerk-Dienst gestartet und die
  Netzlaufwerke aus `/etc/fstab` sind eingehängt (außer `nofail`- oder
  Automount-Einträge).
- Ob Interfaces schon eine Adresse haben oder DNS funktioniert, ist nicht
  garantiert. `network.target` heißt nur „Netzwerkverwaltung läuft“.
- Beim Herunterfahren werden die User Units gestoppt, bevor das Netzwerk
  abgebaut wird.
- Zur Reihenfolge gegenüber anderen System-Diensten (z.B. einer
  Datenbank als System-Unit) gibt es keine Zusage, weder beim Start noch
  beim Stopp.

### Auf Netzwerk oder einen System-Dienst warten

Am robustesten ist eine Anwendung, die selbst wiederholt verbindet. Dazu in
der Unit:

```ini
[Service]
Restart=on-failure
RestartSec=5s
```

Muss die Unit wirklich warten, hilft ein `ExecStartPre=` mit Timeout:

```ini
[Service]
# wartet bis zu 60 s, bis der Ziel-Host per DNS auflösbar ist
ExecStartPre=/bin/sh -c 'for i in $(seq 60); do getent hosts nexus.example.com >/dev/null && exit 0; sleep 1; done; exit 1'
```

Auf einen System-Dienst lässt sich genauso warten. `systemctl is-active`
ohne `--user` fragt den System-Manager und braucht keine Root-Rechte:

```ini
ExecStartPre=/bin/sh -c 'for i in $(seq 60); do systemctl is-active -q postgresql.service && exit 0; sleep 1; done; exit 1'
```

Ein Warten auf `systemctl is-active network-online.target` funktioniert nur,
wenn auf System-Ebene etwas dieses Target anzieht. Sonst bleibt es
`inactive`, und die Schleife läuft in den Timeout. Prüfen mit
`systemctl is-active network-online.target`.

Podman-Quadlets als User lösen das mit dem Hilfsdienst
`podman-user-wait-network-online.service`, der genau diese Prüfung macht.
Abschalten lässt er sich mit `DefaultDependencies=false` im Abschnitt
`[Quadlet]`.

## 2. `WantedBy=default.target` statt `multi-user.target`

`default.target` ist im User-Manager das Gegenstück zu `multi-user.target`.
Mit `WantedBy=multi-user.target` legt `systemctl --user enable` trotzdem
einen Symlink an und meldet nur einen Hinweis *(getestet)*:

```
Unit ... is added as a dependency to a non-existent unit multi-user.target.
```

Die Unit gilt dann als `enabled`, startet aber nie automatisch.

Weitere Targets im User-Manager: `basic.target`, `sockets.target`,
`timers.target`, `paths.target`, `shutdown.target`, `exit.target`.
`graphical-session.target` gibt es nur in Desktop-Sitzungen.

## 3. Start nur mit Linger

Ohne Linger startet der User-Manager erst beim ersten Login und stoppt nach
dem letzten Logout. Dann:

- starten die Units nicht nach einem Reboot,
- laufen Timer nur, solange jemand eingeloggt ist,
- wird `/run/user/<UID>` gelöscht, also auch alles aus `RuntimeDirectory=`.

Linger setzt root: `loginctl enable-linger <user>` (siehe
[podman/podman.md](../podman/podman.md), A4).

`OnStartupSec=` in Timern zählt ab dem Start des User-Managers, nicht ab dem
Booten.

## 4. Keine Rechte über den eigenen User hinaus

- **`User=`, `Group=`**: Nur der eigene User ist erlaubt. `User=root` endet
  mit `status=216/GROUP` und `Operation not permitted` *(getestet)*.
  `SupplementaryGroups=`, `DynamicUser=` und `PAMName=` sind laut Manpage
  nur für System-Units da. Gruppen muss root dem User direkt geben
  (`usermod -aG`), danach den User-Manager neu starten.
- **Capabilities**: `AmbientCapabilities=` kann keine Rechte verleihen, die
  der User-Manager nicht hat, und der hat keine.
- **Ports unter 1024**: Ein Dienst kann sie nicht binden. Er lauscht
  stattdessen auf einem hohen Port, den firewalld weiterleitet, oder root
  senkt `net.ipv4.ip_unprivileged_port_start`.
- **`Nice=` negativ**: Der Wert wird ohne Fehlermeldung auf den nächsten
  erlaubten Wert gesetzt, meist 0. `Nice=-5` ergibt `nice` = 0
  *(getestet)*. `CPUSchedulingPolicy=fifo|rr` und
  `IOSchedulingClass=realtime` brauchen ebenfalls Privilegien.
- **`OOMScoreAdjust=`**: `user@.service` setzt 100. User Units erben das
  und können den Wert nur erhöhen. Senken braucht `CAP_SYS_RESOURCE`.

## 5. `Limit*=` (ulimits)

`LimitNOFILE=`, `LimitMEMLOCK=` usw. können in User Units nur senken oder das
Soft-Limit bis zum Hard-Limit anheben. Das Hard-Limit des User-Managers
ist die Obergrenze. `LimitNOFILE=65536` klappt also nur, wenn der
User-Manager ein Hard-Limit von mindestens 65536 hat.

Prüfen:

```bash
systemctl --user show -p DefaultLimitNOFILE -p DefaultLimitNOFILESoft
```

Höhere Grenzen setzt root per Drop-in für den User-Manager:

```ini
# /etc/systemd/system/user@.service.d/limits.conf   (alle User)
# /etc/systemd/system/user@1001.service.d/limits.conf   (nur UID 1001)
[Service]
LimitNOFILE=1048576
```

Danach `systemctl daemon-reload` und den User-Manager neu starten
(`systemctl restart user@1001.service`, stoppt alle Units des Users).

## 6. Ressourcen-Steuerung (cgroups)

`user@.service` delegiert nur die Controller `cpu memory pids` (systemd 252
und 259, siehe `Delegate=` in der Unit) *(getestet)*:

```bash
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
# cpu memory pids
```

| Funktioniert | Wirkungslos |
|---|---|
| `MemoryMax=`, `MemoryHigh=`, `MemorySwapMax=` | `IOWeight=`, `IOReadBandwidthMax=`, `IODeviceLatencyTargetSec=` … |
| `CPUQuota=`, `CPUWeight=` | `AllowedCPUs=`, `AllowedMemoryNodes=` |
| `TasksMax=` | `DeviceAllow=`, `DevicePolicy=` |

`AllowedCPUs=0` startet ohne Fehler und lässt den Prozess trotzdem auf
allen CPUs laufen *(getestet)*. Ebenso lässt `DevicePolicy=strict` den
Zugriff auf `/dev/urandom` zu *(getestet)*. Für IO- und cpuset-Limits muss root mehr
delegieren:

```ini
# /etc/systemd/system/user@.service.d/delegate.conf
[Service]
Delegate=cpu cpuset io memory pids
```

Zusätzlich gilt für alle Prozesse eines Users zusammen das Limit von
`user-<UID>.slice` (Standard `TasksMax=33%`).

### Netzwerk-Filter

`IPAccounting=` und `NFTSet=` sind laut Manpage nur für System-Units.
`IPAddressDeny=any` wird in einer User Unit ohne Fehler angenommen und
blockiert nichts *(getestet)*. Für `IPAddressAllow=`/`IPAddressDeny=`,
`SocketBindAllow=`/`SocketBindDeny=` und `RestrictNetworkInterfaces=` gilt
deshalb: in User Units nicht darauf verlassen.

## 7. Sandboxing

Optionen, die Mount-Namespaces brauchen (`PrivateTmp=`, `ProtectSystem=`,
`ProtectHome=`, `ReadOnlyPaths=`, `PrivateDevices=`, `PrivateNetwork=`,
`ProtectKernelTunables=` …), funktionieren in User Units nur zusammen mit
`PrivateUsers=`. Neuere systemd-Versionen schalten das automatisch zu.
Voraussetzung sind unprivilegierte User-Namespaces im Kernel. Auf RHEL 9
sind sie aktiv, solange `user.max_user_namespaces` größer 0 ist (Rootless
Podman braucht das ebenfalls).

Fehlt diese Unterstützung, fallen einige Optionen ohne Fehlermeldung weg.
Auf Ubuntu mit eingeschränkten User-Namespaces (AppArmor) zeigte eine
User Unit mit `PrivateTmp=yes` weiterhin das normale `/tmp` *(getestet)*.
Deshalb auf dem Zielsystem prüfen:

```bash
systemd-run --user --wait --pipe -p PrivateTmp=yes ls -la /tmp
systemd-run --user --wait --pipe -p ProtectSystem=strict touch /opt/local/test
```

Mit `PrivateUsers=` erscheinen Dateien anderer User (auch root) im Dienst
als `nobody`. Lesen geht weiter nach den normalen Rechten.

Nur für System-Units laut Manpage: `RootImage=`, `MountImages=`,
`ExtensionImages=`, `ProcSubset=`, `ProtectProc=`, `SELinuxContext=`,
`AppArmorProfile=`, `LogNamespace=`, `LogExtraFields=`,
`LogFilterPatterns=`.

## 8. Pfade und Specifier

Die `*Directory=`-Optionen und Specifier zeigen in User Units auf
XDG-Verzeichnisse:

| Option | Specifier | System Unit | User Unit (Standard) |
|---|---|---|---|
| `RuntimeDirectory=` | `%t` | `/run` | `/run/user/<UID>` |
| `StateDirectory=` | `%S` | `/var/lib` | `~/.local/state` |
| `CacheDirectory=` | `%C` | `/var/cache` | `~/.cache` |
| `LogsDirectory=` | `%L` | `/var/log` | `~/.local/state/log` |
| `ConfigurationDirectory=` | `%E` | `/etc` | `~/.config` |
| keine | `%h` | `/root` | Home des Users |
| keine | `%u`, `%U` | `root`, `0` | Name und UID des Users |

`StateDirectory=` legt das Verzeichnis im Home an. Dienste, die nach
`/opt/local/...` schreiben sollen, bekommen den Pfad deshalb direkt in der
Unit oder in ihrer Konfiguration.

`/run/user/<UID>` ist ein tmpfs mit Größenlimit (`RuntimeDirectorySize=` in
`logind.conf`, Standard 10 % des RAM). Für Sockets und PID-Dateien reicht
das, für Daten nicht.

Suchpfade für Unit-Dateien (Auswahl, höchste Priorität zuerst):

- `~/.config/systemd/user/`: eigene Units
- `/etc/systemd/user/`: vom Admin für alle User, auch für root's
  User-Manager
- `~/.local/share/systemd/user/`
- `/usr/lib/systemd/user/`: aus Paketen

Eine Unit in `/etc/systemd/user/` aktiviert root für alle User mit
`systemctl --global enable <unit>`.

## 9. Umgebung

- Der User-Manager liest weder `~/.bashrc` noch `~/.bash_profile` oder
  `/etc/profile`. Was dort gesetzt wird (`PATH`, `JAVA_HOME`, Proxy), fehlt
  im Dienst. In `ExecStart=` absolute Pfade verwenden und Variablen per
  `Environment=` oder `EnvironmentFile=` setzen.
- Variablen für alle User Units eines Users kommen nach
  `~/.config/environment.d/*.conf` (Format `KEY=value`). Die Dateien wirken
  erst nach einem Neustart des User-Managers.
- `systemctl --user import-environment` kopiert die aktuelle Shell-Umgebung
  in den laufenden User-Manager. Das wirkt bis zu dessen Neustart und ist
  schwer nachzuvollziehen. In Skripten vermeiden.
- `PassEnvironment=` hat im User-Manager keine Wirkung, weil Dienste dessen
  Umgebung ohnehin erben.

## 10. `systemctl --user` braucht den Session-Bus

Nach `su - <user>` oder `sudo -u <user>` fehlen `XDG_RUNTIME_DIR` und
`DBUS_SESSION_BUS_ADDRESS`. `systemctl --user` meldet dann
`Failed to connect to bus`. Wege, die funktionieren:

```bash
# direkter SSH-Login als Service-User: alles gesetzt

# als root, ohne Login (braucht Paket systemd-container)
machinectl shell svc-app@
systemctl --user -M svc-app@ status app.service

# nach sudo -iu svc-app, nur mit Linger
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
```

Für Ansible mit `become_user` und `scope: user` gilt dasselbe: dort
`XDG_RUNTIME_DIR` per `environment:` setzen.

## 11. Journal

- `journalctl --user` zeigt nur dann etwas, wenn das Journal persistent ist
  (`/var/log/journal` existiert). Im flüchtigen Journal unter
  `/run/log/journal` trennt journald nicht nach User (siehe
  [podman/podman.md](../podman/podman.md), A10).
- Eine eigene Journal-Datei (`user-<UID>.journal`) gibt es nur für normale
  UIDs. Logs von System-UIDs (unter 1000 auf RHEL, z.B. nach `useradd -r`)
  landen im System-Journal, und `journalctl --user` bleibt leer. Das betrifft
  auch `checkunits.py`. Lesen kann dann nur root oder ein Mitglied von
  `systemd-journal`/`wheel`/`adm`:
  `journalctl --user-unit=app.service _UID=<UID>`.
- Service-User deshalb mit `useradd -m` (normale UID) anlegen, nicht mit
  `useradd -r`.

## 12. Timer, Mounts, sonstiges

- **`WakeSystem=`** braucht Privilegien und geht nur im System-Manager.
- **Timer** laufen nur, solange der User-Manager läuft (Linger, siehe 3).
  `Persistent=true` funktioniert und holt verpasste Läufe nach.
- **Mounts, Swap**: Der User-Manager kann nichts einhängen. `.mount`-,
  `.automount`- und `.swap`-Units gehören auf System-Ebene.
- **Socket-Aktivierung** geht, aber nur auf Ports ab 1024 und an Orten,
  an denen der User Dateien anlegen darf. Unix-Sockets gehören immer dem
  User selbst. `SocketUser=` oder `SocketGroup=` mit einem fremden User
  scheitert an den fehlenden Rechten.
- **Stopp-Timeout beim Herunterfahren**: `user@.service` hat upstream
  `TimeoutStopSec=120s` für alle Units des Users zusammen. Ubuntu kürzt
  das per Drop-in auf 5 s. Dienste mit langem Shutdown
  (PostgreSQL, RabbitMQ) können sonst per SIGKILL beendet werden. Prüfen:
  `systemctl show user@<UID>.service -p TimeoutStopUSec`.
- **SELinux**: Der User-Manager läuft im SELinux-Kontext des Users, auf
  RHEL meist `unconfined_t`. Die Policy-Domänen für System-Dienste (z.B.
  `postgresql_t`) greifen nicht, der Dienst läuft also unconfined. Das ist
  kein Fehler, aber anders als beim RPM-Dienst.

## Checkliste für neue User Units

- [ ] `WantedBy=default.target`
- [ ] keine `After=`/`Wants=`/`Requires=` auf System-Units, stattdessen
      `Restart=on-failure` oder ein `ExecStartPre=`-Warten mit Timeout
- [ ] Linger für den Service-User aktiv
- [ ] absolute Pfade in `ExecStart=`, Variablen per `Environment=` bzw.
      `EnvironmentFile=`
- [ ] kein `User=`, `Group=`, `Nice=` < 0, kein Port < 1024
- [ ] `Limit*=` nicht über dem Hard-Limit des User-Managers
- [ ] IO-/cpuset-/IP-Limits nur, wenn root delegiert hat, sonst wirkungslos
- [ ] Sandboxing-Optionen auf dem Zielsystem mit `systemd-run --user`
      geprüft
- [ ] persistentes Journal, Service-User mit UID ≥ 1000
- [ ] `TimeoutStopSec=` der Unit passt in das Stopp-Timeout von
      `user@.service`

## Quellen

- [systemd.special(7)](https://www.freedesktop.org/software/systemd/man/latest/systemd.special.html),
  Abschnitt „Units managed by the user service manager“
- [systemd.exec(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html),
  Abschnitte „User/Group Identity“, „Sandboxing“, „Process Properties“
- [systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
- [user@.service(5)](https://www.freedesktop.org/software/systemd/man/latest/user@.service.html)
- [systemd.timer(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html), `WakeSystem=`
- [systemd.unit(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html),
  Specifier und Suchpfade
- systemd v252:
  [units/user@.service.in](https://github.com/systemd/systemd/blob/v252/units/user%40.service.in),
  [units/user@.service.d/10-login-barrier.conf](https://github.com/systemd/systemd/blob/v252/units/user%40.service.d/10-login-barrier.conf),
  [units/systemd-user-sessions.service.in](https://github.com/systemd/systemd/blob/v252/units/systemd-user-sessions.service.in),
  [src/journal/journald-server.c](https://github.com/systemd/systemd/blob/v252/src/journal/journald-server.c)
  (`uid_for_system_journal()`)
- [systemd.io: Running Services After the Network Is Up](https://systemd.io/NETWORK_ONLINE/)
- [systemd Issue #26305: Enable dependencies on system units from user scope](https://github.com/systemd/systemd/issues/26305)
- [podman-systemd.unit(5)](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html),
  `podman-user-wait-network-online.service`
- [containers/podman Issue #24796: User units and network-online.target](https://github.com/containers/podman/issues/24796)
