# Morning Revenue Brief

**An AI-assisted morning revenue brief for hotel general managers.**

> 📸 *Screenshot: add here*  
> 🔗 *Live demo: add link here*

## The problem

Every morning a GM wants to know three things: how did we do, how does that compare with last week, and what should I do about it. The answers are scattered across PMS reports and spreadsheets, and the numbers are easy to misread. This app pulls them into one page and a five-line summary you can read in 30 seconds.

## What it shows

**KPI cards** for any chosen day, each compared with the same weekday last week:
- Occupancy, ADR (average daily rate) and RevPAR (revenue per available room)
- New bookings and cancellations
- New and cancelled room nights, and net pickup (new room nights minus cancelled room nights)

**Morning Brief**, five short lines at the top of the page:
1. How today did
2. How it compares with last week
3. Booking activity (pickup)
4. The biggest risk
5. One recommended action

Optional: Claude can write the brief when an API key is set. The rule-based brief works without one. If the key is missing or the call fails, the app quietly falls back to the rule-based version.

## Looking ahead

Below the KPI cards, the app shows **rooms on the books** for the next 7 and 30 nights: bookings made on or before today that haven't been cancelled by today. Each is compared with the same nights last year **at the same lead time**, meaning last year's figure is counted as it stood on the same morning a year earlier. "Last year" means the same weekday, 364 days back, so a Saturday is compared with a Saturday.

It flags the **weakest upcoming night** (the one furthest behind last year), and the Morning Brief can name it as the biggest risk, with the action chosen by how full that night already is.

I checked one future night by hand from the raw rows (`check_future.py`): standing on Sat 15 Jul 2017, the City Hotel had **219** rooms on the books for Sat 22 Jul 2017 against **214** for Sat 23 Jul 2016 at the same point, a gap of +5. The app shows the same numbers.

The data starts in July 2015 and 2015 is a much smaller sample, so comparisons against 2015 look better than they really are. The app shows a note when that happens, and starts on a 2017 date so the comparison is against 2016.

## How it runs automatically

Every morning the brief is also **emailed** to the GM: the five lines, the 7 and 30 day outlook, the weakest upcoming night and a link to the live app.

- **Schedule:** a GitHub Actions workflow runs `send_brief.py` every day at **6:00 AM US Eastern**. (GitHub schedules in UTC, so it is set for both 10:00 and 11:00 UTC and only the run that lands on 6 AM Eastern goes ahead, which keeps it right through daylight saving.) There is also a **Run workflow** button on the repo's Actions tab for demos.
- **Replay mode:** the data is history, not live, so each real day the email "replays" the next day of the data. The first run is for 15 Jul 2017, the next real day gives 16 Jul, and so on. The brief needs 30 days of data after the replay day, so it loops back to 15 Jul after 18 days. The email says it is a demo.
- **Claude with a safety net:** the brief is written by Claude (`claude-haiku-4-5`). If the API call fails, the rule-based brief is sent instead, so the email always goes out.
- **Secrets:** the API key and Gmail details are kept in GitHub's encrypted secrets (`ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`), never in the code. Gmail needs an [app password](https://support.google.com/accounts/answer/185833), not your normal password.
- **Try it without sending:** `python send_brief.py --dry-run` prints the email instead of sending it.

**Connecting it to a real hotel:** most property management systems (PMS) can export reservations on a schedule. Point `kpis.py` at that nightly export instead of the Kaggle file. The columns needed are arrival date, number of nights, booking date, cancellation date, status, room rate and hotel. Then drop the replay logic so the brief is simply for today's date. Everything else (KPIs, outlook, brief, email, schedule) stays the same.

## How I checked the numbers

I recalculated one day by hand, straight from the raw booking rows and without using the app's code: **City Hotel, 15 August 2016**.

| | Raw rows | App |
|---|---|---|
| Occupied rooms | 212 | 212 |
| Room revenue | €26,958.56 | €26,958.56 |
| ADR | €128.37 | €128.37 |

The check script is `check_day.py` (run `python check_day.py`).

## Why it never suggests discounts above 90% occupancy

The recommendation depends on how full the hotel is, not just on how today compares with last week:

- **Above 90%:** hold or raise rates, add minimum length-of-stay restrictions, protect the last rooms. Never discount.
- **75–90%:** targeted moves, such as focusing on the weakest channel or day.
- **Below 75%:** promotions, discounts or group business are fine.
- **High cancellations** (at any occupancy): suggest a deposit or non-refundable rate for long-lead bookings.

The same rules are given to Claude, and the app also checks its answer. If it recommends discounting a nearly full hotel, that answer is thrown away and the rule-based brief is used.

## Data

The data comes from a city hotel and a resort hotel in Portugal: about 119,000 bookings from 2015 to 2017. It is historical, so "today" is a date you pick. Source: [Hotel Booking Demand on Kaggle](https://www.kaggle.com/datasets/jessemostipak/hotel-booking-demand).

> Antonio, N., de Almeida, A., and Nunes, L. (2019). "Hotel booking demand datasets." *Data in Brief*, 22, 41–49.

## Assumptions

- **Room count:** the data doesn't include one. I assume capacity equals the busiest night in the data (226 rooms for the City Hotel). You can change it in the sidebar, and occupancy and RevPAR update.
- **Booking date:** not in the data, so it is worked out as arrival date minus lead time.
- **No-shows:** left out of occupancy, ADR and RevPAR.
- **€0 ADR bookings (1,960 rows):** kept in booking counts, cancellations and occupancy, but left out of revenue, ADR and RevPAR.
- **ADR above €1,000:** treated as a data error and removed everywhere (one row).
- **Currency:** shown as €; the dataset doesn't state one.

## Run it locally

```
pip install -r requirements.txt
streamlit run app.py
```

If `streamlit` isn't recognised, use `python -m streamlit run app.py`.

**Optional, for the Claude-written brief:** set an `ANTHROPIC_API_KEY` environment variable, or copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and add your key. Keys are never stored in the code or committed to git.
