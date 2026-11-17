# NameNode-Zugriff auf bestimmte Rechner beschränken (hadoop-policy.xml)

Diese Anleitung zeigt, wie man den RPC-Zugriff auf die NameNodes mit der Service Level Authorization auf bestimmte Rechner beschränkt. Die DataNodes bleiben für alle Clients erreichbar. Grundlage ist Hadoop 3.4.2 (siehe [versions.env](versions.env)).

## Ausgangslage

- Mit `hadoop.security.authentication=simple` prüft Hadoop keine Identität. Ein Client nennt einfach einen Benutzernamen, zum Beispiel über `HADOOP_USER_NAME=hdfs`. In der Ansible-Rolle steht außerdem `dfs_permissions_enabled: false`, Dateirechte werden also auch nicht geprüft. Wer den NameNode-RPC-Port erreicht, darf damit alles, bis hin zum Löschen.
- Microservices sprechen nicht direkt mit HDFS, sondern mit einem Java-Gateway über REST. Nur das Gateway hat Hadoop als Abhängigkeit und streamt die Daten durch.
- Die etwa 1000 Desktop-Clients sollen weiterhin auf die DataNodes zugreifen können.

Ziel: Client-RPC auf den NameNodes (Port 8020) nehmen nur noch das Gateway, die Admin-Rechner und die Cluster-Knoten an.

## Was die Policy prüft und was nicht

Jeder HDFS-Dienst (NameNode, ZKFC, JournalNode, DataNode) liest beim Start `hadoop-policy.xml` aus seinem Konfigurationsverzeichnis. Für jedes RPC-Protokoll gibt es zwei Listen:

- `security.<protokoll>.acl`: erlaubte Benutzer und Gruppen. Bei `simple` bringt das nichts, weil sich jeder Client als beliebiger User ausgeben kann.
- `security.<protokoll>.hosts`: erlaubte IP-Adressen, CIDR-Bereiche oder Hostnamen. Die Quell-IP kann ein Client nicht frei wählen, deshalb ist das hier die eigentliche Sperre.

Geprüft wird beim Verbindungsaufbau. Ein abgelehnter Client bekommt eine `AuthorizationException` mit dem Text `Host <ip> is not authorized for protocol ...`.

Die Policy greift **nur für Hadoop-RPC**. Diese Wege prüft sie nicht:

| Port | Dienst | Von der Policy geprüft |
|---|---|---|
| 8020 | NameNode RPC (`ClientProtocol` u. a.) | ja |
| 8019 | ZKFC RPC | ja |
| 8485 | JournalNode RPC | ja |
| 9867 | DataNode IPC (`ClientDatanodeProtocol`, `InterDatanodeProtocol`) | ja |
| 9870 | NameNode HTTP: Web-UI **und WebHDFS** | **nein** |
| 8480 | JournalNode HTTP | nein |
| 9866 | DataNode Datentransfer (kein RPC) | nein |
| 9864 | DataNode HTTP, WebHDFS-Datenpfad | nein |

Wichtig ist Port 9870. Über WebHDFS lassen sich dort dieselben Operationen ausführen wie über RPC, zum Beispiel `?op=DELETE&user.name=hdfs`. Ohne Firewall vor 9870 lässt sich die RPC-Sperre also einfach umgehen (Schritt 5).

## Schritt 1: Kommunikationsmatrix aufstellen

Zuerst festhalten, wer mit wem über welches Protokoll spricht. Die IPs stammen aus [ansible/inventory.yml.example](ansible/inventory.yml.example). Gateway und Admin-Rechner sind Platzhalter.

| Gruppe | IPs |
|---|---|
| NameNodes (mit ZKFC) | 192.168.1.10, 192.168.1.11 |
| DataNodes | 192.168.1.20, 192.168.1.21, 192.168.1.22 |
| JournalNodes | 192.168.1.30, 192.168.1.31, 192.168.1.32 |
| YARN (ResourceManager, NodeManager) | 192.168.1.40, 192.168.1.50 |
| Java-Gateway | 192.168.2.10, 192.168.2.11 |
| Admin-Rechner (Ansible, `hdfs dfsadmin`, Balancer) | 192.168.2.5 |

