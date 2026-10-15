"""Zugriff auf HDFS über den Kommandozeilen-Client "hdfs dfs".

Es gibt keine Python-Bibliothek für HDFS in der Standardbibliothek, deshalb
werden getfattr/setfattr als Unterprozess aufgerufen. Jeder Aufruf startet eine
JVM (ca. 1-2 s), daher wird gelesen mit wenigen Glob-Aufrufen und geschrieben
parallel.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from hdfs.changes import Change
from hdfs.common import HdfsError, info


def java_major(home: Path) -> Optional[int]:
    """Hauptversion des Java unter home, None wenn dort keins ist."""
    java = home / "bin" / "java"
    if not java.is_file():
        return None
    # "java -version" schreibt nach stderr, z. B. 'openjdk version "17.0.9"'
    # oder bei Java 8 'version "1.8.0_392"' (dann zählt die zweite Zahl)
    proc = subprocess.run([str(java), "-version"], capture_output=True, text=True)
    match = re.search(r'version "(\d+)(?:\.(\d+))?', proc.stderr)
    if not match:
        return None
    major = int(match[1])
    return int(match[2] or 0) if major == 1 else major


def find_java(repo: Path) -> Path:
    """Hadoop 3.4.2 ruft Subject.getSubject() auf, das ab JDK 23 entfernt ist.

    Gesucht wird in derselben Reihenfolge wie in test.sh; das erste Java < 23
    gewinnt. HDFS_JAVA_HOME hat Vorrang und wird nur geprüft.
    """
    explicit = os.environ.get("HDFS_JAVA_HOME")
    if explicit:
        home = Path(explicit).expanduser()
        if (java_major(home) or 99) >= 23:
            raise HdfsError(f"HDFS_JAVA_HOME={home} ist kein Java < 23.")
        return home
    sdkman = Path.home() / ".sdkman" / "candidates" / "java"
    candidates = [repo / "downloads" / "jdk-11"]
    candidates += sorted(sdkman.glob("11*")) + sorted(sdkman.glob("17*"))
    # dnf: java-{11,17}-openjdk-headless
    for pattern in ("jre-11*", "java-11*", "jre-17*", "java-17*"):
        candidates += sorted(Path("/usr/lib/jvm").glob(pattern))
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]))
    for home in candidates:
        major = java_major(home)
        if major is not None and major < 23:
            return home
    raise HdfsError("Kein Java 11/17 gefunden (HDFS_JAVA_HOME setzen).")


def stderr_text(proc: subprocess.CompletedProcess) -> str:
    """stderr eines hdfs-Aufrufs ohne die übliche NativeCodeLoader-Warnung."""
    lines = (line for line in proc.stderr.splitlines() if "NativeCodeLoader" not in line)
    return "\n".join(lines).strip()


def decode_value(value: str) -> str:
    """Wert aus getfattr-Ausgabe: "text", 0x<hex> oder 0s<base64>."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    if value[:2].lower() == "0x":
        return bytes.fromhex(value[2:]).decode("utf-8")
    if value[:2].lower() == "0s":
        return base64.b64decode(value[2:]).decode("utf-8")
    return value


def parse_getfattr(output: str, attr: str) -> dict:
    """Ausgabe von "getfattr -d" -> {pfad: wert oder None}.

    Die Ausgabe sieht so aus (Pfade ohne Attribute haben nur die erste Zeile):
        # file: /fs/account1
        user.acl="@client1|@client2=3"
        user.other="..."
    Jeder Pfad landet im Ergebnis, auch ohne Attribut (None). Das ist wichtig
    für die Vererbung: ein Container ohne ACL muss bekannt sein, um zu erben.
    """
    nodes = {}
    path = None
    prefix = attr + "="
    for line in output.splitlines():
        if line.startswith("# file: "):
            path = line[len("# file: "):]
            nodes[path] = None
        elif path is not None and line.startswith(prefix):
            nodes[path] = decode_value(line[len(prefix):])
    return nodes


