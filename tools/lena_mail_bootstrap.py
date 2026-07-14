#!/usr/bin/env python3
"""
tools/lena_mail_bootstrap.py

Einmaliger Bestandsinbox-Bootstrap: kategorisiert alle noch unkategorisierten
Mails der letzten N Tage (default 90) mit derselben Triage-Logik wie der
laufende Poller — inklusive Hindsight-Recall nach 4 Wochen Betrieb.

Voraussetzung: HBE-945-Lernloop läuft seit ≥ 4 Wochen (frühester Start 2026-07-15).

Usage:
    python3 tools/lena_mail_bootstrap.py [--days 90] [--dry-run]

Env-Vars (gleich wie Poller):
    MEIN_ASSISTENT_API_URL     API-Basis (default: http://127.0.0.1:8502)
    API_SECRET_KEY             X-API-Key Header
    ANTHROPIC_API_KEY          Pflicht für LLM-Triage
    LENA_MAIL_TRIAGE_STATE_FILE  State-File (processed_message_ids)
    LENA_MAIL_TRIAGE_LLM_MODEL   Claude-Modell (default: claude-haiku-4-5)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

# Import triage_mail from the poller — no duplication of triage logic.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lena_mail_triage_poller import triage_mail  # noqa: E402

load_dotenv()

API_URL    = os.getenv("MEIN_ASSISTENT_API_URL", "http://127.0.0.1:8502")
API_KEY    = os.getenv("API_SECRET_KEY", "")
STATE_FILE = os.getenv(
    "LENA_MAIL_TRIAGE_STATE_FILE",
    "/opt/mein-assistent/data/lena-mail-triage-poller.state",
)

# 2 s between LLM-driven categorizations → ≤ 30 mails/min (Anthropic rate limit)
_LLM_SLEEP_SEC = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _headers() -> Dict[str, str]:
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def _fetch_mails(days: int) -> List[Dict[str, Any]]:
    url = f"{API_URL.rstrip('/')}/api/lena/mail/inbox-for-triage"
    resp = requests.get(
        url,
        headers=_headers(),
        params={"days": days, "include_categorized": "false", "limit": 200},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"inbox-for-triage HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("mails", [])


def _categorize(message_id: str, action: str) -> bool:
    resp = requests.post(
        f"{API_URL.rstrip('/')}/api/lena/mail/categorize",
        headers=_headers(),
        json={"message_id": message_id, "action": action},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("categorize HTTP %d: %s", resp.status_code, resp.text[:200])
        return False
    return True


_PRIORITY_TO_IMPORTANCE = {"hoch": "high", "mittel": "normal", "niedrig": "low"}


def _set_importance(message_id: str, priority: str) -> None:
    importance = _PRIORITY_TO_IMPORTANCE.get(priority, "normal")
    requests.post(
        f"{API_URL.rstrip('/')}/api/lena/mail/set-importance",
        headers=_headers(),
        json={"message_id": message_id, "importance": importance},
        timeout=30,
    )


def _load_state() -> Dict[str, Any]:
    path = Path(STATE_FILE)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            logger.warning("State parse error, treating as empty: %s", exc)
    return {"processed_message_ids": []}


def _save_state(state: Dict[str, Any]) -> None:
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lena Bestandsinbox-Bootstrap — kategorisiert ältere Mails einmalig"
    )
    parser.add_argument("--days", type=int, default=90, help="Lookback in Tagen (default: 90)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur zeigen was kategorisiert würde, nichts schreiben",
    )
    args = parser.parse_args()

    logger.info("[bootstrap] Start: days=%d dry_run=%s", args.days, args.dry_run)

    mails = _fetch_mails(args.days)
    total = len(mails)
    logger.info("[bootstrap] %d Mails gefunden (≤%d Tage, noch nicht kategorisiert)", total, args.days)

    if total == 0:
        logger.info("[bootstrap] Nichts zu tun. Fertig.")
        return

    state = _load_state()
    processed_set: set = set(state.get("processed_message_ids", []))
    new_ids: List[str] = list(state.get("processed_message_ids", []))

    llm_count = rules_count = errors = skipped = 0

    for i, mail in enumerate(mails, start=1):
        mid = mail.get("message_id", "")
        if not mid:
            errors += 1
            continue

        if mid in processed_set:
            skipped += 1
            continue

        action, priority, rule_id, _ = triage_mail(
            mail.get("subject", ""),
            mail.get("sender_email", ""),
            mail.get("body_preview", ""),
            mail.get("sender_name", ""),
        )

        is_llm = rule_id.startswith("llm")

        if args.dry_run:
            logger.info(
                "[bootstrap] %d/%d DRY-RUN: %s → %s/%s (%s)",
                i, total,
                (mail.get("subject", "") or "")[:60],
                action, priority, rule_id,
            )
        else:
            ok = _categorize(mid, action)
            if not ok:
                errors += 1
                logger.warning("[bootstrap] %d/%d FAILED: %s", i, total, mid[:20])
                continue
            _set_importance(mid, priority)
            new_ids.append(mid)
            processed_set.add(mid)

        if is_llm:
            llm_count += 1
            time.sleep(_LLM_SLEEP_SEC)
        else:
            rules_count += 1

        logger.info(
            "[bootstrap] %d/%d done (llm=%d, rules=%d, errors=%d)",
            i, total, llm_count, rules_count, errors,
        )

    if not args.dry_run:
        state["processed_message_ids"] = new_ids
        state["last_bootstrap_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
        logger.info("[bootstrap] State-File aktualisiert (%d processed_message_ids gesamt)", len(new_ids))

    logger.info(
        "[bootstrap] Fertig. total=%d llm=%d rules=%d errors=%d skipped=%d",
        total, llm_count, rules_count, errors, skipped,
    )


if __name__ == "__main__":
    main()
