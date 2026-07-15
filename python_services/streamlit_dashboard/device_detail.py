# streamlit_dashboard/device_detail.py
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone

from queries import (
    get_device_telemetry,
    get_device_alert_history,
    get_device_status,
)
from config import SEVERITY_COLOURS, STATUS_COLOURS, STATUS_ICONS


def render(device_id: str, on_back):
    """
    Per-device detail page.
    Shows metric charts over time and alert history for one device.
    """

    # ── Back button ───────────────────────────────────────────────────────────
    if st.button("← Back to Fleet Overview"):
        on_back()
        return

    st.title(f"📟 Device Detail — {device_id}")

    # ── Device status header ──────────────────────────────────────────────────
    status_row = get_device_status(device_id)

    if status_row:
        status    = status_row["status"]
        icon      = STATUS_ICONS.get(status, "❓")
        colour    = STATUS_COLOURS.get(status, "#ffffff")
        last_seen = status_row["last_seen"]

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                f"**Status:** <span style='color:{colour}'>"
                f"{icon} {status.upper()}</span>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(f"**Type:** `{status_row['device_type']}`")
        with c3:
            st.markdown(
                f"**Last seen:** "
                f"`{_fmt_time(last_seen)}`"
            )
    else:
        st.warning(f"No status record found for {device_id}")

    st.divider()

    # ── Time window selector ──────────────────────────────────────────────────
    minutes = st.select_slider(
        "Time window",
        options=[5, 10, 15, 30, 60],
        value=30,
        format_func=lambda x: f"Last {x} minutes",
    )

    # ── Telemetry charts ──────────────────────────────────────────────────────
    st.subheader("📈 Metric History")

    telemetry = get_device_telemetry(device_id, minutes=minutes)

    if not telemetry:
        st.info(f"No telemetry data in the last {minutes} minutes.")
    else:
        df = pd.DataFrame(telemetry)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        # get metric columns (everything except timestamp)
        metric_cols = [c for c in df.columns if c != "timestamp"]

        for metric in metric_cols:
            if metric not in df.columns:
                continue

            fig = go.Figure()

            fig.add_trace(go.Scatter(
                x=df["timestamp"],
                y=df[metric],
                mode="lines",
                name=metric,
                line=dict(color="#3498db", width=1.5),
            ))

            # overlay alert markers if any
            alert_history = get_device_alert_history(device_id, limit=200)
            alert_df      = pd.DataFrame(alert_history)

            if not alert_df.empty and "metric" in alert_df.columns:
                metric_alerts = alert_df[alert_df["metric"] == metric]
                if not metric_alerts.empty:
                    metric_alerts = metric_alerts.copy()
                    metric_alerts["timestamp"] = pd.to_datetime(
                        metric_alerts["timestamp"], utc=True
                    )
                    # only show alerts within the selected window
                    metric_alerts = metric_alerts[
                        metric_alerts["timestamp"] >= df["timestamp"].min()
                    ]

                    if not metric_alerts.empty:
                        colours = metric_alerts["severity"].map(
                            SEVERITY_COLOURS
                        ).fillna("#ffffff")

                        fig.add_trace(go.Scatter(
                            x=metric_alerts["timestamp"],
                            y=metric_alerts["value"],
                            mode="markers",
                            name="Alert",
                            marker=dict(
                                color=colours,
                                size=10,
                                symbol="x",
                            ),
                        ))

            fig.update_layout(
                title=metric,
                xaxis_title="Time",
                yaxis_title=metric,
                height=280,
                margin=dict(l=0, r=0, t=40, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(gridcolor="#2d2d2d"),
                yaxis=dict(gridcolor="#2d2d2d"),
                font=dict(color="#ffffff"),
            )

            st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ── Alert history ─────────────────────────────────────────────────────────
    st.subheader("🚨 Alert History")

    alert_history = get_device_alert_history(device_id)

    if not alert_history:
        st.success("No alerts recorded for this device.")
    else:
        for alert in alert_history:
            severity = alert["severity"]
            colour   = SEVERITY_COLOURS.get(severity, "#ffffff")

            with st.container(border=True):
                c1, c2 = st.columns([3, 1])

                with c1:
                    st.markdown(
                        f"<span style='color:{colour};font-weight:bold'>"
                        f"[{severity}]</span> **{alert['metric']}** "
                        f"= `{alert['value']:.2f}`",
                        unsafe_allow_html=True,
                    )
                    st.caption(alert["message"])

                with c2:
                    st.caption(f"`{alert['detection_layer']}`")
                    st.caption(_fmt_time(alert["timestamp"]))


def _fmt_time(ts) -> str:
    if ts is None:
        return "—"
    if hasattr(ts, "strftime"):
        return ts.strftime("%H:%M:%S")
    return str(ts)