Welche Gruppe welches Protokoll braucht:

| Protokoll (Schlüssel ohne `.hosts`) | Läuft auf | Aufrufer |
|---|---|---|
| `security.client.protocol` | NameNode | Gateway, Admin, NameNodes, YARN; DataNodes nur bei WebHDFS über DataNodes (siehe Schritt 6) |
| `security.client.datanode.protocol` | DataNode | alle Clients: `*` |
| `security.datanode.protocol` | NameNode | DataNodes |
| `security.datanode.lifeline.protocol` | NameNode | DataNodes |
| `security.inter.datanode.protocol` | DataNode | DataNodes |
| `security.namenode.protocol` | NameNode | NameNodes, Admin (Balancer) |
| `security.qjournal.service.protocol` | JournalNode | NameNodes |
| `security.interqjournal.service.protocol` | JournalNode | JournalNodes |
| `security.ha.service.protocol` | NameNode | NameNodes (ZKFC), Admin (`hdfs haadmin`) |
| `security.zkfc.protocol` | ZKFC | NameNodes (ZKFC), Admin (`hdfs haadmin -failover`) |
| alle übrigen (`refresh.*`, `reconfiguration`, `get.user.mappings`) | alle | Admin, Cluster-Knoten (über `security.service.authorization.default.hosts`) |

Läuft das Gateway in Kubernetes oder hinter NAT, sieht der NameNode die IP des Nodes oder des NAT-Gateways. Damit hätten alle Pods auf diesen Nodes Zugriff. Das Gateway sollte deshalb auf eigenen Rechnern oder mit fester Egress-IP laufen.

## Schritt 2: hadoop-policy.xml schreiben

Die Datei kommt in das Konfigurationsverzeichnis jedes HDFS-Dienstes (`/etc/hadoop` laut Ansible-Rolle). Die Gruppen stehen einmal oben als eigene Properties und werden mit `${...}` eingesetzt. Hadoop löst solche Variablen beim Lesen der Konfiguration auf.

IP-Adressen und CIDR-Bereiche sind Hostnamen vorzuziehen. Bei Hostnamen muss die Rückwärtsauflösung der Client-IP stimmen, sonst wird der Client abgelehnt. Ein CIDR-Bereich darf keine Desktop-Clients enthalten.

