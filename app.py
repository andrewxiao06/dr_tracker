"""Provider view: what a GI provider needs in the 30 seconds before walking into the room.

    streamlit run app.py

Reads only through named SQL queries (queries.sql). Flags are simple, deterministic
rules shown for clinician review; they are not diagnoses and no LLM is involved here.
"""
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import db

st.set_page_config(page_title="IBD Taper View", page_icon="🩺", layout="wide")

RED, BLUE, GREY = "#d64545", "#2f6fb5", "#8a8f98"
LABELS = {"stool": "Stools", "blood_in_stool": "Blood", "urgency": "Urgency", "nocturnal": "Woke at night",
          "cramping": "Cramping", "mucus": "Mucus", "trouble_sleeping": "Trouble sleeping",
          "increased_appetite": "Increased appetite", "mood_change": "Mood change", "missed_dose": "Missed dose"}

# ---------- Patient picker ----------
patients = db.query("patients")
with st.sidebar:
    st.markdown("### Dr.Tracker · Clinic")
    pid = st.radio("Patient", patients["id"], format_func=lambda i: patients.set_index("id").loc[i, "name"])
    st.caption("Synthetic, fictional patients only.")

p = patients.set_index("id").loc[pid]
weekly = db.query("weekly_summary", patient_id=pid)
daily = db.query("daily_trend", patient_id=pid)
changes = db.query("dose_change_impact", patient_id=pid)
doses = db.query("dose_schedule", patient_id=pid)
events = db.query("report_events", patient_id=pid)

last, prev = weekly.iloc[-1], weekly.iloc[-2] if len(weekly) > 1 else weekly.iloc[-1]

# ---------- Header ----------
st.title(p["name"])
st.markdown(f"**{p['diagnosis']}** · Prednisone taper 40 mg → 0 over 8 weeks · "
            f"**Week {int(last.week)} · currently {last.prednisone_mg:g} mg** · "
            f"{len(daily)} check-ins")

# ---------- Flag: did symptoms worsen after the latest dose step? ----------
# Rule (tunable per clinic): in the 7 days after a step vs. the 7 before,
# stools/day up >=50% OR at least 2 more days with blood. Thresholds avoid flagging day-to-day noise.
if not changes.empty:
    worse = changes[(changes.stools_after >= 1.5 * changes.stools_before)
                    | (changes.blood_days_after - changes.blood_days_before >= 2)]
    if not worse.empty:
        c = worse.iloc[-1]
        st.error(f"**For review:** symptoms rose after the {c.from_mg:g} → {c.to_mg:g} mg step on {c.change_date}: "
                 f"stools {c.stools_before:g} → {c.stools_after:g}/day, blood days {int(c.blood_days_before)} → "
                 f"{int(c.blood_days_after)} (7 days before vs. after).")
    else:
        st.success("No worsening flagged after any dose step so far.")
st.caption("Flags are simple rules to prompt clinician review, not a diagnosis.")

# ---------- This week vs last week ----------
def delta(cur, before, fmt="{:+.1f}"):
    """Delta text for st.metric; None if not computable. Zero change is shown as neutral text."""
    if pd.isna(cur) or pd.isna(before):
        return None
    d = cur - before
    return "no change vs last week" if abs(d) < 0.05 else fmt.format(d) + " vs last week"


def color(d):  # lower is better for all three metrics; grey when unchanged
    return "off" if d is None or d.startswith("no change") else "inverse"


d1 = delta(last.avg_stools_per_day, prev.avg_stools_per_day)
d2 = delta(last.blood_days, prev.blood_days, "{:+.0f}")
d3 = delta(last.avg_bristol, prev.avg_bristol)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Stools / day", f"{last.avg_stools_per_day:g}" if pd.notna(last.avg_stools_per_day) else "—",
          d1, delta_color=color(d1))
m2.metric("Days with blood", f"{int(last.blood_days)} of {int(last.check_ins)} check-ins",
          d2, delta_color=color(d2))
m3.metric("Stool form (Bristol 1–7)", f"{last.avg_bristol:g}" if pd.notna(last.avg_bristol) else "not described",
          d3, delta_color=color(d3), help="Typical range is 3–4. Higher = looser.")
n_review = int(events.needs_review.fillna(0).sum())
m4.metric("Items to double-check", n_review, help="Hedged, contradictory, or unverified extractions")

# ---------- Main chart ----------
st.subheader("Symptom trend across the taper")
daily["day"] = pd.to_datetime(daily["day"])
st.caption("Shaded columns = prednisone dose that week. Red dots = days the patient reported blood.")
fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.62, 0.38])

