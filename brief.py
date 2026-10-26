"""Morning Brief writers. No Streamlit in here, so it is easy to test.

Both writers take today's KPIs, last week's KPIs (the dicts produced by
kpis_for_day in app.py) and the forward-looking summary (forward.py), and
return exactly 5 lines.
"""

import json
import os
import re

import pandas as pd

MODEL = "claude-haiku-4-5"  # small and cheap; plenty for a 5-line summary

# Occupancy bands that decide what kind of action is allowed.
FULL = 0.90   # above this: never discount
HEALTHY = 0.75  # below this: discounts / promotions / groups are fine

# A percentage change only counts as a risk when the absolute change is
# meaningful too - 40% more cancellations means little if it is 3 extra rooms.
MIN_ROOM_NIGHTS = 20   # smallest change in room nights (over a day or a window)
MIN_ONE_NIGHT = 5      # smallest change in rooms on a single night
MIN_ADR_MOVE = 5       # smallest ADR move, in euros
BIG_NIGHT_GAP = 15     # a future night this far behind last year outranks small swings
MIN_NET_LOSS = 10      # smallest negative pickup (net room nights) worth flagging


# ---------------------------------------------------------------------------
# 1. RULE-BASED BRIEF (free, always works)
# ---------------------------------------------------------------------------
def _pct_change(now, before):
    """Percent change, or None when last week was zero (can't divide)."""
    return (now - before) / before * 100 if before else None


def cancellations_high(now, before):
    """True when cancellations are a problem: cancellations clearly outran new
    bookings, or cancelled room nights are well above last week (by more than
    25% AND by a meaningful number of room nights)."""
    change = _pct_change(now["cancelled_room_nights"], before["cancelled_room_nights"])
    extra = now["cancelled_room_nights"] - before["cancelled_room_nights"]
    return now["pickup"] <= -MIN_NET_LOSS or (
        change is not None and change > 25 and extra >= MIN_ROOM_NIGHTS)


def _risks(now, before, fwd=None):
    """List the things that look worrying, each with a severity score.

    The biggest score wins and becomes 'the biggest risk'. The scores are
    rough - they only need to rank the risks against each other.

    Returns a list of (score, risk sentence, occupancy, target):
    - occupancy: how full the hotel is on the date(s) the risk is about. It
      decides which kind of action is allowed (see recommended_action).
    - target: a label for future dates ("Fri 19 Aug"), or None when the risk
      is about today.
    """
    risks = []
    today_occ = now["occupancy"]

    # --- Today versus last week ---

    # Pickup is clearly negative: more room nights cancelled than booked.
    if now["pickup"] <= -MIN_NET_LOSS:
        risks.append((
            3 + abs(now["pickup"]) / 20,
            f"Cancellations outran new bookings: {now['pickup']:,} net room nights.",
            today_occ, None,
        ))

    # Cancelled room nights well above last week.
    cancel_change = _pct_change(now["cancelled_room_nights"], before["cancelled_room_nights"])
    cancel_extra = now["cancelled_room_nights"] - before["cancelled_room_nights"]
    if cancel_change is not None and cancel_change > 25 and cancel_extra >= MIN_ROOM_NIGHTS:
        risks.append((
            2 + cancel_change / 50,
            f"Cancelled room nights are up {cancel_change:.0f}% on last week.",
            today_occ, None,
        ))

    # Occupancy dropped.
    occ_drop = (before["occupancy"] - now["occupancy"]) * 100  # in points
    rooms_lost = before["rooms_occupied"] - now["rooms_occupied"]
    if occ_drop > 3 and rooms_lost >= MIN_ONE_NIGHT:
        risks.append((2 + occ_drop / 5, f"Occupancy is down {occ_drop:.1f} points on last week.",
                      today_occ, None))

    # Booking pace is slower than last week.
    pace_change = _pct_change(now["new_room_nights"], before["new_room_nights"])
    pace_drop = before["new_room_nights"] - now["new_room_nights"]
    if pace_change is not None and pace_change < -25 and pace_drop >= MIN_ROOM_NIGHTS:
        risks.append((
            1.5 + abs(pace_change) / 100,
            f"New booking pace is down {abs(pace_change):.0f}% on last week.",
            today_occ, None,
        ))

    # Price is slipping.
    adr_change = _pct_change(now["adr"], before["adr"])
    if adr_change is not None and adr_change < -5 and before["adr"] - now["adr"] >= MIN_ADR_MOVE:
        risks.append((1 + abs(adr_change) / 10, f"ADR has slipped {abs(adr_change):.0f}% on last week.",
                      today_occ, None))

    # Nearly full: the risk is selling too cheap, not selling too little.
    if now["occupancy"] > 0.95:
        risks.append((
            1,
            f"The hotel is almost full ({now['occupancy']:.0%}), so there is little room for error.",
            today_occ, None,
        ))

    # --- Future dates versus the same dates last year ---
    if fwd:
        for key, label in (("next7", "next 7 days"), ("next30", "next 30 days")):
            w = fwd[key]
            # more than 10% behind last year, by a meaningful number of room nights
            if w["pct"] is not None and w["pct"] < -0.10 and -w["diff"] >= MIN_ROOM_NIGHTS:
                behind = abs(w["pct"])
                score = (2.5 if key == "next7" else 2) + behind * 100 / (20 if key == "next7" else 25)
                risks.append((
                    score,
                    f"The {label} are {behind:.0%} behind last year at the same point "
                    f"({w['on_books_compared']:,} room nights on the books vs {w['last_year']:,}).",
                    w["occupancy"], f"the {label}",
                ))

        weak = fwd["weakest"]
        capacity = fwd["capacity"]
        if weak["diff"] is not None and weak["diff"] <= -MIN_ONE_NIGHT:
            night = pd.Timestamp(weak["date"])
            # 15+ rooms behind last year on one night outranks small day-over-day swings.
            base = 5 if weak["diff"] <= -BIG_NIGHT_GAP else 2
            risks.append((
                base + abs(weak["diff"]) / capacity * 10,
                f"{night:%A %d %B} is the weakest upcoming night: {weak['on_books']} rooms on the books, "
                f"{abs(weak['diff'])} behind last year.",
                weak["occupancy"], f"{night:%a %d %b}",
            ))

    return risks


