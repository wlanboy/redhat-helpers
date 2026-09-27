# valkeycluster – Valkey Cluster mit mehreren Instanzen pro VM (RHEL 9, offline)

Valkey im Cluster-Modus: Daten werden über 16384 Slots auf mehrere
Primaries verteilt, jede Primary hat Replicas auf anderen VMs. Fällt eine
Primary aus, übernimmt ihre Replica automatisch. Pro VM laufen mehrere
Instanzen als systemd User Units `valkey-cluster@<port>.service`.

Binaries, Version und Nexus-Proxy wie in [../valkey/readme.md](../valkey/readme.md):
der jammy-Build von download.valkey.io, Version und SHA256 in
`valkey/versions.conf`. Der Ordner `valkey/` muss daneben liegen.

## Topologie

Minimum sind 3 Primaries. Mit 1 Replica pro Primary und 3 VMs:

```
          vm1                vm2                vm3
   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
   │ 7001 Primary A│  │ 7001 Primary B│  │ 7001 Primary C│
   │ 7002 Replica C│  │ 7002 Replica A│  │ 7002 Replica B│
   └───────────────┘  └───────────────┘  └───────────────┘
```

`valkey-cli --cluster create` verteilt die Replicas so, dass sie nicht auf
derselben VM wie ihre Primary liegen, sofern die VMs unterschiedliche
Adressen haben. Die ersten N/(Replicas+1) Einträge in
`VALKEY_CLUSTER_NODES` werden Primaries, deshalb zuerst je eine Instanz
pro VM aufführen. Fällt eine ganze VM aus, übernehmen die Replicas auf den
anderen VMs.

## Dateien

- **cluster.conf** – Ports dieser VM (`VALKEY_PORTS`, Default `7001 7002`),
  Liste aller Instanzen für `create`, Replicas pro Primary, optional
  `VALKEY_ANNOUNCE_IP`, Node-Timeout.
- **install-valkeycluster.sh** – als User, jede VM. Download, Config pro
  Instanz, gemeinsames Passwort, Template-Unit, Start der Instanzen
  nacheinander.
- **cluster.sh** – als User, eine VM. `create` (einmalig), `check`, `info`,
  `nodes`.

## Voraussetzungen (einmalig als root, jede VM)

```bash
loginctl enable-linger valkey
mkdir -p /opt/local/valkey && chown valkey: /opt/local/valkey
echo 'vm.overcommit_memory = 1' > /etc/sysctl.d/90-valkey.conf && sysctl --system
# Client-Ports und Cluster-Bus (Port + 10000)
firewall-cmd --permanent --add-port=7001-7002/tcp --add-port=17001-17002/tcp
firewall-cmd --reload
```

Die Hostnamen bzw. IPs in `VALKEY_CLUSTER_NODES` müssen von allen VMs und
von den Clients aus erreichbar sein. Die Instanzen teilen sich ihre
Adressen gegenseitig mit. Bei mehreren NICs `VALKEY_ANNOUNCE_IP` setzen,
sonst wird eventuell die falsche Adresse bekanntgegeben.

## Installation

Auf der ersten VM (Passwort erzeugen lassen oder vorgeben):

```bash
valkeycluster/install-valkeycluster.sh
grep requirepass /opt/local/valkey/etc/auth.conf
```

Auf den weiteren VMs mit demselben Passwort:

```bash
VALKEY_PASSWORD=... ENABLE=j valkeycluster/install-valkeycluster.sh
```

Dann einmalig auf einer VM den Cluster bilden:

```bash
export VALKEY_CLUSTER_NODES="vm1:7001 vm2:7001 vm3:7001 vm1:7002 vm2:7002 vm3:7002"
valkeycluster/cluster.sh create     # zeigt die Slot-Verteilung, mit "yes" bestätigen
valkeycluster/cluster.sh check
```

Die Node-Liste kann auch fest in `cluster.conf` eingetragen werden.

`systemctl --user` braucht eine echte Login-Session (SSH direkt als User
oder `machinectl shell valkey@`), nicht `su -` oder `sudo -u`.

Verzeichnisse unter `/opt/local/valkey` (änderbar per `VALKEY_BASE`):

```
/opt/local/valkey/
├── server/valkey-9.1.2-jammy-x86_64/   + current -> valkey-9.1.2-jammy-x86_64
├── etc/auth.conf           requirepass + masterauth, chmod 600
├── etc/valkey.env          bei jedem Lauf neu generiert
├── instances/7001/valkey.conf   einmalig angelegt, danach eigene Pflege
├── instances/7001/data/         RDB, AOF, nodes.conf (Cluster-Zustand)
├── instances/7002/...
├── log/valkey-7001.log
└── downloads/
```

`nodes.conf` schreibt Valkey selbst. Nicht von Hand ändern. Wer eine
Instanz komplett neu aufsetzen will, stoppt sie, leert `data/`, startet sie
und nimmt sie per `valkey-cli --cluster add-node` wieder auf.

## Betrieb

```bash
set -a; . /opt/local/valkey/etc/valkey.env; set +a
export VALKEYCLI_AUTH=$(sed -n 's/^requirepass "\(.*\)"$/\1/p' /opt/local/valkey/etc/auth.conf)

valkey-cli -c -p 7001 set foo bar           # -c folgt MOVED-Redirects
valkeycluster/cluster.sh info               # Keys und Slots pro Primary
valkeycluster/cluster.sh nodes              # wer ist Primary, wer Replica
systemctl --user status 'valkey-cluster@*'
```

Clients müssen Cluster-fähig sein (z.B. Lettuce/Jedis im Cluster-Modus,
Spring Data Redis mit `spring.data.redis.cluster.nodes`) und brauchen
mindestens einige der Instanzen als Seed-Liste.

Nach einem Failover bleibt die frühere Replica Primary. Zurück auf die
ursprüngliche Verteilung (auf der gewünschten Primary, einer Replica):

```bash
valkey-cli -p 7002 cluster failover
```

## Upgrade

Neue Version in `valkey/versions.conf` eintragen und
`install-valkeycluster.sh` VM für VM ausführen. Das Skript startet die
Instanzen nacheinander neu und wartet jeweils, bis die Instanz geladen hat.
Vor der nächsten VM `cluster.sh check` abwarten, bis alle Nodes wieder
`[OK]` melden. Rollback: `current` zurücksetzen und die Units neu starten.

Ports aus `VALKEY_PORTS` entfernen stoppt keine laufende Instanz. Erst per
`valkey-cli --cluster del-node` aus dem Cluster nehmen, dann
`systemctl --user disable --now valkey-cluster@<port>`.
