"""Morning Brief writers. No Streamlit in here, so it is easy to test.

Both writers take the same two inputs - today's KPIs and last week's KPIs
(the dicts produced by kpis_for_day in app.py) - and return exactly 5 lines.
"""

import json
import os
import re

MODEL = "claude-haiku-4-5"  # small and cheap; plenty for a 5-line summary

# Occupancy bands that decide what kind of action is allowed.
FULL = 0.90   # above this: never discount
HEALTHY = 0.75  # below this: discounts / promotions / groups are fine


# ---------------------------------------------------------------------------
# 1. RULE-BASED BRIEF (free, always works)
# ---------------------------------------------------------------------------
def _pct_change(now, before):
    """Percent change, or None when last week was zero (can't divide)."""
    return (now - before) / before * 100 if before else None


def cancellations_high(now, before):
    """True when cancellations are a problem: more room nights cancelled than
    booked today, or cancelled room nights well above last week."""
    change = _pct_change(now["cancelled_room_nights"], before["cancelled_room_nights"])
    return now["pickup"] < 0 or (change is not None and change > 25)


def _risks(now, before):
    """List the things that look worrying, each with a severity score.

    The biggest score wins and becomes 'the biggest risk'. The scores are
    rough - they only need to rank the risks against each other.
    Returns a list of (score, risk sentence). The ACTION is chosen separately,
    by how full the hotel is (see recommended_action).
    """
    risks = []

    # Pickup is negative: more room nights cancelled than booked.
    if now["pickup"] < 0:
        risks.append((
            3 + abs(now["pickup"]) / 20,
            f"Cancellations outran new bookings: {now['pickup']:,} net room nights.",
        ))

    # Cancelled room nights well above last week.
    cancel_change = _pct_change(now["cancelled_room_nights"], before["cancelled_room_nights"])
    if cancel_change is not None and cancel_change > 25:
        risks.append((
            2 + cancel_change / 50,
            f"Cancelled room nights are up {cancel_change:.0f}% on last week.",
        ))

    # Occupancy dropped.
    occ_drop = (before["occupancy"] - now["occupancy"]) * 100  # in points
    if occ_drop > 3:
        risks.append((2 + occ_drop / 5, f"Occupancy is down {occ_drop:.1f} points on last week."))

    # Booking pace is slower than last week.
    pace_change = _pct_change(now["new_room_nights"], before["new_room_nights"])
    if pace_change is not None and pace_change < -25:
        risks.append((
            1.5 + abs(pace_change) / 100,
            f"New booking pace is down {abs(pace_change):.0f}% on last week.",
        ))

    # Price is slipping.
    adr_change = _pct_change(now["adr"], before["adr"])
    if adr_change is not None and adr_change < -5:
        risks.append((1 + abs(adr_change) / 10, f"ADR has slipped {abs(adr_change):.0f}% on last week."))

    # Nearly full: the risk is selling too cheap, not selling too little.
    if now["occupancy"] > 0.95:
        risks.append((
            1,
            f"The hotel is almost full ({now['occupancy']:.0%}), so there is little room for error.",
        ))

    return risks


def recommended_action(now, before):
    """One action, chosen by how full the hotel is - not by last week's change."""
    occ = now["occupancy"]

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

    # High cancellations get an extra protection, whatever the occupancy.
    if cancellations_high(now, before):
        action += (" Cancellations are high, so require a deposit or offer a "
                   "non-refundable rate on long-lead bookings.")
    return action


def rule_based_brief(now, before, hotel, day):
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

    # Line 3: booking activity.
    line3 = (f"Pickup: {now['new_bookings']} new bookings ({now['new_room_nights']:,} room nights) "
             f"against {now['cancellations']} cancellations ({now['cancelled_room_nights']:,} room nights), "
             f"net {now['pickup']:+,} room nights (last week {before['pickup']:+,}).")

    # Lines 4 and 5: biggest risk and what to do about it.
    risks = _risks(now, before)
    if risks:
        _, risk_text = max(risks)  # highest score wins
        line4 = f"Biggest risk: {risk_text}"
    else:
        line4 = "Biggest risk: nothing stands out versus last week."
    line5 = f"Recommended action: {recommended_action(now, before)}"

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

Notes: occupancy is a fraction (0.9 = 90%); ADR and RevPAR are in euros;
pickup = new room nights minus cancelled room nights.

Rules for line 5 (the recommended action) - follow them strictly. They depend on
today's occupancy, not on the change versus last week:
- Occupancy above 90%: NEVER suggest discounts, promotions or lower rates.
  Suggest holding or raising rates, adding length-of-stay restrictions, and
  protecting the last rooms.
- Occupancy 75% to 90%: suggest a targeted action, such as focusing on the
  weakest channel or day. No across-the-board discounting.
- Occupancy below 75%: discounts, promotions or group business are fine.
- If cancellations are high (cancelled room nights more than 25% above last
  week, or pickup below zero), also suggest a deposit or non-refundable rate
  for long-lead bookings.

Write exactly 5 short lines, plain English, readable in 30 seconds:
1. How today did.
2. How it compares to the same day last week.
3. Booking activity (pickup).
4. The biggest risk.
5. One recommended action.
Output only the 5 lines: no markdown, no bullets, no numbering, no blank lines."""


def claude_brief(now, before, hotel, day, api_key):
    """Ask Claude for the 5 lines. Raises an exception if anything goes wrong,
    so the caller can fall back to the rule-based brief."""
    import anthropic  # imported here so the app still runs without the package

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": PROMPT.format(
            hotel=hotel, day=f"{day:%A %d %B %Y}",
            now=json.dumps(now), before=json.dumps(before),
        )}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"Claude stopped early ({response.stop_reason})")

    text = "".join(b.text for b in response.content if b.type == "text")
    lines = [ln.strip(" -•*\t") for ln in text.splitlines() if ln.strip()]
    if len(lines) != 5:
        raise RuntimeError(f"Expected 5 lines, got {len(lines)}")

    # Safety net: even if the model ignores the prompt, a nearly full hotel
    # must never be told to discount. Raising an error triggers the fallback.
    if now["occupancy"] > FULL and re.search(
            r"discount|promotion|promo|lower (the )?rate|cut (the )?rate|reduce (the )?rate|cheaper",
            lines[4], re.I):
        raise RuntimeError("Claude suggested discounting at >90% occupancy")
    return lines
