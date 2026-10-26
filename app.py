"""Morning Revenue Brief - Step 1: KPI cards for one "today" date.

Run with:  streamlit run app.py
"""

import altair as alt
import pandas as pd
import streamlit as st

import kpis
from brief import claude_brief, get_api_key, rule_based_brief
from forward import LAST_YEAR_SHIFT, forward_summary, forward_table

st.set_page_config(page_title="Morning Revenue Brief", layout="wide")


# ---------------------------------------------------------------------------
# 1. DATA + KPI MATH live in kpis.py (shared with the daily email).
#    Here we only add Streamlit's cache so they aren't recomputed on every click.
# ---------------------------------------------------------------------------
MAX_ADR = kpis.MAX_ADR
load_data = st.cache_data(kpis.load_data)               # only runs once, then Streamlit remembers it
daily_stay_table = st.cache_data(kpis.daily_stay_table)
kpis_for_day = kpis.kpis_for_day


def money(v):
    return f"€{v:,.2f}"


def signed_money(v):
    """A change in money, with the sign before the currency: +€1.72 or −€1.05."""
    return f"{'+' if v >= 0 else '−'}€{abs(v):,.2f}"


def card(col, label, value, last_week, fmt, lower_is_better=False, fmt_delta=None):
    """Draw one KPI card with a 'vs same day last week' arrow."""
    delta = value - last_week
    col.metric(
        label,
        fmt(value),
        delta=(fmt_delta or fmt)(delta) if delta else None,
        # Say the arrow and colour ourselves: Streamlit only recognises a plain
        # "-" as negative, not the proper minus sign "−", so it would colour
        # "−€1.05" green. Up is good, except for "bad when up" KPIs.
        delta_arrow="up" if delta > 0 else "down",
        delta_color="green" if (delta > 0) != lower_is_better else "red",
    )


# ---------------------------------------------------------------------------
# 3. THE PAGE
# ---------------------------------------------------------------------------
st.title("Morning Revenue Brief")

data, removed = load_data()

# --- Sidebar: choose hotel and "today" ---
hotel = st.sidebar.selectbox("Hotel", sorted(data["hotel"].unique()))
hotel_df = data[data["hotel"] == hotel]
stay_table = daily_stay_table(hotel_df)

# The data has no room count, so we assume capacity = the busiest night in the
# file (that night must have been ~full). The GM can overwrite it.
default_capacity = kpis.default_capacity(stay_table)
capacity = st.sidebar.number_input(
    "Rooms available (assumed)", min_value=1, value=default_capacity,
    help="Not in the data. Default = most rooms occupied on any night.",
)

# Only offer dates that have a full week of history behind them.
first_day = hotel_df["arrival_date"].min() + pd.Timedelta(days=7)
last_day = hotel_df["arrival_date"].max()
today = pd.Timestamp(st.sidebar.date_input(
    "Today", value=pd.Timestamp("2017-07-15"), min_value=first_day, max_value=last_day,
))
last_week = today - pd.Timedelta(days=7)

st.caption(f"{hotel} · {today:%A, %d %B %Y} · compared with {last_week:%A, %d %B %Y}")

now = kpis_for_day(today, hotel_df, stay_table, capacity)
before = kpis_for_day(last_week, hotel_df, stay_table, capacity)

# --- Looking ahead: rooms on the books for the next 30 nights (see forward.py) ---
@st.cache_data(show_spinner="Counting rooms on the books...")
def forward_for(_hotel_df, hotel, day, capacity):
    # _hotel_df starts with an underscore so Streamlit doesn't hash 80k rows;
    # hotel + day identify it instead.
    table = forward_table(_hotel_df, day)
    return table, forward_summary(table, capacity)


fwd_table, fwd = forward_for(hotel_df, hotel, today, capacity)

# --- Morning Brief: 5 plain-English lines at the top ---
@st.cache_data(show_spinner="Asking Claude...")
def ai_brief(now, before, hotel, day, api_key, fwd):
    # Cached so changing nothing doesn't re-bill the API on every page refresh.
    return claude_brief(now, before, hotel, day, api_key, fwd)


def secrets_or_none():
    try:
        return st.secrets
    except Exception:  # no secrets file - that's fine
        return None


api_key = get_api_key(secrets_or_none())
use_claude = st.sidebar.checkbox(
    "Write the brief with Claude", value=False, disabled=not api_key,
    help="Needs ANTHROPIC_API_KEY (environment variable or .streamlit/secrets.toml)."
    if not api_key else "Uses your API key; each new date/hotel is one API call.",
)

lines, source = None, "Rule-based"
if use_claude:
    try:
        lines, source = ai_brief(now, before, hotel, today, api_key, fwd), "Written by Claude"
    except Exception as err:  # bad key, network, refusal, odd output...
        st.warning(f"Claude brief unavailable ({type(err).__name__}); showing the rule-based brief.")
if lines is None:
    lines = rule_based_brief(now, before, hotel, today, fwd)

st.subheader("Morning Brief")
for line in lines:
    st.markdown(f"- {line}")
st.caption(source)
st.divider()

