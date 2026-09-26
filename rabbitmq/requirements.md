# Nexus-Anforderungen für Erlang/RabbitMQ auf RHEL 9 (offline)

Build-VM und Runtime-VMs haben keinen Internetzugang. Alles, was sie
brauchen, kommt über Nexus. Dieses Dokument beschreibt, welche Repositories
Nexus dafür bereitstellen muss.

## Übersicht

| # | Repository            | Typ          | Upstream                         | Genutzt von          |
|---|-----------------------|--------------|----------------------------------|----------------------|
| 1 | `rhel9-baseos`        | yum (proxy)  | Red Hat CDN, RHEL 9 BaseOS       | Build-VM, Runtime-VMs |
| 1 | `rhel9-appstream`     | yum (proxy)  | Red Hat CDN, RHEL 9 AppStream    | Build-VM, Runtime-VMs |
| 2 | `github-releases`     | raw (proxy)  | `https://github.com/`            | Build-VM, Runtime-VMs |
| 3 | `rabbitmq-builds`     | raw (hosted) | –                                | Build-VM schreibt, Runtime-VMs lesen |

Die Repo-Namen sind Vorschläge. Abweichende Namen werden in
[versions.conf](versions.conf) eingetragen (`NEXUS_GITHUB_REPO`,
`NEXUS_BUILDS_REPO`).

Nicht benötigt: EPEL, CodeReady Builder, die RPM-Repos von Team RabbitMQ
(Cloudsmith/packagecloud), Hex.pm. Erlang wird selbst gebaut, RabbitMQ kommt
als Generic-Unix-Tarball.

## 1. RHEL 9 Paket-Repos (yum proxy)

Falls schon ein RHEL-9-Mirror existiert (Nexus, Satellite), reicht der.
Für einen neuen Proxy auf das Red Hat CDN braucht Nexus ein
Entitlement-Client-Zertifikat, weil `cdn.redhat.com` ohne Client-Zertifikat
nichts ausliefert.

**Build-VM** braucht zusätzlich zur Basisinstallation:

| Paket          | Repo      | Wofür |
|----------------|-----------|-------|
| `gcc`          | AppStream | C-Compiler für ERTS |
| `gcc-c++`      | AppStream | BeamAsm JIT (asmjit ist C++) |
| `make`         | BaseOS    | Build |
| `perl`         | AppStream | Build-Skripte von OTP |
| `openssl-devel`| AppStream | `crypto`/`ssl`-Applikation, ohne sie kein TLS und kein RabbitMQ |
| `ncurses-devel`| AppStream | Terminal-Support für `erl` |
| `tar`, `gzip`, `xz`, `curl` bzw. `curl-minimal` | BaseOS | Download und Entpacken |

**Runtime-VMs** brauchen nur Laufzeit-Bibliotheken, die auf RHEL 9 in der
Regel schon installiert sind: `openssl-libs` (3.x), `ncurses-libs`,
`libstdc++`, `glibc`, `zlib`, dazu `tar`, `xz`, `curl`.
`prepare-runtime.sh` prüft, ob sie vorhanden sind.

## 2. GitHub Releases (raw proxy)

Ein Raw-Proxy mit Remote-URL `https://github.com/`. Die Pfade im Proxy
entsprechen den GitHub-Pfaden, z.B.

```
http://maven.big.lan/repository/github-releases/erlang/otp/releases/download/OTP-27.3.4.18/otp_src_27.3.4.18.tar.gz
```

Anforderungen an den Proxy:

- **Redirects folgen**: GitHub leitet Release-Downloads auf
  `objects.githubusercontent.com` bzw. `release-assets.githubusercontent.com`
  um. Die Outbound-Firewall von Nexus muss `github.com` und diese beiden
  Hosts per HTTPS erlauben.
- **Lange Cache-Dauer**: Release-Assets ändern sich nicht. Maximum
  Component Age auf `-1` (nie neu prüfen) ist in Ordnung.
