# redhat-helpers

Sammlung von Bash-Skripten zum Einrichten von RHEL 9 Servern für den Betrieb
von Containern via Podman und Kubernetes-Test-Clustern via kind.

## Inhalt

### scripts/

- **create-ssh-user.sh** – Legt interaktiv einen neuen Linux-User an und
  richtet SSH-Key-Login ein (`~/.ssh/authorized_keys` mit korrekten
  Rechten). Muss als root ausgeführt werden.
- **install-podman.sh** – Installiert Podman + podman-compose auf RHEL 9,
  konfiguriert optional eine interne Nexus/Artifactory Registry (inkl.
  insecure SSL und unqualified-search-registry) sowie ein eigenes
  Storage-Verzeichnis (graphroot) inklusive SELinux-Labeling. Muss als
  root ausgeführt werden.
- **manage-user-units.sh** – Listet alle systemd User Units
  (`systemctl --user`) durchnummeriert auf und bietet an, alle Units oder
  eine einzelne per Nummer zu starten, zu stoppen oder neu zu starten.
  Läuft im normalen User-Kontext, kein root nötig.
- **show-journal-errors.sh** – Fragt interaktiv einen Zeitraum ab (letzte
  Stunde/24h/7 Tage/seit Boot/eigene Angabe) und zeigt Journal-Einträge
  mit Priorität err oder höher: erst eine Zusammenfassung mit Fehleranzahl
  pro Unit, danach die vollständigen Log-Einträge. Braucht Lesezugriff auf
  das System-Journal, ggf. mit sudo ausführen.

### unit/

Python-Pendant zu `manage-user-units.sh` mit mehr Funktionsumfang
(enable/disable/delete, Namensfilter, Fehler-/Warnungs-Check per
Journal, Unit-Generator für Java-/Python-Anwendungen). Siehe
[unit/userunits.md](unit/userunits.md).

### rabbitmq/

Erlang/OTP auf einer Build-VM als non-root bauen und als Tarball in Nexus
ablegen, RabbitMQ (Generic-Unix-Tarball) auf mehreren Runtime-VMs als
systemd User Unit betreiben, alles offline über Nexus. Siehe
[rabbitmq/readme.md](rabbitmq/readme.md) und die Nexus-Anforderungen in
[rabbitmq/requirements.md](rabbitmq/requirements.md).

### valkey/

Valkey (fertiger jammy-Build von download.valkey.io) über einen Nexus
Raw-Proxy laden und als systemd User Unit betreiben. Siehe
[valkey/readme.md](valkey/readme.md).

### postgres/

PostgreSQL aus den fertigen PGDG-RPMs für RHEL 9: Build-VM prüft die
Signatur und legt einen verschiebbaren Tarball in Nexus ab, die Runtime-VMs
entpacken ihn als User (ohne dnf) und betreiben ihn als systemd User Unit,
inklusive Anleitung für Minor- und Major-Upgrades. Siehe
[postgres/readme.md](postgres/readme.md).

### valkeycluster/

Valkey im Cluster-Modus mit mehreren Instanzen pro VM
(`valkey-cluster@<port>` User Units), Primaries und Replicas über mehrere
VMs verteilt. Siehe [valkeycluster/readme.md](valkeycluster/readme.md).

### kind/

- **install-kind.sh** – Richtet einen kind-Cluster (Kubernetes IN Docker)
  mit Podman als Runtime ein. Prüft Voraussetzungen (Podman, cgroup v2),
  installiert kind/kubectl bei Bedarf (offline aus lokalem Binary oder per
  Download) und erstellt optional den Cluster aus
  `kind-cluster-config.yaml`. Muss als root ausgeführt werden.
- **kind-cluster-config.yaml** – Cluster-Konfiguration mit
  Registry-Mirror-Einträgen (Nexus/Artifactory) für Docker Hub,
  registry.k8s.io und Quay. Wird nicht generiert, sondern manuell gepflegt.
- **kind-linux-amd64** – Lokales kind-Binary für die Offline-Installation.

## Voraussetzungen

- RHEL 9
- root-Rechte für alle Skripte

## Nutzung

```bash
# 1. SSH-User anlegen
sudo scripts/create-ssh-user.sh

# 2. Podman installieren und konfigurieren
sudo scripts/install-podman.sh

# 3. kind-Cluster mit Podman-Provider aufsetzen
sudo kind/install-kind.sh

# 4. User Units verwalten (start/stop/restart)
scripts/manage-user-units.sh

# 5. Journal nach Fehlern durchsuchen
scripts/show-journal-errors.sh
```

## Lizenz

MIT, siehe [LICENSE](LICENSE).
