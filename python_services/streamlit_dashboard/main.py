# streamlit_dashboard/main.py
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import DASHBOARD_REFRESH_SECONDS
from dashboard import render as render_dashboard
from device_detail import render as render_device_detail

st.set_page_config(
    page_title="Telemetry Pipeline",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
st_autorefresh(
    interval=DASHBOARD_REFRESH_SECONDS * 1000,  # milliseconds
    key="autorefresh",
)

# ── Page routing via session state ────────────────────────────────────────────
if "page" not in st.session_state:
    st.session_state.page      = "fleet"
    st.session_state.device_id = None


def go_to_device(device_id: str):
    st.session_state.page      = "device"
    st.session_state.device_id = device_id
    st.rerun()


def go_to_fleet():
    st.session_state.page      = "fleet"
    st.session_state.device_id = None
    st.rerun()


# ── Render ────────────────────────────────────────────────────────────────────
if st.session_state.page == "fleet":
    render_dashboard(on_device_select=go_to_device)

elif st.session_state.page == "device":
    render_device_detail(
        device_id=st.session_state.device_id,
        on_back=go_to_fleet,
    )