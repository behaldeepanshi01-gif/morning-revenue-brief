"""Hand check for the forward-looking section: rooms on the books for ONE future
night, counted row by row in plain Python from the raw file (no pandas, no
running totals), so it is independent of forward.py.

Run:  python check_future.py
"""
import csv
import gzip
from datetime import date, datetime, timedelta

HOTEL = "City Hotel"
TODAY = date(2017, 7, 15)            # the "today" we stand on
NIGHT = date(2017, 7, 22)            # the future night we count (today + 7)
SHIFT = timedelta(days=364)          # same weekday last year
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


def on_the_books(asof, night):
    """Rooms booked for `night` as known on the morning of `asof`."""
    rooms = 0
    for r in csv.DictReader(gzip.open("hotel_bookings.csv.gz", "rt", newline="")):
        if r["hotel"] != HOTEL or float(r["adr"]) > 1000:    # the one ADR data error is dropped everywhere
            continue
        arrive = date(int(r["arrival_date_year"]), MONTHS[r["arrival_date_month"]],
                      int(r["arrival_date_day_of_month"]))
        leave = arrive + timedelta(days=int(r["stays_in_weekend_nights"]) + int(r["stays_in_week_nights"]))
        booked_on = arrive - timedelta(days=int(r["lead_time"]))       # no booking date in the file
        cancelled_on = (datetime.strptime(r["reservation_status_date"], "%Y-%m-%d").date()
                        if r["is_canceled"] == "1" else None)
        made_by_then = booked_on <= asof
        still_alive = cancelled_on is None or cancelled_on > asof       # cancelled LATER still counts
        staying = arrive <= night < leave
        if made_by_then and still_alive and staying:
            rooms += 1
    return rooms


this_year = on_the_books(TODAY, NIGHT)
last_year = on_the_books(TODAY - SHIFT, NIGHT - SHIFT)
print(f"{HOTEL}, standing on {TODAY:%a %d %b %Y}, looking at the night of {NIGHT:%a %d %b %Y}:")
print(f"  rooms on the books                         = {this_year}")
print(f"  last year (as of {TODAY - SHIFT:%a %d %b %Y}, night {NIGHT - SHIFT:%a %d %b %Y}) = {last_year}")
print(f"  difference                                 = {this_year - last_year:+d}")
