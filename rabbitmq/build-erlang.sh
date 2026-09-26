#!/usr/bin/env bash
#
# build-erlang.sh
#
# Baut Erlang/OTP auf der Build-VM als normaler User und erzeugt einen
# verschiebbaren Tarball für die Runtime-VMs:
#   - lädt otp_src_<OTP_VERSION>.tar.gz über den Nexus GitHub-Proxy
#   - prüft optional den SHA256 (OTP_SHA256 in versions.conf)
#   - configure ohne Java/wx/ODBC/GUI-Tools, mit OpenSSL (Pflicht für RabbitMQ)
#   - bricht ab, wenn configure die crypto-Applikation überspringt
#   - "make release" + Smoke-Test (crypto, ssl, OTP-Version)
#   - packt erlang-<OTP_VERSION>-el9-<arch>.tar.gz inkl. .sha256
#   - bietet den Upload in das Nexus Raw-Hosted-Repo an
#     (ohne Nachfrage: UPLOAD=j bzw. UPLOAD=n)
#
# Build-Abhängigkeiten vorher einmalig als root: install-build-deps.sh
# Arbeitsverzeichnis: $WORK_DIR (Default: ~/build/erlang)
#
# Läuft im User-Kontext (kein root).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/versions.conf"

if [[ $EUID -eq 0 ]]; then
    echo "Fehler: Bitte als normaler User ausführen, nicht als root." >&2
    exit 1
fi

MISSING=()
for pkg in gcc gcc-c++ make perl openssl-devel ncurses-devel tar gzip; do
    rpm -q "$pkg" &>/dev/null || MISSING+=("$pkg")
done
# curl über das Kommando prüfen: RHEL 9 hat je nach Installation curl oder curl-minimal
command -v curl &>/dev/null || MISSING+=("curl")
if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Fehler: Folgende Pakete fehlen: ${MISSING[*]}" >&2
    echo "Als root ausführen: $SCRIPT_DIR/install-build-deps.sh" >&2
    exit 1
fi

WORK_DIR=${WORK_DIR:-$HOME/build/erlang}
SRC_TGZ="otp_src_${OTP_VERSION}.tar.gz"
SRC_URL="${NEXUS_GITHUB}/erlang/otp/releases/download/OTP-${OTP_VERSION}/${SRC_TGZ}"
SRC_DIR="${WORK_DIR}/otp_src_${OTP_VERSION}"
RELEASE_NAME="erlang-${OTP_VERSION}"
RELEASE_DIR="${WORK_DIR}/release/${RELEASE_NAME}"
DIST_DIR="${WORK_DIR}/dist"

mkdir -p "$WORK_DIR" "$DIST_DIR" "${WORK_DIR}/release"

echo "== Lade Erlang/OTP ${OTP_VERSION} =="
if [[ -f "${WORK_DIR}/${SRC_TGZ}" ]]; then
    echo "Bereits vorhanden: ${WORK_DIR}/${SRC_TGZ}"
else
    curl -fL --netrc-optional -o "${WORK_DIR}/${SRC_TGZ}.part" "$SRC_URL"
    mv "${WORK_DIR}/${SRC_TGZ}.part" "${WORK_DIR}/${SRC_TGZ}"
fi

ACTUAL_SHA=$(sha256sum "${WORK_DIR}/${SRC_TGZ}" | awk '{print $1}')
if [[ -n "$OTP_SHA256" ]]; then
    if [[ "$ACTUAL_SHA" != "$OTP_SHA256" ]]; then
        echo "Fehler: SHA256 stimmt nicht." >&2
        echo "  erwartet: $OTP_SHA256" >&2
        echo "  gefunden: $ACTUAL_SHA" >&2
        exit 1
    fi
    echo "SHA256 ok."
else
    echo "Warnung: OTP_SHA256 nicht gesetzt, keine Prüfung." >&2
    echo "  Berechnet: $ACTUAL_SHA  (in versions.conf eintragen)" >&2
fi

echo "== Entpacke Quellcode =="
rm -rf "$SRC_DIR"
tar -C "$WORK_DIR" -xzf "${WORK_DIR}/${SRC_TGZ}"

cd "$SRC_DIR"
export ERL_TOP="$SRC_DIR"

