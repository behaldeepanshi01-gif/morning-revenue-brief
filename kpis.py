"""Data loading and daily KPI math. No Streamlit in here, so the web app
(app.py) and the daily email (send_brief.py) share exactly the same numbers.
"""

from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# 1. LOAD + PREPARE THE DATA
# ---------------------------------------------------------------------------
DATA_FILE = Path(__file__).parent / "hotel_bookings.csv.gz"
MAX_ADR = 1000  # ADR above this is a data error and removed everywhere


def load_data():
    df = pd.read_csv(DATA_FILE)

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


def default_capacity(stay_table):
    """The data has no room count, so we assume capacity = the busiest night in
    the file (that night must have been ~full)."""
    return int(stay_table["rooms_occupied"].max())
