"""
Cached API-Funktionen für Asana und Outlook (Performance-Optimierung).

Alle Funktionen müssen auf Modul-Ebene definiert sein (nicht in Klassen),
damit st.cache_data korrekt funktioniert.
"""
import os
from typing import Optional
import streamlit as st


@st.cache_data(ttl=600, show_spinner=False)
def cached_get_asana_projects(_asana_agent):
    """Cached: Lade Asana-Projekte (10 Min Cache)

    Note: _asana_agent mit _ prefix um Streamlit mitzuteilen,
    dass dieses Objekt nicht gehasht werden soll
    """
    try:
        return _asana_agent.list_projects()
    except Exception as e:
        print(f"[Cache] Fehler beim Laden der Projekte: {e}")
        return []


@st.cache_data(ttl=600, show_spinner=False)
def cached_get_asana_tasks(_asana_agent, project_gid=None, days=7):
    """Cached: Lade Asana-Aufgaben (10 Min Cache)

    Note: _asana_agent mit _ prefix um Streamlit mitzuteilen,
    dass dieses Objekt nicht gehasht werden soll
    """
    try:
        if project_gid:
            return _asana_agent.get_project_tasks(project_gid, limit=50)
        else:
            return _asana_agent.get_upcoming_tasks(days=days, limit=50)
    except Exception as e:
        print(f"[Cache] Fehler beim Laden der Aufgaben: {e}")
        return []


@st.cache_data(ttl=600, show_spinner=False)
def cached_get_outlook_events(_outlook_tool):
    """Cached: Lade Outlook-Termine (10 Min Cache)

    Note: _outlook_tool mit _ prefix um Streamlit mitzuteilen,
    dass dieses Objekt nicht gehasht werden soll
    """
    try:
        if not _outlook_tool.is_authenticated():
            return None
        return _outlook_tool.get_todays_events()
    except Exception as e:
        print(f"[Cache] Fehler beim Laden der Termine: {e}")
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def cached_get_asana_user(_asana_agent):
    """Cached: Hole aktuellen Asana-User (1 Std Cache)"""
    try:
        import asana
        client = asana.Client.access_token(os.getenv("ASANA_ACCESS_TOKEN"))
        result = client.users.get_user("me")
        return result
    except Exception as e:
        print(f"[Cache] Fehler beim Laden des Users: {e}")
        return None


@st.cache_data(ttl=600, show_spinner=False)
def cached_find_asana_user(_agent_id: int, name: str, workspace_gid: Optional[str] = None):
    """Cached: Sucht einen Asana-Nutzer nach Namen im Workspace (10 Min Cache).

    Performance: Spart den API-Call get_users_for_workspace bei wiederholten
    Task-Erstellungen mit gleichem Assignee. Der Agent selbst wird nicht
    gehasht (per Streamlit-Konvention `_`-Präfix); stattdessen dient seine
    id() als Cache-Key, damit pro Agent-Instanz (= pro User) ein Eintrag.

    Args:
        _agent_id: id() des AsanaAgent — wird als Cache-Differentiator genutzt,
                   aber nicht an den Agent durchgereicht.
        name: Name des Nutzers (Vorname, Nachname oder Email)
        workspace_gid: Optionale Workspace GID

    Returns:
        Dict mit user info {gid, name, email} oder None wenn nicht gefunden.
    """
    agent = st.session_state.orchestrator.asana_agent
    return agent.find_user_by_name(name, workspace_gid=workspace_gid)
