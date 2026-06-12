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
if [[ ! -f "$ENV_FILE" ]]; then
    echo "FEHLER: $ENV_FILE nicht gefunden." >&2
    exit 1
fi
GITHUB_BOT_TOKEN=$(grep '^GITHUB_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")

if [[ -z "$GITHUB_BOT_TOKEN" || "$GITHUB_BOT_TOKEN" == "ghp_..." ]]; then
    echo "FEHLER: GITHUB_BOT_TOKEN ist leer oder nicht gesetzt in $ENV_FILE" >&2
    exit 1
fi

# Credentials in ~/.netrc speichern (chmod 600).
# git nutzt ~/.netrc automatisch für HTTPS-Auth — kein Token in URLs nötig.
NETRC_FILE="$HOME/.netrc"
NETRC_ENTRY="machine github.com login x-access-token password $GITHUB_BOT_TOKEN"
if grep -q "machine github.com" "$NETRC_FILE" 2>/dev/null; then
    # Update existing entry
    sed -i "/machine github.com/c\\$NETRC_ENTRY" "$NETRC_FILE"
    echo "==> ~/.netrc: Eintrag für github.com aktualisiert."
else
    echo "$NETRC_ENTRY" >> "$NETRC_FILE"
    echo "==> ~/.netrc: Eintrag für github.com angelegt."
fi
chmod 600 "$NETRC_FILE"

echo "==> Klone herbertgruppe/vault-memory nach $VAULT_DIR ..."
if [[ -d "$VAULT_DIR/.git" ]]; then
    echo "    Bereits vorhanden — übersprungen."
else
    GIT_TERMINAL_PROMPT=0 git clone "$REPO_URL" "$VAULT_DIR"
fi

# Commit-Author-Config
git -C "$VAULT_DIR" config user.name  "mein-assistent-bot"
git -C "$VAULT_DIR" config user.email "bot@herbertgruppe.com"

# 09 Lena Inbox anlegen falls nicht vorhanden
mkdir -p "$VAULT_DIR/09 Lena Inbox"
if [[ ! -f "$VAULT_DIR/09 Lena Inbox/.gitkeep" ]]; then
    touch "$VAULT_DIR/09 Lena Inbox/.gitkeep"
    git -C "$VAULT_DIR" add "09 Lena Inbox/.gitkeep"
    GIT_AUTHOR_NAME="Jonas (HBE-Setup)" GIT_AUTHOR_EMAIL="setup@herbertgruppe.com" \
    GIT_TERMINAL_PROMPT=0 git -C "$VAULT_DIR" commit -m "[Setup] 09 Lena Inbox anlegen"
    GIT_TERMINAL_PROMPT=0 git -C "$VAULT_DIR" push origin master
fi

echo "==> Cron-Job einrichten (alle 2 Minuten pull) ..."
CRON_LINE="*/2 * * * * /opt/mein-assistent/scripts/vault-pull-cron.sh >> /var/log/vault-pull-cron.log 2>&1"
( crontab -l 2>/dev/null | grep -qF "vault-pull-cron.sh" ) || \
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -

echo "==> Logrotate konfigurieren ..."
LOGROTATE_FILE="/etc/logrotate.d/vault-lena"
if [[ ! -f "$LOGROTATE_FILE" ]]; then
    cat > "$LOGROTATE_FILE" <<'LOGROTATE'
/var/log/vault-pull-cron.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
LOGROTATE
    echo "    $LOGROTATE_FILE angelegt."
else
    echo "    $LOGROTATE_FILE bereits vorhanden — übersprungen."
fi

echo ""
echo "Vault-Mirror eingerichtet: $VAULT_DIR"
echo "Cron: alle 2 Minuten pull von origin/master"
echo ""
echo "Nächste Schritte:"
echo "  1. docker compose up -d --build api   (neues Image mit git + Vault-Mount)"
echo "  2. Endpoint testen: POST /api/lena/vault/write"
