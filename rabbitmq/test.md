# Test im Docker-Container (UBI 9)

Beschreibt, wie `build-erlang.sh` und der Betrieb von RabbitMQ mit dem
gebauten Erlang lokal in einem `registry.access.redhat.com/ubi9/ubi`
Container getestet wurden, ohne Build-VM, Runtime-VM oder Nexus.

## Was der Test abdeckt

| Schritt | Getestet | Wie |
|---------|----------|-----|
| Build-Abhängigkeiten | ja | `dnf install` im Container (entspricht `install-build-deps.sh`) |
| `build-erlang.sh` als non-root | ja | unverändertes Skript als User `builder` |
| Download über Nexus | nein | GitHub direkt statt Nexus-Proxy |
| Upload nach Nexus | nein | `UPLOAD=n` |
| Erlang in anderen Pfad verschieben | ja | Tarball als zweiter User `runner` entpackt, `Install -minimal` |
| RabbitMQ Generic-Unix mit diesem Erlang | ja | manuell gestartet mit `rabbitmq-server -detached` |
| `install-rabbitmq.sh` | nein | Container hat kein systemd, User Unit nicht testbar |
| `prepare-runtime.sh` | nein | braucht `loginctl`/firewalld |
| Cluster | nein | nur ein Node |

Der Runtime-Teil bildet nach, was `install-rabbitmq.sh` tut (Entpacken,
`Install -minimal`, `PATH`, `RABBITMQ_MNESIA_BASE`/`RABBITMQ_LOG_BASE`),
ruft das Skript aber nicht auf.

## Vorbereitung

Der Test läuft auf einer Kopie des Verzeichnisses, damit `versions.conf`
im Repo unverändert bleibt. In der Kopie zeigt der GitHub-Proxy direkt auf
`https://github.com`, weil lokal kein Nexus da ist. Die Pfade sind
identisch, nur die Basis-URL ändert sich.

```bash
TEST=$(mktemp -d)
cp -r rabbitmq "$TEST/"
sed -i 's|^NEXUS_GITHUB=.*|NEXUS_GITHUB="https://github.com"|' "$TEST/rabbitmq/versions.conf"
```

## Testskript

`$TEST/run.sh`, läuft im Container als root und wechselt für Build und
Betrieb auf zwei getrennte non-root User:

```bash
set -euo pipefail

# Build-Abhängigkeiten (entspricht install-build-deps.sh)
dnf install -y -q gcc gcc-c++ make perl openssl-devel ncurses-devel tar gzip xz procps-ng hostname >/dev/null 2>&1

# 1. Build als non-root User
useradd -m builder
su - builder -c 'UPLOAD=n /work/rabbitmq/build-erlang.sh' 2>&1 | tail -15

# 2. Tarball an einen zweiten User übergeben (/home/builder ist 0700)
useradd -m runner
cp /home/builder/build/erlang/dist/*.tar.gz /tmp/ && chmod 644 /tmp/erlang-*.tar.gz
cp /tmp/erlang-*.tar.gz /work/          # Tarball für weitere Tests aufheben
T=$(ls /tmp/erlang-*.tar.gz)
ls -la $T

# 3. Als runner in einen anderen Pfad entpacken, verschieben, RabbitMQ starten
su - runner -c "set -e; mkdir -p x/erl x/srv
tar -C x/erl -xzf $T && cd x/erl/erlang-* && ./Install -minimal \$PWD >/dev/null && cd
curl -fsSL -o r.tar.xz https://github.com/rabbitmq/rabbitmq-server/releases/download/v4.3.6/rabbitmq-server-generic-unix-4.3.6.tar.xz
tar -C x/srv -xJf r.tar.xz
export PATH=\$(echo \$HOME/x/erl/erlang-*/bin):\$HOME/x/srv/rabbitmq_server-4.3.6/sbin:\$PATH
export RABBITMQ_MNESIA_BASE=\$HOME/data RABBITMQ_LOG_BASE=\$HOME/log
rabbitmq-server -detached
sleep 5
rabbitmqctl -q await_startup --timeout 120
rabbitmq-diagnostics -q status | head -20
rabbitmqctl -q list_queues"
```

Der Build-Pfad (`/home/builder/build/erlang/release/...`) und der
Laufzeit-Pfad (`/home/runner/x/erl/...`) unterscheiden sich absichtlich.
Nur so zeigt der Test, dass `Install -minimal` den Tarball wirklich
verschiebbar macht.

## Ausführen

```bash
docker run --rm -v "$TEST":/work:Z registry.access.redhat.com/ubi9/ubi:latest \
    bash /work/run.sh > "$TEST/out.log" 2>&1; echo "exit=$?" >> "$TEST/out.log"
tail -40 "$TEST/out.log"
```

Mit Podman genauso (`podman run ...`). Laufzeit etwa 5–10 Minuten, der
größte Teil ist `make` von Erlang/OTP.

## Ergebnis

Smoke-Test aus `build-erlang.sh`:

```
== Smoke-Test ==
OTP 27, ERTS 15.2.7.13, JIT: jit, OpenSSL 3.5.8 25 Aug 2026
== Packe Tarball ==

Erzeugt:
  /home/builder/build/erlang/dist/erlang-27.3.4.18-el9-x86_64.tar.gz
  /home/builder/build/erlang/dist/erlang-27.3.4.18-el9-x86_64.tar.gz.sha256
```

Der Tarball ist rund 60 MB groß. RabbitMQ als `runner` aus dem verschobenen
Erlang:

```
RabbitMQ version: 4.3.6
Node name: rabbit@ff287f06a37b
Erlang configuration: Erlang/OTP 27 [erts-15.2.7.13] [source] [64-bit] [smp:16:16] [ds:16:16:10] [async-threads:1] [jit:ns]
Crypto library: OpenSSL 3.5.8 25 Aug 2026
exit=0
```

## Im Test gefundene und behobene Fehler

1. **`curl-minimal` statt `curl`**: UBI 9 und viele RHEL-9-Installationen
   haben nur `curl-minimal`. `rpm -q curl` schlug fehl, `build-erlang.sh`
   brach mit "Pakete fehlen: curl" ab. Ein `dnf install curl` würde mit
   `curl-minimal` kollidieren. Die Skripte prüfen curl jetzt per
   `command -v curl` und installieren bei Bedarf `curl-minimal`.
2. **Upload-Prompt bricht ab**: Der Build liest stdin leer. Das
   anschließende `read` bekam EOF, `set -e` beendete das Skript mit
   Exit 1, obwohl der Tarball fertig war. Die Prompts brechen jetzt nicht
   mehr ab (`read ... || true`) und lassen sich per `UPLOAD=j|n`
   (`build-erlang.sh`) bzw. `ENABLE=j|n` (`install-rabbitmq.sh`) ohne
   Nachfrage steuern.

Zwei weitere Fehlläufe lagen am Testskript selbst: fehlende Leserechte von
`runner` auf `/home/builder` und `await_startup`, das ein Befehl von
`rabbitmqctl` ist, nicht von `rabbitmq-diagnostics`.

## Offen: Test auf echter VM

Auf einer Runtime-VM mit systemd noch zu prüfen:

- `prepare-runtime.sh` (Lingering, firewalld)
- `install-rabbitmq.sh` mit Downloads über Nexus
- User Unit mit `Type=notify`: meldet sich RabbitMQ per sd_notify als
  gestartet, bevor `TimeoutStartSec` greift?
- `systemctl --user stop rabbitmq` beendet Broker und epmd sauber
- Neustart der VM: startet die Unit dank Lingering ohne Login
- Cluster aus drei Nodes mit gemeinsamem Cookie
