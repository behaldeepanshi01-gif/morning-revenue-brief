"""Send the daily Morning Brief by email.

    python send_brief.py             # build the brief and email it
    python send_brief.py --dry-run   # build it and PRINT the email instead

All the numbers come from the same code the web app uses: kpis.py (daily KPIs),
forward.py (rooms on the books vs last year) and brief.py (the 5 lines).

Settings come from environment variables - never from the code:
    GMAIL_ADDRESS        the Gmail account that sends (and receives) the email
    GMAIL_APP_PASSWORD   a Google "app password" for that account (not the normal password)
    ANTHROPIC_API_KEY    optional; without it the rule-based brief is used
    APP_URL              optional; link to the live app shown in the email
"""

import argparse
import html
import os
import smtplib
import sys
from datetime import date, timedelta
from email.message import EmailMessage

import pandas as pd

import kpis
from brief import claude_brief, get_api_key, rule_based_brief
from forward import forward_summary, forward_table

HOTEL = "City Hotel"
PLACEHOLDER = "your-address@gmail.com"  # only shown in --dry-run when GMAIL_ADDRESS is not set
DEFAULT_APP_URL = "https://github.com/behaldeepanshi01-gif/morning-revenue-brief"

# --- Replay mode -----------------------------------------------------------
# The data is historical, so each real day we "replay" the next day of history:
# on REPLAY_ANCHOR (the real day this was set up) the brief is for REPLAY_START,
# the next real day it is for the day after, and so on.
REPLAY_START = date(2017, 7, 15)
REPLAY_ANCHOR = date(2026, 10, 26)
MIN_DAYS_AHEAD = 30   # a replay day needs a full 30 days of data after it


# ---------------------------------------------------------------------------
# 1. WHICH DAY ARE WE REPLAYING?
# ---------------------------------------------------------------------------
def pick_replay_date(real_today, last_data_day):
    """Move forward one day per real day since the anchor; loop when the data runs out."""
    last_valid = last_data_day - timedelta(days=MIN_DAYS_AHEAD)
    length = (last_valid - REPLAY_START).days + 1
    return REPLAY_START + timedelta(days=(real_today - REPLAY_ANCHOR).days % length)


# ---------------------------------------------------------------------------
# 2. NUMBERS AND BRIEF (all reused from the app's modules)
# ---------------------------------------------------------------------------
def build_report(day=None, real_today=None):
    """Compute everything the email needs for one replay day."""
    data, _ = kpis.load_data()
    hotel_df = data[data["hotel"] == HOTEL]
    if day is None:
        day = pick_replay_date(real_today or date.today(), hotel_df["arrival_date"].max().date())
    day = pd.Timestamp(day)

    stay_table = kpis.daily_stay_table(hotel_df)
    capacity = kpis.default_capacity(stay_table)
    now = kpis.kpis_for_day(day, hotel_df, stay_table, capacity)
    before = kpis.kpis_for_day(day - pd.Timedelta(days=7), hotel_df, stay_table, capacity)
    table = forward_table(hotel_df, day)
    fwd = forward_summary(table, capacity)
    return {"day": day, "now": now, "before": before, "fwd": fwd}


def write_brief(report):
    """5 lines from Claude if possible, otherwise from the rules. Returns (lines, source)."""
    day, now, before, fwd = report["day"], report["now"], report["before"], report["fwd"]
    api_key = get_api_key()
    if api_key:
        try:
            return claude_brief(now, before, HOTEL, day, api_key, fwd), "Written by Claude"
        except Exception as err:  # bad key, network, refusal, odd output...
            print(f"Claude brief failed ({type(err).__name__}); using the rule-based brief.", file=sys.stderr)
    return rule_based_brief(now, before, HOTEL, day, fwd), "Rule-based brief"


# ---------------------------------------------------------------------------
# 3. THE EMAIL
# ---------------------------------------------------------------------------
def _outlook_rows(fwd):
    """(label, on the books, last year, difference text, is_behind) per window."""
    rows = []
    for key in ("next7", "next30"):
        w = fwd[key]
        if w["diff"] is None:
            rows.append((f"Next {w['days']} days", f"{w['on_books']:,}", "n/a", "n/a", False))
        else:
            pct = f" ({w['pct']:+.1%})" if w["pct"] is not None else ""
            rows.append((f"Next {w['days']} days", f"{w['on_books']:,}", f"{w['last_year']:,}",
                         f"{w['diff']:+,}{pct}", w["diff"] < 0))
    return rows


def _weakest_text(fwd):
    weak = fwd["weakest"]
    night = pd.Timestamp(weak["date"])
    text = f"{night:%a %d %b}: {weak['on_books']} rooms on the books ({weak['occupancy']:.0%})"
    if weak["diff"] is not None:
        text += (f", {abs(weak['diff'])} {'behind' if weak['diff'] < 0 else 'ahead of'} "
                 f"the {weak['last_year']} at this point last year")
    return text + "."


def build_subject(day):
    return f"Morning Brief – {HOTEL} – {day:%a %d %b %Y}"


