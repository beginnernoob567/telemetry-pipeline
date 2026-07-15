# streamlit_dashboard/dashboard.py
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from queries import (
    get_fleet_status,
    get_fleet_summary,
    get_current_alerts,
    get_alert_counts,
    get_recent_failures,
    get_failure_count,
)
from config import SEVERITY_COLOURS, STATUS_COLOURS, STATUS_ICONS


def render(on_device_select):
    """
    Main fleet dashboard page.
    `on_device_select` is a callback that switches to the
    device detail page when a device row is clicked.
    """

    st.title("🏭 Telemetry Pipeline — Fleet Overview")
    st.caption(f"Last refreshed: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

    # ── Summary metric cards ──────────────────────────────────────────────────
    summary       = get_fleet_summary()
    alert_counts  = get_alert_counts()
    failure_count = get_failure_count()

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("✅ Healthy",      summary.get("healthy",      0))
    with col2:
        st.metric("🔴 Unhealthy",    summary.get("unhealthy",    0))
    with col3:
        st.metric("⚫ Disconnected", summary.get("disconnected", 0))
    with col4:
        total_alerts = sum(alert_counts.values())
        st.metric("🚨 Active Alerts", total_alerts)
    with col5:
        st.metric("⚠️ Failures",     failure_count)

    st.divider()

    # ── Fleet status table ────────────────────────────────────────────────────
    st.subheader("Fleet Status")

    fleet = get_fleet_status()

    if not fleet:
        st.info("No devices registered yet. Waiting for telemetry...")
        return

    for device in fleet:
        status     = device["status"]
        icon       = STATUS_ICONS.get(status, "❓")
        colour     = STATUS_COLOURS.get(status, "#ffffff")
        since      = device["since"]
        last_seen  = device["last_seen"]
        device_id  = device["device_id"]

        # calculate how long in current status
        now        = datetime.now(timezone.utc)
        if since and hasattr(since, 'replace'):
            delta  = now - since.replace(tzinfo=timezone.utc) \
                     if since.tzinfo is None else now - since
            since_str = _format_duration(delta.total_seconds())
        else:
            since_str = "—"

        col_icon, col_id, col_type, col_status, col_since, col_btn = \
            st.columns([0.5, 2, 1, 1.2, 1.5, 1])

        with col_icon:
            st.markdown(f"<span style='font-size:20px'>{icon}</span>",
                        unsafe_allow_html=True)
        with col_id:
            st.markdown(f"**{device_id}**")
        with col_type:
            st.caption(device["device_type"])
        with col_status:
            st.markdown(
                f"<span style='color:{colour};font-weight:bold'>"
                f"{status.upper()}</span>",
                unsafe_allow_html=True,
            )
        with col_since:
            st.caption(f"{since_str}")
        with col_btn:
            if st.button("Details →", key=f"btn_{device_id}"):
                on_device_select(device_id)

    st.divider()

    # ── Active alerts panel ───────────────────────────────────────────────────
    st.subheader("🚨 Active Alerts")

    alerts = get_current_alerts()

    if not alerts:
        st.success("No active alerts — fleet is operating normally.")
    else:
        for alert in alerts:
            severity = alert["severity"]
            colour   = SEVERITY_COLOURS.get(severity, "#ffffff")

            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 1, 1])

                with c1:
                    st.markdown(
                        f"<span style='color:{colour};font-weight:bold'>"
                        f"[{severity}]</span> **{alert['device_id']}** "
                        f"— {alert['metric']}",
                        unsafe_allow_html=True,
                    )
                    st.caption(alert["message"])

                with c2:
                    st.caption(f"Layer: `{alert['detection_layer']}`")
                    st.caption(f"Value: `{alert['value']:.2f}`")

                with c3:
                    if alert["first_seen"]:
                        st.caption(f"Since: {_fmt_time(alert['first_seen'])}")
                    if alert["last_updated"]:
                        st.caption(f"Last: {_fmt_time(alert['last_updated'])}")

    st.divider()

    # ── Pipeline failures ─────────────────────────────────────────────────────
    st.subheader("⚠️ Pipeline Failures (DLQ)")

    failures = get_recent_failures()

    if not failures:
        st.success("No pipeline failures.")
    else:
        for f in failures:
            with st.expander(
                f"❌ {f['error_reason']} — {_fmt_time(f['received_at'])}",
                expanded=False,
            ):
                st.code(f["raw_payload"][:500], language="json")
                st.caption(
                    f"Resolved: {'✅' if f['resolved'] else '❌'}"
                )


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


def _fmt_time(ts) -> str:
    if ts is None:
        return "—"
    if hasattr(ts, "strftime"):
        return ts.strftime("%H:%M:%S")
    return