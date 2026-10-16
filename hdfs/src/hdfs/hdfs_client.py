"""Zugriff auf HDFS über den Kommandozeilen-Client "hdfs".

Es gibt keine Python-Bibliothek für HDFS in der Standardbibliothek, deshalb
wird "hdfs dfs -getfattr/-setfattr" bzw. "hdfs oiv" als Unterprozess
aufgerufen. Jeder Aufruf startet eine JVM (ca. 1-2 s). getfattr/setfattr
nehmen nur ein Pfad-Argument, viele Pfade passen aber als Glob-Alternativen
/{fs/a/c1,fs/a/c2,...} in einen Aufruf (siehe path_globs).
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator, Optional

from hdfs.common import HdfsError, info
from hdfs.fsimage import parse_fsimage_xml

# Ein einzelnes Argument darf unter Linux höchstens 128 KiB lang sein (MAX_ARG_STRLEN)
GLOB_BYTES = 100_000


def _stop(proc: subprocess.Popen) -> Optional[int]:
    """Beendet einen Prozess, dessen stdout nicht mehr gelesen wird (sonst
    blockiert er auf der vollen Pipe). Exit-Code, None wenn abgebrochen."""
    killed = proc.poll() is None
    if killed:
        proc.kill()
    returncode = proc.wait()
    if proc.stdout:
        proc.stdout.close()
    return None if killed else returncode


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
        # auch .../bin/java oder .../bin angegeben? Dann das JDK darüber nehmen
        if home.name == "java" and home.parent.name == "bin":
            home = home.parent.parent
        elif home.name == "bin":
            home = home.parent
        major = java_major(home)
        if major is None:
            raise HdfsError(f"HDFS_JAVA_HOME={explicit}: kein {home}/bin/java gefunden "
                            "(Ordner des JDK angeben, ohne Wildcards).")
        if major >= 23:
            raise HdfsError(f"HDFS_JAVA_HOME={explicit} ist Java {major}, nötig ist Java < 23.")
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


def command_errors(proc: subprocess.CompletedProcess, command: str, pattern: str) -> list:
    """Fehler eines "hdfs dfs -<command>"-Aufrufs als [(pfad oder None, zeile)].

    FsShell macht nach einem Fehler an einem Pfad mit den übrigen weiter und
    schreibt je Fehler eine Zeile "<command>: ...". Der Pfad steht darin als
    `pfad' (z. B. nicht gefunden) oder inode="pfad" (keine Berechtigung).
    Ein Glob ohne Treffer (Fehler am Muster selbst) zählt nicht als Fehler.
    """
    lines = [line for line in proc.stderr.splitlines() if line.startswith(command + ": ")]
    if proc.returncode != 0 and not lines:
        # kein Fehler an einem Pfad, sondern z. B. Aufruf oder JVM kaputt
        return [(None, stderr_text(proc) or f"Exit-Code {proc.returncode}")]
    errors = []
    for line in lines:
        match = re.search(r"`(.*)': ", line) or re.search(r'inode="([^"]*)"', line)
        path = match[1] if match else None
        if not (path == pattern and line.endswith("No such file or directory")):
            errors.append((path, line))
    return errors


def glob_escape(path: str) -> str:
    """Zeichen, die im Hadoop-Glob eine Bedeutung haben, mit \\ entwerten."""
    return re.sub(r"([\\{}\[\]*?,])", r"\\\1", path)


def path_globs(paths: list) -> Iterator[tuple]:
    """Pfade -> [(glob, anzahl)], je Glob /{a/b,c/d,...} höchstens GLOB_BYTES.

    Klammern, deren Alternativen ein "/" enthalten, löst Hadoop vorab in
    einzelne Pfade auf (GlobExpander): jeder Pfad wird direkt angesprochen,
    kein Ordner gelistet. Nicht (mehr) vorhandene Pfade passen einfach nicht.
    Das "/" steht vor der Klammer, sonst wären die Pfade relativ zum Home-Ordner.
    """
    batch, size = [], 0
    for path in paths:
        part = glob_escape(path.lstrip("/"))
        length = len(part.encode()) + 1
        if batch and size + length > GLOB_BYTES:
            yield "/{" + ",".join(batch) + "}", len(batch)
            batch, size = [], 0
        batch.append(part)
        size += length
    if batch:
        yield "/{" + ",".join(batch) + "}", len(batch)


