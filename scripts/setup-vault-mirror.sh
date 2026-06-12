#!/bin/bash
# setup-vault-mirror.sh — Phase 1 (HBE-757): Vault-Mirror auf Hetzner einrichten
#
# Voraussetzung: herbertgruppe/vault-memory existiert auf GitHub und
#   GITHUB_BOT_TOKEN ist in /opt/mein-assistent/.env gesetzt.
#
# Einmalig ausführen als root auf dem Hetzner-VPS:
#   bash /opt/mein-assistent/scripts/setup-vault-mirror.sh
set -euo pipefail

VAULT_DIR="/opt/vault-mirror"
REPO_URL="https://github.com/herbertgruppe/vault-memory.git"
ENV_FILE="/opt/mein-assistent/.env"

# Token aus .env laden
if [[ -f "$ENV_FILE" ]]; then
    GITHUB_BOT_TOKEN=$(grep '^GITHUB_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")
else
    echo "FEHLER: $ENV_FILE nicht gefunden." >&2
    exit 1
fi

if [[ -z "$GITHUB_BOT_TOKEN" || "$GITHUB_BOT_TOKEN" == "ghp_..." ]]; then
    echo "FEHLER: GITHUB_BOT_TOKEN ist leer oder nicht gesetzt in $ENV_FILE" >&2
    exit 1
fi

CLONE_URL="https://x-access-token:${GITHUB_BOT_TOKEN}@github.com/herbertgruppe/vault-memory.git"

echo "==> Klone herbertgruppe/vault-memory nach $VAULT_DIR ..."
if [[ -d "$VAULT_DIR/.git" ]]; then
    echo "    Bereits vorhanden — übersprungen."
else
    git clone "$CLONE_URL" "$VAULT_DIR"
fi

# Remote-URL auf anonyme HTTPS setzen (Token nur für Push nötig)
git -C "$VAULT_DIR" remote set-url origin "$REPO_URL"

# Commit-Author-Config für den Cron-Pull
git -C "$VAULT_DIR" config user.name  "mein-assistent-bot"
git -C "$VAULT_DIR" config user.email "bot@herbertgruppe.com"

# 09 Lena Inbox anlegen falls nicht vorhanden
mkdir -p "$VAULT_DIR/09 Lena Inbox"
if [[ ! -f "$VAULT_DIR/09 Lena Inbox/.gitkeep" ]]; then
    touch "$VAULT_DIR/09 Lena Inbox/.gitkeep"
    git -C "$VAULT_DIR" add "09 Lena Inbox/.gitkeep"
    GIT_AUTHOR_NAME="Jonas (HBE-Setup)" GIT_AUTHOR_EMAIL="setup@herbertgruppe.com" \
    git -C "$VAULT_DIR" commit -m "[Setup] 09 Lena Inbox anlegen"
    git -C "$VAULT_DIR" push "$CLONE_URL" master
fi

echo "==> Cron-Job einrichten (alle 2 Minuten pull) ..."
CRON_LINE="*/2 * * * * /opt/mein-assistent/scripts/vault-pull-cron.sh >> /var/log/vault-pull-cron.log 2>&1"
# Nur hinzufügen wenn noch nicht vorhanden
( crontab -l 2>/dev/null | grep -qF "vault-pull-cron.sh" ) || \
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -

echo "==> Logrotate konfigurieren ..."
cat > /etc/logrotate.d/vault-lena <<'LOGROTATE'
/var/log/vault-pull-cron.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
LOGROTATE

echo ""
echo "✅ Vault-Mirror eingerichtet: $VAULT_DIR"
echo "   Cron: alle 2 Minuten pull von origin/master"
echo ""
echo "Nächste Schritte:"
echo "  1. docker compose up -d --build api   (neues Image mit git + Vault-Mount)"
echo "  2. Endpoint testen: POST /api/lena/vault/write"