def recommended_action(occ, now, before, target=None):
    """One action, chosen by how full the hotel is on the date the risk is
    about (`occ`) - not by last week's change."""
    if occ > FULL:
        # Nearly full: protect price and the last rooms. Never discount.
        action = ("Hold or raise rates, add minimum length-of-stay restrictions "
                  "and protect the last rooms for higher-paying guests.")
    elif occ >= HEALTHY:
        # Mid range: small, targeted moves rather than across-the-board ones.
        action = ("Make a targeted move: focus on the weakest sales channel or "
                  "the weakest day of the week rather than changing every rate.")
    else:
        # Plenty of empty rooms: stimulating demand is reasonable.
        action = ("Stimulate demand with a promotion or discounted rate, "
                  "and go after group business to fill the empty rooms.")

    # For a future date, say which date and how full it already is.
    if target:
        action = f"For {target} ({occ:.0%} on the books): {action[0].lower()}{action[1:]}"

    # High cancellations get an extra protection, whatever the occupancy.
    if cancellations_high(now, before):
        action += (" Cancellations are high, so require a deposit or offer a "
                   "non-refundable rate on long-lead bookings.")
    return action


def rule_based_brief(now, before, hotel, day, fwd=None):
    """Write 5 plain-English lines from simple rules."""
    occ_pts = (now["occupancy"] - before["occupancy"]) * 100
    revpar_change = _pct_change(now["revpar"], before["revpar"])

    # Line 1: how today did.
    line1 = (f"{hotel}, {day:%A %d %B}: {now['occupancy']:.0%} occupancy, "
             f"ADR €{now['adr']:,.0f}, RevPAR €{now['revpar']:,.0f}.")

    # Line 2: versus the same day last week.
    revpar_txt = (f"RevPAR {'up' if revpar_change >= 0 else 'down'} {abs(revpar_change):.0f}%"
                  if revpar_change is not None else "RevPAR has no comparison")
    line2 = (f"Versus last week: occupancy {'up' if occ_pts >= 0 else 'down'} "
             f"{abs(occ_pts):.1f} points, {revpar_txt}.")

    # Line 3: booking activity today, plus the 7-day outlook.
    line3 = (f"Pickup: {now['new_bookings']} new bookings ({now['new_room_nights']:,} room nights) "
             f"against {now['cancellations']} cancellations ({now['cancelled_room_nights']:,} room nights), "
             f"net {now['pickup']:+,} room nights (last week {before['pickup']:+,}).")
    if fwd:
        w = fwd["next7"]
        if w["pct"] is not None:
            line3 += (f" Next 7 days: {w['on_books']:,} room nights on the books, "
                      f"{abs(w['pct']):.0%} {'ahead of' if w['pct'] >= 0 else 'behind'} last year.")
        else:
            line3 += f" Next 7 days: {w['on_books']:,} room nights on the books (no last-year comparison)."

    # Lines 4 and 5: biggest risk and what to do about it.
    risks = _risks(now, before, fwd)
    if risks:
        _, risk_text, occ, target = max(risks, key=lambda r: r[0])  # highest score wins
        line4 = f"Biggest risk: {risk_text}"
    else:
        occ, target = now["occupancy"], None
        line4 = "Biggest risk: nothing stands out versus last week or last year."
    line5 = f"Recommended action: {recommended_action(occ, now, before, target)}"

    return [line1, line2, line3, line4, line5]