class Hdfs:
    """Ein konfigurierter HDFS-Client. Der Konstruktor sucht Hadoop und Java und
    bricht mit HdfsError ab, wenn etwas fehlt, bevor irgendetwas gelesen wird."""

    def __init__(self, parallel: int, version: str):
        # Defaults passen zum Checkout ~/git/hadoop, alles per Umgebung änderbar;
        # version bestimmt nur den Default von HADOOP_HOME
        repo = Path(os.environ.get("HADOOP_REPO", "~/git/hadoop")).expanduser()
        home = Path(os.environ.get("HADOOP_HOME", repo / "downloads" / f"hadoop-{version}")).expanduser()
        conf = Path(os.environ.get("HADOOP_CONF_DIR", repo / "clientconfig")).expanduser()
        self.bin = home / "bin" / "hdfs"
        if not self.bin.is_file():
            raise HdfsError(f"{self.bin} nicht gefunden (HADOOP_HOME oder --hadoop-version setzen).")
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

    def getfattr(self, pattern: str, attr: str) -> tuple:
        """Ein "getfattr -d" -> ({pfad: wert oder None}, [Fehler ohne Pfad im Ergebnis]).

        getfattr schreibt "# file: pfad" schon vor dem Lesen der Attribute. Ein
        Pfad, dessen Attribute nicht lesbar waren (inzwischen gelöscht, keine
        Berechtigung), sähe sonst aus wie "ohne ACL" und würde fälschlich erben.
        Er wird deshalb entfernt und gemeldet. Fehler, zu denen kein Pfad in
        der Ausgabe steht (z. B. ein Ordner war beim Globben nicht lesbar),
        gehen an den Aufrufer.
        """
        proc = self.dfs("-getfattr", "-d", pattern)
        nodes = parse_getfattr(proc.stdout, attr)
        unexplained = []
        for path, line in command_errors(proc, "getfattr", pattern):
            if path in nodes:
                del nodes[path]
                info(f"Warnung: übersprungen, {line}")
            else:
                unexplained.append(line)
        return nodes, unexplained

    def read_acls(self, base: str, attr: str, depth: int) -> dict:
        """Ein getfattr je Ebene über Globs (base/*, base/*/*, ...), nicht rekursiv,
        damit Dateien unterhalb der Container nicht mitgelesen werden.

        Belastet den NameNode mit einem RPC je Pfad; bei Millionen Pfaden ist
        read_fsimage() der bessere Weg.
        """
        patterns = [base + "/*" * level for level in range(1, depth + 1)]
        info(f"Lese {attr} aus {', '.join(patterns)} ...")
        # Die Ebenen sind unabhängig voneinander, also alle gleichzeitig lesen
        with ThreadPoolExecutor(len(patterns)) as pool:
            results = list(pool.map(lambda p: self.getfattr(p, attr), patterns))
        nodes = {}
        for pattern, (found, errors) in zip(patterns, results):
            if errors:
                # Ergebnis wäre lückenhaft: lieber abbrechen als darauf planen
                raise HdfsError(f"getfattr {pattern} fehlgeschlagen:\n" + "\n".join(errors))
            nodes.update(found)
        return nodes

    def read_paths(self, paths: list, attr: str) -> dict:
        """Liest genau diese Pfade, gebündelt zu Globs und parallel.

        Gelöschte oder nicht lesbare Pfade fehlen im Ergebnis; Fehler werden
        nur gemeldet, der Aufrufer prüft, welche Pfade fehlen.
        """
        patterns = [pattern for pattern, _ in path_globs(paths)]
        with ThreadPoolExecutor(self.parallel) as pool:
            results = list(pool.map(lambda p: self.getfattr(p, attr), patterns))
        wanted = set(paths)
        nodes = {}
        for found, errors in results:
            for error in errors:
                info(f"Warnung: {error}")
            nodes.update((path, value) for path, value in found.items() if path in wanted)
        return nodes

    def read_fsimage(self, image: Path, base: str, attr: str, depth: int) -> dict:
        """Wertet ein fsimage lokal mit "hdfs oiv -p XML" aus. Die XML-Ausgabe
        wird als Stream gelesen, es entsteht keine Zwischendatei."""
        info(f"Lese {attr} aus {image} (hdfs oiv) ...")
        # stderr in eine Datei: eine zweite Pipe könnte volllaufen, während
        # stdout gelesen wird, und oiv blockieren
        with tempfile.TemporaryFile("w+") as err:
            proc = subprocess.Popen([str(self.bin), "oiv", "-p", "XML", "-i", str(image), "-o", "-"],
                                    env=self.env, stdout=subprocess.PIPE, stderr=err)
            assert proc.stdout is not None  # stdout=PIPE
            problem = None
            try:
                nodes = parse_fsimage_xml(proc.stdout, base, attr, depth)
            except ET.ParseError as error:
                problem = error
            finally:
                # Der Parser hört nach <INodeDirectorySection> auf, oder bricht
                # ab; oiv kann dann noch laufen und braucht nicht weiterzulaufen
                returncode = _stop(proc)
            if problem:
                err.seek(0)
                lines = [line for line in err.read().splitlines() if "NativeCodeLoader" not in line]
                code = f" (Exit-Code {returncode})" if returncode else ""
                raise HdfsError(f"oiv {image} fehlgeschlagen{code}, Ausgabe nicht lesbar: {problem}"
                                + "".join(f"\n{line}" for line in lines[-20:]))
        return nodes

    def write(self, changes: list, attr: str) -> list:
        """Setzt/entfernt das Attribut; liefert die Fehlermeldungen.

        Pfade mit demselben neuen Wert gehen gebündelt in einen Aufruf (bei
        replace/remove haben oft Tausende Pfade denselben neuen Wert). Ein
        Fehler an einem Pfad bricht nicht ab; ob jeder Pfad stimmt, prüft
        execute() danach ohnehin durch erneutes Lesen.
        """
        by_value = {}
        for change in changes:
            by_value.setdefault(change.new, []).append(change.path)
        tasks = [(new, pattern, count) for new, paths in by_value.items()
                 for pattern, count in path_globs(paths)]

        def run(task: tuple) -> subprocess.CompletedProcess:
            new, pattern, _ = task
            if new is None:
                return self.dfs("-setfattr", "-x", attr, pattern)
            # Anführungszeichen: Wert wird immer als Text behandelt, auch bei 0x/0s
            return self.dfs("-setfattr", "-n", attr, "-v", f'"{new}"', pattern)

        errors = []
        written = 0
        with ThreadPoolExecutor(self.parallel) as pool:
            futures = {pool.submit(run, task): task for task in tasks}
            # as_completed: Fortschritt in der Reihenfolge, in der Aufrufe fertig werden
            for future in as_completed(futures):
                _, pattern, count = futures[future]
                proc = future.result()
                lines = [line for _, line in command_errors(proc, "setfattr", pattern)]
                errors += lines
                written += count
                info(f"  [{written}/{len(changes)}] {'FEHLER' if lines else 'ok'}, {count} Pfade")
        return errors
