#!/bin/bash
# vault-pull-cron.sh — Phase 1 (HBE-757): Vault-Mirror alle 2 Min aktualisieren
# Läuft als Cron-Job auf dem Hetzner-Host (außerhalb Docker).
# Zieht Svens neue Commits von GitHub und hält den Mirror aktuell.
set -euo pipefail

VAULT_DIR="/opt/vault-mirror"
ENV_FILE="/opt/mein-assistent/.env"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [[ ! -d "$VAULT_DIR/.git" ]]; then
    echo "[$TS] SKIP: $VAULT_DIR ist kein git-Repo — setup-vault-mirror.sh zuerst ausführen"
    exit 0
fi

# Token aus .env laden (für pull/fetch nötig bei privaten Repos)
GITHUB_BOT_TOKEN=$(grep '^GITHUB_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
CLONE_URL="https://x-access-token:${GITHUB_BOT_TOKEN}@github.com/herbertgruppe/vault-memory.git"

# Fetch + reset — übernimmt Svens neue Commits, behält Lenas bereits gepushte Commits
git -C "$VAULT_DIR" fetch "$CLONE_URL" master --quiet 2>&1 || { echo "[$TS] FEHLER: git fetch fehlgeschlagen"; exit 1; }
git -C "$VAULT_DIR" reset --hard origin/master --quiet

echo "[$TS] OK: vault-mirror aktualisiert ($(git -C "$VAULT_DIR" rev-parse --short HEAD))"
