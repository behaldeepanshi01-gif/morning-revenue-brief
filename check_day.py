"""Hand check: occupied rooms + revenue for one day, straight from raw CSV rows
(row-by-row filter, no running totals), so it is independent of app.py."""
import pandas as pd

DAY = pd.Timestamp("2016-08-15")
raw = pd.read_csv("hotel_bookings.csv")
print("raw rows:", len(raw))
print(f"removed everywhere: ADR>1000 -> {(raw.adr > 1000).sum()}  (ADR<=0 rows are kept)")
d = raw[(raw.adr <= 1000) & (raw.hotel == "City Hotel")].copy()

months = {m: i for i, m in enumerate(["January","February","March","April","May","June","July",
          "August","September","October","November","December"], 1)}
d["arr"] = pd.to_datetime(dict(year=d.arrival_date_year, month=d.arrival_date_month.map(months),
                               day=d.arrival_date_day_of_month))
d["dep"] = d.arr + pd.to_timedelta(d.stays_in_weekend_nights + d.stays_in_week_nights, unit="D")

stay = d[(d.arr <= DAY) & (DAY < d.dep) & (d.is_canceled == 0) & (d.reservation_status != "No-Show")]
paid = stay[stay.adr > 0]   # only ADR > 0 counts for revenue and ADR
rooms, revenue = len(stay), paid.adr.sum()
print(f"City Hotel {DAY:%d %b %Y}: occupied rooms = {rooms} (of which paying = {len(paid)}), "
      f"revenue = {revenue:,.2f}, ADR = {revenue/len(paid):.2f}")
print("excluded that night: cancelled =", len(d[(d.arr <= DAY) & (DAY < d.dep) & (d.is_canceled == 1)]),
      "| no-shows =", len(d[(d.arr <= DAY) & (DAY < d.dep) & (d.reservation_status == "No-Show")]))
