# shellcheck shell=bash
# lib.sh
#
# Gemeinsame Funktionen für test.sh und migrate.sh, wird per "." eingebunden:
#   setup_hdfs       Java < 23 und Hadoop-Client suchen, JAVA_HOME,
#                    HADOOP_HOME und HADOOP_CONF_DIR exportieren
#   hdfs_cmd         "hdfs ..." mit diesem Client
#   start_namenode   frischen NameNode per Docker starten (Konfiguration in
#                    namenode/), legt WORK_DIR an, räumt beim Beenden auf
#                    (cleanup, außer KEEP=1)
#
# Erwartet SCRIPT_DIR (Ordner von lib.sh) und ACL_ATTR. Umgebungsvariablen:
# HADOOP_REPO, HADOOP_VERSION, HADOOP_HOME, HADOOP_CONF_DIR, HDFS_JAVA_HOME,
# DOCKER_IMAGE (siehe test.sh --help).

HADOOP_REPO=${HADOOP_REPO:-$HOME/git/hadoop}
HADOOP_VERSION=${HADOOP_VERSION:-3.4.2}
DOCKER_IMAGE=${DOCKER_IMAGE:-apache/hadoop:$HADOOP_VERSION}
NN_CONF_DIR=$SCRIPT_DIR/namenode
KEEP=${KEEP:-0}

# --- HDFS-Client --------------------------------------------------------------

# Hadoop 3.4.2 ruft Subject.getSubject() auf, das ab JDK 23 entfernt ist.
java_usable() {
    local home=$1 version
    [[ -n $home && -x $home/bin/java ]] || return 1
    version=$("$home/bin/java" -version 2>&1 | awk -F'"' '/version/ {print $2; exit}')
    version=${version%%.*}
    [[ $version == 1 ]] && version=8
    [[ $version =~ ^[0-9]+$ ]] && (( version < 23 ))
}

setup_hdfs() {
    local candidate java_home=""
    if [[ -n ${HDFS_JAVA_HOME:-} ]]; then
        # auch .../bin/java oder .../bin angegeben? Dann das JDK darüber nehmen
        java_home=${HDFS_JAVA_HOME%/}
        java_home=${java_home%/bin/java}
        java_home=${java_home%/bin}
        if [[ ! -x $java_home/bin/java ]]; then
            echo "Fehler: HDFS_JAVA_HOME=$HDFS_JAVA_HOME: kein $java_home/bin/java gefunden." >&2
            exit 1
        fi
        if ! java_usable "$java_home"; then
            echo "Fehler: HDFS_JAVA_HOME=$HDFS_JAVA_HOME ist kein Java < 23." >&2
            exit 1
        fi
    else
        for candidate in "$HADOOP_REPO/downloads/jdk-11" \
                         "$HOME"/.sdkman/candidates/java/11* \
                         "$HOME"/.sdkman/candidates/java/17* \
                         /usr/lib/jvm/jre-11* /usr/lib/jvm/java-11* \
                         /usr/lib/jvm/jre-17* /usr/lib/jvm/java-17* \
                         "${JAVA_HOME:-}"; do
            if java_usable "$candidate"; then
                java_home=$candidate
                break
            fi
        done
    fi
    if [[ -z $java_home ]]; then
        echo "Fehler: Kein Java 11/17 gefunden (HDFS_JAVA_HOME setzen)." >&2
        exit 1
    fi

    export JAVA_HOME=$java_home
    export HADOOP_HOME=${HADOOP_HOME:-$HADOOP_REPO/downloads/hadoop-$HADOOP_VERSION}
    export HADOOP_CONF_DIR=${HADOOP_CONF_DIR:-$HADOOP_REPO/clientconfig}
    export ACL_ATTR

    if [[ ! -x $HADOOP_HOME/bin/hdfs ]]; then
        echo "Fehler: $HADOOP_HOME/bin/hdfs nicht gefunden (HADOOP_HOME oder HADOOP_VERSION setzen)." >&2
        exit 1
    fi
    echo "HDFS-Client: $HADOOP_HOME/bin/hdfs"
    echo "  JAVA_HOME=$JAVA_HOME"
    echo "  HADOOP_CONF_DIR=$HADOOP_CONF_DIR"
}

hdfs_cmd() {
    "$HADOOP_HOME/bin/hdfs" "$@"
}
export -f hdfs_cmd

# --- Docker-NameNode ----------------------------------------------------------

# Ein NameNode ohne DataNodes reicht: Ordner, xattrs und fsimage sind reine
# Metadaten. Die Konfiguration in $NN_CONF_DIR dient dem NameNode im
# Container und dem Client auf dem Host (Ports 19000/19870, siehe dort).
start_namenode() {
    local i
    local name
    name=hdfs-acl-$(basename "$0" .sh)
    WORK_DIR=$(mktemp -d "/tmp/$name.XXXXXX")
    NN_CONTAINER=$name-$$
    trap cleanup EXIT
    mkdir -p "$WORK_DIR/work"
    export HADOOP_CONF_DIR=$NN_CONF_DIR

    echo "Starte NameNode $NN_CONTAINER ($DOCKER_IMAGE, Konfiguration $NN_CONF_DIR) ..."
    # Der User "hadoop" im Container muss die Dateien lesen können
    docker run -d --name "$NN_CONTAINER" \
        -p 127.0.0.1:19000:19000 -p 127.0.0.1:19870:19870 \
        -v "$NN_CONF_DIR:/conf:ro" -e HADOOP_CONF_DIR=/conf \
        --entrypoint bash "$DOCKER_IMAGE" \
        -c 'hdfs namenode -format -nonInteractive && exec hdfs namenode' > /dev/null

    for (( i = 0; i < 90; i++ )); do
        if [[ $(docker inspect -f '{{.State.Running}}' "$NN_CONTAINER" 2>/dev/null) != true ]]; then
            break
        fi
        if hdfs_cmd dfs -ls / > /dev/null 2>&1; then
            # Superuser ist der User, der den NameNode startet ("hadoop").
            # Ohne Kerberos glaubt der NameNode HADOOP_USER_NAME einfach.
            HADOOP_USER_NAME=hadoop hdfs_cmd dfs -chown "$(id -un)" /
            echo "NameNode antwortet nach ${i}s."
            return
        fi
        sleep 1
    done
    echo "Fehler: NameNode antwortet nicht. Log:" >&2
    docker logs --tail 30 "$NN_CONTAINER" >&2 || true
    exit 1
}

cleanup() {
    local rc=$?
    if (( KEEP )); then
        echo "Behalten: Container $NN_CONTAINER, Arbeitsordner $WORK_DIR"
        echo "  export HADOOP_CONF_DIR=$NN_CONF_DIR"
        echo "  aufräumen: docker rm -f $NN_CONTAINER && rm -rf $WORK_DIR"
        return $rc
    fi
    echo "Lösche NameNode $NN_CONTAINER und $WORK_DIR ..."
    docker rm -f "$NN_CONTAINER" > /dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
    return $rc
}