```xml
<?xml version="1.0"?>
<configuration>

  <!-- Rechnergruppen. Mehrere Einträge durch Komma trennen, ohne Leerzeichen. -->
  <property><name>cluster.hosts.namenodes</name>
    <value>192.168.1.10,192.168.1.11</value></property>
  <property><name>cluster.hosts.datanodes</name>
    <value>192.168.1.20,192.168.1.21,192.168.1.22</value></property>
  <property><name>cluster.hosts.journalnodes</name>
    <value>192.168.1.30,192.168.1.31,192.168.1.32</value></property>
  <property><name>cluster.hosts.yarn</name>
    <value>192.168.1.40,192.168.1.50</value></property>
  <property><name>cluster.hosts.gateway</name>
    <value>192.168.2.10,192.168.2.11</value></property>
  <property><name>cluster.hosts.admin</name>
    <value>192.168.2.5</value></property>

  <!-- Benutzer-ACLs bleiben offen; bei simple-Auth nicht wirksam. -->
  <property><name>security.service.authorization.default.acl</name>
    <value>*</value></property>

  <!-- Alles, was unten nicht eigens steht, nur aus dem Cluster und vom Admin-Rechner. -->
  <property><name>security.service.authorization.default.hosts</name>
    <value>${cluster.hosts.namenodes},${cluster.hosts.datanodes},${cluster.hosts.journalnodes},${cluster.hosts.yarn},${cluster.hosts.admin}</value></property>

  <!-- NameNode: Client-RPC (hdfs dfs, FileSystem-API). Hier gehört das Gateway hinein. -->
  <property><name>security.client.protocol.hosts</name>
    <value>${cluster.hosts.gateway},${cluster.hosts.admin},${cluster.hosts.namenodes},${cluster.hosts.yarn}</value></property>

  <!-- DataNode-IPC für Clients: für alle offen. -->
  <property><name>security.client.datanode.protocol.hosts</name>
    <value>*</value></property>

  <!-- NameNode: Heartbeats und Blockreports der DataNodes. -->
  <property><name>security.datanode.protocol.hosts</name>
    <value>${cluster.hosts.datanodes}</value></property>
  <property><name>security.datanode.lifeline.protocol.hosts</name>
    <value>${cluster.hosts.datanodes}</value></property>

  <!-- DataNode untereinander (Block-Recovery). -->
  <property><name>security.inter.datanode.protocol.hosts</name>
    <value>${cluster.hosts.datanodes}</value></property>

  <!-- NameNode untereinander (Edit-Log-Roll, Checkpoint) und Balancer. -->
  <property><name>security.namenode.protocol.hosts</name>
    <value>${cluster.hosts.namenodes},${cluster.hosts.admin}</value></property>

  <!-- JournalNodes: Schreiben der Edits durch die NameNodes, Abgleich untereinander. -->
  <property><name>security.qjournal.service.protocol.hosts</name>
    <value>${cluster.hosts.namenodes}</value></property>
  <property><name>security.interqjournal.service.protocol.hosts</name>
    <value>${cluster.hosts.journalnodes}</value></property>

  <!-- HA: ZKFC steuert die NameNodes, haadmin vom Admin-Rechner. -->
  <property><name>security.ha.service.protocol.hosts</name>
    <value>${cluster.hosts.namenodes},${cluster.hosts.admin}</value></property>
  <property><name>security.zkfc.protocol.hosts</name>
    <value>${cluster.hosts.namenodes},${cluster.hosts.admin}</value></property>

</configuration>
```

Wer zusätzlich einzelne Rechner sperren will, nutzt `security.<protokoll>.hosts.blocked`. Zugelassen wird nur, wer in `.hosts` steht und nicht in `.hosts.blocked`.

## Schritt 3: Datei verteilen

Die Datei muss auf **allen** NameNodes, DataNodes und JournalNodes liegen. Das Gateway und andere Clients brauchen sie nicht.

Die Ansible-Rolle verteilt bisher nur die Dateien aus `hadoop_hdfs_config_files` in [ansible/roles/hadoop-config/defaults/main.yml](ansible/roles/hadoop-config/defaults/main.yml). Dafür:

1. Die Datei als `ansible/roles/hadoop-config/templates/hadoop-policy.xml.j2` anlegen. Die Gruppen-IPs lassen sich aus dem Inventory erzeugen, etwa `{{ groups['datanodes'] | map('extract', hostvars, 'ansible_host') | join(',') }}`. Gateway und Admin-Rechner brauchen dafür eigene Variablen in `group_vars`.
2. `hadoop-policy.xml` in die Liste `hadoop_hdfs_config_files` aufnehmen. Dann prüft die Rolle die Datei mit `xmllint` und merkt den Neustart vor.

Im Docker-Lab kommt die Datei in das jeweilige Config-Verzeichnis (`config/`, `configsingle/`, `configzwei/`). Die Container mounten es als `/opt/hadoop/etc/hadoop`.

## Schritt 4: Einschalten und rollierend neu starten

Die Policy wirkt erst mit diesem Eintrag in `core-site.xml`, auf allen HDFS-Knoten:

```xml
<property>
  <name>hadoop.security.authorization</name>
  <value>true</value>
</property>
```

