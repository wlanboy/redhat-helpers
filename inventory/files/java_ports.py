#!/usr/bin/env python3
"""Listet alle Java-Prozesse mit ihren TCP-Listen-Ports als JSON.

Wird von java-ports.yml per ansible.builtin.script auf den Zielhosts
ausgeführt. Braucht nur python3 und ss (iproute). Ohne root sieht ss -p
nur die Prozesse des eigenen Users.

Optionen:
  --probe              Jeden Port per HTTP(S) auf Metrics-Pfade prüfen
  --paths a,b,...      Zu prüfende Pfade (Default: /actuator/prometheus)
  --timeout SEKUNDEN   Timeout pro Probe (Default: 2)
"""
import argparse
import ipaddress
import json
import os
import re
import ssl
import subprocess
import urllib.request

USERS_RE = re.compile(r'\("([^"]+)",pid=(\d+)')
CLASS_RE = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")
# java-Optionen, deren Wert als eigenes Argument folgt
VALUE_OPTS = ("-cp", "-classpath", "--class-path", "-p", "--module-path",
              "--add-modules", "--add-opens", "--add-exports", "--add-reads",
              "--upgrade-module-path", "--limit-modules", "--patch-module")


def read_proc(pid, name):
    try:
        with open("/proc/%s/%s" % (pid, name), "rb") as f:
            return f.read()
    except OSError:
        return b""


def is_java(pid, comm):
    """java-Binary oder anderer Launcher (jwebserver, umbenannt) mit JVM."""
    if comm == "java":
        return True
    try:
        if os.path.basename(os.readlink("/proc/%s/exe" % pid)) == "java":
            return True
    except OSError:
        pass
    return b"/libjvm.so" in read_proc(pid, "maps")


def parse_addr(addr):
    """ss-Adresse -> ipaddress-Objekt, None bei Wildcard (*)."""
    addr = addr.strip("[]")
    if addr == "*":
        return None
    ip = ipaddress.ip_address(addr)
    # IPv4-mapped wie ::ffff:127.0.0.1
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def is_wildcard(addr):
    ip = parse_addr(addr)
    return ip is None or ip.is_unspecified


def is_loopback(addr):
    ip = parse_addr(addr)
    return ip is not None and ip.is_loopback


def proc_user(pid):
    try:
        import pwd
        return pwd.getpwuid(os.stat("/proc/%s" % pid).st_uid).pw_name
    except (OSError, KeyError):
        return ""


def app_name(args, environ):
    """Name der Anwendung aus Kommandozeile/Umgebung ableiten."""
    for a in args:
        if a.startswith("-Dspring.application.name="):
            return a.split("=", 1)[1]
    if environ.get("SPRING_APPLICATION_NAME"):
        return environ["SPRING_APPLICATION_NAME"]
    if "-jar" in args:
        idx = args.index("-jar") + 1
        if idx < len(args):
            jar = os.path.basename(args[idx])
            # mirrorservice-1.2.3.jar -> mirrorservice
            return re.sub(r"(-\d[\w.\-]*)?\.jar$", "", jar)
    # Launcher wie jwebserver, kafka, ... -> dessen Name
    launcher = os.path.basename(args[0]) if args else "java"
    if launcher != "java":
        return launcher
    # erstes Argument ohne Optionen nach java = Main-Klasse
    skip = False
    for a in args[1:]:
        if skip:
            skip = False
            continue
        if a in VALUE_OPTS:
            skip = True
            continue
        if a.startswith("-"):
            continue
        if "/" in a:
            # -m modul/klasse
            a = a.rsplit("/", 1)[-1]
        if CLASS_RE.match(a):
            return a.rsplit(".", 1)[-1]
    return "java"


def listening_sockets():
    out = subprocess.run(
        ["ss", "-Hltnp"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        universal_newlines=True, check=False,
    ).stdout
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 6:
            continue
        local = cols[3]
        addr, _, port = local.rpartition(":")
        # Interface-Suffix wie 127.0.0.1%lo entfernen
        addr = addr.split("%", 1)[0]
        for comm, pid in USERS_RE.findall(" ".join(cols[5:])):
            yield comm, int(pid), addr, int(port)


def probe(addr, port, paths, timeout):
    hosts = ["127.0.0.1", "::1"] if is_wildcard(addr) else [str(parse_addr(addr))]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for scheme in ("http", "https"):
        for host in hosts:
            h = "[%s]" % host if ":" in host else host
            for path in paths:
                url = "%s://%s:%d%s" % (scheme, h, port, path)
                try:
                    kw = {"context": ctx} if scheme == "https" else {}
                    with urllib.request.urlopen(url, timeout=timeout, **kw) as r:
                        body = r.read(65536).decode("utf-8", "replace")
                        if r.status == 200 and "# TYPE" in body:
                            return scheme, path
                except Exception:
                    continue
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--paths", default="/actuator/prometheus")
    ap.add_argument("--timeout", type=float, default=2.0)
    opts = ap.parse_args()
    paths = [p for p in opts.paths.split(",") if p]

    procs = {}
    seen = set()
    for comm, pid, addr, port in listening_sockets():
        if (pid, port) in seen or not is_java(pid, comm):
            continue
        seen.add((pid, port))
        if pid not in procs:
            args = [a.decode("utf-8", "replace")
                    for a in read_proc(pid, "cmdline").split(b"\0") if a]
            environ = dict(
                e.decode("utf-8", "replace").split("=", 1)
                for e in read_proc(pid, "environ").split(b"\0") if b"=" in e
            )
            procs[pid] = {
                "pid": pid,
                "user": proc_user(pid),
                "app": app_name(args, environ),
                "cmdline": " ".join(args),
                "ports": [],
            }
        entry = {
            "address": addr,
            "port": port,
            "local_only": is_loopback(addr),
            "scheme": None,
            "metrics_path": None,
        }
        if opts.probe:
            entry["scheme"], entry["metrics_path"] = probe(
                addr, port, paths, opts.timeout)
        procs[pid]["ports"].append(entry)

    result = sorted(procs.values(), key=lambda p: (p["app"], p["pid"]))
    for p in result:
        p["ports"].sort(key=lambda e: e["port"])
    print(json.dumps(result))


if __name__ == "__main__":
    main()