# Dose steps as shaded weekly bands labeled with the dose (data range only)
pred = doses[doses.drug == "prednisone"]
lo, hi = daily.day.min(), daily.day.max()
for i, (_, d) in enumerate(pred.iterrows()):
    x0, x1 = pd.to_datetime(d.start_date), pd.to_datetime(d.end_date) + pd.Timedelta(days=1)
    if x1 < lo or x0 > hi + pd.Timedelta(days=1):
        continue
    if i % 2 == 0:
        fig.add_shape(type="rect", xref="x", yref="paper", x0=x0, x1=x1, y0=0, y1=1,
                      fillcolor="rgba(47,111,181,0.08)", line_width=0, layer="below")
    fig.add_annotation(x=x0 + (x1 - x0) / 2, y=1.0, xref="x", yref="paper", text=f"<b>{d.dose_mg:g} mg</b>",
                       showarrow=False, yanchor="bottom", font=dict(size=12, color=BLUE))

fig.add_shape(type="rect", xref="x2 domain", yref="y2", x0=0, x1=1, y0=3, y1=4,
              fillcolor="rgba(46,160,67,0.2)", line_width=0, layer="below")
fig.add_trace(go.Scatter(x=daily.day, y=daily.stools, mode="lines+markers", name="Stools/day",
                         line=dict(color=BLUE, width=2.5), connectgaps=True), row=1, col=1)
blood = daily[daily.blood == 1]
fig.add_trace(go.Scatter(x=blood.day, y=blood.stools.fillna(0), mode="markers", name="Blood reported",
                         marker=dict(color=RED, size=13, line=dict(color="white", width=1.5))), row=1, col=1)
fig.add_trace(go.Scatter(x=daily.day, y=daily.bristol, mode="lines+markers", name="Bristol type (green = typical 3–4)",
                         line=dict(color="#555", width=2), connectgaps=True), row=2, col=1)
fig.update_yaxes(title_text="Stools per day", rangemode="tozero", gridcolor="#eee", row=1, col=1)
fig.update_yaxes(title_text="Stool form<br>(Bristol 1–7)", range=[0.5, 7.5], dtick=2, gridcolor="#eee", row=2, col=1)
fig.update_layout(height=520, margin=dict(t=40, b=10, l=80, r=10), hovermode="x unified",
                  legend=dict(orientation="h", y=-0.1), plot_bgcolor="white", paper_bgcolor="white")
fig.update_xaxes(showgrid=False)
st.plotly_chart(fig, use_container_width=True, theme=None)

# ---------- Symptoms at each dose step ----------
st.subheader("Symptoms at each dose step")
table = weekly.rename(columns={
    "week": "Week", "prednisone_mg": "Prednisone (mg)", "check_ins": "Check-ins",
    "avg_stools_per_day": "Stools/day", "avg_bristol": "Bristol avg", "blood_days": "Blood days",
    "urgency_days": "Urgency days", "night_waking_days": "Night waking days", "items_to_review": "To review"})
st.dataframe(table, hide_index=True, use_container_width=True)

# ---------- Traceability: patient words -> extracted data ----------
def describe(e) -> str:
    name = LABELS.get(e.symptom, e.symptom)
    if not e.present:
        return f"no {name.lower()}"
    bits = [name]
    if pd.notna(e["count"]):
        bits.append(f"{int(e['count'])}×")
    if pd.notna(e.bristol_type):
        bits.append(f"Bristol {int(e.bristol_type)}")
    if pd.notna(e.severity):
        bits.append(["none", "mild", "moderate", "severe"][int(e.severity)])
    return " · ".join(bits)


review = events[events.needs_review == 1]
with st.expander(f"⚠️ Needs a second look ({len(review)})", expanded=False):
    if review.empty:
        st.write("Nothing flagged.")
    for _, e in review.iterrows():
        st.markdown(f"**{e.reported_at[:10]}** — {describe(e)}  \n"
                    f"> “{e.evidence}”  \n_Why flagged: {e.review_reason}_")

with st.expander("Patient check-ins and what was extracted"):
    for rid, grp in events.groupby("report_id", sort=False):
        first = grp.iloc[0]
        chips = "  ".join(f"`{'⚠️ ' if e.needs_review else ''}{describe(e)}`"
                          for _, e in grp.iterrows() if pd.notna(e.symptom))
        st.markdown(f"**{first.reported_at[:16].replace('T', ' ')}** — “{first.raw_text}”  \n{chips or '_nothing extracted_'}")

eval_path = Path(__file__).parent / "data" / "eval.json"
if eval_path.exists():
    with st.expander("How the parser is checked"):
        st.markdown("Patient text is parsed by an LLM into the fixed schema, then checked by code: every value "
                    "must be backed by an exact quote from the patient, hedged or contradictory statements are "
                    "flagged, and results are scored against hand-known answers for the synthetic data.")
        st.json(json.loads(eval_path.read_text()))
