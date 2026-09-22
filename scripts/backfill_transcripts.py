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


# Plaud weist den Default-User-Agent von urllib mit HTTP 403 ab — mit curl
# liefert derselbe Aufruf 200. Ohne diesen Header scheitert jeder Abruf, und
# zwar mit einem Statuscode, der nach fehlender Berechtigung aussieht statt
# nach Client-Filterung.
USER_AGENT = "herbert-backfill/1.0"


def http(url: str, headers: dict, method: str = "GET", body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={**headers, "User-Agent": USER_AGENT}, method=method
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def id_kandidaten(recording_id: str) -> list:
    """
    Schreibweisen, unter denen eine Aufnahme in Plaud zu finden sein kann.

    Protokolle aus der Zeit vor September 2026 haben die Recording-ID als
    nacktes 32-Hex gespeichert. Plaud loest seit dem Formatwechsel aber nur
    noch die praefigierte Form auf (`of_<32hex>`) — ein Abruf mit der alten
    Schreibweise scheitert mit 403/404. Von 30 Bestandsprotokollen betrifft
    das 29.

    Zuerst die gespeicherte Form (falls Plaud das Format erneut dreht),
    danach mit Praefix.
    """
    kandidaten = [recording_id]
    if "_" not in recording_id:
        kandidaten.append("of_" + recording_id)
    return kandidaten


def _zeit(ms) -> str:
    """Millisekunden als [mm:ss]."""
    try:
        s = int(ms) // 1000
        return "[%02d:%02d]" % (s // 60, s % 60)
    except (TypeError, ValueError):
        return ""


def transkript_text(datei: dict) -> str:
    """
    Baut aus source_list den lesbaren Transkripttext.

    Aufbau der Plaud-Antwort (nicht dokumentiert, empirisch ermittelt):

        source_list[] mit data_type
          "transaction"        -> das Transkript, data_content ist ein
                                  JSON-Array von Segmenten
          "transaction_polish" -> geglaettete Fassung, oft leer
          "outline"            -> Themengliederung, kein Volltext
        Segment: {content, speaker, original_speaker, start_time, end_time}

    `speaker` traegt den KORRIGIERTEN Namen (z. B. "Dr. Sven Herbert"),
    `original_speaker` die Rohzuordnung ("Speaker 1"). Wir nehmen die
    korrigierte Fassung — sie ist der Grund, warum im zweistufigen Workflow
    erst nach Svens Korrektur gezogen wird.

    Defensiv, weil sich das Format schon geaendert hat: laesst sich die
    Struktur nicht lesen, wird die Rohform als JSON abgelegt. Unformatiert
    archiviert schlaegt nicht archiviert.
    """
    eintraege = datei.get("source_list") or []
    roh = None

    for bevorzugt in ("transaction_polish", "transaction"):
        for eintrag in eintraege:
            if eintrag.get("data_type") != bevorzugt:
                continue
            inhalt = eintrag.get("data_content")
            if not inhalt:
                continue
            try:
                segmente = json.loads(inhalt) if isinstance(inhalt, str) else inhalt
            except (TypeError, ValueError):
                roh = roh or str(inhalt)
                continue
            if not isinstance(segmente, list):
                roh = roh or json.dumps(segmente, ensure_ascii=False)
                continue

            zeilen = []
            for seg in segmente:
                if not isinstance(seg, dict):
                    zeilen.append(str(seg))
                    continue
                text = (seg.get("content") or "").strip()
                if not text:
                    continue
                sprecher = (seg.get("speaker") or "").strip()
                marke = _zeit(seg.get("start_time"))
                kopf = " ".join(x for x in (marke, sprecher) if x)
                zeilen.append(f"{kopf}: {text}" if kopf else text)
            if zeilen:
                return "\n".join(zeilen).strip()

    return (roh or "").strip()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    p.add_argument("--limit", type=int, default=0, help="hoechstens N Protokolle")
    p.add_argument(
        "--force", action="store_true",
        help="auch bereits archivierte Transkripte neu holen — noetig nach einer "
             "Sprecherkorrektur in Plaud, weil die gespeicherte Fassung sonst die "
             "alte Zuordnung konserviert",
    )
    p.add_argument("--api", default=os.getenv("MEIN_ASSISTENT_API_URL", DEFAULT_API))
    p.add_argument("--token-file", default=os.getenv("PLAUD_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    args = p.parse_args()

    tok = plaud_token(args.token_file)
    key = api_key()
    plaud_headers = {"Authorization": f"Bearer {tok}"}
    api_headers = {"X-API-Key": key, "Content-Type": "application/json"}

    url = f"{args.api}/api/protocols/missing-transcripts"
    if args.force:
        url += "?include_existing=true"
    offen = http(url, api_headers)
    protokolle = offen.get("protocols", [])
    if args.limit:
        protokolle = protokolle[: args.limit]

    if args.force:
        print(f"Protokolle mit Aufnahme (alle, auch archivierte): {offen.get('count', 0)}")
    else:
        print(f"Protokolle ohne Transkript: {offen.get('count', 0)}")
    if args.limit:
        print(f"Bearbeite davon: {len(protokolle)}")
    print()

    erfolg = leer = fehler = 0
    for i, prot in enumerate(protokolle, 1):
        rid = prot["recording_id"]
        name = (prot.get("meeting_name") or "")[:45]
        datei = None
        letzter_fehler = ""
        for kandidat in id_kandidaten(rid):
            try:
                datei = http(PLAUD_BASE + kandidat, plaud_headers)
                break
            except urllib.error.HTTPError as exc:
                letzter_fehler = f"HTTP {exc.code}"
            except Exception as exc:
                letzter_fehler = type(exc).__name__

        if datei is None:
            print(f"{i:3}. FEHLER {letzter_fehler:9} {rid[:26]}  {name}")
            fehler += 1
            continue

        text = transkript_text(datei)

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
