# ansible-pull: Playbook bei neuem Tag oder Release-Branch ausführen

Ziel: Eine VM holt sich ihre Konfiguration selbst aus Git. Sobald im Repo ein
neuer Release-Tag (`v1.4.0`) oder ein neuer Release-Branch (`release/1.4`)
auftaucht, führt sie ein bestimmtes Playbook aus. Kein zentraler
Ansible-Controller, kein SSH von außen auf die VM.

Ausgangslage:

- RHEL 9, Service-User `svc-app` mit Linger (wie in
  [podman/podman.md](../podman/podman.md))
- Git-Repo unter `git@gitlab.example.local:ops/app-deploy.git` (bitte ersetzen)
- Die VM darf das Repo lesen (Deploy-Key oder HTTPS-Token)

---

## 1. Was ansible-pull kann und was nicht

`ansible-pull` dreht das normale Ansible-Modell um. Es läuft auf der Ziel-VM
selbst, klont ein Repo nach `--directory`, checkt `--checkout` aus und führt
darin ein Playbook gegen `localhost` aus.

Die wichtigsten Optionen:

| Option | Bedeutung |
|---|---|
| `-U`, `--url` | Git-Repo mit den Playbooks |
| `-C`, `--checkout` | Branch, Tag oder Commit, der ausgecheckt wird |
| `-d`, `--directory` | Lokales Arbeitsverzeichnis für den Clone |
| `-i` | Inventory, für lokale Läufe `localhost,` |
| `-o`, `--only-if-changed` | Playbook nur ausführen, wenn sich der Checkout geändert hat |
| `--clean` | Lokale Änderungen im Clone verwerfen |
| `--accept-host-key` | SSH-Hostkey des Git-Servers beim ersten Mal annehmen |
| `--vault-password-file` | Vault-Passwort für verschlüsselte Variablen |
| `-e` | Extra-Variablen, wie bei `ansible-playbook` |
| `playbook.yml` | Playbook relativ zum Repo-Root. Fehlt es, sucht ansible-pull `<fqdn>.yml`, `<hostname>.yml`, dann `local.yml` |

Der Haken: `-C` nimmt genau einen festen Namen. ansible-pull hat keine
Option für "neuester Tag, der auf `v*` passt" oder "neuester Branch unter
`release/`". Und `-o` erkennt nur neue Commits auf dem Branch, der schon
in `-C` steht, einen neu angelegten Tag oder Branch sieht es nicht.

Darum braucht es einen kleinen Wrapper davor:

1. `git ls-remote` listet die Tags bzw. Branches im Repo, ohne zu klonen.
2. Daraus wird per Versionssortierung (`sort -V`) der neueste gewählt.
3. Ref und Commit-SHA werden mit dem zuletzt ausgerollten Stand verglichen.
4. Nur bei einer Änderung läuft `ansible-pull -C <ref> <playbook>`.
5. Nach erfolgreichem Lauf wird der neue Stand gespeichert.

Ein systemd Timer ruft den Wrapper regelmäßig auf.

---

## 2. Voraussetzungen auf der VM

Als root bzw. vom Linux-Team:

```bash
dnf install -y ansible-core git
loginctl enable-linger svc-app
```

`ansible-pull` ist Teil von `ansible-core`. Collections, die das Playbook
braucht, kommen in eine `requirements.yml` im Repo oder werden vorab als
`svc-app` installiert (`ansible-galaxy collection install -r ...`).

Als `svc-app` den Zugriff auf das Repo testen:

```bash
git ls-remote --tags git@gitlab.example.local:ops/app-deploy.git
```

Bei SSH muss der Hostkey des Git-Servers in `~/.ssh/known_hosts` stehen,
sonst hängt der erste Lauf im Timer. Einmal interaktiv verbinden oder
`ssh-keyscan gitlab.example.local >> ~/.ssh/known_hosts`.

---

## 3. Der Wrapper: ansible-pull-ref.sh

Das Skript liegt unter [ansible-pull-ref.sh](ansible-pull-ref.sh) und kommt
nach `~/bin/`:

```bash
install -m 0755 ansible-pull-ref.sh ~/bin/ansible-pull-ref.sh
```

Konfiguriert wird es über Umgebungsvariablen:

