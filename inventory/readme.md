# inventory

Ansible-Playbook, das auf allen Hosts die Java-Prozesse mit ihren
TCP-Listen-Ports einsammelt und daraus Prometheus-Scrape-Targets erzeugt.

## Dateien

- **java-ports.yml** – Playbook
- **files/java_ports.py** – läuft auf den Zielhosts (nur `python3` und `ss`
  nötig, keine zusätzlichen Collections). Findet JVMs über `ss -ltnp`
  (Prozessname `java`, Binary `java` oder geladene `libjvm.so`).
- **templates/** – Vorlagen für die Ausgabedateien
- **hosts.ini.example** – Beispiel-Inventory

## Nutzung

```bash
cp hosts.ini.example hosts.ini   # Hosts eintragen
ansible-playbook -i hosts.ini java-ports.yml
```

Das Playbook läuft ohne `become` als der Deploy-User, unter dem auch die
Java-Prozesse laufen (`ansible_user` im Inventory). `ss -p` zeigt ohne root
nur die Prozesse dieses Users. Java-Prozesse anderer User fehlen also.

Pro Port wird per HTTP und HTTPS geprüft, ob `/actuator/prometheus`
Prometheus-Metriken liefert (Antwort 200 mit `# TYPE`). Nur Ports mit
Treffer landen in der Scrape-Config.

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `java_ports_hosts` | `all` | Host-Gruppe |
| `java_ports_probe` | `true` | Ports auf Metrics-Pfade prüfen; bei `false` werden alle nicht-lokalen Ports Targets |
| `java_ports_metrics_paths` | `[/actuator/prometheus]` | zu prüfende Pfade, z. B. zusätzlich `/metrics` |
| `java_ports_probe_timeout` | `2` | Timeout pro Probe in Sekunden |
| `java_ports_output_dir` | `output/` | Zielverzeichnis |
| `java_ports_sd_dir` | `/etc/prometheus/file_sd` | Pfad der Targets-Datei auf dem Prometheus-Server |
| `prometheus_target_host` | Inventory-Name | Host-Variable: Name/IP, unter dem Prometheus den Host erreicht |

## Ergebnis (output/)

- **java-processes.csv** – alle Java-Prozesse mit Ports:
  `host;app;pid;user;address;port;local_only;scheme;metrics_path;cmdline`
- **java-targets.yml** – `file_sd_configs`-Targets mit den Labels `app`
  und `host`, `__metrics_path__` und `__scheme__` pro Target
- **prometheus-scrape-config.yml** – Job `java`, der `java-targets.yml`
  einbindet

`java-targets.yml` auf den Prometheus-Server nach `java_ports_sd_dir`
kopieren. Prometheus liest die Datei bei Änderungen ohne Neustart neu ein.

Ports, die nur auf Loopback lauschen (`127.0.0.1`, `::1`), werden als
`nur lokal` markiert und nicht als Target übernommen.

## Label `app`

Das Label wird in dieser Reihenfolge ermittelt:

1. `-Dspring.application.name=...` auf der Kommandozeile
2. Umgebungsvariable `SPRING_APPLICATION_NAME`
3. Jar-Name bei `java -jar` ohne Version (`mirrorservice-1.2.3.jar` → `mirrorservice`)
4. Name des Launchers, falls nicht `java` (z. B. `jwebserver`)
5. Main-Klasse ohne Paket

Bei Start über `org.springframework.boot.loader.launch.JarLauncher` ergibt
das `JarLauncher`. Dann am besten `-Dspring.application.name` setzen.

## Einschränkung: Container

Bei Java-Anwendungen in Podman-Containern ist der Host-Port an
`rootlessport`/`conmon` gebunden, nicht an die JVM. Diese Anwendungen
tauchen deshalb nicht auf.
