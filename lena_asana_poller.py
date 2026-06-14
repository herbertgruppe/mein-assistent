#!/usr/bin/env python3
"""
lena_asana_poller.py

Pollt Asana auf @lena-Mentions und neue Task-Assignments und erstellt Paperclip-Issues.

Env-Vars:
  ASANA_ACCESS_TOKEN              Asana Personal Access Token
  LENA_ASANA_USER_GID             Lena's Asana User GID (Standard: 1214903090695663)
  LENA_ASANA_WORKSPACE_GID        Workspace GID (automatisch ermittelt wenn nicht gesetzt)
  LENA_ASANA_POLL_INTERVAL_SEC    Polling-Intervall in Sekunden (Standard: 300)
  LENA_ASANA_STATE_FILE           State-File (Standard: /app/data/lena-asana-poller.state)
  LENA_ASANA_LOG_FILE             Log-Datei (Standard: /app/data/lena-asana-poller.log)
  PAPERCLIP_API_URL               Paperclip-API-Basis-URL
  PAPERCLIP_API_KEY_MA            Bearer-Token fuer Paperclip (mein-assistent App-Key)
  PAPERCLIP_COMPANY_ID_MA         HBE Company-ID
  TELEGRAM_BOT_TOKEN              Telegram-Bot-Token fuer Fehler-Alerts
  TELEGRAM_ADMIN_CHAT_ID          Chat-ID (Sven) fuer Fehler-Alerts
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = int(os.getenv("LENA_ASANA_POLL_INTERVAL_SEC", "300"))
LENA_USER_GID     = os.getenv("LENA_ASANA_USER_GID", "1214903090695663")
WORKSPACE_GID_ENV = os.getenv("LENA_ASANA_WORKSPACE_GID", "")
STATE_FILE        = os.getenv("LENA_ASANA_STATE_FILE", "/app/data/lena-asana-poller.state")
LOG_FILE          = os.getenv("LENA_ASANA_LOG_FILE", "/app/data/lena-asana-poller.log")

ASANA_TOKEN     = os.getenv("ASANA_ACCESS_TOKEN", "")
ASANA_BASE_URL  = "https://app.asana.com/api/1.0"

PC_API_URL      = os.getenv("PAPERCLIP_API_URL", "https://paperclip.herbertgruppe.com")
PC_API_KEY      = os.getenv("PAPERCLIP_API_KEY_MA", "")
PC_COMPANY_ID   = os.getenv("PAPERCLIP_COMPANY_ID_MA", "9df4976b-9ac8-4e8f-a156-c06c7fa40cdc")
LENA_AGENT_ID   = "7517114f-e731-4df5-96cf-a044719e9318"

TG_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN_CHAT   = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")

MAX_BACKOFF_SEC        = 300
MAX_BODY_CHARS         = 2000
ALERT_THRESHOLD        = 3
MAX_PROCESSED_GIDS     = 50_000  # cap to prevent unbounded state growth
INITIAL_LOOKBACK_HOURS = 24       # first run looks back this far

RE_LENA_MENTION = re.compile(r'@lena\b', re.IGNORECASE)


# ── Logging ───────────────────────────────────────────────────────────────────
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "time":  datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg":   record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    handlers: List[logging.Handler] = [stream_handler]

    try:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(str(log_path), maxBytes=10_485_760, backupCount=5)
        fh.setFormatter(_JsonFormatter())
        handlers.append(fh)
    except OSError as exc:
        print(f"[warn] Cannot open log file {LOG_FILE}: {exc}", file=sys.stderr)

    log = logging.getLogger("lena_asana_poller")
    log.setLevel(logging.INFO)
    for h in handlers:
        log.addHandler(h)
    log.propagate = False
    return log


logger = _setup_logging()


# ── State ─────────────────────────────────────────────────────────────────────
def _load_state() -> tuple[str, Dict[str, None]]:
    """Load (last_sync_at_iso, processed_story_gids) from state file.

    processed_story_gids is a dict[str, None] ordered by insertion (= recency).
    """
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            # dict.fromkeys preserves JSON-array order, which equals insertion order
            return data.get("last_sync_at", ""), dict.fromkeys(data.get("processed_story_gids", []))
        except Exception as exc:
            logger.warning("State file parse error, resetting: %s", exc)
    # First run — look back INITIAL_LOOKBACK_HOURS
    fallback = (
        datetime.now(timezone.utc) - timedelta(hours=INITIAL_LOOKBACK_HOURS)
    ).isoformat()
    return fallback, {}


def _save_state(last_sync_at: str, processed_gids: Dict[str, None]) -> None:
    """Persist state to file, capping processed_gids to MAX_PROCESSED_GIDS.

    processed_gids keys are in insertion order (oldest first), so slicing
    [-MAX_PROCESSED_GIDS:] correctly retains the most-recently-seen GIDs.
    """
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(processed_gids.keys())
    gids_list = keys[-MAX_PROCESSED_GIDS:] if len(keys) > MAX_PROCESSED_GIDS else keys
    path.write_text(json.dumps(
        {"last_sync_at": last_sync_at, "processed_story_gids": gids_list},
        ensure_ascii=False,
        indent=2,
    ))


# ── Asana API ─────────────────────────────────────────────────────────────────
def _asana_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}


def _asana_get(path: str, params: Optional[Dict] = None) -> Any:
    url = f"{ASANA_BASE_URL}/{path.lstrip('/')}"
    resp = requests.get(url, headers=_asana_headers(), params=params, timeout=20)
    resp.raise_for_status()
    return resp.json().get("data", resp.json())


def _get_workspace_gid() -> str:
    """Return workspace GID from env or auto-discover via API."""
    if WORKSPACE_GID_ENV:
        return WORKSPACE_GID_ENV
    workspaces = _asana_get("/workspaces", {"opt_fields": "gid,name"})
    if not workspaces:
        raise RuntimeError("No Asana workspaces found for this token")
    gid = workspaces[0]["gid"]
    logger.info(json.dumps({"event": "workspace_autodiscovered", "gid": gid,
                            "name": workspaces[0].get("name", "?")}))
    return gid


def _get_tasks_since(workspace_gid: str, modified_since: str) -> List[Dict]:
    """Get tasks assigned to Lena modified since given ISO timestamp."""
    return _asana_get("/tasks", {
        "assignee":      LENA_USER_GID,
        "workspace":     workspace_gid,
        "modified_since": modified_since,
        "opt_fields":    "gid,name,permalink_url,created_at,modified_at",
        "limit":         100,
    })


def _get_task_stories(task_gid: str) -> List[Dict]:
    """Get all stories for a task."""
    return _asana_get(f"/tasks/{task_gid}/stories", {
        "opt_fields": "gid,type,text,created_at,created_by.name,resource_subtype",
    })


# ── Event classification ───────────────────────────────────────────────────────
def _is_assignment_story(story: Dict) -> bool:
    """True when story indicates task was (re)assigned to Lena."""
    sub = story.get("resource_subtype", "")
    text = story.get("text", "")
    # Asana generates system stories like "assigned to Lena Herbert" or "reassigned to Lena Herbert"
    if sub == "assigned" and re.search(r'\blena\b', text, re.IGNORECASE):
        return True
    return False


def _is_mention_story(story: Dict) -> bool:
    """True when story is a comment containing an @lena mention."""
    if story.get("resource_subtype") != "comment_added":
        return False
    return bool(RE_LENA_MENTION.search(story.get("text", "")))


# ── Paperclip ──────────────────────────────────────────────────────────────────
def _create_pc_issue(payload: Dict) -> Optional[str]:
    if not PC_API_KEY:
        logger.warning("PAPERCLIP_API_KEY_MA not set — skipping issue creation")
        return None
    url = f"{PC_API_URL}/api/companies/{PC_COMPANY_ID}/issues"
    resp = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {PC_API_KEY}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("identifier") or data.get("id", "?"))


def _build_mention_payload(
    story: Dict,
    task_name: str,
    task_url: str,
) -> Dict:
    author = story.get("created_by", {}).get("name", "Unbekannt")
    text   = (story.get("text") or "")[:MAX_BODY_CHARS]
    title  = f"📬 Asana-Mention von {author} auf Task: {task_name[:80]}"
    description = (
        f"**Von:** {author}\n"
        f"**Task:** [{task_name}]({task_url})\n"
        f"**Story-ID:** {story.get('gid', '?')}\n\n"
        "---\n\n"
        f"{text}"
    )
    return {
        "title":           title,
        "description":     description,
        "assigneeAgentId": LENA_AGENT_ID,
        "priority":        "medium",
    }


def _build_assignment_payload(
    story: Dict,
    task_name: str,
    task_url: str,
) -> Dict:
    author = story.get("created_by", {}).get("name", "Unbekannt")
    title  = f"📋 Asana-Zuweisung von {author}: {task_name[:80]}"
    description = (
        f"**Zugewiesen von:** {author}\n"
        f"**Task:** [{task_name}]({task_url})\n"
        f"**Story-ID:** {story.get('gid', '?')}\n\n"
        "---\n\n"
        f"{story.get('text', '')[:MAX_BODY_CHARS]}"
    )
    return {
        "title":           title,
        "description":     description,
        "assigneeAgentId": LENA_AGENT_ID,
        "priority":        "medium",
    }


# ── Telegram ───────────────────────────────────────────────────────────────────
def _tg_alert(text: str) -> None:
    if not TG_BOT_TOKEN or not TG_ADMIN_CHAT:
        logger.warning(
            "Telegram alert suppressed — set TELEGRAM_BOT_TOKEN + TELEGRAM_ADMIN_CHAT_ID"
        )
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_ADMIN_CHAT, "text": text},
            timeout=10,
        )
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)


# ── Poll cycle ────────────────────────────────────────────────────────────────
def _poll_once(workspace_gid: str, last_sync_at: str, processed_gids: Dict[str, None]) -> tuple[int, int]:
    """Run one poll cycle. Returns (events_found, issues_created)."""
    events_found   = 0
    issues_created = 0

    tasks = _get_tasks_since(workspace_gid, last_sync_at)
    logger.info(json.dumps({"event": "tasks_fetched", "count": len(tasks), "modified_since": last_sync_at}))

    for task in tasks:
        task_gid  = task["gid"]
        task_name = task.get("name", "(kein Name)")
        task_url  = task.get("permalink_url", f"https://app.asana.com/0/0/{task_gid}")

        try:
            stories = _get_task_stories(task_gid)
        except Exception as exc:
            logger.error(json.dumps({"event": "stories_fetch_error", "task_gid": task_gid, "error": str(exc)}))
            continue

        # Filter to stories created after last_sync_at
        try:
            cutoff = datetime.fromisoformat(last_sync_at.replace("Z", "+00:00"))
        except ValueError:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=INITIAL_LOOKBACK_HOURS)

        new_stories = [
            s for s in stories
            if _parse_iso(s.get("created_at", "")) > cutoff
            and s.get("gid") not in processed_gids
        ]

        for story in new_stories:
            story_gid = story.get("gid", "")
            is_mention    = _is_mention_story(story)
            is_assignment = _is_assignment_story(story)

            if not is_mention and not is_assignment:
                # Mark as seen so we don't re-check it
                if story_gid:
                    processed_gids[story_gid] = None
                continue

            events_found += 1
            payload = (
                _build_mention_payload(story, task_name, task_url)
                if is_mention
                else _build_assignment_payload(story, task_name, task_url)
            )

            try:
                issue_id = _create_pc_issue(payload)
            except Exception as exc:
                logger.error(json.dumps({
                    "event":     "issue_create_failed",
                    "story_gid": story_gid,
                    "error":     str(exc),
                }))
                continue

            if issue_id:
                if story_gid:
                    processed_gids[story_gid] = None
                issues_created += 1
                logger.info(json.dumps({
                    "event":           "issue_created",
                    "kind":            "mention" if is_mention else "assignment",
                    "story_gid":       story_gid,
                    "task_name":       task_name[:80],
                    "paperclip_issue": issue_id,
                }))

    return events_found, issues_created


def _parse_iso(ts: str) -> datetime:
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


# ── Main ───────────────────────────────────────────────────────────────────────
_running = True


def _sig_handler(sig: int, _frame: object) -> None:
    global _running
    logger.info(json.dumps({"event": "signal", "sig": sig}))
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    logger.info(json.dumps({
        "event":             "startup",
        "poll_interval_sec": POLL_INTERVAL_SEC,
        "lena_user_gid":     LENA_USER_GID,
    }))

    if not ASANA_TOKEN:
        logger.error("ASANA_ACCESS_TOKEN not set — exiting")
        sys.exit(1)

    try:
        workspace_gid = _get_workspace_gid()
    except Exception as exc:
        logger.error("Failed to get workspace GID: %s", exc)
        sys.exit(1)

    last_sync_at, processed_gids = _load_state()
    consecutive_errors = 0
    current_backoff    = POLL_INTERVAL_SEC

    while _running:
        cycle_start  = time.monotonic()
        cycle_ok     = True
        new_sync_at  = datetime.now(timezone.utc).isoformat()

        try:
            events, created = _poll_once(workspace_gid, last_sync_at, processed_gids)
            last_sync_at = new_sync_at
            _save_state(last_sync_at, processed_gids)
            consecutive_errors = 0
            current_backoff    = POLL_INTERVAL_SEC
        except Exception as exc:
            cycle_ok            = False
            consecutive_errors += 1
            logger.error(json.dumps({
                "event":              "poll_error",
                "error":              str(exc),
                "consecutive_errors": consecutive_errors,
            }))
            if consecutive_errors >= ALERT_THRESHOLD:
                _tg_alert(
                    f"🚨 lena-asana-poller: {consecutive_errors} aufeinanderfolgende Fehler\n"
                    f"Fehler: {exc}"
                )
                consecutive_errors = 0
            events, created = 0, 0

        logger.info(json.dumps({
            "event":          "poll_cycle",
            "events_found":   events if cycle_ok else 0,
            "issues_created": created if cycle_ok else 0,
            "duration_sec":   round(time.monotonic() - cycle_start, 2),
            "next_sleep_sec": current_backoff,
        }))

        if not cycle_ok:
            current_backoff = min(current_backoff * 2, MAX_BACKOFF_SEC)

        deadline = time.monotonic() + current_backoff
        while _running and time.monotonic() < deadline:
            time.sleep(1)

    logger.info(json.dumps({"event": "shutdown"}))


if __name__ == "__main__":
    main()