class Hdfs:
    """Ein konfigurierter HDFS-Client. Der Konstruktor sucht Hadoop und Java und
    bricht mit HdfsError ab, wenn etwas fehlt, bevor irgendetwas gelesen wird."""

    def __init__(self, parallel: int):
        # Defaults passen zum Checkout ~/git/hadoop, alles per Umgebung änderbar
        repo = Path(os.environ.get("HADOOP_REPO", "~/git/hadoop")).expanduser()
        home = Path(os.environ.get("HADOOP_HOME", repo / "downloads" / "hadoop-3.4.2")).expanduser()
        conf = Path(os.environ.get("HADOOP_CONF_DIR", repo / "clientconfig")).expanduser()
        self.bin = home / "bin" / "hdfs"
        if not self.bin.is_file():
            raise HdfsError(f"{self.bin} nicht gefunden (HADOOP_HOME setzen).")
        java_home = find_java(repo)
        # eigene Umgebung nur für die hdfs-Unterprozesse, os.environ bleibt unverändert
        self.env = {
            **os.environ,
            "JAVA_HOME": str(java_home),
            "HADOOP_HOME": str(home),
            "HADOOP_CONF_DIR": str(conf),
        }
        self.parallel = parallel
        info(f"HDFS-Client: {self.bin} (JAVA_HOME={java_home}, HADOOP_CONF_DIR={conf})")

    def dfs(self, *args: str) -> subprocess.CompletedProcess:
        """Ein "hdfs dfs ..."-Aufruf. Fehler werden nicht geworfen, sondern über
        returncode/stderr ausgewertet, weil manche (leerer Glob) normal sind."""
        return subprocess.run([str(self.bin), "dfs", *args], env=self.env, capture_output=True, text=True)

    def read_acls(self, base: str, attr: str, depth: int) -> dict:
        """Ein getfattr je Ebene über Globs (base/*, base/*/*, ...), nicht rekursiv,
        damit Dateien unterhalb der Container nicht mitgelesen werden."""
        patterns = [base + "/*" * level for level in range(1, depth + 1)]
        info(f"Lese {attr} aus {', '.join(patterns)} ...")
        # Die Ebenen sind unabhängig voneinander, also alle gleichzeitig lesen
        with ThreadPoolExecutor(len(patterns)) as pool:
            procs = list(pool.map(lambda p: self.dfs("-getfattr", "-d", p), patterns))
        nodes = {}
        for pattern, proc in zip(patterns, procs):
            if proc.returncode != 0:
                error = stderr_text(proc)
                if "No such file or directory" in error:
                    continue  # Glob ohne Treffer
                raise HdfsError(f"getfattr {pattern} fehlgeschlagen:\n{error}")
            nodes.update(parse_getfattr(proc.stdout, attr))
        return nodes

    def write(self, changes: list, attr: str) -> list:
        """Setzt/entfernt das Attribut parallel; liefert [(Change, Fehlertext)].

        Ein Fehler bricht nicht ab: die übrigen Pfade werden trotzdem geschrieben
        und alle Fehler gesammelt zurückgegeben. execute() prüft danach ohnehin
        jeden Wert durch erneutes Lesen.
        """

        def run(change: Change) -> subprocess.CompletedProcess:
            if change.new is None:
                return self.dfs("-setfattr", "-x", attr, change.path)
            # Anführungszeichen: Wert wird immer als Text behandelt, auch bei 0x/0s
            return self.dfs("-setfattr", "-n", attr, "-v", f'"{change.new}"', change.path)

        failures = []
        with ThreadPoolExecutor(self.parallel) as pool:
            futures = {pool.submit(run, change): change for change in changes}
            # as_completed: Fortschritt in der Reihenfolge, in der Aufrufe fertig werden
            for done, future in enumerate(as_completed(futures), 1):
                change, proc = futures[future], future.result()
                status = "ok" if proc.returncode == 0 else "FEHLER"
                info(f"  [{done}/{len(changes)}] {status} {change.path}")
                if proc.returncode != 0:
                    failures.append((change, stderr_text(proc)))
        return failures
