#!/usr/bin/env bash
#
# create-ssh-user.sh
#
# Legt auf RHEL 9 einen neuen User an und richtet SSH-Key-Login ein:
#   - fragt interaktiv nach Username und SSH Public Key
#   - legt den User an (useradd)
#   - legt ~/.ssh mit Modus 700 an (Pflicht für sshd, 600 wäre nicht traversierbar)
#   - legt ~/.ssh/authorized_keys mit Modus 600 an und schreibt den Pubkey hinein
#   - setzt korrektes Ownership (user:user)
#
# Muss als root ausgeführt werden.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Fehler: Dieses Skript muss als root ausgeführt werden." >&2
    exit 1
fi

read -rp "Username: " USERNAME

if [[ -z "$USERNAME" ]]; then
    echo "Fehler: Username darf nicht leer sein." >&2
    exit 1
fi

if ! [[ "$USERNAME" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "Fehler: Ungültiger Username '$USERNAME'." >&2
    exit 1
fi

read -rp "SSH Public Key: " SSH_PUBKEY

if [[ -z "$SSH_PUBKEY" ]]; then
    echo "Fehler: SSH Public Key darf nicht leer sein." >&2
    exit 1
fi

if ! [[ "$SSH_PUBKEY" =~ ^(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)\ [A-Za-z0-9+/]+=*(\ .*)?$ ]]; then
    echo "Fehler: '$SSH_PUBKEY' sieht nicht wie ein gültiger SSH Public Key aus." >&2
    exit 1
fi

if id "$USERNAME" &>/dev/null; then
    echo "Fehler: User '$USERNAME' existiert bereits." >&2
    exit 1
fi

echo "Lege User '$USERNAME' an ..."
useradd -m -s /bin/bash "$USERNAME"

HOME_DIR=$(getent passwd "$USERNAME" | cut -d: -f6)
SSH_DIR="$HOME_DIR/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"

mkdir -p "$SSH_DIR"
echo "$SSH_PUBKEY" > "$AUTH_KEYS"

chmod 700 "$SSH_DIR"
chmod 600 "$AUTH_KEYS"
chown -R "$USERNAME:$USERNAME" "$SSH_DIR"

echo "Fertig."
echo "  User:            $USERNAME"
echo "  Home:            $HOME_DIR"
echo "  Authorized Keys: $AUTH_KEYS"
