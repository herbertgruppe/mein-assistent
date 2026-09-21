#!/usr/bin/env python3
"""
Traegt Rohtranskripte aus Plaud fuer bestehende Protokolle nach.

Hintergrund: Bis September 2026 wurde nur das fertige Protokoll gespeichert,
nicht die Quelle. Das Transkript lag ausschliesslich in Plaud — verschwindet
die Aufnahme dort, laesst sich das Protokoll nicht mehr ueberpruefen.
Plaud selbst haelt die Transkripte unbegrenzt vor (geprueft bis Oktober 2023),
sie lassen sich also vollstaendig nachziehen.

Laeuft auf dem HOST, nicht im Container: nur dort liegt der Plaud-Token
(/opt/mein-assistent/data/.plaud/tokens.json). Die Protokolle erreicht das
Skript ueber die API.

    python3 scripts/backfill_transcripts.py --dry-run
    python3 scripts/backfill_transcripts.py --limit 10
    python3 scripts/backfill_transcripts.py

Ohne --limit werden alle offenen Protokolle bearbeitet.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PLAUD_BASE = "https://platform.plaud.ai/developer/api/open/third-party/files/"
DEFAULT_TOKEN_FILE = "/opt/mein-assistent/data/.plaud/tokens.json"
DEFAULT_API = "http://127.0.0.1:8502"


def plaud_token(path: str) -> str:
    try:
        return json.loads(Path(path).read_text())["access_token"]
    except Exception as exc:
        sys.exit(f"Plaud-Token nicht lesbar ({path}): {exc}")


def api_key(env_file: str = "/opt/mein-assistent/.env") -> str:
    for line in Path(env_file).read_text().splitlines():
        if line.startswith("API_SECRET_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("API_SECRET_KEY nicht in .env gefunden")


def http(url: str, headers: dict, method: str = "GET", body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def transkript_text(datei: dict) -> str:
    """
    Baut aus source_list den lesbaren Transkripttext.

    Plaud liefert das Transkript als verschachtelte Struktur; Aufbau und
    Feldnamen sind nicht dokumentiert und haben sich schon geaendert (die
    Recording-IDs bekamen im September ein Praefix). Deshalb defensiv: erst
    die bekannten Textfelder versuchen, sonst die Rohstruktur als JSON
    ablegen. Lieber unformatiert archiviert als gar nicht.
    """
    teile = []
    for eintrag in datei.get("source_list") or []:
        daten = eintrag.get("data")
        if isinstance(daten, str):
            teile.append(daten)
            continue
        if isinstance(daten, list):
            for segment in daten:
                if isinstance(segment, dict):
                    sprecher = segment.get("speaker") or segment.get("speaker_name") or ""
                    text = segment.get("text") or segment.get("content") or ""
                    if text:
                        teile.append(f"{sprecher}: {text}" if sprecher else text)
                elif isinstance(segment, str):
                    teile.append(segment)
            continue
        if daten is not None:
            teile.append(json.dumps(daten, ensure_ascii=False))
    return "\n".join(t for t in teile if t).strip()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    p.add_argument("--limit", type=int, default=0, help="hoechstens N Protokolle")
    p.add_argument("--api", default=os.getenv("MEIN_ASSISTENT_API_URL", DEFAULT_API))
    p.add_argument("--token-file", default=os.getenv("PLAUD_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    args = p.parse_args()

    tok = plaud_token(args.token_file)
    key = api_key()
    plaud_headers = {"Authorization": f"Bearer {tok}"}
    api_headers = {"X-API-Key": key, "Content-Type": "application/json"}

    offen = http(f"{args.api}/api/protocols/missing-transcripts", api_headers)
    protokolle = offen.get("protocols", [])
    if args.limit:
        protokolle = protokolle[: args.limit]

    print(f"Protokolle ohne Transkript: {offen.get('count', 0)}")
    if args.limit:
        print(f"Bearbeite davon: {len(protokolle)}")
    print()

    erfolg = leer = fehler = 0
    for i, prot in enumerate(protokolle, 1):
        rid = prot["recording_id"]
        name = (prot.get("meeting_name") or "")[:45]
        try:
            datei = http(PLAUD_BASE + rid, plaud_headers)
            text = transkript_text(datei)
        except urllib.error.HTTPError as exc:
            print(f"{i:3}. FEHLER {exc.code:3}  {rid[:24]}  {name}")
            fehler += 1
            continue
        except Exception as exc:
            print(f"{i:3}. FEHLER      {rid[:24]}  {name} — {type(exc).__name__}")
            fehler += 1
            continue

        if not text:
            print(f"{i:3}. LEER        {rid[:24]}  {name}")
            leer += 1
            continue

        if args.dry_run:
            print(f"{i:3}. wuerde {len(text):7} Zeichen sichern  {name}")
            erfolg += 1
            continue

        try:
            http(
                f"{args.api}/api/protocols/{prot['draft_id']}/transcript",
                api_headers, method="PUT", body={"transcript": text},
            )
            print(f"{i:3}. ok     {len(text):7} Zeichen          {name}")
            erfolg += 1
        except Exception as exc:
            print(f"{i:3}. SPEICHERN FEHLGESCHLAGEN {name} — {exc}")
            fehler += 1

        time.sleep(0.4)  # Plaud nicht fluten

    print()
    print(f"Gesichert: {erfolg} | ohne Transkript in Plaud: {leer} | Fehler: {fehler}")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