Die Dienste lesen den Schalter nur beim Start. Ein Fehler in den clusterinternen Listen hat ernste Folgen: Erreicht ein NameNode die JournalNodes nicht mehr, beendet er sich. DataNodes ohne Heartbeat gelten nach etwa zehn Minuten als tot. Deshalb in zwei Phasen vorgehen.

**Phase A: clusterinterne Listen scharf, Client-RPC noch offen.** In der ersten Fassung `security.client.protocol.hosts` auf `*` setzen. Dann rollierend neu starten, wie es [ansible/site.yml](ansible/site.yml) vorsieht: JournalNodes, dann den Standby-NameNode, Failover, den zweiten NameNode, zuletzt die DataNodes. Nach jedem Schritt prüfen:

```bash
hdfs haadmin -getAllServiceState        # beide NameNodes antworten, einer active
hdfs dfsadmin -report | grep -E 'Live|Dead'   # alle DataNodes live
grep -i 'not authorized' /var/log/hadoop/*.log   # auf NN, JN, DN: keine Treffer
```

**Phase B: Client-RPC einschränken.** `security.client.protocol.hosts` auf die Liste aus Schritt 2 setzen, auf beide NameNodes verteilen und ohne Neustart neu laden, vom Admin-Rechner aus:

```bash
hdfs dfsadmin -refreshServiceAcl
# Erwartet: "Refresh service acl successful for ..." für beide NameNodes
```

Bei HA lädt `-refreshServiceAcl` die Policy auf allen NameNodes des Nameservice neu. Steht der Admin-Rechner nicht in der Default-Liste, die für `security.refresh.policy.protocol` gilt, schlägt schon dieser Aufruf fehl. Dann auf den NameNode-Rechnern die Datei korrigieren und die NameNodes neu starten.

`-refreshServiceAcl` wirkt nur auf die NameNodes. JournalNodes, DataNodes und ZKFC übernehmen Änderungen erst beim nächsten Neustart.

## Schritt 5: HTTP-Ports per Firewall schließen

Ohne diesen Schritt ist die RPC-Sperre wirkungslos, weil WebHDFS auf 9870 dieselben Operationen anbietet. Auf den NameNode- und JournalNode-Rechnern die HTTP-Ports nur für diese Absender öffnen:

| Port | Erlaubt für |
|---|---|
| 9870 (NameNode HTTP) | NameNodes (Checkpoint-Upload, `bootstrapStandby`), Admin-Rechner, ggf. Gateway, falls es WebHDFS nutzt |
| 8480 (JournalNode HTTP) | NameNodes, JournalNodes |
| 8020 (NameNode RPC) | zusätzlich zur Policy dieselben Rechner wie `security.client.protocol.hosts` plus DataNodes |

Beispiel mit nftables auf einem NameNode:

```
table inet hadoop {
  set nn_http_ok { type ipv4_addr; elements = { 192.168.1.10, 192.168.1.11, 192.168.2.5 } }
  chain input {
    type filter hook input priority 0; policy accept;
    tcp dport 9870 ip saddr != @nn_http_ok drop
  }
}
```

Die Web-UI für Admins am Arbeitsplatz läuft dann über einen Reverse Proxy mit Basic Auth auf dem Admin-Rechner oder über einen SSH-Tunnel.

Prometheus fragt den JMX-Exporter (28080/28081) ab, nicht 9870. Die Firewall-Regel betrifft das Monitoring also nicht.

## Schritt 6: Zugriff der Desktop-Clients auf die DataNodes

Die DataNode-Ports bleiben für alle offen:

- **9866 (Datentransfer)** und **9864 (HTTP)** sind kein RPC. Die Policy greift dort nicht, nichts ist zu tun.
- **9867 (IPC)** läuft über `security.client.datanode.protocol.hosts=*`. Clients brauchen es zum Beispiel, um die sichtbare Länge einer Datei abzufragen, in die noch geschrieben wird.

Welche Blöcke wo liegen, erfährt ein Client nur vom NameNode. Wie die Desktop-Clients zu den DataNodes kommen, hängt deshalb vom Gateway ab:

