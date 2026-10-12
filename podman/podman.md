# Podman rootless als User Service auf RHEL 9 mit /opt/local

Ziel: Ein Service-User betreibt Container mit rootless Podman als systemd
User Unit. Images, Container und Volumes liegen auf der eigenen Platte unter
`/opt/local`, die Images kommen aus Nexus.

Ausgangslage:

- RHEL 9 Standardinstallation, `/opt/local` ist ein eigenes Dateisystem (XFS)
- Service-User `svc-app` (in den Beispielen, bitte ersetzen)
- Nexus Docker-Repository unter `nexus.example.com:8443` (bitte ersetzen)
- Wir haben keinen root-Zugang. Teil A erledigt das root/Linux-Team, Teil B
  machen wir selbst als `svc-app`.

> Achtung: `scripts/install-podman.sh` setzt `graphroot` in
> `/etc/containers/storage.conf`. Das gilt nur für rootful Podman. Rootless
> Podman ignoriert diesen Wert und legt alles unter
> `~/.local/share/containers` ab, also im Home auf der Root-Platte. Für
> rootless braucht es `rootless_storage_path` (A6) oder eine eigene
> `storage.conf` im Home (B2).

---

## Teil A: Aufgaben für root

### A1. Pakete installieren

```bash
dnf install -y podman container-selinux passt slirp4netns \
               policycoreutils-python-utils
podman --version
```

`passt` liefert `pasta`, das ab Podman 5 (RHEL 9.5+) das Standard-Netzwerk
für rootless Container ist. Bei Podman 4 wird noch `slirp4netns` genutzt.

### A2. System prüfen

```bash
# User Namespaces müssen erlaubt sein (> 0). CIS-gehärtete Images setzen hier oft 0.
sysctl user.max_user_namespaces

# overlay braucht d_type, bei XFS heißt das ftype=1
xfs_info /opt/local | grep ftype

# Mount-Optionen: noexec auf /opt/local führt zu "permission denied" beim Containerstart
findmnt /opt/local

# cgroup v2 (RHEL 9 Default)
stat -fc %T /sys/fs/cgroup    # erwartet: cgroup2fs
```

### A3. Service-User mit subuid/subgid anlegen

```bash
useradd -m svc-app
grep svc-app /etc/subuid /etc/subgid
```

`useradd` trägt auf RHEL 9 automatisch 65536 subuids/subgids ein. Bei
Usern aus LDAP/IdM/AD (SSSD) passiert das nicht. Dann von Hand eintragen,
ohne Überschneidung mit vorhandenen Bereichen:

```bash
usermod --add-subuids 200000-265535 --add-subgids 200000-265535 svc-app
```

Ohne diese Einträge laufen nur Images, die ausschließlich UID 0 nutzen. Alle
anderen brechen beim Pull mit `potentially insufficient UIDs or GIDs` ab.

### A4. Linger aktivieren

```bash
loginctl enable-linger svc-app
ls /var/lib/systemd/linger/
```

Ohne Linger startet der systemd User-Manager erst beim Login und stoppt beim
Logout. Die Container laufen dann nicht nach einem Reboot und sterben, wenn
die letzte SSH-Session endet.

### A5. Storage-Verzeichnis auf /opt/local anlegen

```bash
mkdir -p /opt/local/podman/svc-app/{storage,tmp}
chown -R svc-app:svc-app /opt/local/podman/svc-app
chmod 700 /opt/local/podman/svc-app

# SELinux: gleiche Labels wie der Standard-Storage
semanage fcontext -a -e /var/lib/containers/storage /opt/local/podman/svc-app/storage
restorecon -R -v /opt/local/podman/svc-app
```

Jeder User braucht ein eigenes Verzeichnis. Zwei User dürfen sich keinen
Storage teilen.

Die Equivalence-Regel (`-e`) sorgt dafür, dass Image-Layer
`container_ro_file_t` und Volume-Inhalte `container_file_t` bekommen. Ein
pauschales `container_var_lib_t` auf das ganze Verzeichnis, wie es
`install-podman.sh` setzt, reicht dafür nicht.

Falls die Anwendung zusätzlich Bind-Mounts braucht, z.B. für Daten oder
Konfiguration:

```bash
mkdir -p /opt/local/svc-app/data
chown -R svc-app:svc-app /opt/local/svc-app
```

### A6. Rootless-Storage systemweit auf /opt/local legen (empfohlen)

In `/etc/containers/storage.conf` im Block `[storage]` ergänzen:

```toml
[storage]
driver = "overlay"
rootless_storage_path = "/opt/local/podman/$USER/storage"
```

Damit landet der Storage jedes Users automatisch auf `/opt/local`, auch wenn
jemand vergisst, B2 zu machen. Das Verzeichnis aus A5 muss pro User
existieren. Wer stattdessen nur B2 nutzt, kann diesen Schritt weglassen.

