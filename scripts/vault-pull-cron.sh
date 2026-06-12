#!/bin/bash
# vault-pull-cron.sh — Phase 1 (HBE-757): Vault-Mirror alle 2 Min aktualisieren
# Läuft als Cron-Job auf dem Hetzner-Host (außerhalb Docker).
# Credentials via ~/.netrc (angelegt von setup-vault-mirror.sh, chmod 600).
set -euo pipefail

VAULT_DIR="/opt/vault-mirror"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [[ ! -d "$VAULT_DIR/.git" ]]; then
    echo "[$TS] SKIP: $VAULT_DIR ist kein git-Repo — setup-vault-mirror.sh zuerst ausführen"
    exit 0
fi

# Fetch + merge (ff-only) — übernimmt Svens neue Commits ohne lokale Commits zu verwerfen.
# ~/.netrc (root, chmod 600) liefert die Credentials für HTTPS.
GIT_TERMINAL_PROMPT=0 git -C "$VAULT_DIR" fetch origin master --quiet 2>&1 \
    || { echo "[$TS] FEHLER: git fetch fehlgeschlagen"; exit 1; }
git -C "$VAULT_DIR" merge --ff-only FETCH_HEAD --quiet

echo "[$TS] OK: vault-mirror aktualisiert ($(git -C "$VAULT_DIR" rev-parse --short HEAD))"