echo "== configure =="
# Weggelassene Applikationen (RabbitMQ braucht keine davon, auch das
# Management-Web-UI nicht: rabbitmq_management läuft auf cowboy/ranch, die
# RabbitMQ selbst mitbringt, und braucht von OTP nur ssl, crypto, inets u. a.):
#   --without-javac     jinterface fehlt (Java <-> Erlang-Knoten). Kein JDK nötig.
#   --without-wx        wx fehlt (wxWidgets-Binding). Damit fehlt die Grundlage
#                       aller OTP-GUIs; spart wxGTK3-devel auf der Build-VM.
#   --without-odbc      odbc fehlt (SQL-Datenbanken über ODBC). Spart unixODBC-devel.
#   --without-debugger  debugger fehlt (grafischer Debugger, Modul int).
#   --without-observer  observer fehlt: GUI, crashdump_viewer, etop, ttb.
#   --without-et        et fehlt (Event Tracer, GUI-Visualisierung von Traces).
#
# Was zur Diagnose bleibt:
#   - rabbitmq-diagnostics (inkl. "observer", das ist das textbasierte
#     observer_cli aus RabbitMQ, nicht der OTP-observer)
#   - runtime_tools (dbg, msacc, observer_backend) ist weiter enthalten.
#     Ein OTP mit GUI auf einem anderen Rechner kann sich per Distribution
#     verbinden und den Knoten mit observer anzeigen.
#   - erl_crash.dump auf einem Rechner mit vollem OTP auswerten
#     (crashdump_viewer).
./configure \
    --without-javac \
    --without-wx \
    --without-odbc \
    --without-debugger \
    --without-observer \
    --without-et \
    --with-ssl

if [[ -f lib/crypto/SKIP ]]; then
    echo "Fehler: configure hat die crypto-Applikation deaktiviert:" >&2
    cat lib/crypto/SKIP >&2
    echo "Ohne crypto/ssl startet RabbitMQ nicht. openssl-devel prüfen." >&2
    exit 1
fi

echo "== make (-j$(nproc)) =="
make -j"$(nproc)"

echo "== make release =="
rm -rf "$RELEASE_DIR"
make release RELEASE_ROOT="$RELEASE_DIR"

echo "== Smoke-Test =="
# Install -minimal setzt ROOTDIR in bin/erl. Auf der Runtime-VM wird es mit
# dem dortigen Zielpfad erneut aufgerufen.
(cd "$RELEASE_DIR" && ./Install -minimal "$RELEASE_DIR" >/dev/null)
"$RELEASE_DIR/bin/erl" -noshell -eval '
    ok = crypto:start(),
    ok = ssl:start(),
    [{_, _, OpenSSL}] = crypto:info_lib(),
    io:format("OTP ~s, ERTS ~s, JIT: ~p, ~s~n",
              [erlang:system_info(otp_release), erlang:system_info(version),
               erlang:system_info(emu_flavor), OpenSSL]),
    halt().'

echo "== Packe Tarball =="
tar -C "${WORK_DIR}/release" -czf "${DIST_DIR}/${ERLANG_TARBALL}" "$RELEASE_NAME"
(cd "$DIST_DIR" && sha256sum "$ERLANG_TARBALL" > "${ERLANG_TARBALL}.sha256")

echo
echo "Erzeugt:"
echo "  ${DIST_DIR}/${ERLANG_TARBALL}"
echo "  ${DIST_DIR}/${ERLANG_TARBALL}.sha256"

UPLOAD_BASE="${NEXUS_BUILDS}/erlang/${OTP_VERSION}"
echo
DO_UPLOAD=${UPLOAD:-}
if [[ -z "$DO_UPLOAD" ]]; then
    read -rp "Nach '${UPLOAD_BASE}/' hochladen? [j/N]: " DO_UPLOAD || true
fi
if [[ "$DO_UPLOAD" =~ ^[JjYy]$ ]]; then
    AUTH=(--netrc-optional)
    if [[ -n "${NEXUS_USER:-}" ]]; then
        AUTH=(-u "$NEXUS_USER")
    fi
    for f in "$ERLANG_TARBALL" "${ERLANG_TARBALL}.sha256"; do
        echo "-> $f"
        curl -fsS "${AUTH[@]}" --upload-file "${DIST_DIR}/${f}" "${UPLOAD_BASE}/${f}"
    done
    echo "Upload fertig."
fi