### A7. Nexus als Registry eintragen

CA-Zertifikat von Nexus systemweit vertrauen:

```bash
cp nexus-ca.crt /etc/pki/ca-trust/source/anchors/
update-ca-trust
```

Registry-Konfiguration als Drop-in, z.B.
`/etc/containers/registries.conf.d/900-nexus.conf`:

```toml
# Kurznamen wie "podman pull myapp:1.0" über Nexus auflösen
unqualified-search-registries = ["nexus.example.com:8443"]

[[registry]]
location = "nexus.example.com:8443"
# insecure = true   # nur, wenn Nexus kein gültiges Zertifikat hat

# Optional: docker.io-Pulls transparent über den Nexus Docker-Hub-Proxy leiten.
# "docker.io/library/nginx" wird dann von nexus.example.com:8443/library/nginx geholt.
[[registry]]
prefix = "docker.io"
location = "docker.io"

[[registry.mirror]]
location = "nexus.example.com:8443"
```

Auf Nexus-Seite muss das Docker-Repository (am besten ein Group-Repo aus
hosted + Docker-Hub-Proxy) über einen eigenen HTTPS-Port-Connector oder
eine Subdomain erreichbar sein. Diese Adresse kommt in `location`.

Falls auf dem Server ein HTTP-Proxy gesetzt ist: Nexus in `NO_PROXY`
eintragen, sonst laufen die Pulls über den Proxy.

### A8. Firewall und Ports

```bash
firewall-cmd --permanent --add-port=8080/tcp
firewall-cmd --reload
```

Rootless Container können keine Ports unter 1024 binden. Entweder einen
hohen Port nehmen (besser) oder die Grenze absenken:

```bash
echo 'net.ipv4.ip_unprivileged_port_start=80' > /etc/sysctl.d/90-unprivileged-ports.conf
sysctl --system
```

### A9. cgroup-Delegation (nur für CPU-/IO-Limits)

Standardmäßig bekommt ein User nur die Controller `memory` und `pids`.
`--cpus` oder `CPUQuota` greifen dann nicht. Freigeben über
`/etc/systemd/system/user@.service.d/delegate.conf`:

```ini
[Service]
Delegate=memory pids cpu cpuset io
```

```bash
systemctl daemon-reload
```

Wird nach dem nächsten Start des User-Managers aktiv (Reboot oder Linger
aus/an).

### A10. Persistentes Journal

`journalctl --user` zeigt nur dann Logs, wenn das Journal persistent ist:

```bash
ls -d /var/log/journal || {
  mkdir -p /var/log/journal
  systemd-tmpfiles --create --prefix /var/log/journal
  systemctl restart systemd-journald
}
```

### A11. Zugang für uns

Am einfachsten ist direkter SSH-Login als `svc-app` mit unserem Key, z.B.
über `scripts/create-ssh-user.sh` bzw. `~svc-app/.ssh/authorized_keys`.

Alternativ eine sudo-Regel, mit der wir zu `svc-app` wechseln dürfen:

```
%app-team ALL=(svc-app) NOPASSWD: ALL
```

Nach `sudo -iu svc-app` fehlen allerdings die Umgebungsvariablen für
`systemctl --user` (siehe B0).

---

## Teil B: Aufgaben als svc-app (ohne root)

### B0. Session prüfen