- **Anonymer Lesezugriff** oder ein Lese-User für Build- und Runtime-VMs.

Benötigte Artefakte (Versionen aus [versions.conf](versions.conf)):

| Artefakt | Pfad im Proxy | Genutzt von |
|----------|---------------|-------------|
| Erlang/OTP Quellcode | `erlang/otp/releases/download/OTP-<OTP_VERSION>/otp_src_<OTP_VERSION>.tar.gz` | Build-VM |
| RabbitMQ Generic Unix | `rabbitmq/rabbitmq-server/releases/download/v<RABBITMQ_VERSION>/rabbitmq-server-generic-unix-<RABBITMQ_VERSION>.tar.xz` | Runtime-VMs |
| RabbitMQ Signatur | dieselbe URL + `.asc` | Runtime-VMs (optional) |

Optional, bei Bedarf:

- RabbitMQ Release Signing Key für die GPG-Prüfung. Die aktuelle Quelle
  steht unter https://www.rabbitmq.com/docs/signatures, die URL (über den
  Proxy) in `RABBITMQ_GPG_KEY_URL` eintragen.
- `rabbitmqadmin` v2 (`rabbitmq/rabbitmqadmin-ng` Releases), falls die
  HTTP-API per CLI genutzt werden soll.
- Community-Plugins (`.ez`-Dateien aus den jeweiligen GitHub-Releases).

**Falls ein Proxy auf github.com nicht erlaubt ist:** stattdessen ein
Raw-Hosted-Repo mit demselben Namen anlegen und die Dateien unter
denselben Pfaden manuell hochladen. Die Skripte merken keinen Unterschied.

## 3. Eigene Build-Artefakte (raw hosted)

Ein Raw-Hosted-Repo für die auf der Build-VM gebauten Erlang-Tarballs.
Layout:

```
rabbitmq-builds/
└── erlang/
    └── 27.3.4.18/
        ├── erlang-27.3.4.18-el9-x86_64.tar.gz
        └── erlang-27.3.4.18-el9-x86_64.tar.gz.sha256
```

Berechtigungen:

| Wer | Recht |
|-----|-------|
| Build-User (Build-VM) | `nx-repository-view-raw-rabbitmq-builds-add`, `-edit`, `-read` |
| Runtime-VMs | `-read` (oder anonym) |

Deployment Policy: **Disable redeploy**, damit ein einmal verteilter Build
nicht überschrieben wird. Neuer Build = neue Version bzw. vorher im Nexus
löschen.

Zugangsdaten für den Upload liest `curl` aus `~/.netrc` des Build-Users:

```
machine maven.big.lan login <user> password <token>
```

(`chmod 600 ~/.netrc`), alternativ fragt `build-erlang.sh` per `NEXUS_USER`
interaktiv nach dem Passwort.

## 4. TLS

Hat Nexus ein Zertifikat einer internen CA, muss die CA auf allen VMs im
System-Truststore liegen (`/etc/pki/ca-trust/source/anchors/` +
`update-ca-trust`, einmalig als root). `curl` nutzt diesen Truststore.

## Checkliste für das Nexus-Team

- [ ] yum-Proxy RHEL 9 BaseOS + AppStream erreichbar von Build- und Runtime-VMs
- [ ] Raw-Proxy `github-releases` auf `https://github.com/`, Outbound zu
      `github.com`, `objects.githubusercontent.com`,
      `release-assets.githubusercontent.com` freigeschaltet
- [ ] `otp_src_<OTP_VERSION>.tar.gz` über den Proxy abrufbar
- [ ] `rabbitmq-server-generic-unix-<RABBITMQ_VERSION>.tar.xz` (+ `.asc`) über den Proxy abrufbar
- [ ] Raw-Hosted `rabbitmq-builds` angelegt, Redeploy deaktiviert
- [ ] Deploy-User für die Build-VM, Leserecht für die Runtime-VMs
- [ ] Nexus-CA auf den VMs verteilt (falls interne CA)
