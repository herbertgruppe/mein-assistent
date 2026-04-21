"""
Auth-Modul: Laden/Speichern der Benutzerkonfiguration, Rollen- und Username-Mapping.
"""
import shutil
import yaml
import streamlit as st
from pathlib import Path


USERS_CONFIG_PATH = Path("config/users_config.yaml")


@st.cache_data(ttl=60, show_spinner=False)
def _load_users_config() -> dict:
    """Lädt Rollen-Mapping aus YAML.

    Seit Phase 2.4 (SSO) enthält die Datei nur noch Rollen + optionales
    Username-Mapping — Passwörter/Sessions liegen vollständig bei Authentik.

    Erwartetes Schema:
        roles:
          email@domain: admin|user
        default_role: user
        username_map:
          email@domain: interner_username   # optional

    Cache: 60s TTL. `_save_users_config()` ruft `.clear()` auf, damit
    Rollen-Änderungen sofort sichtbar sind.
    """
    if USERS_CONFIG_PATH.exists():
        with open(USERS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    # Fallback: Datei aus dem App-Verzeichnis (erster Start)
    bundled = Path("users_config.yaml")
    if bundled.exists():
        USERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, USERS_CONFIG_PATH)
        with open(USERS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_users_config(config: dict):
    """Speichert die User-Konfiguration und invalidiert den Load-Cache."""
    USERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    # Cache invalidieren, damit Änderungen sofort sichtbar sind
    _load_users_config.clear()


def get_user_role(email: str) -> str:
    """Liefert die App-Rolle (admin|user) für eine Authentik-Email."""
    cfg = _load_users_config()
    return cfg.get('roles', {}).get((email or '').lower(), cfg.get('default_role', 'user'))


def get_username(email: str) -> str:
    """Mapped Authentik-Email auf den internen Username (UserContext-Schlüssel).

    Priorität: explizite Zuordnung in `username_map:` der users_config.yaml,
    sonst Fallback auf "alles vor @, Punkte raus, lowercase" — so bleibt
    z.B. s.herbert@herbert.de → "sherbert" und die bestehenden
    per-User-Verzeichnisse (/app/users/sherbert/...) bleiben zugreifbar.
    """
    email = (email or '').lower()
    cfg = _load_users_config()
    mapped = cfg.get('username_map', {}).get(email)
    if mapped:
        return mapped
    local = email.split('@')[0] if '@' in email else email
    return local.replace('.', '').replace('-', '').replace('+', '') or 'anon'
