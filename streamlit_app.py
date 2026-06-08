"""
Multi-Cloud Data Engineer Agent — Streamlit UI

Run locally:
    streamlit run streamlit_app.py

Deploy to Streamlit Cloud:
    Push to GitHub, connect at share.streamlit.io
    Add secrets: OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_INDEX_NAME, GITHUB_TOKEN
"""

import os
import queue
import logging
import datetime
import threading
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap — env vars must be set BEFORE any project imports
# ---------------------------------------------------------------------------

load_dotenv()

try:
    for key, val in st.secrets.items():
        if not os.getenv(key):
            os.environ[key] = str(val)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Multi-Cloud Data Engineer Agent",
    page_icon="🤖",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Custom CSS — dark cloud / tech theme
# ---------------------------------------------------------------------------

st.markdown("""
<style>

/* ── GLOBAL ─────────────────────────────────────────────── */
.stApp {
    background: linear-gradient(160deg, #060c1a 0%, #0d1b35 45%, #070e20 100%);
    color: #e2e8f0;
}
.main .block-container {
    padding-top: 1rem;
    padding-bottom: 3rem;
}

/* ── TOP HEADER BAR ──────────────────────────────────────── */
[data-testid="stHeader"] {
    background: rgba(6, 12, 26, 0.97) !important;
    border-bottom: 1px solid rgba(56, 189, 248, 0.10) !important;
    backdrop-filter: blur(16px) !important;
}
/* Hide the rainbow decoration line */
[data-testid="stDecoration"],
[data-testid="stDecorationTop"] {
    display: none !important;
}
/* Hamburger / nav icons */
[data-testid="stHeader"] button,
[data-testid="stHeader"] [data-testid="collapsedControl"] {
    color: #64748b !important;
    border: none !important;
    background: transparent !important;
}
[data-testid="stHeader"] button:hover {
    color: #38bdf8 !important;
    background: rgba(56, 189, 248, 0.1) !important;
    border-radius: 8px !important;
}
/* Deploy button — make it fit the theme */
[data-testid="stToolbar"] {
    background: transparent !important;
}
.stDeployButton > button,
[data-testid="stDeployButton"] > button {
    background: rgba(14, 165, 233, 0.10) !important;
    border: 1px solid rgba(56, 189, 248, 0.30) !important;
    color: #38bdf8 !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    padding: 0.25rem 0.75rem !important;
}
.stDeployButton > button:hover,
[data-testid="stDeployButton"] > button:hover {
    background: rgba(14, 165, 233, 0.20) !important;
    box-shadow: 0 0 12px rgba(56, 189, 248, 0.25) !important;
}
/* Toolbar overflow menu icons */
[data-testid="stToolbarActions"] button {
    color: #475569 !important;
    background: transparent !important;
}
[data-testid="stToolbarActions"] button:hover {
    color: #38bdf8 !important;
    background: rgba(56, 189, 248, 0.08) !important;
    border-radius: 6px !important;
}

/* ── SIDEBAR ─────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: rgba(10, 18, 40, 0.97) !important;
    border-right: 1px solid rgba(56, 189, 248, 0.18);
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] div {
    color: #94a3b8 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #e2e8f0 !important;
}

/* ── HEADINGS ────────────────────────────────────────────── */
h1, h2, h3 { color: #e2e8f0 !important; }
p, li { color: #cbd5e1 !important; }

/* ── METRIC CARDS ────────────────────────────────────────── */
[data-testid="metric-container"] {
    background: rgba(30, 41, 59, 0.7) !important;
    border: 1px solid rgba(56, 189, 248, 0.25) !important;
    border-radius: 14px !important;
    padding: 1.1rem 1.3rem !important;
    backdrop-filter: blur(12px);
}
[data-testid="stMetricValue"] {
    color: #38bdf8 !important;
    font-weight: 700 !important;
    font-size: 1.4rem !important;
}
[data-testid="stMetricLabel"] {
    color: #64748b !important;
    font-size: 0.8rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* ── TEXT AREA ───────────────────────────────────────────── */
.stTextArea textarea {
    background: rgba(10, 18, 40, 0.85) !important;
    border: 1px solid rgba(56, 189, 248, 0.3) !important;
    border-radius: 12px !important;
    color: #e2e8f0 !important;
    font-size: 15px !important;
    line-height: 1.6;
}
.stTextArea textarea:focus {
    border-color: #38bdf8 !important;
    box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2) !important;
}
.stTextArea label { color: #94a3b8 !important; }

/* ── BUTTONS ─────────────────────────────────────────────── */
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1d4ed8 0%, #0ea5e9 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 15px !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 2px 12px rgba(14, 165, 233, 0.3);
}
.stButton > button[kind="primary"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 20px rgba(14, 165, 233, 0.45) !important;
}
.stButton > button[kind="secondary"] {
    background: rgba(30, 41, 59, 0.7) !important;
    color: #94a3b8 !important;
    border: 1px solid rgba(56, 189, 248, 0.25) !important;
    border-radius: 10px !important;
}
.stButton > button[kind="secondary"]:hover {
    border-color: #38bdf8 !important;
    color: #38bdf8 !important;
}

/* ── CODE BLOCKS (agent log) ─────────────────────────────── */
[data-testid="stCode"] {
    background: rgba(6, 12, 26, 0.9) !important;
    border: 1px solid rgba(56, 189, 248, 0.18) !important;
    border-radius: 12px !important;
}
[data-testid="stCode"] code, [data-testid="stCode"] pre {
    color: #7dd3fc !important;
    font-size: 13px !important;
    line-height: 1.7 !important;
}

/* ── STATUS WIDGET ───────────────────────────────────────── */
[data-testid="stStatus"] {
    background: rgba(10, 18, 40, 0.8) !important;
    border: 1px solid rgba(56, 189, 248, 0.25) !important;
    border-radius: 14px !important;
}

/* ── TABS ────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background: rgba(10, 18, 40, 0.6) !important;
    border-radius: 12px !important;
    padding: 6px !important;
    border: 1px solid rgba(56, 189, 248, 0.15) !important;
    gap: 8px;                       /* more space between tabs */
    margin-bottom: 1.2rem !important;  /* air between the tab bar and the content panel */
}
.stTabs [data-baseweb="tab"] {
    color: #64748b !important;
    border-radius: 9px !important;
    font-weight: 500;
    padding: 0.55rem 1.1rem !important;  /* internal breathing room per tab */
}
.stTabs [aria-selected="true"] {
    background: rgba(56, 189, 248, 0.15) !important;
    color: #38bdf8 !important;
}
/* a touch of air at the top of the selected tab's content */
.stTabs [data-baseweb="tab-panel"] {
    padding-top: 0.5rem !important;
}

/* Hide Streamlit's own "Deploy" button (framework chrome — it publishes the APP to Streamlit
   Cloud, unrelated to our pipeline deploy; off-brand for a demo). The ⋮ menu (Rerun/Settings)
   stays for development. */
[data-testid="stDeployButton"],
.stDeployButton { display: none !important; }

/* ── SELECTBOX — trigger ─────────────────────────────────── */
.stSelectbox [data-baseweb="select"] > div {
    background: rgba(10, 18, 40, 0.85) !important;
    border: 1px solid rgba(56, 189, 248, 0.3) !important;
    border-radius: 10px !important;
    color: #e2e8f0 !important;
}
.stSelectbox [data-baseweb="select"] span,
.stSelectbox [data-baseweb="select"] div {
    color: #e2e8f0 !important;
}
.stSelectbox label { color: #94a3b8 !important; }
/* ── SELECTBOX — dropdown popup ──────────────────────────── */
[data-baseweb="popover"],
[data-baseweb="popover"] > div,
[data-baseweb="menu"] {
    background: #0d1b35 !important;
    border: 1px solid rgba(56, 189, 248, 0.25) !important;
    border-radius: 12px !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6) !important;
}
[data-baseweb="option"] {
    background: transparent !important;
    color: #cbd5e1 !important;
    border-radius: 8px !important;
    padding: 0.55rem 0.9rem !important;
}
[data-baseweb="option"]:hover,
[data-baseweb="option"][aria-selected="true"] {
    background: rgba(56, 189, 248, 0.12) !important;
    color: #38bdf8 !important;
}
li[role="option"] {
    background: transparent !important;
    color: #cbd5e1 !important;
}
li[role="option"]:hover {
    background: rgba(56, 189, 248, 0.12) !important;
    color: #38bdf8 !important;
}

/* ── DIVIDER ─────────────────────────────────────────────── */
hr {
    border: none !important;
    border-top: 1px solid rgba(56, 189, 248, 0.15) !important;
    margin: 1.5rem 0 !important;
}

/* ── ALERTS ──────────────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: 12px !important;
    border: 1px solid rgba(56, 189, 248, 0.2) !important;
}

/* ── SPINNER ─────────────────────────────────────────────── */
.stSpinner > div { border-top-color: #38bdf8 !important; }

/* ── SELECTBOX — dropdown popup (portal-rendered by BaseUI) ─ */
ul[role="listbox"] {
    background-color: #0d1b35 !important;
    border: 1px solid rgba(56,189,248,0.25) !important;
    border-radius: 12px !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.7) !important;
    padding: 4px !important;
}
ul[role="listbox"] li,
ul[role="listbox"] li * {
    background-color: transparent !important;
    color: #cbd5e1 !important;
    font-size: 0.9rem !important;
}
ul[role="listbox"] li:hover,
ul[role="listbox"] li:hover * {
    background-color: rgba(56,189,248,0.12) !important;
    color: #38bdf8 !important;
    border-radius: 8px !important;
}
ul[role="listbox"] li[aria-selected="true"],
ul[role="listbox"] li[aria-selected="true"] * {
    background-color: rgba(56,189,248,0.18) !important;
    color: #38bdf8 !important;
}
/* also catch data-baseweb portals */
[data-baseweb="menu"],
[data-baseweb="menu"] ul,
[data-baseweb="list"] {
    background-color: #0d1b35 !important;
    border-radius: 12px !important;
}
[data-baseweb="option"] {
    background-color: transparent !important;
    color: #cbd5e1 !important;
}
[data-baseweb="option"]:hover {
    background-color: rgba(56,189,248,0.12) !important;
    color: #38bdf8 !important;
}

/* ── ARCHITECTURE REPORT CARDS ──────────────────────────── */
.arch-card {
    background: rgba(10,18,40,0.75);
    border-radius: 14px;
    padding: 1rem 1.1rem;
    height: 100%;
    transition: border-color 0.2s;
}
.arch-card-rec {
    border: 2px solid #4ade80 !important;
    box-shadow: 0 0 20px rgba(74,222,128,0.12);
}
.arch-card-plain { border: 1px solid rgba(56,189,248,0.18); }
.arch-card-selected { border: 2px solid #38bdf8 !important; }
.arch-cloud-name {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 0 0 0.15rem;
}
.arch-price {
    font-size: 2rem;
    font-weight: 800;
    margin: 0 0 0.5rem;
    line-height: 1;
}
.arch-label {
    font-size: 0.78rem;
    color: #64748b;
    margin: 0 0 0.7rem;
    font-style: italic;
}
.arch-pro  { color: #4ade80; font-size: 0.8rem; margin: 0.15rem 0; }
.arch-con  { color: #f87171; font-size: 0.8rem; margin: 0.15rem 0; }
.arch-rec-badge {
    background: rgba(74,222,128,0.12);
    border: 1px solid rgba(74,222,128,0.3);
    border-radius: 6px;
    color: #4ade80;
    font-size: 0.72rem;
    font-weight: 700;
    padding: 0.1rem 0.5rem;
    display: inline-block;
    margin-bottom: 0.5rem;
}

/* ── SIDEBAR STATUS CARDS ────────────────────────────────── */
.sb-card {
    background: rgba(10,18,40,0.65);
    border: 1px solid rgba(56,189,248,0.13);
    border-radius: 10px;
    padding: 0.55rem 0.85rem 0.35rem;
    margin-bottom: 0.75rem;
}
.sb-title {
    font-size: 0.65rem !important;
    color: #475569 !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 0 0 0.45rem !important;
}
.sb-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.32rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.82rem;
}
.sb-row:last-child { border-bottom: none; }
.sb-lbl { color: #94a3b8; }
.sb-ok  { color: #4ade80; font-weight: 600; }
.sb-err { color: #f87171; font-weight: 600; }
.sb-warn-card {
    background: rgba(251,191,36,0.06);
    border: 1px solid rgba(251,191,36,0.2);
    border-radius: 10px;
    padding: 0.6rem 0.85rem;
    margin-bottom: 0.75rem;
}
.sb-warn-title { color: #fbbf24; font-size: 0.82rem; font-weight: 600; margin: 0 0 0.25rem; }
.sb-warn-body  { color: #64748b; font-size: 0.74rem; line-height: 1.55; margin: 0; }
.sb-warn-body code { color: #7dd3fc; }

</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Lazy graph loader
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="Loading agent graph...")
def get_graph():
    from graph import app
    return app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NODE_LABELS = {
    "supervisor":    "🎯 SUPERVISOR",
    "architect":     "🏗️  ARCHITECT",
    "infra":         "⚙️  INFRA",
    "medic":         "🏥 MEDIC",
    "execute_tools": "🔧 TOOLS",
}
_CLOUD_FLAGS = {"aws": "🟠 AWS", "azure": "🔵 Azure", "gcp": "🟢 GCP"}

# ---------------------------------------------------------------------------
# Pipeline visualization helpers
# ---------------------------------------------------------------------------

_PIPELINE_NODES = [
    ("supervisor", "🎯 SUPERVISOR"),
    ("architect",  "🏗️ ARCHITECT"),
    ("infra",      "⚙️ INFRA"),
    ("medic",      "🏥 MEDIC"),
]
_NODE_STATUS_STYLE = {
    "pending":   ("⬜", "#475569", "rgba(71,85,105,0.15)"),
    "active":    ("🔄", "#38bdf8", "rgba(56,189,248,0.15)"),
    "completed": ("✅", "#4ade80", "rgba(74,222,128,0.12)"),
    "failed":    ("❌", "#f87171", "rgba(248,113,113,0.12)"),
}


def _render_pipeline_graph(node_statuses: dict) -> str:
    parts = []
    for i, (key, label) in enumerate(_PIPELINE_NODES):
        status = node_statuses.get(key, "pending")
        icon, border, bg = _NODE_STATUS_STYLE.get(status, _NODE_STATUS_STYLE["pending"])
        parts.append(
            f'<div style="background:{bg};border:1px solid {border};border-radius:8px;'
            f'padding:0.4rem 0.75rem;display:flex;align-items:center;gap:0.5rem;">'
            f'<span>{icon}</span>'
            f'<span style="color:#e2e8f0;font-size:0.82rem;font-weight:600;">{label}</span>'
            f'</div>'
        )
        if i < len(_PIPELINE_NODES) - 1:
            parts.append(
                f'<div style="text-align:center;color:{border};line-height:1.2;'
                f'font-size:1rem;margin:0.05rem 0;">↓</div>'
            )
    return (
        '<div style="display:flex;flex-direction:column;gap:0.2rem;">'
        + "".join(parts)
        + "</div>"
    )


def _render_agent_log_html(agent_messages: dict) -> str:
    import html as _html
    _LABELS = {
        "supervisor": "🎯 Supervisor",
        "architect":  "🏗️ Architect",
        "infra":      "⚙️ Infra",
        "medic":      "🏥 Medic",
    }
    rows = []
    for key, label in _LABELS.items():
        raw = (agent_messages.get(key) or "").strip()
        if not raw:
            continue
        safe = _html.escape(raw)
        pre_style = (
            "color:#7dd3fc;font-size:0.71rem;white-space:pre-wrap;"
            "background:rgba(6,12,26,0.8);border-radius:6px;padding:0.45rem;"
            "margin:0.25rem 0 0.45rem;overflow:auto;max-height:110px;"
        )
        if len(raw) > 500:
            short = _html.escape(raw[:500])
            rows.append(
                f'<details style="margin-bottom:0.4rem;">'
                f'<summary style="cursor:pointer;color:#94a3b8;font-size:0.76rem;'
                f'padding:0.15rem 0;">{label}'
                f'&nbsp;<span style="color:#475569;">▸ show more</span></summary>'
                f'<pre style="{pre_style}">{short}…</pre>'
                f'</details>'
            )
        else:
            rows.append(
                f'<p style="color:#64748b;font-size:0.74rem;margin:0.25rem 0 0.05rem;">'
                f'{label}</p>'
                f'<pre style="{pre_style}">{safe}</pre>'
            )
    return (
        "".join(rows)
        if rows
        else '<p style="color:#334155;font-size:0.75rem;">No agent output yet.</p>'
    )


# ---------------------------------------------------------------------------
# Agent runner (background thread)
# ---------------------------------------------------------------------------

def _run_agent(pipe_conf, db_conf, rules_conf, infra_conf,
               pipeline_id, task, log_q: queue.Queue, state_q: queue.Queue):

    class _QH(logging.Handler):
        def emit(self, record):
            log_q.put(("log", f"  {record.name} — {record.getMessage()}"))

    handler = _QH()
    root = logging.getLogger()
    root.addHandler(handler)

    try:
        graph = get_graph()
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        project_id = f"{pipeline_id.upper()}-{timestamp}"
        os.environ["PROJECT_ID"] = project_id

        initial_state = {
            "task": task, "messages": [], "generated_code": "", "error_log": "",
            "project_id": project_id, "config_path": "",
            "target_infra": infra_conf.get("service_name", pipe_conf.get("cloud_provider", "unknown")),
            "written_files": [], "infra_provisioned": False, "collected_specs": {},
            "architect_status": "", "infra_status": "", "schema_discovered": False,
            "github_done": False, "last_push_sha": "", "medic_fix_requested": False,
            "raw_configs": {"pipeline": pipe_conf, "database": db_conf,
                            "rules": rules_conf, "infrastructure": infra_conf},
        }

        log_q.put(("head", f"🚀  Pipeline : {pipeline_id}"))
        log_q.put(("head", f"🆔  Project  : {project_id}"))
        log_q.put(("head", f"☁️   Cloud    : {pipe_conf.get('cloud_provider','?').upper()}"))
        log_q.put(("sep",  "─" * 55))
        state_q.put({"project_id": project_id})

        for output in graph.stream(initial_state, config={
            "run_name": f"streamlit_{pipeline_id}_{timestamp}",
            "recursion_limit": 200,
            "configurable": {"thread_id": project_id},
        }):
            for node_name, state_update in output.items():
                log_q.put(("node", f"\n{_NODE_LABELS.get(node_name, node_name.upper())}"))
                if nxt := state_update.get("next_step"):
                    if nxt != "FINISH":
                        log_q.put(("route", f"  → {nxt.upper()}"))
                if state_update.get("architect_status") == "completed":
                    log_q.put(("ok", "  ✅ Architect phase complete"))
                if state_update.get("infra_status") == "completed":
                    log_q.put(("ok", "  ✅ Infra phase complete"))
                if state_update.get("github_done"):
                    sha = state_update.get("last_push_sha", "")
                    log_q.put(("ok", f"  ✅ Pushed to GitHub{' — SHA ' + sha[:7] if sha else ''}"))
                if files := state_update.get("written_files"):
                    state_q.put({"written_files": files})

                # ── Pipeline visualization updates ────────────────────────
                if node_name in ("supervisor", "architect", "infra", "medic"):
                    nxt = state_update.get("next_step", "").lower()
                    state_q.put({"node_update": {
                        "completed": node_name,
                        "next_active": nxt if nxt and nxt != "finish" else None,
                    }})
                    msgs = state_update.get("messages", [])
                    if msgs:
                        last = msgs[-1]
                        content = getattr(last, "content", "") or ""
                        if content:
                            state_q.put({"agent_message": (node_name, str(content))})

                # Healing cycle: medic reset architect or infra status to pending
                if node_name == "medic" and (
                    state_update.get("architect_status") == "pending"
                    or state_update.get("infra_status") == "pending"
                ):
                    state_q.put({"healing_cycle": 1})

        log_q.put(("sep", "─" * 55))
        log_q.put(("ok",  "🎉  Deployment complete!"))
        state_q.put({"status": "DONE"})

    except Exception as exc:
        log_q.put(("err", f"❌  {exc}"))
        state_q.put({"status": "ERROR", "error": str(exc)})
    finally:
        root.removeHandler(handler)

# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------

st.markdown("""
<div style="
    background: linear-gradient(135deg, rgba(29,78,216,0.18) 0%, rgba(14,165,233,0.12) 50%, rgba(99,102,241,0.18) 100%);
    border: 1px solid rgba(56,189,248,0.3);
    border-radius: 18px;
    padding: 2rem 2.5rem 1.8rem;
    margin-bottom: 1.8rem;
    backdrop-filter: blur(16px);
    position: relative;
    overflow: hidden;
">
  <!-- faint grid lines for depth -->
  <div style="
    position:absolute; inset:0; opacity:0.04;
    background-image: linear-gradient(rgba(255,255,255,.5) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(255,255,255,.5) 1px, transparent 1px);
    background-size: 32px 32px;
    border-radius: 18px;
  "></div>

  <h1 style="
    margin: 0 0 0.5rem;
    font-size: 2rem;
    font-weight: 800;
    background: linear-gradient(135deg, #38bdf8 0%, #818cf8 60%, #c084fc 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.02em;
  ">🤖 Multi-Cloud Data Engineer Agent</h1>

  <p style="margin:0; color:#94a3b8; font-size:1rem; line-height:1.6;">
    Describe a pipeline in plain English — the agent designs, deploys, and monitors it across&nbsp;
    <span style="color:#f97316; font-weight:600;">AWS</span>,&nbsp;
    <span style="color:#38bdf8; font-weight:600;">Azure</span>, and&nbsp;
    <span style="color:#4ade80; font-weight:600;">GCP</span>.
  </p>

  <div style="display:flex; gap:1.5rem; margin-top:1.2rem; flex-wrap:wrap;">
    <span style="
      background:rgba(249,115,22,0.15); color:#f97316;
      border:1px solid rgba(249,115,22,0.3); border-radius:8px;
      padding:0.25rem 0.8rem; font-size:0.8rem; font-weight:600; letter-spacing:0.05em;
    ">🟠 AWS EKS + S3</span>
    <span style="
      background:rgba(56,189,248,0.12); color:#38bdf8;
      border:1px solid rgba(56,189,248,0.3); border-radius:8px;
      padding:0.25rem 0.8rem; font-size:0.8rem; font-weight:600; letter-spacing:0.05em;
    ">🔵 Azure AKS + ADLS</span>
    <span style="
      background:rgba(74,222,128,0.12); color:#4ade80;
      border:1px solid rgba(74,222,128,0.3); border-radius:8px;
      padding:0.25rem 0.8rem; font-size:0.8rem; font-weight:600; letter-spacing:0.05em;
    ">🟢 GCP GKE + GCS</span>
    <span style="
      background:rgba(129,140,248,0.12); color:#818cf8;
      border:1px solid rgba(129,140,248,0.3); border-radius:8px;
      padding:0.25rem 0.8rem; font-size:0.8rem; font-weight:600; letter-spacing:0.05em;
    ">⚡ Self-Healing</span>
    <span style="
      background:rgba(192,132,252,0.12); color:#c084fc;
      border:1px solid rgba(192,132,252,0.3); border-radius:8px;
      padding:0.25rem 0.8rem; font-size:0.8rem; font-weight:600; letter-spacing:0.05em;
    ">🔮 Trino Federation</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------------------

with st.sidebar:
    # ── Header ──────────────────────────────────────────────
    st.markdown(
        '<p class="sb-title" style="margin:0 0 0.15rem;">Environment</p>'
        '<p style="margin:0 0 1rem; font-size:1.05rem; font-weight:700; color:#e2e8f0;">Status</p>',
        unsafe_allow_html=True,
    )

    # ── API Keys (read-only status) ──────────────────────────
    checks = {
        "OpenAI":   bool(os.getenv("OPENAI_API_KEY")),
        "Pinecone": bool(os.getenv("PINECONE_API_KEY")),
        "GitHub":   bool(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")),
    }

    key_rows = "".join(
        f'<div class="sb-row">'
        f'<span class="sb-lbl">🔑 {name}</span>'
        f'<span class="{"sb-ok" if ok else "sb-err"}">{"● set" if ok else "○ missing"}</span>'
        f'</div>'
        for name, ok in checks.items()
    )
    st.markdown(
        f'<div class="sb-card"><p class="sb-title">API Keys</p>{key_rows}</div>',
        unsafe_allow_html=True,
    )

    # ── Bootstrap outputs ────────────────────────────────────
    bootstrap_file = Path(__file__).parent / ".bootstrap_outputs.json"
    if bootstrap_file.exists():
        import json
        with open(bootstrap_file) as f:
            bo = json.load(f)
        flags = {"aws": "🟠", "azure": "🔵", "gcp": "🟢"}
        cloud_rows = "".join(
            f'<div class="sb-row">'
            f'<span class="sb-lbl">{flags.get(c, "●")} {c.upper()}</span>'
            f'<span class="sb-ok">● ready</span>'
            f'</div>'
            for c in bo.keys()
        )
        st.markdown(
            f'<div class="sb-card"><p class="sb-title">Cloud Bootstrap</p>{cloud_rows}</div>',
            unsafe_allow_html=True,
        )
    # When bootstrap outputs are ABSENT, show nothing. They are an optional REAL-DEPLOY
    # prerequisite, irrelevant to the preview/cost flow — surfacing their absence as a warning
    # reads like a broken setup in a demo/interview. (The positive "ready" card above still shows
    # once outputs exist; the requirement is documented in CLAUDE.md + the loader logs.)

    # ── Footer ───────────────────────────────────────────────
    st.markdown(
        '<p style="color:#1e293b; font-size:0.72rem; text-align:center; margin-top:1rem;">'
        'LangGraph · OpenAI · Pinecone</p>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# ARCHITECTURE REPORT RENDERER
# ---------------------------------------------------------------------------

def _render_arch_report(report: dict):
    specs  = report.get("specs", {})
    rec    = report.get("recommendation", "")
    flags  = {"aws": "🟠 AWS", "azure": "🔵 Azure", "gcp": "🟢 GCP", "databricks": "⚡ Databricks"}
    colors = {"aws": "#f97316", "azure": "#38bdf8", "gcp": "#4ade80", "databricks": "#FF3621"}

    # ── Summary strip ─────────────────────────────────────────
    compliance_str = ", ".join(specs.get("compliance", [])) or "None detected"
    ml_badge = ' &nbsp;·&nbsp; 🤖 ML pipeline' if specs.get("needs_ml") else ""
    etl_badge = ' &nbsp;·&nbsp; ⚙️ Heavy transforms' if specs.get("heavy_transforms") else ""
    st.markdown(
        f'<div style="background:rgba(29,78,216,0.1);border:1px solid rgba(56,189,248,0.2);'
        f'border-radius:12px;padding:0.8rem 1.1rem;margin:0.8rem 0;">'
        f'<p style="margin:0;font-size:0.72rem;color:#475569;text-transform:uppercase;'
        f'letter-spacing:0.08em;">Architecture Report</p>'
        f'<p style="margin:0.2rem 0 0.6rem;color:#e2e8f0;font-weight:600;">{report["summary"]}</p>'
        f'<span style="color:#64748b;font-size:0.8rem;">'
        f'📦 ~{specs.get("data_volume_gb_day",50)} GB/day &nbsp;·&nbsp; '
        f'🔄 {specs.get("frequency","daily")} &nbsp;·&nbsp; '
        f'🌍 {specs.get("region","eu-central-1")} &nbsp;·&nbsp; '
        f'🔒 {compliance_str}{ml_badge}{etl_badge}'
        f'</span></div>',
        unsafe_allow_html=True,
    )

    # ── 3 cloud cards (top row) ───────────────────────────────
    cloud_options = [o for o in report["options"] if o["cloud"] != "databricks"]
    db_option     = next((o for o in report["options"] if o["cloud"] == "databricks"), None)

    cols = st.columns(3)
    for i, opt in enumerate(sorted(cloud_options, key=lambda o: o["cloud"])):
        cloud    = opt["cloud"]
        color    = colors[cloud]
        is_rec   = cloud == rec
        card_cls = "arch-card arch-card-rec" if is_rec else "arch-card arch-card-plain"

        pros_html  = "".join(f'<p class="arch-pro">✓ {p}</p>' for p in opt["pros"])
        cons_html  = "".join(f'<p class="arch-con">✗ {p}</p>' for p in opt["cons"])
        badge      = '<span class="arch-rec-badge">★ Recommended</span><br>' if is_rec else ""
        items_html = "".join(
            f'<div style="display:flex;justify-content:space-between;font-size:0.71rem;'
            f'padding:0.12rem 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<span style="color:#475569;">{k}</span>'
            f'<span style="color:#64748b;">${v:.2f}</span></div>'
            for k, v in opt["items"].items()
        )
        with cols[i]:
            st.markdown(
                f'<div class="{card_cls}">{badge}'
                f'<p class="arch-cloud-name" style="color:{color};">{flags[cloud]}</p>'
                f'<p class="arch-price" style="color:{color};">'
                f'${opt["total"]:.0f}<span style="font-size:0.8rem;color:#64748b;font-weight:400;">/mo</span></p>'
                f'<p class="arch-label">{opt["label"]}</p>'
                f'{pros_html}{cons_html}'
                f'<details style="margin-top:0.6rem;">'
                f'<summary style="color:#475569;font-size:0.74rem;cursor:pointer;">Cost breakdown</summary>'
                f'<div style="margin-top:0.4rem;">{items_html}</div>'
                f'</details></div>',
                unsafe_allow_html=True,
            )

    # ── Databricks card (full-width enterprise option) ────────
    if db_option:
        is_db_rec  = rec == "databricks"
        db_border  = "2px solid #FF3621" if is_db_rec else "1px solid rgba(255,54,33,0.3)"
        db_shadow  = "box-shadow:0 0 24px rgba(255,54,33,0.15);" if is_db_rec else ""
        db_badge   = '<span style="background:rgba(255,54,33,0.15);border:1px solid rgba(255,54,33,0.4);border-radius:6px;color:#FF3621;font-size:0.72rem;font-weight:700;padding:0.1rem 0.5rem;margin-bottom:0.5rem;display:inline-block;">★ Recommended</span><br>' if is_db_rec else ""
        host       = db_option.get("host_cloud", "azure").upper()
        when_html  = (f'<p style="margin:0.5rem 0 0;color:#64748b;font-size:0.78rem;font-style:italic;">'
                      f'💡 {db_option.get("recommended_when","")}</p>') if db_option.get("recommended_when") else ""

        pros_html  = "".join(f'<p class="arch-pro">✓ {p}</p>' for p in db_option["pros"])
        cons_html  = "".join(f'<p class="arch-con">✗ {p}</p>' for p in db_option["cons"])
        items_html = "".join(
            f'<div style="display:flex;justify-content:space-between;font-size:0.71rem;'
            f'padding:0.12rem 0;border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<span style="color:#475569;">{k}</span>'
            f'<span style="color:#64748b;">${v:.2f}</span></div>'
            for k, v in db_option["items"].items()
        )

        st.markdown(
            f'<div style="background:rgba(20,8,6,0.7);border:{db_border};{db_shadow}'
            f'border-radius:14px;padding:1rem 1.2rem;margin-top:0.8rem;">'
            f'{db_badge}'
            f'<div style="display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;">'
            f'<div>'
            f'<p class="arch-cloud-name" style="color:#FF3621;">⚡ Databricks Lakehouse</p>'
            f'<p class="arch-price" style="color:#FF3621;">'
            f'${db_option["total"]:.0f}<span style="font-size:0.8rem;color:#64748b;font-weight:400;">/mo</span></p>'
            f'<p class="arch-label">Runs on: {host} &nbsp;·&nbsp; Delta Lake + Spark + Unity Catalog + Mosaic AI</p>'
            f'</div>'
            f'<div style="flex:1;min-width:200px;">{pros_html}{cons_html}</div>'
            f'<div style="flex:1;min-width:160px;">'
            f'<details><summary style="color:#475569;font-size:0.74rem;cursor:pointer;">Cost breakdown</summary>'
            f'<div style="margin-top:0.4rem;">{items_html}</div></details>'
            f'</div></div>'
            f'{when_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Recommendation reason ────────────────────────────────
    if report.get("recommendation_reason"):
        rec_color = colors.get(rec, "#4ade80")
        st.markdown(
            f'<div style="background:rgba(74,222,128,0.07);border:1px solid rgba(74,222,128,0.2);'
            f'border-radius:10px;padding:0.7rem 1rem;margin-top:0.8rem;">'
            f'<span style="font-weight:700;color:{rec_color};">💡 Recommendation: {flags.get(rec,rec)}</span>'
            f'<p style="margin:0.3rem 0 0;color:#94a3b8;font-size:0.85rem;">'
            f'{report["recommendation_reason"]}</p></div>',
            unsafe_allow_html=True,
        )

    # ── Option selection ─────────────────────────────────────
    all_options = ["aws", "azure", "gcp", "databricks"]
    st.markdown('<p style="color:#64748b;font-size:0.85rem;margin-top:1rem;">Select option to deploy:</p>',
                unsafe_allow_html=True)
    choice = st.radio(
        "cloud_select",
        options=all_options,
        format_func=lambda c: flags[c],
        index=all_options.index(rec) if rec in all_options else 0,
        horizontal=True,
        label_visibility="collapsed",
    )
    st.session_state.arch_selected_cloud = choice


# ---------------------------------------------------------------------------
# RULES SELECTOR HELPER
# ---------------------------------------------------------------------------

def _render_rules_selector(key_prefix: str):
    from utils.rules_loader import (
        load_demo_rules, list_demo_domains, parse_rules_file,
        extract_rules_from_nl, SIMPLE_TEMPLATE,
    )

    mode = st.radio(
        "Rules source",
        ["📋 Demo rules", "📝 Extract from description", "📁 Upload file"],
        key=f"rules_mode_{key_prefix}",
        horizontal=True,
        label_visibility="collapsed",
    )

    rules_conf = None

    if mode == "📋 Demo rules":
        domains = list_demo_domains()
        domain  = st.selectbox("Domain", domains, key=f"rules_domain_{key_prefix}")
        rules_conf = load_demo_rules(domain)

    elif mode == "📝 Extract from description":
        st.markdown(
            '<p style="color:#64748b;font-size:0.82rem;margin:0.3rem 0;">'
            'Rules will be extracted automatically when you click <b>Analyze &amp; Plan</b>.</p>',
            unsafe_allow_html=True,
        )
        rules_conf = st.session_state.get(f"rules_conf_{key_prefix}")

    elif mode == "📁 Upload file":
        st.download_button(
            "⬇️ Download template",
            data=SIMPLE_TEMPLATE,
            file_name="rules_template.yaml",
            mime="text/yaml",
            key=f"rules_dl_{key_prefix}",
        )
        uploaded = st.file_uploader(
            "Upload YAML or JSON",
            type=["yaml", "yml", "json"],
            key=f"rules_upload_{key_prefix}",
            label_visibility="collapsed",
        )
        if uploaded:
            try:
                rules_conf = parse_rules_file(uploaded.read(), uploaded.name)
                st.session_state[f"rules_conf_{key_prefix}"] = rules_conf
            except ValueError as e:
                st.error(str(e))
                rules_conf = None

    # Preview
    if rules_conf:
        standards = rules_conf.get("quality_standards", [])
        domain_lbl = rules_conf.get("domain", "")
        st.markdown(
            f'<p style="color:#4ade80;font-size:0.8rem;margin:0.5rem 0 0.3rem;">'
            f'✓ {len(standards)} rule{"s" if len(standards)!=1 else ""} loaded'
            f'{" · domain: " + domain_lbl if domain_lbl else ""}</p>',
            unsafe_allow_html=True,
        )
        with st.expander("Preview rules", expanded=False):
            for s in standards:
                st.markdown(
                    f'<div style="border-left:2px solid rgba(56,189,248,0.3);'
                    f'padding:0.3rem 0.7rem;margin:0.3rem 0;">'
                    f'<p style="margin:0;color:#e2e8f0;font-size:0.82rem;font-weight:600;">'
                    f'{s.get("capability","")}</p>'
                    f'<p style="margin:0;color:#64748b;font-size:0.76rem;">'
                    f'{s.get("logic","")}</p>'
                    f'<span style="color:#f97316;font-size:0.72rem;">'
                    f'on failure → {s.get("on_failure_action","")}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.session_state[f"rules_conf_{key_prefix}"] = rules_conf


def _get_active_rules(key_prefix: str) -> dict | None:
    """Return the rules_conf chosen for this tab, or None to use pipeline defaults."""
    return st.session_state.get(f"rules_conf_{key_prefix}")


# ---------------------------------------------------------------------------
# COST CHARTS HELPER
# ---------------------------------------------------------------------------

def _render_cost_charts(size_gb: int = 50, key: str = "cost"):
    # `key` namespaces the breakdown-selector widget — this function renders in >1 tab and all
    # tabs execute every run, so a shared widget key would raise DuplicateWidgetID.
    from utils.cost_estimator import compare_clouds
    estimates = compare_clouds(storage_gb=size_gb)

    _colors = {"aws": "#f97316", "azure": "#38bdf8", "gcp": "#4ade80", "databricks": "#FF3621"}
    _labels = {"aws": "🟠 AWS", "azure": "🔵 Azure", "gcp": "🟢 GCP", "databricks": "⚡ Databricks"}

    cheapest     = min(estimates, key=lambda e: e["total"])
    savings      = round(max(e["total"] for e in estimates) - cheapest["total"], 2)

    # Bar chart
    bar_df = pd.DataFrame({
        "Option":   [_labels[e["cloud"]] for e in estimates],
        "$/month":  [e["total"] for e in estimates],
    }).set_index("Option")
    st.bar_chart(bar_df, color="#38bdf8", height=210, use_container_width=True)

    # Summary row — 4 columns
    cols = st.columns(4)
    for i, est in enumerate(sorted(estimates, key=lambda e: e["cloud"])):
        c          = est["cloud"]
        is_cheapest = c == cheapest["cloud"]
        color      = _colors[c]
        badge      = " ✓" if is_cheapest else ""
        with cols[i]:
            st.markdown(
                f'<div style="text-align:center;padding:0.4rem;">'
                f'<p style="margin:0;font-size:0.68rem;color:#475569;">{_labels[c]}</p>'
                f'<p style="margin:0;font-size:1.3rem;font-weight:700;color:{color};">'
                f'${est["total"]:.0f}'
                f'<span style="font-size:0.7rem;color:#64748b;">/mo</span>'
                f'<span style="font-size:0.7rem;color:#4ade80;">{badge}</span></p>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown(
        f'<p style="color:#64748b;font-size:0.78rem;margin-top:0.3rem;">'
        f'💡 Cheapest: <b style="color:#4ade80;">{_labels[cheapest["cloud"]]}</b> — '
        f'saves <b style="color:#4ade80;">${savings}/mo</b> vs most expensive'
        f' &nbsp;·&nbsp; {size_gb} GB storage assumed</p>',
        unsafe_allow_html=True,
    )

    # Cost breakdown — pick ANY cloud (defaults to the cheapest).
    _bd_order  = sorted(estimates, key=lambda e: e["total"])           # cheapest first
    _bd_labels = [_labels[e["cloud"]] for e in _bd_order]
    with st.expander("Cost breakdown — pick a cloud", expanded=False):
        _pick = st.radio(
            "Breakdown cloud", _bd_labels, index=0, horizontal=True,
            label_visibility="collapsed", key=f"{key}_breakdown_pick",
        )
        _sel = _bd_order[_bd_labels.index(_pick)]
        _bd_df = pd.DataFrame(
            [{"Service": k, "$/month": round(v, 2)} for k, v in _sel["items"].items()]
        )
        st.dataframe(
            _bd_df, use_container_width=True, hide_index=True,
            column_config={"$/month": st.column_config.NumberColumn("$/month", format="$%.2f")},
        )
        st.markdown(
            f'<p style="color:#94a3b8;font-size:0.85rem;margin-top:0.3rem;">'
            f'Total: <b style="color:{_colors[_sel["cloud"]]};">${_sel["total"]:.0f}/mo</b>'
            f'{" ✓ cheapest" if _sel["cloud"] == cheapest["cloud"] else ""}</p>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# COST ESTIMATION HELPER
# ---------------------------------------------------------------------------

def _render_cost_panel(selected_cloud: str | None = None):
    from utils.cost_estimator import compare_clouds
    estimates = compare_clouds()
    cheapest  = estimates[0]["cloud"]
    flags     = {"aws": "🟠 AWS", "azure": "🔵 Azure", "gcp": "🟢 GCP", "databricks": "⚡ Databricks"}
    colors    = {"aws": "#f97316",  "azure": "#38bdf8",  "gcp": "#4ade80", "databricks": "#FF3621"}

    with st.expander("💰 Estimated monthly infrastructure cost", expanded=False):
        cols = st.columns(len(estimates))   # compare_clouds() returns 4 (incl. databricks)
        for i, est in enumerate(sorted(estimates, key=lambda x: x["cloud"])):
            c   = est["cloud"]
            col = colors.get(c, "#38bdf8")
            is_cheapest  = c == cheapest
            is_selected  = c == selected_cloud
            border_color = "#4ade80" if is_cheapest else (col if is_selected else "rgba(56,189,248,0.18)")
            badge = " ✓ cheapest" if is_cheapest else (" ← selected" if is_selected else "")

            with cols[i]:
                rows_html = "".join(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:0.18rem 0;border-bottom:1px solid rgba(255,255,255,0.04);'
                    f'font-size:0.73rem;">'
                    f'<span style="color:#64748b;">{k}</span>'
                    f'<span style="color:#94a3b8;">${v:.2f}</span></div>'
                    for k, v in est["items"].items()
                )
                st.markdown(
                    f'<div style="background:rgba(10,18,40,0.7);border:1px solid {border_color};'
                    f'border-radius:12px;padding:0.85rem 1rem;">'
                    f'<p style="margin:0 0 0.1rem;font-size:0.72rem;color:#475569;'
                    f'text-transform:uppercase;letter-spacing:0.08em;">{flags.get(c, c.upper())}</p>'
                    f'<p style="margin:0 0 0.6rem;font-size:1.5rem;font-weight:700;color:{col};">'
                    f'${est["total"]:.0f}<span style="font-size:0.8rem;color:#64748b;">/mo</span>'
                    f'<span style="font-size:0.72rem;color:#4ade80;margin-left:0.4rem;">{badge}</span></p>'
                    f'{rows_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        st.markdown(
            '<p style="color:#334155;font-size:0.72rem;margin-top:0.6rem;">'
            '* List prices (~2026-06) · representative regions · fixed bootstrap footprint · '
            '50 GB storage · Databricks is usage-billed (see cost_estimator disclaimer)</p>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# EXECUTION PLAN HELPER — illustrative "what will be created" (no run)
# ---------------------------------------------------------------------------

# The artifact SET is deterministic per cloud (the architect decides exact contents at run time,
# but the structure is fixed). NL authoring covers the three object-storage clouds (Option A).
_EXEC_PLAN_CLOUD = {
    "aws":   {"store": "S3 bucket",          "fsdriver": "s3fs",
              "iac": "S3 bucket + IAM role (IRSA) + Glue catalog access",
              "registry": "ECR", "k8s": "EKS"},
    "azure": {"store": "ADLS Gen2 container", "fsdriver": "adlfs",
              "iac": "ADLS container + user-assigned managed identity + role assignment",
              "registry": "ACR", "k8s": "AKS"},
    "gcp":   {"store": "GCS bucket",          "fsdriver": "gcsfs",
              "iac": "GCS bucket + processed/ prefix + Workload Identity SA",
              "registry": "Artifact Registry", "k8s": "GKE Autopilot"},
}


def _render_execution_plan(cloud: str, pipeline_id: str):
    """Illustrative pre-deploy plan: the deterministic deliverable set for `cloud`. No agent run."""
    c = _EXEC_PLAN_CLOUD.get(cloud)
    if not c:
        return
    groups = [
        ("Pipeline code", [
            f"scripts/{pipeline_id}.py — extract → business rules → parquet → {c['store']}",
            f"requirements.txt — pandas, pyarrow, {c['fsdriver']}, sqlalchemy, trino, prometheus-client",
        ]),
        ("Catalog & observability", [
            "sql/setup_trino.sql — Trino external table, run_date-partitioned",
            "dashboards/monitoring_specs.json — Grafana dashboard (5 panels)",
        ]),
        ("Kubernetes", [
            "k8s/ — namespaces · configmaps · trino · grafana · prometheus(+pushgateway) · job",
            "Dockerfile — pipeline image",
        ]),
        ("Infrastructure (Terraform)", [
            "terraform/ — providers · main · variables · outputs · tfvars",
            f"provisions: {c['iac']}",
        ]),
        ("CI/CD", [
            f".github/workflows/{pipeline_id}_pipeline.yml — build → {c['registry']} → deploy to {c['k8s']}",
        ]),
    ]
    blocks = ""
    for title, items in groups:
        lis = "".join(f'<li style="margin:0.12rem 0;color:#94a3b8;">{it}</li>' for it in items)
        blocks += (
            f'<p style="margin:0.55rem 0 0.1rem;color:#7dd3fc;font-size:0.72rem;'
            f'text-transform:uppercase;letter-spacing:0.06em;">{title}</p>'
            f'<ul style="margin:0;padding-left:1.1rem;font-size:0.8rem;">{lis}</ul>'
        )
    st.markdown(
        f'<div style="background:rgba(10,18,40,0.6);border:1px solid rgba(56,189,248,0.18);'
        f'border-radius:12px;padding:0.85rem 1.1rem;margin:0.2rem 0 0.7rem;">{blocks}'
        f'<p style="margin:0.55rem 0 0;color:#334155;font-size:0.7rem;">'
        f'Illustrative — the agents generate the exact contents at deploy time. '
        f'Self-healing (Medic) repairs any CI/CD failure automatically.</p></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# INPUT SECTION
# ---------------------------------------------------------------------------

tab_nl, tab_upload, tab_existing, tab_trino, tab_obs = st.tabs([
    "💬 Natural Language",
    "📁 Upload Dataset",
    "📂 Existing Pipeline",
    "🔮 Trino Federation",
    "📊 Observability",
])

# ── Upload Dataset tab ──────────────────────────────────────────────────────
with tab_upload:
    st.markdown(
        '<p style="color:#94a3b8;margin-bottom:0.8rem;">'
        'Upload a sample of your dataset — the agent detects the schema, '
        'spots PII fields, and pre-fills the business rules for you.</p>',
        unsafe_allow_html=True,
    )

    uploaded_ds = st.file_uploader(
        "Dataset",
        type=["csv", "json", "jsonl", "parquet"],
        label_visibility="collapsed",
        key="dataset_upload",
    )

    if uploaded_ds:
        with st.spinner("Analysing dataset…"):
            try:
                from utils.dataset_analyzer import analyze as analyze_dataset
                ds = analyze_dataset(uploaded_ds.read(), uploaded_ds.name)
                st.session_state.dataset_analysis = ds
            except Exception as e:
                st.error(str(e))
                ds = None

        if ds:
            stats = ds["stats"]
            # ── Stats row ──────────────────────────────────────
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Rows",       f"{stats['rows']:,}")
            c2.metric("Columns",    stats["columns"])
            c3.metric("Size",       f"{stats['size_mb']} MB")
            c4.metric("Duplicates", stats["dup_rows"])

            # ── PII alert ──────────────────────────────────────
            if ds["pii_fields"]:
                pii_list = ", ".join(f"`{f}`" for f in ds["pii_fields"])
                st.markdown(
                    f'<div style="background:rgba(249,115,22,0.08);border:1px solid '
                    f'rgba(249,115,22,0.3);border-radius:10px;padding:0.65rem 1rem;margin:0.6rem 0;">'
                    f'<p style="margin:0;color:#f97316;font-weight:600;font-size:0.85rem;">'
                    f'⚠️ PII detected — {len(ds["pii_fields"])} field(s): {pii_list}</p>'
                    f'<p style="margin:0.2rem 0 0;color:#64748b;font-size:0.78rem;">'
                    f'Masking rules have been added automatically.</p></div>',
                    unsafe_allow_html=True,
                )

            # ── Quality issues ─────────────────────────────────
            if ds["quality_issues"]:
                with st.expander(f"⚠️ {len(ds['quality_issues'])} data quality issue(s) found"):
                    for iss in ds["quality_issues"]:
                        sev_color = "#f87171" if iss["severity"] == "high" else "#fbbf24"
                        st.markdown(
                            f'<div style="display:flex;gap:0.6rem;align-items:flex-start;'
                            f'padding:0.3rem 0;border-bottom:1px solid rgba(255,255,255,0.05);">'
                            f'<span style="color:{sev_color};font-size:0.8rem;min-width:60px;">'
                            f'{iss["severity"].upper()}</span>'
                            f'<span style="color:#94a3b8;font-size:0.8rem;">'
                            f'<b style="color:#e2e8f0;">{iss["column"]}</b> — {iss["detail"]}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

            # ── Schema table ───────────────────────────────────
            with st.expander("📋 Schema", expanded=True):
                schema_df = pd.DataFrame([
                    {
                        "Column":   s["column"],
                        "Type":     s["type"],
                        "Nullable": "✓" if s["nullable"] else "✗",
                        "PII":      "⚠️" if s["column"] in ds["pii_fields"] else "",
                        "Sample values": " · ".join(s["sample"]),
                    }
                    for s in ds["schema"]
                ])
                st.dataframe(schema_df, use_container_width=True, hide_index=True)

            # ── Suggested rules summary ────────────────────────
            rules = ds["suggested_rules"]
            n     = len(rules["quality_standards"])
            st.markdown(
                f'<div style="background:rgba(74,222,128,0.07);border:1px solid '
                f'rgba(74,222,128,0.2);border-radius:10px;padding:0.65rem 1rem;margin-top:0.6rem;">'
                f'<p style="margin:0;color:#4ade80;font-weight:600;font-size:0.85rem;">'
                f'✓ {n} business rule{"s" if n!=1 else ""} auto-generated from your data</p>'
                f'<p style="margin:0.2rem 0 0;color:#64748b;font-size:0.78rem;">'
                f'Go to the <b>💬 Natural Language</b> tab, open ⚙️ Business Rules '
                f'and select <em>Extract from description</em> — or the rules are already '
                f'applied if you use the dataset analysis.</p></div>',
                unsafe_allow_html=True,
            )
            # Store rules so the NL tab can pick them up
            st.session_state["rules_conf_nl"] = rules
            st.session_state["rules_conf_ex"] = rules

            # ── Cost chart preview ─────────────────────────────
            st.markdown("##### 💰 Estimated monthly cost based on your data volume")
            _render_cost_charts(size_gb=max(round(ds["size_gb_day"] * 30), 1), key="upload")

    else:
        st.markdown(
            '<div style="border:2px dashed rgba(56,189,248,0.2);border-radius:14px;'
            'padding:2.5rem;text-align:center;margin-top:0.5rem;">'
            '<p style="color:#334155;font-size:0.9rem;margin:0;">CSV · JSON · Parquet</p>'
            '<p style="color:#1e293b;font-size:0.78rem;margin:0.4rem 0 0;">'
            'Max ~100 MB recommended for preview</p>'
            '</div>',
            unsafe_allow_html=True,
        )

with tab_nl:
    # ── Session-state defaults ────────────────────────────────────────────
    _NL_DEFAULTS = {
        "nl_step": 0,
        "nl_description": "",
        "nl_intent": {},
        "nl_answers": {},
        "nl_rules": [],
        "nl_rules_mode": "detected",
        "nl_summary_ok": False,
        "nl_back_to_step": None,
        "nl_rules_initialized": False,
        "nl_wizard_editing_idx": None,
        "nl_wizard_adding_rule": False,
        "nl_wizard_suggested_rules": [],
        "nl_wizard_loaded_rules": [],
    }
    for _k, _v in _NL_DEFAULTS.items():
        if _k not in st.session_state:
            st.session_state[_k] = _v

    _nl_step = st.session_state.nl_step

    # ── Step progress bar ─────────────────────────────────────────────────
    _STEP_LABELS = ["📝 Describe", "⚙️ Fields", "📋 Rules", "✅ Confirm", "🚀 Deploy"]
    _prog_cols = st.columns(len(_STEP_LABELS))
    for _pi, (_pc, _pl) in enumerate(zip(_prog_cols, _STEP_LABELS)):
        _is_active = _nl_step == _pi
        _is_done   = _nl_step > _pi
        _color = "#38bdf8" if _is_active else ("#4ade80" if _is_done else "#334155")
        _bg    = "rgba(56,189,248,0.10)" if _is_active else ("rgba(74,222,128,0.07)" if _is_done else "transparent")
        _fw    = "700" if _is_active else "400"
        with _pc:
            st.markdown(
                f'<div style="text-align:center;padding:0.35rem 0.2rem;background:{_bg};'
                f'border-radius:8px;border:1px solid {_color}33;">'
                f'<span style="color:{_color};font-size:0.76rem;font-weight:{_fw};">{_pl}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    st.markdown("<div style='margin-top:1.1rem;'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 0 — Natural Language Input
    # ════════════════════════════════════════════════════════════════════════
    if _nl_step == 0:
        st.markdown(
            '<p style="color:#94a3b8;margin-bottom:0.5rem;">'
            'Describe your data pipeline in plain English.</p>',
            unsafe_allow_html=True,
        )
        _desc = st.text_area(
            "Pipeline description",
            value=st.session_state.nl_description,
            placeholder=(
                'e.g. "Daily sales data from PostgreSQL in Frankfurt to AWS S3, '
                'with PII masking on customer emails"'
            ),
            height=120,
            label_visibility="collapsed",
            key="nl_wizard_desc_textarea",
        )
        _s0c1, _ = st.columns([1, 3])
        with _s0c1:
            if st.button("Continue →", key="nl_wizard_step0_continue", type="primary",
                         disabled=not _desc.strip()):
                with st.spinner("Extracting intent from your description…"):
                    from utils.nlp_parser import _extract_intent
                    _intent = _extract_intent(_desc.strip())
                # Relevance gate — reject off-topic input instead of advancing into the wizard
                # with a fabricated config. (Mirrors nlp_parser.check_pipeline_request.)
                if not _intent.get("is_pipeline_request", True):
                    st.error(
                        "🚫 " + (_intent.get("rejection_reason")
                                 or "This doesn't look like a data-pipeline request. Describe a "
                                    "source table and a cloud destination (e.g. \"Postgres orders "
                                    "table to GCP daily\").")
                    )
                else:
                    st.session_state.nl_description     = _desc.strip()
                    st.session_state.nl_intent          = _intent
                    st.session_state.nl_rules_initialized = False
                    st.session_state.nl_step            = 1
                    st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # STEP 1 — Field Clarification
    # ════════════════════════════════════════════════════════════════════════
    elif _nl_step == 1:
        from utils.nlp_parser import FIELD_OPTIONS as _FO
        st.markdown(
            '<p style="color:#94a3b8;margin-bottom:1rem;">'
            'Review and confirm every pipeline field below.</p>',
            unsafe_allow_html=True,
        )

        _intent_raw = st.session_state.nl_intent
        _prev_ans   = st.session_state.nl_answers  # non-empty when editing from step 3

        def _pre1(field, default=""):
            return _prev_ans.get(field) or _intent_raw.get(field) or default

        def _lbl(text):
            st.markdown(
                f'<p style="color:#94a3b8;font-size:0.84rem;margin:0.8rem 0 0.2rem;">{text}</p>',
                unsafe_allow_html=True,
            )

        _lbl("Pipeline name (slug)")
        _slug_v = st.text_input("slug", value=_pre1("pipeline_slug"),
                                placeholder="e.g. eu_sales",
                                label_visibility="collapsed", key="nl_wizard_f_slug")

        _domain_opts = _FO["data_domain"]
        _domain_pre  = _pre1("data_domain", "sales")
        _domain_idx  = _domain_opts.index(_domain_pre) if _domain_pre in _domain_opts else 0
        _lbl("Data domain")
        _domain_v = st.radio("domain", _domain_opts, index=_domain_idx, horizontal=True,
                             label_visibility="collapsed", key="nl_wizard_f_domain")

        _db_opts = _FO["source_db_type"]
        _db_pre  = _pre1("source_db_type", "postgres")
        _db_idx  = _db_opts.index(_db_pre) if _db_pre in _db_opts else 0
        _lbl("Source database type")
        _db_v = st.radio("dbtype", _db_opts, index=_db_idx, horizontal=True,
                         label_visibility="collapsed", key="nl_wizard_f_dbtype")

        _lbl("Source table name")
        _table_v = st.text_input("table", value=_pre1("source_table"),
                                 placeholder="e.g. raw_orders",
                                 label_visibility="collapsed", key="nl_wizard_f_table")

        _cloud_opts = _FO["target_cloud"]
        _cloud_pre  = _pre1("target_cloud", "aws")
        _cloud_idx  = _cloud_opts.index(_cloud_pre) if _cloud_pre in _cloud_opts else 0
        _lbl("Target cloud")
        _cloud_v = st.radio("cloud", _cloud_opts, index=_cloud_idx, horizontal=True,
                            label_visibility="collapsed", key="nl_wizard_f_cloud")

        _freq_opts = _FO["frequency"]
        _freq_pre  = _pre1("frequency", "daily")
        _freq_idx  = _freq_opts.index(_freq_pre) if _freq_pre in _freq_opts else 0
        _lbl("Frequency")
        _freq_v = st.radio("freq", _freq_opts, index=_freq_idx, horizontal=True,
                           label_visibility="collapsed", key="nl_wizard_f_freq")

        _lbl("Owner team")
        _owner_v = st.text_input("owner", value=_pre1("owner_team", "analytics_team"),
                                 placeholder="e.g. analytics_team",
                                 label_visibility="collapsed", key="nl_wizard_f_owner")

        st.markdown("<hr style='margin:1.2rem 0;'>", unsafe_allow_html=True)
        _s1c1, _s1c2, _ = st.columns([1, 1, 3])
        with _s1c1:
            if st.button("Confirm fields →", key="nl_wizard_step1_confirm", type="primary"):
                _errs = []
                if not _slug_v.strip():  _errs.append("Pipeline name is required.")
                if not _table_v.strip(): _errs.append("Source table is required.")
                if _errs:
                    for _e in _errs: st.error(_e)
                else:
                    st.session_state.nl_answers = {
                        "pipeline_slug": _slug_v.strip(),
                        "data_domain":   _domain_v,
                        "source_db_type": _db_v,
                        "source_table":  _table_v.strip(),
                        "target_cloud":  _cloud_v,
                        "frequency":     _freq_v,
                        "owner_team":    _owner_v.strip() or "analytics_team",
                    }
                    _dest = st.session_state.pop("nl_back_to_step", None) or 2
                    st.session_state.nl_step = _dest
                    st.rerun()
        with _s1c2:
            if st.button("← Back", key="nl_wizard_step1_back", type="secondary"):
                st.session_state.nl_step = 0
                st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # STEP 2 — Business Rules
    # ════════════════════════════════════════════════════════════════════════
    elif _nl_step == 2:
        st.markdown(
            '<p style="color:#94a3b8;margin-bottom:1rem;">Review and edit business rules.</p>',
            unsafe_allow_html=True,
        )

        _raw_rules = st.session_state.nl_intent.get("business_rules", [])
        _domain    = st.session_state.nl_answers.get("data_domain", "other")

        if not st.session_state.nl_rules_initialized:
            st.session_state.nl_rules               = list(_raw_rules)
            st.session_state.nl_rules_initialized   = True
            st.session_state.nl_wizard_editing_idx  = None
            st.session_state.nl_wizard_adding_rule  = False
            st.session_state.nl_wizard_suggested_rules = []
            st.session_state.nl_wizard_loaded_rules = []

        _ACTION_OPTS = ["DROP_RECORD", "EXCLUDE_AND_LOG", "FLAG_AS_SUSPICIOUS", "DEFAULT_VALUE"]
        _ACTION_COLORS = {
            "DROP_RECORD": "#f87171", "EXCLUDE_AND_LOG": "#fbbf24",
            "FLAG_AS_SUSPICIOUS": "#f97316", "DEFAULT_VALUE": "#4ade80",
        }

        def _rule_edit_form(prefix, existing=None):
            existing = existing or {}
            _ri = st.text_input("Rule ID", value=existing.get("rule_id", ""),
                                key=f"{prefix}_id")
            _rc = st.text_input("Target column", value=existing.get("target_column", ""),
                                key=f"{prefix}_col")
            st.markdown(
                '<p style="color:#475569;font-size:0.75rem;margin:0.2rem 0 0.1rem;">'
                "Condition — pandas expression, e.g. df['col'].notna()</p>",
                unsafe_allow_html=True,
            )
            _rk = st.text_input("Condition", value=existing.get("condition", ""),
                                 key=f"{prefix}_cond")
            _ai = _ACTION_OPTS.index(existing.get("action", "DROP_RECORD")) \
                  if existing.get("action") in _ACTION_OPTS else 0
            _ra = st.radio("Action", _ACTION_OPTS, index=_ai, horizontal=True,
                           key=f"{prefix}_action")
            _rr = st.text_input("Reason", value=existing.get("reason", ""),
                                key=f"{prefix}_reason")
            return {"rule_id": _ri, "target_column": _rc,
                    "condition": _rk, "action": _ra, "reason": _rr}

        # ─────────────────────────────────────────────────────────────────────
        # CASE A — GPT extracted rules
        # ─────────────────────────────────────────────────────────────────────
        if _raw_rules:
            _rules      = st.session_state.nl_rules
            _edit_idx   = st.session_state.nl_wizard_editing_idx

            st.markdown(
                f'<p style="color:#4ade80;font-size:0.82rem;margin-bottom:0.5rem;">'
                f'✓ {len(_raw_rules)} rule(s) extracted — review below.</p>',
                unsafe_allow_html=True,
            )

            for _ri_i, _ri_r in enumerate(_rules):
                if _edit_idx == _ri_i:
                    # ── Inline edit form ─────────────────────────────────────
                    st.markdown(
                        f'<div style="background:rgba(56,189,248,0.08);border:1px solid '
                        f'rgba(56,189,248,0.3);border-radius:10px;padding:0.8rem 1rem;margin:0.4rem 0;">'
                        f'<p style="color:#38bdf8;font-size:0.82rem;font-weight:600;margin:0 0 0.6rem;">'
                        f'✏️ Editing rule {_ri_i + 1}</p></div>',
                        unsafe_allow_html=True,
                    )
                    _edited = _rule_edit_form(f"nl_wizard_edit_{_ri_i}", existing=_ri_r)
                    _ef1, _ef2, _ = st.columns([1, 1, 3])
                    with _ef1:
                        if st.button("💾 Save", key=f"nl_wizard_rule_save_{_ri_i}",
                                     type="primary"):
                            st.session_state.nl_rules[_ri_i] = _edited
                            st.session_state.nl_wizard_editing_idx = None
                            st.rerun()
                    with _ef2:
                        if st.button("✗ Cancel", key=f"nl_wizard_rule_cancel_{_ri_i}",
                                     type="secondary"):
                            st.session_state.nl_wizard_editing_idx = None
                            st.rerun()
                else:
                    # ── Rule card ─────────────────────────────────────────────
                    with st.expander(
                        f"Rule {_ri_i + 1}: {_ri_r.get('rule_id', '?')}", expanded=True
                    ):
                        _card1, _card2, _card3 = st.columns([4, 2, 1])
                        with _card1:
                            _ac = _ACTION_COLORS.get(_ri_r.get("action", ""), "#94a3b8")
                            st.markdown(
                                f'<p style="margin:0;color:#94a3b8;font-size:0.8rem;">'
                                f'<b style="color:#e2e8f0;">column:</b> {_ri_r.get("target_column","")}'
                                f' &nbsp;|&nbsp; '
                                f'<span style="color:{_ac};font-weight:600;">{_ri_r.get("action","")}</span>'
                                f'</p>'
                                f'<p style="margin:0.2rem 0 0;color:#475569;font-size:0.75rem;">'
                                f'<code style="color:#7dd3fc;">{_ri_r.get("condition","")}</code></p>',
                                unsafe_allow_html=True,
                            )
                        with _card3:
                            _btn1, _btn2 = st.columns(2)
                            with _btn1:
                                if st.button("✏️", key=f"nl_wizard_rule_edit_{_ri_i}",
                                             help="Edit rule"):
                                    st.session_state.nl_wizard_editing_idx = _ri_i
                                    st.rerun()
                            with _btn2:
                                if st.button("🗑️", key=f"nl_wizard_rule_del_{_ri_i}",
                                             help="Remove rule"):
                                    st.session_state.nl_rules.pop(_ri_i)
                                    st.session_state.nl_wizard_editing_idx = None
                                    st.rerun()

            # ── Add rule ─────────────────────────────────────────────────────
            if st.session_state.nl_wizard_adding_rule:
                st.markdown(
                    '<div style="background:rgba(74,222,128,0.06);border:1px solid '
                    'rgba(74,222,128,0.2);border-radius:10px;padding:0.8rem 1rem;margin:0.8rem 0;">'
                    '<p style="color:#4ade80;font-size:0.82rem;font-weight:600;margin:0 0 0.5rem;">'
                    '+ New Rule</p>',
                    unsafe_allow_html=True,
                )
                _new_r = _rule_edit_form("nl_wizard_add")
                st.markdown("</div>", unsafe_allow_html=True)
                _an1, _an2, _ = st.columns([1, 1, 3])
                with _an1:
                    if st.button("💾 Add", key="nl_wizard_rule_add_save", type="primary"):
                        st.session_state.nl_rules.append(_new_r)
                        st.session_state.nl_wizard_adding_rule = False
                        st.rerun()
                with _an2:
                    if st.button("✗ Cancel", key="nl_wizard_rule_add_cancel",
                                 type="secondary"):
                        st.session_state.nl_wizard_adding_rule = False
                        st.rerun()
            else:
                if st.button("+ Add rule", key="nl_wizard_rule_add_btn", type="secondary"):
                    st.session_state.nl_wizard_adding_rule = True
                    st.rerun()

            st.markdown("<hr style='margin:1.2rem 0;'>", unsafe_allow_html=True)
            _s2a1, _s2a2, _ = st.columns([1, 1, 3])
            with _s2a1:
                if st.button("Continue →", key="nl_wizard_step2_continue", type="primary"):
                    _dest = st.session_state.pop("nl_back_to_step", None) or 3
                    st.session_state.nl_step = _dest
                    st.rerun()
            with _s2a2:
                if st.button("← Back", key="nl_wizard_step2_back", type="secondary"):
                    st.session_state.nl_step = 1
                    st.rerun()

        # ─────────────────────────────────────────────────────────────────────
        # CASE B — No rules detected
        # ─────────────────────────────────────────────────────────────────────
        else:
            st.markdown(
                '<p style="color:#fbbf24;font-size:0.85rem;margin-bottom:0.8rem;">'
                '⚠️ No business rules detected in your description.</p>',
                unsafe_allow_html=True,
            )

            _rule_mode = st.radio(
                "How to handle rules?",
                ["💡 Suggest rules for my domain",
                 "📁 Load from file (.md / .yaml / .json)",
                 "⏭️ Continue without rules"],
                key="nl_wizard_rule_mode_radio",
                label_visibility="collapsed",
            )

            if _rule_mode == "⏭️ Continue without rules":
                # Advance immediately on selection
                st.session_state.nl_rules = []
                _dest = st.session_state.pop("nl_back_to_step", None) or 3
                st.session_state.nl_step = _dest
                st.rerun()

            elif _rule_mode == "💡 Suggest rules for my domain":
                if st.button("✨ Generate suggestions", key="nl_wizard_suggest_btn",
                             type="primary"):
                    with st.spinner(f"Generating rules for '{_domain}' domain…"):
                        from utils.nlp_parser import suggest_rules_for_domain
                        st.session_state.nl_wizard_suggested_rules = \
                            suggest_rules_for_domain(_domain)
                    st.rerun()

                _suggestions = st.session_state.nl_wizard_suggested_rules
                if _suggestions:
                    st.markdown(
                        f'<p style="color:#4ade80;font-size:0.82rem;margin:0.5rem 0 0.3rem;">'
                        f'✓ {len(_suggestions)} suggestion(s) — pick which to keep:</p>',
                        unsafe_allow_html=True,
                    )
                    _keep_mask = []
                    for _si, _sr in enumerate(_suggestions):
                        _ac = _ACTION_COLORS.get(_sr.get("action", ""), "#94a3b8")
                        _k = st.checkbox(
                            f"{_si + 1}. **{_sr.get('rule_id','')}** — "
                            f"`{_sr.get('target_column','')}` — "
                            f"{_sr.get('action','')}",
                            value=True,
                            key=f"nl_wizard_suggest_keep_{_si}",
                        )
                        _keep_mask.append(_k)
                    if st.button("Use selected →", key="nl_wizard_suggest_use",
                                 type="primary"):
                        st.session_state.nl_rules = [
                            _sr for _sr, _k in zip(_suggestions, _keep_mask) if _k
                        ]
                        _dest = st.session_state.pop("nl_back_to_step", None) or 3
                        st.session_state.nl_step = _dest
                        st.rerun()

            elif _rule_mode == "📁 Load from file (.md / .yaml / .json)":
                _up_rules = st.file_uploader(
                    "Upload rules file",
                    type=["md", "yaml", "yml", "json", "txt"],
                    key="nl_wizard_rules_upload",
                    label_visibility="collapsed",
                )
                if _up_rules:
                    if st.button("Parse file →", key="nl_wizard_rules_parse_btn",
                                 type="primary"):
                        with st.spinner("Parsing rules from file…"):
                            from utils.nlp_parser import parse_rules_from_content
                            _content = _up_rules.read().decode("utf-8", errors="replace")
                            st.session_state.nl_wizard_loaded_rules = \
                                parse_rules_from_content(_content)
                        st.rerun()

                _loaded = st.session_state.nl_wizard_loaded_rules
                if _loaded:
                    st.markdown(
                        f'<p style="color:#4ade80;font-size:0.82rem;margin:0.5rem 0 0.3rem;">'
                        f'✓ {len(_loaded)} rule(s) parsed from file.</p>',
                        unsafe_allow_html=True,
                    )
                    for _li, _lr in enumerate(_loaded):
                        st.markdown(
                            f'<div style="border-left:2px solid rgba(56,189,248,0.3);'
                            f'padding:0.25rem 0.7rem;margin:0.2rem 0;font-size:0.8rem;">'
                            f'<b style="color:#e2e8f0;">{_lr.get("rule_id","")}</b>'
                            f' — {_lr.get("target_column","")} — '
                            f'<span style="color:{_ACTION_COLORS.get(_lr.get("action",""),"#94a3b8")};">'
                            f'{_lr.get("action","")}</span></div>',
                            unsafe_allow_html=True,
                        )
                    if st.button("Use these rules →", key="nl_wizard_loaded_use",
                                 type="primary"):
                        st.session_state.nl_rules = _loaded
                        _dest = st.session_state.pop("nl_back_to_step", None) or 3
                        st.session_state.nl_step = _dest
                        st.rerun()

            st.markdown("<hr style='margin:1.2rem 0;'>", unsafe_allow_html=True)
            if st.button("← Back", key="nl_wizard_step2b_back", type="secondary"):
                st.session_state.nl_step = 1
                st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # STEP 3 — Summary & Confirmation
    # ════════════════════════════════════════════════════════════════════════
    elif _nl_step == 3:
        _ans   = st.session_state.nl_answers
        _rules = st.session_state.nl_rules
        _cf    = {"aws": "🟠 AWS", "azure": "🔵 Azure", "gcp": "🟢 GCP"}
        _cloud_label = _cf.get(_ans.get("target_cloud", ""), _ans.get("target_cloud", ""))

        # ── Rules section HTML ────────────────────────────────────────────
        if _rules:
            _rules_header = f'Business Rules ({len(_rules)})'
            _rules_body   = "".join(
                f'<div style="padding:0.15rem 0;color:#94a3b8;font-size:0.79rem;">'
                f'• <b style="color:#e2e8f0;">{_r.get("rule_id","")}</b>'
                f' &nbsp;—&nbsp; {_r.get("target_column","")}'
                f' &nbsp;—&nbsp; <span style="color:#f97316;">{_r.get("action","")}</span>'
                f'</div>'
                for _r in _rules
            )
        else:
            _rules_header = "Business Rules"
            _rules_body   = '<span style="color:#334155;">none</span>'

        st.markdown(
            f'<div style="background:rgba(10,18,40,0.85);border:2px solid rgba(56,189,248,0.35);'
            f'border-radius:14px;padding:1.4rem 1.6rem;margin:0.4rem 0 1rem;">'
            f'<p style="color:#475569;font-size:0.7rem;text-transform:uppercase;'
            f'letter-spacing:0.09em;margin:0 0 0.9rem;">Pipeline Configuration Summary</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:0.84rem;">'
            f'<tr><td style="color:#64748b;padding:0.22rem 0;width:150px;">Pipeline</td>'
            f'<td style="color:#e2e8f0;font-weight:600;">{_ans.get("pipeline_slug","")}</td></tr>'
            f'<tr><td style="color:#64748b;padding:0.22rem 0;">Domain</td>'
            f'<td style="color:#e2e8f0;">{_ans.get("data_domain","")}</td></tr>'
            f'<tr><td style="color:#64748b;padding:0.22rem 0;">Database</td>'
            f'<td style="color:#e2e8f0;">{_ans.get("source_db_type","")} &nbsp;→&nbsp; '
            f'<span style="color:#7dd3fc;">table: {_ans.get("source_table","")}</span></td></tr>'
            f'<tr><td style="color:#64748b;padding:0.22rem 0;">Cloud</td>'
            f'<td style="color:#e2e8f0;">{_cloud_label}</td></tr>'
            f'<tr><td style="color:#64748b;padding:0.22rem 0;">Frequency</td>'
            f'<td style="color:#e2e8f0;">{_ans.get("frequency","")}</td></tr>'
            f'<tr><td style="color:#64748b;padding:0.22rem 0;">Owner</td>'
            f'<td style="color:#e2e8f0;">{_ans.get("owner_team","")}</td></tr>'
            f'<tr><td style="color:#64748b;padding:0.3rem 0 0.1rem;vertical-align:top;">'
            f'{_rules_header}</td>'
            f'<td style="padding:0.3rem 0 0.1rem;">{_rules_body}</td></tr>'
            f'</table></div>',
            unsafe_allow_html=True,
        )

        # ── Execution plan — what will be created (illustrative, no run) ──
        st.markdown(
            '<p style="color:#475569;font-size:0.7rem;text-transform:uppercase;'
            'letter-spacing:0.09em;margin:0.4rem 0 0.3rem;">'
            'Execution plan — what will be created</p>',
            unsafe_allow_html=True,
        )
        _exec_suffix = {"aws": "s3", "azure": "azure", "gcp": "gcp"}.get(
            _ans.get("target_cloud", ""), "s3")
        _render_execution_plan(
            _ans.get("target_cloud", ""),
            f"pipe_{_ans.get('pipeline_slug', 'pipeline')}_to_{_exec_suffix}",
        )

        # ── Cost preview (shown BEFORE the explicit deploy) ───────────────
        st.markdown(
            '<p style="color:#475569;font-size:0.7rem;text-transform:uppercase;'
            'letter-spacing:0.09em;margin:0.4rem 0 0.3rem;">'
            f'Estimated monthly cost &nbsp;·&nbsp; your pick: {_cloud_label}</p>',
            unsafe_allow_html=True,
        )
        _render_cost_charts(key="nl")

        # ── Edit buttons ──────────────────────────────────────────────────
        st.markdown(
            '<p style="color:#475569;font-size:0.8rem;margin:0 0 0.4rem;">'
            'Need to change something?</p>',
            unsafe_allow_html=True,
        )
        _e1, _e2, _ = st.columns([1, 1, 3])
        with _e1:
            if st.button("✏️ Edit fields", key="nl_wizard_step3_edit_fields",
                         type="secondary"):
                st.session_state.nl_back_to_step = 3
                st.session_state.nl_step = 1
                st.rerun()
        with _e2:
            if st.button("📋 Edit rules", key="nl_wizard_step3_edit_rules",
                         type="secondary"):
                st.session_state.nl_back_to_step    = 3
                st.session_state.nl_rules_initialized = False
                st.session_state.pop("nl_wizard_rule_mode_radio", None)
                st.session_state.nl_step = 2
                st.rerun()

        st.markdown("<hr style='margin:1rem 0;'>", unsafe_allow_html=True)
        _a1, _a2, _ = st.columns([1, 1, 3])
        with _a1:
            if st.button("✅ Confirm & Deploy", key="nl_wizard_step3_confirm",
                         type="primary"):
                st.session_state.nl_summary_ok = True
                st.session_state.nl_step = 4
                st.rerun()
        with _a2:
            if st.button("✗ Cancel", key="nl_wizard_step3_cancel", type="secondary"):
                for _ck in [k for k in st.session_state if k.startswith("nl_")]:
                    del st.session_state[_ck]
                st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # STEP 4 — Deploy
    # ════════════════════════════════════════════════════════════════════════
    elif _nl_step == 4:
        st.markdown(
            '<p style="color:#4ade80;font-size:0.9rem;margin-bottom:0.8rem;">'
            '🚀 Building and launching your pipeline…</p>',
            unsafe_allow_html=True,
        )
        with st.spinner("Building pipeline configuration…"):
            try:
                from utils.nlp_parser import _build_from_answers
                _pc, _dc, _rc, _ic, _pid, _tk = _build_from_answers(
                    st.session_state.nl_answers,
                    st.session_state.nl_rules,
                    description=st.session_state.nl_description,
                )
            except Exception as _ex:
                st.error(f"Could not build pipeline config: {_ex}")
                if st.button("← Back to summary", key="nl_wizard_step4_back",
                             type="secondary"):
                    st.session_state.nl_step = 3
                    st.rerun()
                st.stop()

        _start_run(_pc, _dc, _rc, _ic, _pid, _tk)
        # Reset wizard so user can launch another pipeline later
        for _rk in [k for k in st.session_state if k.startswith("nl_")]:
            del st.session_state[_rk]
        st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # Advanced: Architecture Analysis (preserved below wizard)
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("<div style='margin-top:1.5rem;'></div>", unsafe_allow_html=True)
    with st.expander("🔍 Advanced: Architecture Analysis", expanded=False):
        st.markdown(
            '<p style="color:#94a3b8;margin-bottom:0.5rem;">'
            'Get a full cost comparison across all 3 clouds before deploying.</p>',
            unsafe_allow_html=True,
        )
        _adv_desc = st.text_area(
            "Description for analysis",
            value=st.session_state.get("nl_description", ""),
            placeholder="Describe your pipeline for architecture analysis…",
            height=80,
            label_visibility="collapsed",
            key="nl_adv_desc",
        )
        _adv_c1, _ = st.columns([1, 3])
        with _adv_c1:
            _analyze_btn = st.button("🔍 Analyze & Plan", type="primary",
                                     key="nl_wizard_analyze_btn",
                                     disabled=not _adv_desc.strip())
        if _analyze_btn and _adv_desc.strip():
            from utils.nlp_parser import check_pipeline_request
            _adv_ok, _adv_why = check_pipeline_request(_adv_desc.strip())
            if not _adv_ok:
                st.error("🚫 " + (_adv_why or "This doesn't look like a data-pipeline request."))
            else:
                with st.spinner("Analyzing requirements and costing all 3 clouds…"):
                    from utils.architecture_advisor import analyze
                    st.session_state.arch_report = analyze(_adv_desc)
                st.session_state.pop("arch_selected_cloud", None)

        _adv_report = st.session_state.get("arch_report")
        if _adv_report:
            _render_arch_report(_adv_report)

        _adv_cloud = st.session_state.get("arch_selected_cloud")
        if st.button(
            f"🚀 Deploy on {_adv_cloud.upper() if _adv_cloud else '…'} (arch pick)",
            key="nl_wizard_launch_arch",
            type="primary",
            disabled=not _adv_cloud,
        ) and _adv_cloud:
            if st.session_state.get("nl_answers"):
                with st.spinner(f"Building config for {_adv_cloud.upper()}…"):
                    from utils.nlp_parser import _build_from_answers
                    _apc, _adc, _arc, _aic, _apid, _atk = _build_from_answers(
                        st.session_state.nl_answers,
                        st.session_state.get("nl_rules", []),
                        description=st.session_state.get("nl_description", ""),
                        cloud_override=_adv_cloud,
                    )
                _start_run(_apc, _adc, _arc, _aic, _apid, _atk)
                st.rerun()
            else:
                st.warning("Complete the wizard steps first to set pipeline fields.")

# launch_nl is no longer a button — wizard handles deploy directly in step 4
launch_nl = False

with tab_existing:
    st.markdown(
        '<p style="color:#94a3b8; margin-bottom:0.5rem;">Run a pre-configured pipeline from the repo.</p>',
        unsafe_allow_html=True,
    )
    _PIPELINE_CLOUDS = {
        "eu_sales":         "aws",
        "us_crm":           "azure",
        "global_marketing": "gcp",
        "lakehouse_demo":   "databricks",
    }
    _CLOUD_LABELS = {
        "aws": "🟠 AWS", "azure": "🔵 Azure",
        "gcp": "🟢 GCP", "databricks": "⚡ Databricks",
    }
    existing_choice = st.selectbox("Select pipeline", list(_PIPELINE_CLOUDS.keys()))
    sel_cloud       = _PIPELINE_CLOUDS[existing_choice]
    st.markdown(
        f'<span style="color:#64748b;font-size:0.85rem;">Target: {_CLOUD_LABELS[sel_cloud]}</span>',
        unsafe_allow_html=True,
    )
    _render_cost_panel(selected_cloud=sel_cloud)
    with st.expander("⚙️ Business Rules", expanded=False):
        _render_rules_selector(key_prefix="ex")
    launch_existing = st.button("▶️ Run Pipeline", type="primary", key="launch_existing")

# ── Trino Federation tab ────────────────────────────────────────────────────
with tab_trino:
    from utils.trino_client import CROSS_CLOUD_QUERY, run_query

    st.markdown(
        '<p style="color:#94a3b8;margin-bottom:0.8rem;">'
        'Execute a single SQL query that joins data across <b style="color:#f97316;">AWS</b>, '
        '<b style="color:#38bdf8;">Azure</b>, and <b style="color:#4ade80;">GCP</b> simultaneously '
        'via Trino federation.</p>',
        unsafe_allow_html=True,
    )

    sql_input = st.text_area(
        "SQL",
        value=CROSS_CLOUD_QUERY,
        height=260,
        label_visibility="collapsed",
    )

    run_trino = st.button("▶️ Run Query", type="primary", key="run_trino")

    if run_trino:
        with st.spinner("Executing across catalogs…"):
            df, is_live = run_query(sql_input)

        mode_html = (
            '<span style="color:#4ade80;font-size:0.8rem;">● Live Trino connection</span>'
            if is_live else
            '<span style="color:#fbbf24;font-size:0.8rem;">⚡ Demo mode — set TRINO_HOST to connect a live cluster</span>'
        )
        st.markdown(mode_html, unsafe_allow_html=True)
        st.markdown(
            f'<p style="color:#64748b;font-size:0.82rem;margin:0.3rem 0 0.8rem;">'
            f'{len(df):,} rows returned</p>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )
        if not is_live:
            st.markdown(
                '<div style="background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.2);'
                'border-radius:10px;padding:0.7rem 1rem;margin-top:0.8rem;">'
                '<p style="margin:0;color:#94a3b8;font-size:0.82rem;">'
                '💡 To connect a real Trino cluster, add <code style="color:#7dd3fc;">TRINO_HOST=your-host</code> '
                'to your <code style="color:#7dd3fc;">.env</code>. '
                'The catalogs <code>hive</code>, <code>gcp_catalog</code>, <code>azure_catalog</code> '
                'are provisioned by the agent\'s Terraform output.</p>'
                '</div>',
                unsafe_allow_html=True,
            )

# ── Observability tab ───────────────────────────────────────────────────────
with tab_obs:
    from utils.observability import get_pipeline_summary, get_hourly_throughput, get_cloud_breakdown

    last_run_id = st.session_state.get("pipeline_meta", {}).get("pipeline_id", "eu-sales-pipeline")

    st.markdown(
        f'<p style="color:#94a3b8;font-size:0.85rem;margin-bottom:0.8rem;">'
        f'Monitoring: <code style="color:#38bdf8;">{last_run_id}</code>'
        f'{"" if st.session_state.get("pipeline_meta") else " &nbsp;·&nbsp; <em>launch a pipeline to see live metrics</em>"}'
        f'</p>',
        unsafe_allow_html=True,
    )

    summary = get_pipeline_summary(last_run_id)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Records Today",    f"{summary['records_today']:,}")
    m2.metric("Error Rate",       f"{summary['error_rate_pct']}%")
    m3.metric("Avg Latency",      f"{summary['avg_latency_ms']} ms")
    m4.metric("SLA",              f"{summary['sla_met_pct']}%")

    st.markdown(
        f'<p style="color:#334155;font-size:0.72rem;margin:0.2rem 0 1rem;">'
        f'Last run {summary["last_run_ago_min"]} min ago &nbsp;·&nbsp; '
        f'{summary["runs_today"]} runs today &nbsp;·&nbsp; '
        f'source: <em>{summary["source"]}</em></p>',
        unsafe_allow_html=True,
    )

    st.markdown("##### 📈 Hourly Throughput (last 24 h)")
    throughput_df = get_hourly_throughput(last_run_id)
    st.area_chart(
        throughput_df.set_index("time")[["records", "errors"]],
        color=["#38bdf8", "#f87171"],
        use_container_width=True,
        height=200,
    )

    st.markdown("##### ☁️ Per-Cloud Performance")
    cloud_df = get_cloud_breakdown(last_run_id)
    st.dataframe(
        cloud_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "cloud":      st.column_config.TextColumn("Cloud"),
            "avg_ms":     st.column_config.NumberColumn("Avg Latency (ms)", format="%d ms"),
            "p99_ms":     st.column_config.NumberColumn("p99 Latency (ms)", format="%d ms"),
            "error_rate": st.column_config.NumberColumn("Error Rate", format="%.3f%%"),
        },
    )

    if summary["source"] == "simulated":
        st.markdown(
            '<p style="color:#334155;font-size:0.72rem;margin-top:0.5rem;">'
            '* Simulated data — add <code>PROMETHEUS_URL</code> to .env to stream live metrics</p>',
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# LAUNCH HELPERS
# ---------------------------------------------------------------------------

def _start_run(pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task):
    lq, sq = queue.Queue(), queue.Queue()
    st.session_state.update({
        "log_q": lq, "state_q": sq,
        "pipeline_meta": {
            "pipeline_id": pipeline_id,
            "cloud": pipe_conf.get("cloud_provider", "?").upper(),
            "db":    db_conf.get("db_type", "?"),
            "rules": len(rules_conf.get("quality_standards", [])),
        },
        "written_files": [], "run_status": "running",
        "node_statuses":  {"supervisor": "pending", "architect": "pending",
                            "infra": "pending", "medic": "pending"},
        "agent_messages": {"supervisor": "", "architect": "", "infra": "", "medic": ""},
        "healing_cycles": 0,
        "run_start_time": time.time(),
    })
    t = threading.Thread(
        target=_run_agent,
        args=(pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task, lq, sq),
        daemon=True,
    )
    t.start()
    st.session_state.agent_thread = t


# launch_nl is always False — the wizard deploys directly inside tab_nl step 4.

if launch_existing:
    with st.spinner(f"Loading {existing_choice} config..."):
        try:
            from agents.constants import CONFIGS_DIR
            from utils.config_utils import load_pipeline_bundle
            from utils.prompt_utils import format_prompt
            from utils.file_utils import read_file

            pipeline_dir = os.path.join(CONFIGS_DIR, "pipelines")
            pipe_conf, db_conf, rules_conf, infra_conf = load_pipeline_bundle(
                os.getcwd(),
                os.path.join(pipeline_dir, f"{existing_choice}_pipeline.yaml"),
            )
            pipeline_id = pipe_conf.get("pipeline_id", existing_choice)
            ts  = datetime.datetime.now().strftime("%Y%m%d-%H%M")
            task = format_prompt(
                read_file(os.path.join(pipeline_dir, f"{existing_choice}_objective.md")),
                project_id=f"{existing_choice.upper()}-{ts}",
                infra_standards=infra_conf,
                **pipe_conf,
            )
        except Exception as e:
            st.error(f"Could not load pipeline config: {e}")
            st.stop()
    _custom_rules_ex = _get_active_rules("ex")
    if _custom_rules_ex:
        rules_conf = _custom_rules_ex
    _start_run(pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task)

# ---------------------------------------------------------------------------
# LIVE OUTPUT
# ---------------------------------------------------------------------------

if st.session_state.get("run_status") == "running":
    meta = st.session_state.pipeline_meta
    st.divider()

    # Metrics row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pipeline",  meta["pipeline_id"])
    c2.metric("Cloud",     _CLOUD_FLAGS.get(meta["cloud"].lower(), meta["cloud"]))
    c3.metric("Database",  meta["db"].upper())
    c4.metric("Rules",     meta["rules"])

    st.divider()

    log_q: queue.Queue    = st.session_state.log_q
    state_q: queue.Queue  = st.session_state.state_q
    thread: threading.Thread = st.session_state.agent_thread

    # ── Sidebar: Agent Pipeline section ──────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.markdown(
            '<p class="sb-title" style="margin:0 0 0.4rem;">🔄 Agent Pipeline</p>',
            unsafe_allow_html=True,
        )
        _sb_graph_ph = st.empty()

        st.markdown(
            '<p class="sb-title" style="margin:0.75rem 0 0.3rem;">📋 Agent Log</p>',
            unsafe_allow_html=True,
        )
        _sb_log_ph = st.empty()

    # ── Execution metrics row (dynamic, updated every poll cycle) ─────────
    _exec_cols = st.columns(4)
    _time_ph   = _exec_cols[0].empty()
    _cycles_ph = _exec_cols[1].empty()
    _files_ph  = _exec_cols[2].empty()
    _cloud_ph  = _exec_cols[3].empty()

    # Local copies of visualization state — kept in sync with session_state
    _node_statuses  = dict(st.session_state.get("node_statuses",
                           {k: "pending" for k, _ in _PIPELINE_NODES}))
    _agent_messages = dict(st.session_state.get("agent_messages",
                           {k: "" for k, _ in _PIPELINE_NODES}))
    _healing_cycles = st.session_state.get("healing_cycles", 0)
    _run_start_time = st.session_state.get("run_start_time", time.time())

    st.divider()

    with st.status("Agent is running…", expanded=True) as status_box:
        log_area   = st.empty()
        files_area = st.empty()

        log_lines: list[str] = []
        written_files: list[str] = []

        while thread.is_alive() or not log_q.empty() or not state_q.empty():
            while not log_q.empty():
                _, msg = log_q.get_nowait()
                log_lines.append(msg)
            while not state_q.empty():
                upd = state_q.get_nowait()
                if "written_files" in upd:
                    written_files = upd["written_files"]
                    st.session_state.written_files = written_files
                if upd.get("status") in ("DONE", "ERROR"):
                    st.session_state.final_status = upd
                # Node graph updates
                if "node_update" in upd:
                    nu = upd["node_update"]
                    completed = nu.get("completed", "")
                    next_active = nu.get("next_active")
                    if completed in _node_statuses:
                        _node_statuses[completed] = "completed"
                    if next_active and next_active in _node_statuses:
                        _node_statuses[next_active] = "active"
                    st.session_state.node_statuses = dict(_node_statuses)
                # Agent messages
                if "agent_message" in upd:
                    agent, content = upd["agent_message"]
                    if agent in _agent_messages:
                        _agent_messages[agent] = content
                        st.session_state.agent_messages = dict(_agent_messages)
                # Healing cycle counter
                if "healing_cycle" in upd:
                    _healing_cycles += upd["healing_cycle"]
                    st.session_state.healing_cycles = _healing_cycles

            # ── Refresh UI placeholders ───────────────────────────────────
            log_area.code("\n".join(log_lines[-80:]), language=None)
            if written_files:
                files_area.markdown(
                    "<p style='color:#64748b; font-size:0.85rem;'>📁 "
                    + " &nbsp;·&nbsp; ".join(f"<code>{f}</code>" for f in written_files)
                    + "</p>",
                    unsafe_allow_html=True,
                )

            # Sidebar graph + log
            _sb_graph_ph.markdown(
                _render_pipeline_graph(_node_statuses), unsafe_allow_html=True
            )
            _sb_log_ph.markdown(
                _render_agent_log_html(_agent_messages), unsafe_allow_html=True
            )

            # Execution metrics
            elapsed = int(time.time() - _run_start_time)
            _time_ph.metric("⏱️ Elapsed", f"{elapsed}s")
            _cycles_ph.metric("🔄 Healing Cycles", _healing_cycles)
            _files_ph.metric("📁 Files Generated", len(written_files))
            _cloud_ph.metric(
                "☁️ Cloud",
                _CLOUD_FLAGS.get(meta["cloud"].lower(), meta["cloud"]),
            )

            time.sleep(0.25)

        # Final drain
        while not log_q.empty():
            _, msg = log_q.get_nowait()
            log_lines.append(msg)
        while not state_q.empty():
            upd = state_q.get_nowait()
            if upd.get("status"):
                st.session_state.final_status = upd

        log_area.code("\n".join(log_lines), language=None)

        final = st.session_state.get("final_status", {})
        if final.get("status") == "DONE":
            status_box.update(label="✅ Deployment complete!", state="complete")
            st.session_state.run_status = "done"
            # Mark any still-pending/active nodes as completed on clean exit
            for k in _node_statuses:
                if _node_statuses[k] in ("active", "pending"):
                    _node_statuses[k] = "completed"
        elif final.get("status") == "ERROR":
            status_box.update(label="❌ Deployment failed", state="error")
            st.session_state.run_status = "error"
            # Mark the currently-active node as failed
            for k in _node_statuses:
                if _node_statuses[k] == "active":
                    _node_statuses[k] = "failed"

        # Final refresh of sidebar and execution metrics
        _sb_graph_ph.markdown(
            _render_pipeline_graph(_node_statuses), unsafe_allow_html=True
        )
        _sb_log_ph.markdown(
            _render_agent_log_html(_agent_messages), unsafe_allow_html=True
        )
        elapsed = int(time.time() - _run_start_time)
        _time_ph.metric("⏱️ Elapsed", f"{elapsed}s")
        _cycles_ph.metric("🔄 Healing Cycles", _healing_cycles)
        _files_ph.metric("📁 Files Generated", len(written_files))
        _cloud_ph.metric(
            "☁️ Cloud",
            _CLOUD_FLAGS.get(meta["cloud"].lower(), meta["cloud"]),
        )

    # Post-run
    if st.session_state.run_status == "done":
        st.markdown("""
        <div style="
            background: rgba(74,222,128,0.08);
            border: 1px solid rgba(74,222,128,0.3);
            border-radius: 14px;
            padding: 1.2rem 1.5rem;
            margin-top: 1rem;
        ">
            <p style="margin:0; color:#4ade80; font-weight:700; font-size:1.05rem;">
                🎉 Pipeline deployed successfully!
            </p>
        </div>
        """, unsafe_allow_html=True)

        if st.session_state.written_files:
            st.markdown("### 📁 Generated Files")
            for f in st.session_state.written_files:
                st.markdown(f"- `{f}`")

    elif st.session_state.run_status == "error":
        err = st.session_state.get("final_status", {}).get("error", "See log above.")
        st.error(f"Deployment failed: {err}")

    st.divider()
    if st.button("🔄 Run another pipeline", type="secondary"):
        for key in ["run_status", "log_q", "state_q", "agent_thread",
                    "pipeline_meta", "written_files", "final_status"]:
            st.session_state.pop(key, None)
        st.rerun()
