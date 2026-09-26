# tekton – Build- und Publish-Pipelines auf UBI 9

Tekton-Pipelines, die die Build-VM ersetzen: Sie führen die vorhandenen
Build-Skripte unverändert in einem `registry.access.redhat.com/ubi9/ubi`-Container
aus und laden den Tarball ins Nexus Raw-Hosted-Repo hoch.

| Pipeline                      | Skript                      | Tarball                                  | Ziel in Nexus |
|-------------------------------|-----------------------------|------------------------------------------|---------------|
| `postgres-build-publish-ubi9` | `postgres/build.sh`         | `postgresql-<ver>-el9-<arch>.tar.gz`     | `postgres-builds/postgresql/<ver>/` |
| `erlang-build-publish-ubi9`   | `rabbitmq/build-erlang.sh`  | `erlang-<ver>-el9-<arch>.tar.gz`         | `rabbitmq-builds/erlang/<ver>/` |

`install.sh` bzw. `install-rabbitmq.sh` auf den Runtime-VMs bleiben wie
gehabt, sie laden den Tarball aus Nexus.

```
clone (git-clone-ubi9) ──► build (postgres-build-ubi9 | erlang-build-ubi9) ──► publish (nexus-upload-ubi9)
```

## Dateien

- **tasks/git-clone.yaml** – flacher Clone des Repos, installiert
  `git-core` per dnf.
- **tasks/postgres-build.yaml** – startet `postgres/build.sh`.
- **tasks/erlang-build.yaml** – startet `rabbitmq/install-build-deps.sh`
  (root) und danach `rabbitmq/build-erlang.sh`.
- **tasks/nexus-upload.yaml** – prüft den SHA256 und lädt Tarball und
  `.sha256` per `curl --upload-file` hoch.
- **pipelines/** – die beiden Pipelines.
- **runs/** – Beispiel-PipelineRuns.

Beide Build-Skripte verweigern root. Der Build-Step läuft deshalb als root
(Pakete installieren, `HOME` und `.netrc` vorbereiten) und startet das
Skript per `setpriv` als UID 1001. Der Upload im Skript ist abgeschaltet
(`UPLOAD=n`), das übernimmt die Publish-Task. Arbeitsverzeichnis ist
`.build/<name>/` im Workspace `source`.

## Parameter

| Parameter        | Pipeline | Default | Bedeutung |
|------------------|----------|---------|-----------|
| `git-url`        | beide    | `https://github.com/wlanboy/redhat-helpers` | Repo mit den Skripten |
| `git-revision`   | beide    | `main`  | Branch, Tag oder Commit |
| `nexus-url`      | beide    | leer    | `NEXUS_URL` |
| `publish`        | beide    | `true`  | `false` = nur bauen |
| `pg-version`     | postgres | leer    | `PG_VERSION` |
| `pg-rpm-release` | postgres | leer    | `PG_RPM_RELEASE` |
| `otp-version`    | erlang   | leer    | `OTP_VERSION` |
| `otp-sha256`     | erlang   | leer    | `OTP_SHA256` |

Leer heißt: Wert aus `versions.conf` im geklonten Repo. Repo-Namen
(`NEXUS_*_REPO`) kommen immer aus `versions.conf`.

## Voraussetzungen

Tekton Pipelines (API `tekton.dev/v1`), z.B. im kind-Cluster aus
[../kind](../kind). Das Image `registry.access.redhat.com/ubi9/ubi` muss
erreichbar sein, im Offline-Betrieb über einen Registry-Mirror für
`registry.access.redhat.com` in `kind-cluster-config.yaml`.

Die Build-Steps laufen als root im Container (dnf). Auf OpenShift braucht
der ServiceAccount der Pipeline deshalb eine SCC wie `anyuid`.

### Nexus-Zugangsdaten

Secret mit einer `.netrc`, genutzt beim Download (falls die Proxies
Anmeldung verlangen) und beim Upload (Schreibrechte auf das Raw-Hosted-Repo).
Die Run-Dateien erwarten das Secret `nexus-publish` im Namespace `tekton`
und Nexus unter `http://nexus.nexus.svc.cluster.local:8081`. Beides legt
`raspberrypi/tools/nexus.py` an, zusammen mit den Repos `pgdg-yum`,
`github-releases`, `postgres-builds`, `rabbitmq-builds` und den Rechten für
den User `tekton`.

Für einen anderen Nexus das Secret von Hand anlegen und `secretName` sowie
`nexus-url` in den Run-Dateien anpassen:

```bash
cat > netrc <<'EOF'
machine nexus.example.com login <user> password <passwort>
EOF
kubectl create secret generic nexus-netrc -n tekton --from-file=.netrc=netrc
rm netrc
```

### dnf-Repos (optional)

Im Container sind die öffentlichen UBI-Repos eingetragen. Sie enthalten alle
benötigten Pakete (`git-core`, `gcc`, `gcc-c++`, `perl`, `openssl-devel`,
`ncurses-devel`). Ohne Internetzugang eine ConfigMap mit `.repo`-Dateien
anlegen, die auf die Nexus-Proxies zeigen (siehe
[../rabbitmq/requirements.md](../rabbitmq/requirements.md)), und im
PipelineRun als Workspace `dnf-repos` einbinden. Die Dateien ersetzen dann
die UBI-Repos.

```bash
cat > rhel9-nexus.repo <<'EOF'
[rhel9-baseos]
name=RHEL 9 BaseOS (Nexus)
baseurl=https://nexus.example.com/repository/rhel9-baseos/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release

[rhel9-appstream]
name=RHEL 9 AppStream (Nexus)
baseurl=https://nexus.example.com/repository/rhel9-appstream/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release
EOF
kubectl create configmap rhel9-dnf-repos --from-file=rhel9-nexus.repo
```

Die GitHub- und PGDG-Downloads der Skripte laufen wie auf der Build-VM über
die Nexus-Proxies aus `versions.conf`.

## Installation und Start

```bash
kubectl apply -n tekton -f tekton/tasks/ -f tekton/pipelines/

kubectl create -n tekton -f tekton/runs/postgres-build-publish-run.yaml
kubectl create -n tekton -f tekton/runs/erlang-build-publish-run.yaml

tkn pipelinerun logs -n tekton -f --last
```

Der Erlang-Build braucht je nach CPU 10–30 Minuten (Request: 2 CPU, 2 GiB,
Limit 6 GiB). `make -j` nutzt alle CPUs, die `nproc` im Container sieht.

Die Tarballs sind an RHEL 9 und die Architektur des Nodes gebunden
(`uname -m` im Pod). UBI 9 hat dieselben Bibliotheksstände wie RHEL 9, der
Erlang-Tarball läuft daher auf den Runtime-VMs.

Nexus lehnt einen zweiten Upload derselben Version ab, wenn im
Raw-Hosted-Repo "Disable redeploy" gesetzt ist. Dann die Version erhöhen
oder mit `publish=false` nur bauen. Die Repos aus `nexus.py` haben die
Write-Policy `ALLOW`, dort überschreibt ein neuer Lauf den Tarball.