| Variable | Default | Bedeutung |
|---|---|---|
| `REPO_URL` | (Pflicht) | Repo, in dem nach Tags/Branches gesucht wird |
| `MODE` | `tag` | `tag` oder `branch` |
| `REF_PATTERN` | `v*` bzw. `release/*` | Muster für `git ls-remote` |
| `REF_EXCLUDE` | leer | Regex für Refs, die ignoriert werden, z.B. `-(rc\|beta)[0-9]*$` |
| `PLAYBOOK` | (Pflicht) | Playbook relativ zum Repo-Root |
| `STATE_NAME` | `$MODE` | Name der State-Datei, je Instanz eindeutig |
| `PULL_URL` | `$REPO_URL` | Repo, das ansible-pull klont (siehe Abschnitt 6) |
| `PULL_CHECKOUT` | gefundener Ref | Checkout für ansible-pull (siehe Abschnitt 6) |
| `ANSIBLE_PULL_ARGS` | leer | Weitere Optionen, z.B. `--vault-password-file ~/.vault-pass` |

Der zuletzt ausgerollte Stand steht in
`~/.local/state/ansible-pull/<STATE_NAME>.last` als `<ref> <sha>`. Weil auch
die SHA verglichen wird, löst ein neuer Commit auf dem aktuellen
Release-Branch ebenfalls einen Lauf aus. Ein Tag, der nachträglich auf einen
anderen Commit verschoben wird, auch.

An das Playbook übergibt das Skript drei Variablen:

- `release_kind`: `tag` oder `branch`
- `release_ref`: z.B. `v1.4.0` oder `release/1.4`
- `release_sha`: Commit-SHA

Schlägt das Playbook fehl, wird der State nicht geschrieben. Der nächste
Timer-Lauf versucht es erneut.

Manuell testen:

```bash
REPO_URL=git@gitlab.example.local:ops/app-deploy.git \
MODE=tag REF_PATTERN='v*' PLAYBOOK=playbooks/deploy-release.yml \
~/bin/ansible-pull-ref.sh
```

---

## 4. Playbooks im Repo

Beispielstruktur:

```text
app-deploy/
├── playbooks/
│   ├── deploy-release.yml    # bei neuem Tag v*
│   └── deploy-staging.yml    # bei neuem Branch release/*
├── roles/
└── requirements.yml
```

`playbooks/deploy-release.yml`:

```yaml
- name: Release ausrollen
  hosts: localhost
  connection: local
  gather_facts: true

  tasks:
    - name: Ausgerollten Stand anzeigen
      ansible.builtin.debug:
        msg: "Rolle {{ release_kind }} {{ release_ref }} ({{ release_sha }}) aus"

    - name: Version für die Anwendung ablegen
      ansible.builtin.copy:
        dest: "{{ ansible_env.HOME }}/app/VERSION"
        content: "{{ release_ref }}\n"
        mode: "0644"

    # ab hier die eigentlichen Schritte, z.B. Quadlet-Datei schreiben,
    # systemd --user daemon-reload, Unit neu starten
```

Wichtig: ansible-pull checkt den gefundenen Tag bzw. Branch aus und nimmt
das Playbook aus genau diesem Stand. Es muss also in jedem Release-Tag
vorhanden sein. Alte Tags ohne das Playbook schlagen fehl. Wer das nicht
will, trennt Playbooks und Anwendung, siehe Abschnitt 6.

---

## 5. systemd User Units

Eine Template-Unit für beide Fälle, die Instanz (`%i`) wählt die
Konfigurationsdatei.

`~/.config/systemd/user/ansible-pull@.service`:

```ini
[Unit]
Description=ansible-pull für %i

[Service]
Type=oneshot
EnvironmentFile=%h/.config/ansible-pull/%i.env
Environment=STATE_NAME=%i
ExecStart=%h/bin/ansible-pull-ref.sh
```

`~/.config/systemd/user/ansible-pull@.timer`:

```ini
[Unit]
Description=ansible-pull für %i regelmäßig prüfen

[Timer]
OnBootSec=2min
OnCalendar=*:0/5
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
```

`RandomizedDelaySec` verteilt die Abfragen, wenn viele VMs denselben Timer
haben. Ein Timer `ansible-pull@X.timer` startet automatisch
`ansible-pull@X.service`.

Konfiguration für neue Tags, `~/.config/ansible-pull/release-tag.env`:

