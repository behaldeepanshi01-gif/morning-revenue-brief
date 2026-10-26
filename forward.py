"""Forward-looking view: rooms on the books for the next 7 and 30 nights,
compared with the same nights last year at the same lead time.

No Streamlit in here, so it is easy to test.
"""

import numpy as np
import pandas as pd

# "Same dates last year" = 364 days earlier (52 weeks), so a Friday is compared
# with a Friday. A 365-day shift would compare a Friday with a Thursday.
LAST_YEAR_SHIFT = pd.Timedelta(days=364)

# The file starts on 1 Jul 2015. A guest who arrived before that and stayed on
# is missing from the data, so we only trust last-year numbers a month later.
LAST_YEAR_MARGIN = pd.Timedelta(days=30)


def rooms_on_books(df, asof, nights):
    """Rooms booked for each night in `nights`, as known on the morning of `asof`.

    A booking counts if it was made on or before `asof`, it has not been
    cancelled by `asof`, and the guest is staying that night (arrival <= night
    < departure). A booking that was cancelled LATER than `asof` still counts:
    on that morning the hotel did not know yet. No-shows also count, for the
    same reason.
    """
    not_cancelled_yet = ~((df["is_canceled"] == 1) & (df["cancel_date"] <= asof))
    live = df[(df["booking_date"] <= asof) & not_cancelled_yet]
    arrive = live["arrival_date"].values
    leave = live["departure_date"].values
    return np.array([int(((arrive <= n) & (leave > n)).sum()) for n in nights.values])


def forward_table(df, today, horizon=30):
    """One row per upcoming night (tomorrow onwards) with this year's rooms on
    the books and last year's at the same lead time. Returns None if the data
    ends before tomorrow."""
    first_data, last_data = df["arrival_date"].min(), df["arrival_date"].max()

    nights = pd.date_range(today + pd.Timedelta(days=1), periods=horizon)
    nights = nights[nights <= last_data]  # the file has no bookings past its end
    if len(nights) == 0:
        return None

    table = pd.DataFrame({"on_books": rooms_on_books(df, today, nights)}, index=nights)

    # Same lead time: last year's nights, counted as of last year's "today".
    ly_nights = nights - LAST_YEAR_SHIFT
    ly_ok = ly_nights >= first_data + LAST_YEAR_MARGIN
    table["last_year"] = np.nan
    if ly_ok.any():
        table.loc[ly_ok, "last_year"] = rooms_on_books(df, today - LAST_YEAR_SHIFT, ly_nights[ly_ok])

    table["diff"] = table["on_books"] - table["last_year"]
    return table


def forward_summary(table, capacity):
    """Boil the table down to the few numbers the page and the brief need.
    Everything is a plain int/float/str so it can go straight into a prompt."""
    if table is None:
        return None

    def window(days):
        rows = table.iloc[:days]
        comparable = rows.dropna(subset=["last_year"])  # only nights with a last-year figure
        out = {
            "days": len(rows),
            "on_books": int(rows["on_books"].sum()),
            "occupancy": float(rows["on_books"].sum() / (capacity * len(rows))),
            "last_year": None, "diff": None, "pct": None, "on_books_compared": None,
        }
        if len(comparable):
            this_year, last_year = comparable["on_books"].sum(), comparable["last_year"].sum()
            out.update(
                last_year=int(last_year), diff=int(this_year - last_year),
                pct=float((this_year - last_year) / last_year) if last_year else None,
                on_books_compared=int(this_year),  # the same nights as last_year
            )
        return out

    # Weakest night = the biggest shortfall in rooms versus last year
    # (ties: the emptiest night). Without a last-year figure, just the emptiest.
    comparable = table.dropna(subset=["last_year"])
    if len(comparable):
        night = comparable.sort_values(["diff", "on_books"]).index[0]
    else:
        night = table["on_books"].sort_values().index[0]
    row = table.loc[night]
    weakest = {
        "date": night.strftime("%Y-%m-%d"),
        "on_books": int(row["on_books"]),
        "occupancy": float(row["on_books"] / capacity),
        "last_year": None if pd.isna(row["last_year"]) else int(row["last_year"]),
        "diff": None if pd.isna(row["diff"]) else int(row["diff"]),
    }
    return {"capacity": int(capacity), "next7": window(7), "next30": window(30), "weakest": weakest}