def build_text(report, lines, source, app_url):
    """Plain-text version, for email apps that don't show HTML."""
    out = [build_subject(report["day"]), "", *lines, ""]
    fwd = report["fwd"]
    if fwd:
        out.append("Rooms on the books (vs same nights last year):")
        out += [f"  {label}: {books} (last year {ly}, difference {diff})" for label, books, ly, diff, _ in _outlook_rows(fwd)]
        out += ["", "Weakest upcoming night - " + _weakest_text(fwd), ""]
    out += [f"Open the live app: {app_url}", "", f"{source}. Demo: replaying historical data."]
    return "\n".join(out)


def build_html(report, lines, source, app_url):
    """Clean HTML email: simple tables and inline styles so it looks the same in Gmail/Outlook."""
    esc = html.escape
    day, fwd = report["day"], report["fwd"]
    font = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"

    brief_items = "".join(
        f'<tr><td style="padding:8px 0;border-bottom:1px solid #eceff3;font-size:15px;'
        f'line-height:1.45;color:#1f2933;">{esc(line)}</td></tr>'
        for line in lines
    )

    outlook = ""
    if fwd:
        rows = "".join(
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #eceff3;">{esc(label)}</td>'
            f'<td align="right" style="padding:6px 8px;border-bottom:1px solid #eceff3;">{esc(books)}</td>'
            f'<td align="right" style="padding:6px 8px;border-bottom:1px solid #eceff3;">{esc(ly)}</td>'
            f'<td align="right" style="padding:6px 8px;border-bottom:1px solid #eceff3;font-weight:600;'
            f'color:{"#b42318" if behind else "#067647"};">{esc(diff)}</td></tr>'
            for label, books, ly, diff, behind in _outlook_rows(fwd)
        )
        outlook = f"""
        <h2 style="font-size:14px;margin:24px 0 8px;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">Looking ahead: room nights on the books</h2>
        <table width="100%" cellpadding="0" cellspacing="0" style="font-size:14px;color:#1f2933;border-collapse:collapse;">
          <tr style="color:#7b8794;font-size:12px;"><td style="padding:4px 8px;"></td>
            <td align="right" style="padding:4px 8px;">This year</td>
            <td align="right" style="padding:4px 8px;">Last year</td>
            <td align="right" style="padding:4px 8px;">Difference</td></tr>
          {rows}
        </table>
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;"><tr>
          <td style="background:#fff7e6;border-left:4px solid #f0a020;padding:10px 12px;font-size:14px;color:#1f2933;">
            <strong>Weakest upcoming night</strong><br>{esc(_weakest_text(fwd))}
          </td></tr></table>"""

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f6f8;{font}">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;"><tr><td align="center" style="padding:24px 12px;">
  <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px;">
    <tr><td style="padding:24px 28px;">
      <div style="font-size:12px;color:#7b8794;text-transform:uppercase;letter-spacing:.06em;">Morning Brief</div>
      <div style="font-size:22px;font-weight:700;color:#1f2933;margin:2px 0 14px;">{esc(HOTEL)} &middot; {day:%A %d %B %Y}</div>
      <table width="100%" cellpadding="0" cellspacing="0">{brief_items}</table>
      {outlook}
      <p style="margin:24px 0 0;"><a href="{esc(app_url, quote=True)}" style="display:inline-block;background:#1f5eff;color:#ffffff;text-decoration:none;
         padding:10px 18px;border-radius:6px;font-size:14px;font-weight:600;">Open the live app</a></p>
      <p style="margin:20px 0 0;font-size:12px;color:#7b8794;">{esc(source)}. Demo mode: this replays historical hotel data,
         one day per real day.</p>
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def build_message(report, lines, source, sender, recipient, app_url):
    msg = EmailMessage()
    msg["Subject"] = build_subject(report["day"])
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(build_text(report, lines, source, app_url))
    msg.add_alternative(build_html(report, lines, source, app_url), subtype="html")
    return msg


def send_via_gmail(msg, address, app_password):
    """Gmail SMTP over SSL. Needs a Google app password (2-step verification must be on)."""
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(address, app_password)
        server.send_message(msg)


# ---------------------------------------------------------------------------
# 4. RUN IT
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Send the daily Morning Brief email.")
    parser.add_argument("--dry-run", action="store_true", help="print the email instead of sending it")
    parser.add_argument("--date", type=date.fromisoformat, metavar="YYYY-MM-DD",
                        help="replay this day instead of the one picked from today's date")
    args = parser.parse_args()

    address = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not args.dry_run and not (address and password):
        sys.exit("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD (or use --dry-run).")

    report = build_report(day=args.date)
    lines, source = write_brief(report)
    app_url = os.environ.get("APP_URL") or DEFAULT_APP_URL
    msg = build_message(report, lines, source, address or PLACEHOLDER, address or PLACEHOLDER, app_url)

    if args.dry_run:
        sys.stdout.reconfigure(encoding="utf-8")  # so the euro sign and dashes print on Windows
        print(f"From: {msg['From']}\nTo: {msg['To']}\nSubject: {msg['Subject']}\n")
        print("----- plain text -----")
        print(build_text(report, lines, source, app_url))
        print("\n----- HTML -----")
        print(build_html(report, lines, source, app_url))
        print(f"\n(dry run - nothing sent; replay day {report['day']:%Y-%m-%d})")
        return

    send_via_gmail(msg, address, password)
    print(f"Sent: {msg['Subject']}")


if __name__ == "__main__":
    main()