# --- Row 1: how full and how much money ---
c1, c2, c3 = st.columns(3)
card(c1, "Occupancy", now["occupancy"], before["occupancy"], lambda v: f"{v:.1%}")
card(c2, "ADR", now["adr"], before["adr"], money, fmt_delta=signed_money)
card(c3, "RevPAR", now["revpar"], before["revpar"], money, fmt_delta=signed_money)

# --- Row 2: booking activity, counted in bookings ---
c4, c5, _ = st.columns(3)
card(c4, "New bookings", now["new_bookings"], before["new_bookings"], lambda v: f"{v:,.0f}")
card(c5, "Cancellations", now["cancellations"], before["cancellations"], lambda v: f"{v:,.0f}",
     lower_is_better=True)

# --- Row 3: the same activity in room nights, plus pickup ---
c6, c7, c8 = st.columns(3)
card(c6, "New room nights", now["new_room_nights"], before["new_room_nights"], lambda v: f"{v:,.0f}")
card(c7, "Cancelled room nights", now["cancelled_room_nights"], before["cancelled_room_nights"],
     lambda v: f"{v:,.0f}", lower_is_better=True)
card(c8, "Pickup (net room nights)", now["pickup"], before["pickup"], lambda v: f"{v:,.0f}")

st.caption("Arrows show change versus the same weekday last week. "
           "Pickup = new room nights - cancelled room nights.")

# --- Looking ahead: rooms on the books vs the same nights last year ---
st.divider()
st.subheader("Looking ahead: rooms on the books")
if fwd is None:
    st.info("The data has no bookings after this date, so there is nothing to look ahead at.")
else:
    def ahead_card(col, label, w):
        """Rooms on the books for a window, with the gap versus last year."""
        if w["diff"] is None:
            col.metric(label, f"{w['on_books']:,}", help="No last-year data for these dates.")
        else:
            pct = f" ({w['pct']:+.1%})" if w["pct"] is not None else ""
            col.metric(label, f"{w['on_books']:,}", delta=f"{w['diff']:+,}{pct} vs last year")

    a1, a2 = st.columns(2)
    ahead_card(a1, f"Next {fwd['next7']['days']} days (room nights)", fwd["next7"])
    ahead_card(a2, f"Next {fwd['next30']['days']} days (room nights)", fwd["next30"])

    weak = fwd["weakest"]
    weak_night = pd.Timestamp(weak["date"])
    if weak["diff"] is None:
        st.warning(f"Emptiest upcoming night: {weak_night:%a %d %b} with {weak['on_books']} rooms "
                   f"on the books ({weak['occupancy']:.0%}). No last-year figure to compare.")
    else:
        # Only a real warning when the night is actually behind last year.
        show = st.warning if weak["diff"] < 0 else st.info
        show(f"Weakest upcoming night: {weak_night:%a %d %b} with {weak['on_books']} rooms on the "
             f"books ({weak['occupancy']:.0%}), {abs(weak['diff'])} "
             f"{'behind' if weak['diff'] < 0 else 'ahead of'} the {weak['last_year']} "
             f"booked at this point last year.")

    # Long format (one row per night per year) so Altair can draw two lines.
    chart_data = (
        fwd_table[["on_books", "last_year"]]
        .rename(columns={"on_books": "This year", "last_year": "Same night last year"})
        .rename_axis("Night").reset_index()
        .melt(id_vars="Night", var_name="Year", value_name="Room nights")
        .dropna()
    )
    chart = alt.Chart(chart_data).mark_line(point=True).encode(
        # Dates in order, one label every 3 days, tilted so they never overlap.
        x=alt.X("Night:T", title=None, sort="ascending",
                axis=alt.Axis(format="%a %d %b", labelAngle=-45, tickCount={"interval": "day", "step": 3})),
        y=alt.Y("Room nights:Q", title="Room nights", scale=alt.Scale(zero=False)),
        color=alt.Color("Year:N", title=None, legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("Night:T", format="%a %d %b"), "Year:N", "Room nights:Q"],
    )
    st.altair_chart(chart, width="stretch")
    with st.expander("Night-by-night table"):
        st.dataframe(
            fwd_table.rename(columns={"on_books": "On the books", "last_year": "Last year", "diff": "Difference"})
            .rename_axis("Night").style.format({"Last year": "{:.0f}", "Difference": "{:+.0f}"}, na_rep="n/a"),
            width="stretch",
        )
    # Last year's data is thin in 2015 (the file starts in July 2015).
    if (fwd_table.index - LAST_YEAR_SHIFT).year.min() <= 2015:
        st.caption("Last year's data is a smaller sample, so year-over-year gaps look larger than they are.")
    st.caption("Rooms on the books = bookings made on or before today and not cancelled by today. "
               "Last year = the same weekday 364 days earlier, counted as of that day's morning, "
               "so both years have the same lead time. The weakest night is the one furthest behind last year.")

# --- Data-quality note ---
st.sidebar.markdown("---")
st.sidebar.caption(
    f"Rows removed everywhere: {removed['adr_outlier']:,} with ADR > {MAX_ADR:,} (data error). "
    f"{removed['adr_zero_or_less_kept']:,} rows with ADR <= 0 are kept in bookings, "
    f"cancellations and occupancy, but left out of ADR, revenue and RevPAR. "
    f"No-shows are excluded from occupancy, ADR and RevPAR."
)
