#!/usr/bin/env python3
"""
Baut das Absender-Profil aus dem Archiv.

Erzeugt:
  /root/mail-archive/sender_profile.db   — fuer den Poller
  /root/mail-archive/profil-report.md    — zur Durchsicht durch Sven

Regeln (Entscheidung Sven 14.09.2026):
  safe_archive    : >=20 Mails Historie UND 0 Reaktionen
  conversational  : >=10 Mails UND Reaktionsquote >=50 %
  routing         : >=3 Mails in Themenordnern UND >=90 % in EINEM Ordner
"""
import sqlite3
from collections import defaultdict

SRC = "/root/mail-archive/archive.db"
OUT = "/root/mail-archive/sender_profile.db"
REPORT = "/root/mail-archive/profil-report.md"

SAFE_MIN = 20          # Mindest-Historie fuer automatisches Ablegen
CONV_MIN = 10          # Mindest-Historie fuer "Gespraechspartner"
CONV_RATE = 0.50
ROUTE_MIN = 3          # Mindest-Mails fuer Ordner-Routing
ROUTE_SHARE = 0.90

EXCLUDE = {"s.herbert@herbert.de", "sven.herbert@herbert.de"}  # Selbstversand

src = sqlite3.connect(SRC)
c = src.cursor()
c.execute("CREATE TEMP TABLE cs AS SELECT DISTINCT conversation_id FROM sent WHERE conversation_id IS NOT NULL")
c.execute("CREATE INDEX ix ON cs(conversation_id)")

# ---- Basisdaten je Absender (nur Posteingang-Rollen zaehlen fuer die Quote)
base = {}
for em, nm, n, r in c.execute("""
        SELECT sender_email, MAX(sender_name), COUNT(*),
               SUM(CASE WHEN conversation_id IN (SELECT conversation_id FROM cs) THEN 1 ELSE 0 END)
        FROM mails
        WHERE folder_role LIKE 'posteingang%' AND sender_email <> ''
        GROUP BY sender_email"""):
    base[em] = {"name": nm or "", "n": n, "r": r, "deleted": 0, "folders": {}}

# ---- Geloeschte als Zusatzsignal
for em, n in c.execute("""SELECT sender_email, COUNT(*) FROM mails
                          WHERE folder_role='geloescht' AND sender_email<>''
                          GROUP BY sender_email"""):
    base.setdefault(em, {"name": "", "n": 0, "r": 0, "deleted": 0, "folders": {}})["deleted"] = n

# ---- Ordnerverteilung (Themenordner)
for em, fn, n in c.execute("""SELECT sender_email, folder_name, COUNT(*) FROM mails
                              WHERE folder_role='thema' AND sender_email<>''
                              GROUP BY sender_email, folder_name"""):
    base.setdefault(em, {"name": "", "n": 0, "r": 0, "deleted": 0, "folders": {}})["folders"][fn] = n

src.close()

# ---- Bewertung
out = sqlite3.connect(OUT)
out.executescript("""
DROP TABLE IF EXISTS sender_profile;
CREATE TABLE sender_profile (
    sender_email    TEXT PRIMARY KEY,
    sender_name     TEXT,
    n_inbox         INTEGER,
    n_replied       INTEGER,
    reply_rate      REAL,
    n_deleted       INTEGER,
    verdict         TEXT,
    route_folder    TEXT,
    route_share     REAL
);
""")

stats = defaultdict(int)
rows = []
for em, d in base.items():
    if em in EXCLUDE:
        stats["ausgeschlossen"] += 1
        continue
    n, r = d["n"], d["r"]
    rate = (r / n) if n else 0.0

    if n >= SAFE_MIN and r == 0:
        verdict = "safe_archive"
    elif n >= CONV_MIN and rate >= CONV_RATE:
        verdict = "conversational"
    else:
        verdict = "neutral"
    stats[verdict] += 1

    route, share = None, None
    if d["folders"]:
        tot = sum(d["folders"].values())
        top_f = max(d["folders"], key=d["folders"].get)
        sh = d["folders"][top_f] / tot
        if tot >= ROUTE_MIN and sh >= ROUTE_SHARE:
            route, share = top_f, round(sh, 3)
            stats["mit_routing"] += 1

    rows.append((em, d["name"], n, r, round(rate, 4), d["deleted"], verdict, route, share))

out.executemany("INSERT INTO sender_profile VALUES (?,?,?,?,?,?,?,?,?)", rows)
out.execute("CREATE INDEX idx_v ON sender_profile(verdict)")
out.commit()

# ---- Report
tot_inbox = sum(d["n"] for e, d in base.items() if e not in EXCLUDE)
safe_mails = sum(d["n"] for e, d in base.items()
                 if e not in EXCLUDE and d["n"] >= SAFE_MIN and d["r"] == 0)

lines = []
A = lines.append
A("# Absender-Profil — Ergebnis\n")
A(f"Erzeugt aus {tot_inbox:,} Posteingangsmails von {len(rows):,} Absendern.\n".replace(",", "."))
A(f"- Schwellwert automatisches Ablegen: **{SAFE_MIN} Mails ohne jede Reaktion**")
A(f"- Gespraechspartner: >={CONV_MIN} Mails und >={int(CONV_RATE*100)} % Reaktionsquote")
A(f"- Ordner-Routing: >={ROUTE_MIN} Mails und >={int(ROUTE_SHARE*100)} % in einem Ordner\n")
A("## Einstufung\n")
A("| Einstufung | Absender |")
A("|---|---:|")
A(f"| Automatisch ablegen | {stats['safe_archive']} |")
A(f"| Gespraechspartner | {stats['conversational']} |")
A(f"| Neutral (LLM entscheidet) | {stats['neutral']} |")
A(f"| mit Ordner-Routing | {stats['mit_routing']} |")
A(f"\nAbgedecktes Volumen durch automatisches Ablegen: **{safe_mails:,}** Mails "
  f"({safe_mails*100/max(tot_inbox,1):.1f} % der Historie)\n".replace(",", "."))

A("\n## Automatisch ablegen — Top 40\n")
A("| Mails | geloescht | Absender |")
A("|---:|---:|---|")
for em, nm, n, r, rate, dele, v, ro, sh in sorted(
        [x for x in rows if x[6] == "safe_archive"], key=lambda x: -x[2])[:40]:
    A(f"| {n} | {dele} | `{em}` |")

A("\n## Gespraechspartner — alle\n")
A("| Quote | Mails | Absender |")
A("|---:|---:|---|")
for em, nm, n, r, rate, dele, v, ro, sh in sorted(
        [x for x in rows if x[6] == "conversational"], key=lambda x: -x[4]):
    A(f"| {rate*100:.0f} % | {n} | `{em}` — {nm} |")

A("\n## Ordner-Routing — Top 40\n")
A("| Anteil | Mails | Zielordner | Absender |")
A("|---:|---:|---|---|")
rt = [x for x in rows if x[7]]
for em, nm, n, r, rate, dele, v, ro, sh in sorted(rt, key=lambda x: -x[2])[:40]:
    A(f"| {sh*100:.0f} % | {n} | {ro} | `{em}` |")

open(REPORT, "w", encoding="utf-8").write("\n".join(lines))

print(f"sender_profile.db geschrieben: {len(rows)} Absender")
for k, v in sorted(stats.items()):
    print(f"  {k:18s} {v}")
print(f"Volumen automatisch ablegen: {safe_mails} von {tot_inbox} ({safe_mails*100/max(tot_inbox,1):.1f} %)")
print(f"Report: {REPORT}")
out.close()