# ---------------------------------------------------------------------------
# 2. CLAUDE BRIEF (optional, needs an API key)
# ---------------------------------------------------------------------------
def get_api_key(secrets=None):
    """Find the key without ever putting it in code.

    Order: ANTHROPIC_API_KEY environment variable, then Streamlit's
    .streamlit/secrets.toml (both are git-ignored). Returns None if not set.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and secrets is not None:
        try:
            key = secrets.get("ANTHROPIC_API_KEY")
        except Exception:  # no secrets file at all
            key = None
    return key or None


PROMPT = """You are writing the morning revenue brief for a hotel general manager.
Use ONLY the numbers below. Do not invent figures, causes or events.

Hotel: {hotel}
Date: {day}
Today's KPIs: {now}
Same weekday last week: {before}
Forward look (rooms on the books for upcoming nights vs the same nights last
year at the same lead time): {fwd}

Notes: occupancy is a fraction (0.9 = 90%); ADR and RevPAR are in euros;
pickup = new room nights minus cancelled room nights. In the forward look,
"on_books" is room nights booked (and not cancelled) as of today, "last_year"
is the same figure for the same weekdays a year earlier at the same lead time,
"diff" is on_books minus last_year, and "occupancy" is on_books divided by
rooms available. "weakest" is the upcoming night furthest behind last year.
If a last_year figure is null, there is no comparison - do not make one up.

The biggest risk (line 4) may be about today OR about future dates. Pick
whichever matters most to a GM. A percentage change is only a risk if the
absolute change is meaningful too: at least 20 room nights over a day or a
window, at least 5 rooms on a single night, at least 5 euros for ADR. A single
upcoming night that is 15 or more rooms behind last year outranks small
day-over-day swings.

Rules for line 5 (the recommended action) - follow them strictly. They depend on
how full the hotel is on the date the risk is about - today's occupancy if the
risk is about today, or the forward-look occupancy for that night or window if
the risk is about the future - not on the change versus last week or last year:
- Occupancy above 90%: NEVER suggest discounts, promotions or lower rates.
  Suggest holding or raising rates, adding length-of-stay restrictions, and
  protecting the last rooms.
- Occupancy 75% to 90%: suggest a targeted action, such as focusing on the
  weakest channel or day. No across-the-board discounting.
- Occupancy below 75%: discounts, promotions or group business are fine.
- If cancellations are high (cancelled room nights more than 25% AND at least 20
  room nights above last week, or pickup of -10 or worse), also suggest a
  deposit or non-refundable rate for long-lead bookings.
If line 5 is about a future date, name the date.

Write exactly 5 short lines, plain English, readable in 30 seconds:
1. How today did.
2. How it compares to the same day last week.
3. Booking activity (pickup), and how the next 7 days look versus last year.
4. The biggest risk (today or upcoming).
5. One recommended action.
Output only the 5 lines: no markdown, no bullets, no numbering, no blank lines."""


def claude_brief(now, before, hotel, day, api_key, fwd=None):
    """Ask Claude for the 5 lines. Raises an exception if anything goes wrong,
    so the caller can fall back to the rule-based brief."""
    import anthropic  # imported here so the app still runs without the package

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": PROMPT.format(
            hotel=hotel, day=f"{day:%A %d %B %Y}",
            now=json.dumps(now), before=json.dumps(before),
            fwd=json.dumps(fwd) if fwd else "not available",
        )}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Claude stopped early ({response.stop_reason})")

    text = "".join(b.text for b in response.content if b.type == "text")
    lines = [ln.strip(" -•*\t") for ln in text.splitlines() if ln.strip()]
    if len(lines) != 5:
        raise RuntimeError(f"Expected 5 lines, got {len(lines)}")

    # Safety net: even if the model ignores the prompt, a nearly full hotel
    # must never be told to discount. We don't know which date Claude's action
    # is about, so be strict: if today OR any forward window/night is above
    # 90%, discount words in line 5 are rejected. Raising an error triggers
    # the fallback to the rule-based brief.
    occupancies = [now["occupancy"]]
    if fwd:
        occupancies += [fwd["next7"]["occupancy"], fwd["next30"]["occupancy"],
                        fwd["weakest"]["occupancy"]]
    if max(occupancies) > FULL and re.search(
            r"discount|promotion|promo|lower (the )?rate|cut (the )?rate|reduce (the )?rate|cheaper",
            lines[4], re.I):
        raise RuntimeError("Claude suggested discounting at >90% occupancy")
    return lines