Bei direktem SSH-Login ist alles gesetzt. Nach `sudo -iu svc-app` vorher:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
```

Sonst kommt bei `systemctl --user` die Meldung `Failed to connect to bus`.
Das funktioniert nur mit aktivem Linger (A4).

### B1. Grundfunktion testen

```bash
podman info --format '{{.Host.Security.Rootless}} {{.Store.GraphRoot}}'
podman unshare cat /proc/self/uid_map     # zweite Zeile muss den subuid-Bereich zeigen
```

### B2. Storage auf /opt/local legen (falls A6 nicht gemacht wurde)

`~/.config/containers/storage.conf`:

```toml
[storage]
driver = "overlay"
graphroot = "/opt/local/podman/svc-app/storage"
```

`runroot` bleibt auf dem Default `/run/user/<uid>/containers` (tmpfs), das
ist richtig so.

Volumes liegen standardmäßig unter `<graphroot>/volumes` und ziehen damit
automatisch mit um.

Wenn vorher schon Podman als dieser User lief, liegen noch Daten im Home.
Vor dem Umstellen aufräumen, das löscht alle Images, Container und Volumes:

```bash
podman system reset
```

### B3. Temp-Verzeichnis für Image-Pulls

Podman entpackt Images beim Pull erst nach `/var/tmp`. Bei großen Images
läuft dort die Root-Platte voll. `~/.config/containers/containers.conf`:

```toml
[engine]
image_copy_tmp_dir = "/opt/local/podman/svc-app/tmp"
```

Prüfen:

```bash
podman info --format '{{.Store.GraphRoot}} {{.Store.VolumePath}}'
```

Beide Pfade müssen unter `/opt/local` liegen.

### B4. Bei Nexus anmelden

Nur nötig, wenn Nexus keine anonymen Pulls erlaubt. Am besten mit einem
technischen Read-only-Account.

```bash
podman login --authfile ~/.config/containers/auth.json nexus.example.com:8443
chmod 600 ~/.config/containers/auth.json
```

Ohne `--authfile` landet der Login in `$XDG_RUNTIME_DIR/containers/auth.json`.
Das liegt auf tmpfs und ist nach dem Reboot weg, die Units können dann keine
Images mehr ziehen. Podman liest `~/.config/containers/auth.json` beim Pull
automatisch mit.

Falls root das CA-Zertifikat nicht systemweit eingetragen hat (A7), geht es
auch nur für diesen User:

```bash
mkdir -p ~/.config/containers/certs.d/nexus.example.com:8443
cp nexus-ca.crt ~/.config/containers/certs.d/nexus.example.com:8443/ca.crt
```

Gleiches gilt für die Registry-Konfiguration: Ein Drop-in unter
`~/.config/containers/registries.conf.d/` wirkt nur für diesen User.

Test:

```bash
podman pull nexus.example.com:8443/team/app:1.2.3
```

### B5. Service als Quadlet anlegen

Quadlet ist der aktuelle Weg (Podman 4.4+, RHEL 9.2+).
`podman generate systemd` ist deprecated.

`~/.config/containers/systemd/app.container`:

```ini
[Unit]
Description=App Container
RequiresMountsFor=/opt/local

[Container]
# Immer voll qualifizierter Name mit festem Tag. Kurznamen scheitern unter
# systemd im short-name-mode "enforcing", sobald mehrere Registries in Frage kommen.
Image=nexus.example.com:8443/team/app:1.2.3
ContainerName=app
PublishPort=8080:8080

# Named Volume, liegt unter <graphroot>/volumes
Volume=app-data:/data
# oder Bind-Mount, :Z setzt das SELinux-Label
# Volume=/opt/local/svc-app/data:/data:Z

Environment=JAVA_OPTS=-Xmx512m

[Service]
Restart=always
# Erster Start inkl. Pull darf länger dauern
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

Aktivieren:

```bash
/usr/libexec/podman/quadlet -dryrun -user   # Syntax prüfen
systemctl --user daemon-reload
systemctl --user start app.service
systemctl --user status app.service
journalctl --user -u app.service -f
```

`systemctl --user enable` ist bei Quadlets nicht nötig und geht auch nicht.
Der `[Install]`-Block sorgt dafür, dass die Unit beim Boot startet.

### B6. Dateirechte bei Bind-Mounts

Rootless heißt: root im Container ist `svc-app` auf dem Host. Alle anderen
UIDs im Container werden auf den subuid-Bereich gemappt. Läuft die Anwendung
im Container z.B. als UID 1001, gehören ihre Dateien auf dem Host einer UID
wie 201000, und `svc-app` kann sie nicht direkt bearbeiten.

Zwei Wege:

```ini
# Container-User = svc-app auf dem Host
UserNS=keep-id
```

oder die Rechte im User-Namespace setzen:

```bash
podman unshare chown -R 1001:1001 /opt/local/svc-app/data
```

Named Volumes passen die Rechte beim ersten Mount selbst an.

### B7. Optional: Auto-Update und Aufräumen

```ini
# in [Container]
AutoUpdate=registry
```

```bash
systemctl --user enable --now podman-auto-update.timer
```

Alte Images sammeln sich an. Regelmäßig aufräumen:

```bash
podman image prune -a -f
```

---

## Checkliste für die Übergabe an root

| Schritt | Was | Prüfung |
|---|---|---|
| A1 | podman, container-selinux, passt installiert | `podman --version` |
| A2 | user namespaces > 0, XFS ftype=1, kein noexec | siehe A2 |
| A3 | User mit subuid/subgid | `grep svc-app /etc/subuid /etc/subgid` |
| A4 | Linger aktiv | `ls /var/lib/systemd/linger/svc-app` |
| A5 | `/opt/local/podman/svc-app` angelegt, Owner, SELinux | `ls -laZ /opt/local/podman/svc-app` |
| A6 | `rootless_storage_path` gesetzt (oder wir machen B2) | `grep rootless_storage_path /etc/containers/storage.conf` |
| A7 | Nexus-CA + Registry-Drop-in | `ls /etc/containers/registries.conf.d/` |
| A8 | Firewall-Port offen, ggf. unprivileged ports | `firewall-cmd --list-ports` |
| A9 | cgroup-Delegation (nur bei CPU-Limits) | siehe A9 |
| A10 | Journal persistent | `ls -d /var/log/journal` |
| A11 | SSH-Key oder sudo-Regel für `svc-app` | Login testen |
