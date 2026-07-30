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
```

## Lizenz

MIT, siehe [LICENSE](LICENSE).
