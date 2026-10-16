# Einzelner NameNode zum Testen

Für `hdfs-acl` reicht ein einzelner NameNode ohne DataNodes: Ordner, xattrs
und das fsimage sind reine Metadaten. Er startet in wenigen Sekunden aus dem
Hadoop-Client-Tarball und braucht weder Docker noch den Testcluster aus
`~/git/hadoop`.

Was ohne DataNodes **nicht** geht: Dateien mit Inhalt schreiben (`-put`
bricht mit "could only be written to 0 of the 1 minReplication nodes" ab).
Leere Dateien gehen mit `-touchz`.

Andere Ports als der Testcluster (19000/19870 statt 8020/9870), beide
können also gleichzeitig laufen.

## Einrichten

Arbeitsordner, Konfiguration und Umgebung (Ordner frei wählbar):

```bash
export SINGLE=/tmp/hdfs-single
mkdir -p $SINGLE/conf $SINGLE/name $SINGLE/logs

cat > $SINGLE/conf/core-site.xml <<'EOF'
<configuration>
  <property><name>fs.defaultFS</name><value>hdfs://localhost:19000</value></property>
</configuration>
EOF

cat > $SINGLE/conf/hdfs-site.xml <<EOF
<configuration>
  <property><name>dfs.namenode.name.dir</name><value>file://$SINGLE/name</value></property>
  <property><name>dfs.namenode.rpc-address</name><value>localhost:19000</value></property>
  <property><name>dfs.namenode.http-address</name><value>localhost:19870</value></property>
  <property><name>dfs.permissions.enabled</name><value>true</value></property>
</configuration>
EOF

cp ~/git/hadoop/clientconfig/log4j.properties $SINGLE/conf/

cat > $SINGLE/env.sh <<EOF
# Java 11 oder 17, Hadoop 3.4.2 läuft nicht mit Java >= 23
export JAVA_HOME=\$(ls -d ~/.sdkman/candidates/java/11* | head -1)
export HDFS_JAVA_HOME=\$JAVA_HOME
export HADOOP_HOME=~/git/hadoop/downloads/hadoop-3.4.2
export HADOOP_CONF_DIR=$SINGLE/conf
export HADOOP_LOG_DIR=$SINGLE/logs
export HADOOP_PID_DIR=$SINGLE
export PATH=\$HADOOP_HOME/bin:\$PATH
EOF
```

`env.sh` setzt `HADOOP_CONF_DIR` auf die eigene Konfiguration. `test.sh` und
`hdfs-acl` übernehmen das, solange die Variable gesetzt ist, und sprechen
dann mit dem einzelnen NameNode statt mit dem Testcluster.

Einmalig formatieren:

```bash
. $SINGLE/env.sh
hdfs namenode -format -nonInteractive
```

## Starten und stoppen

```bash
. $SINGLE/env.sh
hdfs --daemon start namenode
# warten, bis er antwortet
for i in $(seq 30); do hdfs dfs -ls / >/dev/null 2>&1 && break; sleep 1; done
```

Web-UI: <http://localhost:19870>, Log: `$SINGLE/logs/`.

```bash
hdfs --daemon stop namenode
rm -rf $SINGLE        # alles weg, beim nächsten Mal neu formatieren
```

Der User, der den NameNode startet, ist dessen Superuser.

## Testdaten anlegen

```bash
cd ~/git/redhat-helpers/hdfs
OUT_DIR=$SINGLE/testdata PARALLEL=16 ./test.sh
diff -r $SINGLE/testdata testdata && echo "testdata identisch"
```

`OUT_DIR` zeigt in den Arbeitsordner, damit `testdata/` im Repo nicht
überschrieben wird. Bei gleichem `BASE_DIR` sind die Dateien ohnehin
identisch. Dauer mit `PARALLEL=16` rund 3 Minuten, weil `test.sh` für jeden
Pfad eine JVM startet.

## fsimage erzeugen und auswerten

Ein frisch angelegter NameNode hat nur ein leeres fsimage. Damit die
Testdaten darin stehen, braucht es einen Checkpoint. Hier geht das mit
`saveNamespace`. Im echten Cluster **nicht**, weil der Safemode alle
Schreibzugriffe sperrt (siehe `hdfs.md`).

```bash
mkdir -p $SINGLE/work && cd $SINGLE/work
hdfs dfsadmin -safemode enter
hdfs dfsadmin -saveNamespace
hdfs dfsadmin -safemode leave
hdfs dfsadmin -fetchImage .          # legt fsimage_<txid> hier ab

T=~/git/redhat-helpers/hdfs/src/hdfs/main.py
python3 $T collect                   # aus dem fsimage
python3 $T export | diff - ~/git/redhat-helpers/hdfs/testdata/expected.csv && echo OK
python3 $T collect --live --db live.db
python3 $T export --db live.db | diff - ~/git/redhat-helpers/hdfs/testdata/expected.csv && echo OK
```

Das XML ansehen, das `hdfs-acl` intern liest:

```bash
hdfs oiv -p XML -i $(ls fsimage_* | grep -v md5 | tail -1) -o - | less
```

## Was sich damit testen lässt

**Veraltetes fsimage:** Nach `fetchImage` live etwas ändern. Die ändernden
Kommandos müssen das beim Nachlesen bemerken:

```bash
hdfs dfs -setfattr -n user.acl -v '"@client4|@client9=1"' /fs/account1/container1
hdfs dfs -rm -r -skipTrash /fs/account4/container1
python3 $T replace @client4 @newapp      # "... live nachgelesen: 1 seitdem geändert, 1 fehlen"
python3 $T replace @client4 @newapp --apply
python3 $T restore hdfs-acl-backup-*-replace-client4-newapp.csv --apply
```

**Fehlende Rechte:** Mit `HADOOP_USER_NAME` als anderer User arbeiten
(ohne Kerberos glaubt der NameNode das einfach):

```bash
hdfs dfs -chmod 700 /fs/account5/container3
hdfs dfs -chown nobody /fs/account5/container3
HADOOP_USER_NAME=other python3 $T collect --live --db other.db
# -> Warnung, container3 wird übersprungen statt als "ohne ACL" zu erben

hdfs dfs -chmod 700 /fs/account6
hdfs dfs -chown nobody /fs/account6
HADOOP_USER_NAME=other python3 $T collect --live --db other.db
# -> Abbruch, /fs/account6 ist nicht listbar

hdfs dfs -chmod 755 /fs/account6 /fs/account5/container3
hdfs dfs -chown $USER /fs/account6 /fs/account5/container3
```

**Snapshots und Umbenennen:** Umbenannte Ordner unter einem Snapshot
stehen im fsimage als `refChild` statt `child`:

```bash
hdfs dfsadmin -allowSnapshot /fs
hdfs dfs -createSnapshot /fs s1
hdfs dfs -mv /fs/account1/container1 /fs/account1/container1neu
# danach saveNamespace/fetchImage wie oben, collect muss container1neu finden
hdfs dfs -deleteSnapshot /fs s1 && hdfs dfsadmin -disallowSnapshot /fs
```

**Viele Pfade in einem Aufruf:** Glob-Alternativen direkt ausprobieren:

```bash
hdfs dfs -getfattr -d '/{fs/account1/container1,fs/account2/container2,fs/gibtsnicht}'
```

Nicht vorhandene Pfade passen einfach nicht. Das `/` muss vor der Klammer
stehen, sonst kommen die Pfade relativ zum Home-Ordner zurück (`../../fs/...`).
