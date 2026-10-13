#!/usr/bin/env python3
"""
Archiv-Metadaten-Crawler fuer das Absender-Profil (Lena Mail-Konzept).

Liest NUR Metadaten: Absender, Betreff, Ordner, Zeitstempel, conversationId.
KEINE Mailinhalte, keine Anhaenge.

Ergebnis: /root/mail-archive/archive.db (SQLite)
Log:      /root/mail-archive/crawl.log
Resumierbar: bereits fertige Ordner werden uebersprungen.
"""
import json
import os
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, "/opt/mein-assistent")

OUT_DIR = "/root/mail-archive"
DB_PATH = os.path.join(OUT_DIR, "archive.db")
LOG_PATH = os.path.join(OUT_DIR, "crawl.log")
TOKEN_FILE = "/var/lib/docker/volumes/mein-assistent_users_data/_data/sherbert/auth/outlook_token.json"

# Ordner die gecrawlt werden. (Anzeigename, Rolle)
#   posteingang_archiv = erledigte Posteingangsmails -> Basis fuer Absender-Profil
#   gesendet           = fuer Antwort-Erkennung ueber conversationId
#   thema              = Themenordner, fuer die Ablage-Systematik-Analyse
TARGETS = [
    ("Posteingang erledigt 2026", "posteingang_archiv"),
    ("Posteingang erledigt 2024", "posteingang_archiv"),
    ("Gesendete Elemente", "gesendet"),
    ("KI AB Mails", "gesendet"),
    ("Posteingang", "posteingang_aktiv"),
    ("Newsletter", "thema"),
    ("Systemmeldungen", "thema"),
    ("BTGA", "thema"),
    ("BTGA BIM", "thema"),
    ("ITGA Hessen", "thema"),
    ("Rotary", "thema"),
    ("vHU Energieausschuss", "thema"),
    ("KHS", "thema"),
    ("J. Lauber", "thema"),
    ("PW und Registrierungen", "thema"),
    ("KI Anrufe", "thema"),
    ("Plaud AI", "thema"),
]


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()}  {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS mails (
            message_id       TEXT PRIMARY KEY,
            conversation_id  TEXT,
            folder_name      TEXT,
            folder_role      TEXT,
            sender_email     TEXT,
            sender_name      TEXT,
            subject          TEXT,
            received_at      TEXT,
            has_attachments  INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_conv   ON mails(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_sender ON mails(sender_email);
        CREATE INDEX IF NOT EXISTS idx_role   ON mails(folder_role);

        CREATE TABLE IF NOT EXISTS sent (
            message_id       TEXT PRIMARY KEY,
            conversation_id  TEXT,
            sent_at          TEXT,
            to_emails        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sent_conv ON sent(conversation_id);

        CREATE TABLE IF NOT EXISTS progress (
            folder_name  TEXT PRIMARY KEY,
            folder_id    TEXT,
            next_link    TEXT,
            done         INTEGER DEFAULT 0,
            count        INTEGER DEFAULT 0
        );
    """)
    db.commit()
    return db


def get_tool():
    from tools.outlook_graph_tool import OutlookGraphTool
    return OutlookGraphTool(token_file=TOKEN_FILE)


def graph_get(tool, url, retries=5):
    import requests
    for attempt in range(retries):
        tool._ensure_valid_token()
        headers = {"Authorization": f"Bearer {tool.access_token}"}
        try:
            r = requests.get(url, headers=headers, timeout=60)
        except Exception as exc:
            log(f"    Netzwerkfehler ({exc}), Versuch {attempt+1}/{retries}")
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "10"))
            log(f"    429 Throttling, warte {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 401:
            log("    401 - erzwinge Token-Refresh")
            tool._refresh_access_token()
            time.sleep(2)
            continue
        log(f"    HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(5)
    raise RuntimeError(f"Graph-Abruf endgueltig fehlgeschlagen: {url[:120]}")


def build_folder_map(tool):
    """Alle Ordner rekursiv einsammeln -> {displayName: id}."""
    out = {}

    def walk(url, depth=0):
        if depth > 3:
            return
        data = graph_get(tool, url)
        for f in data.get("value", []):
            name = f.get("displayName", "")
            if name and name not in out:
                out[name] = f["id"]
            if (f.get("childFolderCount") or 0) > 0:
                walk(
                    "https://graph.microsoft.com/v1.0/me/mailFolders/"
                    f"{f['id']}/childFolders?%24top=100&%24select=displayName,id,childFolderCount",
                    depth + 1,
                )

    walk("https://graph.microsoft.com/v1.0/me/mailFolders"
         "?%24top=100&%24select=displayName,id,childFolderCount")
    return out


def crawl_folder(tool, db, folder_name, folder_id, role):
    row = db.execute("SELECT next_link, done, count FROM progress WHERE folder_name=?",
                     (folder_name,)).fetchone()
    if row and row[1]:
        log(f"  [{folder_name}] bereits fertig ({row[2]} Mails) - uebersprungen")
        return row[2]

    if role == "gesendet":
        select = "id,conversationId,sentDateTime,toRecipients"
    else:
        select = "id,conversationId,subject,from,receivedDateTime,hasAttachments"

    url = (row[0] if row and row[0] else
           f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/messages"
           f"?%24top=500&%24select={urllib.parse.quote(select)}")

    total = row[2] if row else 0
    page = 0
    while url:
        data = graph_get(tool, url)
        items = data.get("value", [])
        for m in items:
            if role == "gesendet":
                tos = ",".join(
                    (r.get("emailAddress", {}) or {}).get("address", "").lower()
                    for r in (m.get("toRecipients") or [])
                )
                db.execute(
                    "INSERT OR IGNORE INTO sent VALUES (?,?,?,?)",
                    (m.get("id"), m.get("conversationId"), m.get("sentDateTime"), tos),
                )
            else:
                sender = (m.get("from") or {}).get("emailAddress", {}) or {}
                db.execute(
                    "INSERT OR IGNORE INTO mails VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        m.get("id"),
                        m.get("conversationId"),
                        folder_name,
                        role,
                        (sender.get("address") or "").lower(),
                        sender.get("name") or "",
                        (m.get("subject") or "")[:300],
                        m.get("receivedDateTime"),
                        1 if m.get("hasAttachments") else 0,
                    ),
                )
        total += len(items)
        page += 1
        url = data.get("@odata.nextLink")
        db.execute(
            "INSERT INTO progress(folder_name,folder_id,next_link,done,count) VALUES (?,?,?,0,?) "
            "ON CONFLICT(folder_name) DO UPDATE SET next_link=excluded.next_link, count=excluded.count",
            (folder_name, folder_id, url, total),
        )
        db.commit()
        if page % 5 == 0:
            log(f"  [{folder_name}] {total} Mails ...")

    db.execute("UPDATE progress SET done=1, next_link=NULL WHERE folder_name=?", (folder_name,))
    db.commit()
    log(f"  [{folder_name}] FERTIG: {total} Mails")
    return total


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log("=" * 60)
    log("Archiv-Crawl gestartet")
    db = init_db()
    tool = get_tool()

    if not tool._ensure_valid_token():
        log("FEHLER: Token ungueltig und nicht erneuerbar")
        return 1

    log("Ordner-Map wird gebaut ...")
    fmap = build_folder_map(tool)
    log(f"  {len(fmap)} Ordner gefunden")

    grand = 0
    for name, role in TARGETS:
        fid = fmap.get(name)
        if not fid:
            log(f"  [{name}] NICHT GEFUNDEN - uebersprungen")
            continue
        try:
            grand += crawl_folder(tool, db, name, fid, role)
        except Exception as exc:
            log(f"  [{name}] FEHLER: {exc}")

    log(f"Crawl beendet. Gesamt: {grand} Datensaetze")
    n_mails = db.execute("SELECT COUNT(*) FROM mails").fetchone()[0]
    n_sent = db.execute("SELECT COUNT(*) FROM sent").fetchone()[0]
    log(f"  mails-Tabelle: {n_mails}")
    log(f"  sent-Tabelle:  {n_sent}")
    db.close()
    with open(os.path.join(OUT_DIR, "crawl.done"), "w") as fh:
        fh.write("ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
