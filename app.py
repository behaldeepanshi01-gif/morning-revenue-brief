"""Morning Revenue Brief - Step 1: KPI cards for one "today" date.

Run with:  streamlit run app.py
"""

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Morning Revenue Brief", layout="wide")


# ---------------------------------------------------------------------------
# 1. LOAD + PREPARE THE DATA
# ---------------------------------------------------------------------------
MAX_ADR = 1000  # ADR above this is a data error and removed everywhere


@st.cache_data  # only runs once, then Streamlit remembers the result
def load_data():
    df = pd.read_csv("hotel_bookings.csv.gz")

    # Data cleaning: ADR above MAX_ADR is a data error (the file has a 5,400
    # entry), so those rows are removed everywhere. We keep a count so the
    # page can tell the GM what was removed.
    # Rows with ADR of 0 or less are KEPT here (they are real bookings and
    # still count as bookings / cancellations / occupied rooms); they are only
    # left out of the revenue figures further down.
    bad_high = df["adr"] > MAX_ADR
    removed = {"adr_outlier": int(bad_high.sum())}
    df = df[~bad_high].copy()
    removed["adr_zero_or_less_kept"] = int((df["adr"] <= 0).sum())

    # The file stores the arrival date as year / month name / day. Combine them.
    df["arrival_date"] = pd.to_datetime(
        df["arrival_date_year"].astype(str)
        + "-" + df["arrival_date_month"]
        + "-" + df["arrival_date_day_of_month"].astype(str),
        format="%Y-%B-%d",
    )

    # Nights stayed -> departure date (the guest is gone on this morning).
    df["nights"] = df["stays_in_weekend_nights"] + df["stays_in_week_nights"]
    df["departure_date"] = df["arrival_date"] + pd.to_timedelta(df["nights"], unit="D")

    # The file has no "booked on" column, but lead_time = days between
    # booking and arrival, so we can work it out.
    df["booking_date"] = df["arrival_date"] - pd.to_timedelta(df["lead_time"], unit="D")

    # For cancelled bookings, reservation_status_date is the cancellation date.
    df["cancel_date"] = pd.to_datetime(df["reservation_status_date"]).where(
        df["is_canceled"] == 1
    )
    return df, removed


@st.cache_data
def daily_stay_table(df: pd.DataFrame) -> pd.DataFrame:
    """For each calendar day: rooms occupied, paying rooms and room revenue.

    A booking that is not cancelled occupies a room every night from arrival
    up to (not including) departure. Instead of looping over every night of
    every booking, we add +1 on arrival day and -1 on departure day, then take
    a running total. The running total is the number of occupied rooms.
    """
    # No-shows never occupied a room, so they are excluded here (and therefore
    # from occupancy, ADR and RevPAR).
    stays = df[
        (df["is_canceled"] == 0)
        & (df["reservation_status"] != "No-Show")
        & (df["nights"] > 0)
    ]

    # Only stays with ADR > 0 count towards revenue and ADR. Zero/negative ADR
    # stays still occupy a room (rooms_in / rooms_out above) but earn nothing.
    paid = stays[stays["adr"] > 0]

    rooms_in = stays.groupby("arrival_date").size()
    rooms_out = stays.groupby("departure_date").size()
    paid_in = paid.groupby("arrival_date").size()
    paid_out = paid.groupby("departure_date").size()
    rev_in = paid.groupby("arrival_date")["adr"].sum()   # adr = revenue per night
    rev_out = paid.groupby("departure_date")["adr"].sum()

    days = pd.date_range(stays["arrival_date"].min(), stays["departure_date"].max())
    table = pd.DataFrame(index=days)
    table["rooms_occupied"] = rooms_in.sub(rooms_out, fill_value=0).reindex(days, fill_value=0).cumsum()
    table["paying_rooms"] = paid_in.sub(paid_out, fill_value=0).reindex(days, fill_value=0).cumsum()
    table["revenue"] = rev_in.sub(rev_out, fill_value=0).reindex(days, fill_value=0).cumsum()
    return table


# ---------------------------------------------------------------------------
# 2. KPI MATH FOR ONE DAY
# ---------------------------------------------------------------------------
def kpis_for_day(day, df, stay_table, capacity):
    """Return all six numbers for a single date."""
    occupied = stay_table["rooms_occupied"].get(day, 0)
    paying = stay_table["paying_rooms"].get(day, 0)
    revenue = stay_table["revenue"].get(day, 0.0)

    # Bookings made / cancelled on this day, counted two ways:
    # as bookings (reservations) and as room nights (reservation x nights).
    made = df[df["booking_date"] == day]
    cancelled = df[df["cancel_date"] == day]
    new_bookings = len(made)
    cancellations = len(cancelled)
    new_room_nights = int(made["nights"].sum())
    cancelled_room_nights = int(cancelled["nights"].sum())

    return {
        "occupancy": occupied / capacity,                       # share of rooms filled
        "adr": revenue / paying if paying else 0.0,             # revenue per PAYING room
        "revpar": revenue / capacity,                           # revenue per AVAILABLE room
        "new_bookings": new_bookings,
        "cancellations": cancellations,
        "new_room_nights": new_room_nights,
        "cancelled_room_nights": cancelled_room_nights,
        "pickup": new_room_nights - cancelled_room_nights,      # net room nights gained that day
    }


def card(col, label, value, last_week, fmt, lower_is_better=False):
    """Draw one KPI card with a 'vs same day last week' arrow."""
    delta = value - last_week
    col.metric(
        label,
        fmt(value),
        delta=fmt(delta) if delta else None,
        # Streamlit colours up=green by default; flip it for "bad when up" KPIs.
        delta_color="inverse" if lower_is_better else "normal",
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
default_capacity = int(stay_table["rooms_occupied"].max())
capacity = st.sidebar.number_input(
    "Rooms available (assumed)", min_value=1, value=default_capacity,
    help="Not in the data. Default = most rooms occupied on any night.",
)

# Only offer dates that have a full week of history behind them.
first_day = hotel_df["arrival_date"].min() + pd.Timedelta(days=7)
last_day = hotel_df["arrival_date"].max()
today = pd.Timestamp(st.sidebar.date_input(
    "Today", value=pd.Timestamp("2016-08-15"), min_value=first_day, max_value=last_day,
))
last_week = today - pd.Timedelta(days=7)

st.caption(f"{hotel} · {today:%A, %d %B %Y} · compared with {last_week:%A, %d %B %Y}")

now = kpis_for_day(today, hotel_df, stay_table, capacity)
before = kpis_for_day(last_week, hotel_df, stay_table, capacity)

# --- Row 1: how full and how much money ---
c1, c2, c3 = st.columns(3)
card(c1, "Occupancy", now["occupancy"], before["occupancy"], lambda v: f"{v:.1%}")
card(c2, "ADR", now["adr"], before["adr"], lambda v: f"€{v:,.2f}")
card(c3, "RevPAR", now["revpar"], before["revpar"], lambda v: f"€{v:,.2f}")

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

# --- Data-quality note ---
st.sidebar.markdown("---")
st.sidebar.caption(
    f"Rows removed everywhere: {removed['adr_outlier']:,} with ADR > {MAX_ADR:,} (data error). "
    f"{removed['adr_zero_or_less_kept']:,} rows with ADR <= 0 are kept in bookings, "
    f"cancellations and occupancy, but left out of ADR, revenue and RevPAR. "
    f"No-shows are excluded from occupancy, ADR and RevPAR."
)