```bash
REPO_URL=git@gitlab.example.local:ops/app-deploy.git
MODE=tag
REF_PATTERN=v*
REF_EXCLUDE='-(rc|beta)[0-9]*$'
PLAYBOOK=playbooks/deploy-release.yml
```

Konfiguration für neue Release-Branches, `~/.config/ansible-pull/release-branch.env`:

```bash
REPO_URL=git@gitlab.example.local:ops/app-deploy.git
MODE=branch
REF_PATTERN=release/*
PLAYBOOK=playbooks/deploy-staging.yml
```

In der Regel bekommt eine VM nur eine der beiden Instanzen: Produktion
reagiert auf Tags, Staging auf Release-Branches.

Aktivieren und prüfen:

```bash
systemctl --user daemon-reload
systemctl --user enable --now ansible-pull@release-tag.timer

systemctl --user list-timers 'ansible-pull@*'
journalctl --user -u ansible-pull@release-tag.service -f
```

Sofort einen Lauf auslösen:

```bash
systemctl --user start ansible-pull@release-tag.service
```

Den aktuellen Stand erneut ausrollen erzwingen:

```bash
rm ~/.local/state/ansible-pull/release-tag.last
systemctl --user start ansible-pull@release-tag.service
```

---

## 6. Variante: Playbooks in einem eigenen Repo

Oft liegt die Anwendung in einem Repo und die Deploy-Playbooks in einem
anderen. Dann beobachtet der Wrapper das Anwendungs-Repo, klont aber das
Deploy-Repo auf einem festen Branch und gibt den gefundenen Ref nur als
Variable weiter:

```bash
REPO_URL=git@gitlab.example.local:dev/app.git
MODE=tag
REF_PATTERN=v*
PULL_URL=git@gitlab.example.local:ops/app-deploy.git
PULL_CHECKOUT=main
PLAYBOOK=playbooks/deploy-release.yml
```

Das Playbook holt dann das Artefakt passend zu `release_ref`, z.B.
`app-{{ release_ref }}.jar` aus Nexus oder das Image
`nexus.example.com:8443/app:{{ release_ref }}`.

Vorteil: Ein Fix am Playbook wirkt auch für alte Releases, und der
Anwendungs-Tag muss keine Ansible-Dateien enthalten.

---

## 7. Hinweise

- **Sortierung:** `sort -V` sortiert `v1.10.0` korrekt nach `v1.9.0` und
  `release/1.10` nach `release/1.9`. Gemischte Schemata (`v1.2` und
  `1.3`) im selben Muster vermeiden.
- **Pre-Releases:** Ohne `REF_EXCLUDE` wird `v1.5.0-rc1` ausgerollt.
  Schlimmer: `sort -V` sortiert `v1.5.0-rc1` hinter `v1.5.0`, ein
  RC-Tag würde also auch das fertige Release verdrängen. Sobald es
  RC-Tags gibt, `REF_EXCLUDE` setzen.
- **Einfache Anführungszeichen in der `.env`:** systemd liest die Datei
  selbst, nicht die Shell. Werte mit `|` oder `$` wie bei `REF_EXCLUDE`
  in `'...'` setzen.
- **Rollback:** Der Wrapper rollt immer den höchsten Ref aus. Wird der
  fehlerhafte Tag gelöscht, ist der vorherige wieder der höchste und wird
  beim nächsten Lauf ausgerollt. Alternativ einen neuen, höheren Tag auf
  den alten Commit setzen.
- **root-Rechte:** Läuft der Timer als `svc-app`, kann das Playbook nur
  in dessen Rechten arbeiten (Home, User Units, rootless Podman). Für
  Aufgaben mit `become` müsste der Timer als System-Unit unter root laufen.
- **Secrets:** Vault-Passwort als Datei mit Rechten `0600` ablegen und in
  der `.env` mit vollem Pfad übergeben, z.B.
  `ANSIBLE_PULL_ARGS=--vault-password-file /home/svc-app/.vault-pass`.
  `%h` und `~` werden dort nicht ersetzt.
- **Push statt Pull:** Wenn ein Ansible-Controller oder GitLab Runner die
  VMs ohnehin per SSH erreicht, ist eine CI-Pipeline mit
  `rules: - if: $CI_COMMIT_TAG` meist einfacher. ansible-pull lohnt sich,
  wenn die VMs nicht von außen erreichbar sein sollen.