**Variante A: Das Gateway streamt die Daten selbst durch.** Das Gateway liest über die FileSystem-API und reicht die Bytes per REST weiter. Die Desktop-Clients sprechen dann nur mit dem Gateway und brauchen die DataNodes nicht. Die offenen DataNode-Ports stören nicht. Ohne Block-Tokens (`dfs.block.access.token.enable`) könnte allerdings jeder, der eine Block-ID kennt, den Block direkt lesen.

**Variante B: Das Gateway gibt WebHDFS-Redirects an die Desktop-Clients weiter.** Der Desktop-Client holt die Daten dann direkt von `datanode:9864`. Der DataNode arbeitet dabei selbst als HDFS-Client und ruft den NameNode über `ClientProtocol` auf. Dafür gilt:

- `${cluster.hosts.datanodes}` muss in `security.client.protocol.hosts` stehen.
- Wer 9864 erreicht, kann mit selbst gebauten URLs (`op=OPEN`, `CREATE`, `APPEND`, Parameter `namenoderpcaddress` und `user.name`) über den DataNode auf den NameNode zugreifen. Variante B lässt damit einen Weg an der Sperre vorbei offen.

Für das Ziel dieser Anleitung passt Variante A. Bei Variante B gibt es echten Schutz nur mit Kerberos und Delegation Tokens.

## Schritt 7: Testen

Von einem erlaubten Rechner (Gateway oder Admin):

```bash
hdfs dfs -ls hdfs://ns1/
```

Von einem nicht erlaubten Rechner, zum Beispiel einem Desktop mit Hadoop-Client:

```bash
hdfs dfs -ls hdfs://ns1/
# Erwartet: AuthorizationException ... Host <ip> is not authorized for protocol
#           interface org.apache.hadoop.hdfs.protocol.ClientProtocol
curl -s "http://192.168.1.10:9870/webhdfs/v1/?op=LISTSTATUS"
# Erwartet: Timeout bzw. Verbindung abgelehnt (Firewall aus Schritt 5)
```

Der Client versucht es nacheinander bei beiden NameNodes und gibt erst nach den Retries auf. Die Fehlermeldung kommt daher mit etwas Verzögerung.

Im Docker-Lab: Bei Zugriffen vom Docker-Host über einen veröffentlichten Port (`localhost:8020`) sieht der NameNode je nach Docker-Konfiguration die Gateway-IP der Bridge (zum Beispiel `172.18.0.1`) statt der echten Client-IP. Für Tests deshalb aus einem Container im Netz `hadoop` heraus prüfen und die Container-IPs eintragen, oder dem Netz in der Compose-Datei ein festes Subnetz und den Diensten feste `ipv4_address`-Werte geben.

## Betrieb

- **Neue Gateway-Instanz.** Die IP in `cluster.hosts.gateway` ergänzen, die Datei auf die NameNodes verteilen und `hdfs dfsadmin -refreshServiceAcl` ausführen. Ein Neustart ist nicht nötig.
- **Neuer DataNode oder JournalNode.** Die IP in die passende Gruppe eintragen, die Datei auf alle Knoten verteilen und die NameNodes per `-refreshServiceAcl` neu laden. Bei neuen JournalNodes zusätzlich die übrigen JournalNodes rollierend neu starten, weil sie `interqjournal` nur beim Start lesen.
- **YARN.** Laufen MapReduce-Jobs oder distcp auf dem Cluster, brauchen die NodeManager Client-RPC (`cluster.hosts.yarn`). Bei distcp von einem anderen Cluster müssen dessen NodeManager in `security.client.protocol.hosts` stehen, siehe [distscp.md](distscp.md).
- **Grenzen.** Die Policy ersetzt keine Authentifizierung. Auf einem erlaubten Rechner kann weiterhin jeder Prozess jeden Benutzernamen angeben. Das Gateway muss seine REST-Clients deshalb selbst authentifizieren und autorisieren.
