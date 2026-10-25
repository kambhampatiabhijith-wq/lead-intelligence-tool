"""Smart logic layer: score, segment, next action and email checks.

Runs AFTER the AI. The AI only supplies quoted signals and an email draft;
every point, the segment and the next action are decided here by fixed rules,
so the same lead always gets the same result.
"""

import re

# Illustrative base prices (USD/kg) for this prototype, not real commercial prices.
BASE_PRICES = {"Spray-dried": 8.00, "Agglomerated": 9.00, "Freeze-dried": 14.00}

# Volume x price matrix: band -> (points at or above base price, points below base price)
VOLUME_BANDS = [  # (name, minimum tonnes, label)
    ("Large", 100, "100 t+"),
    ("Medium", 25, "25-99 t"),
    ("Small", 5, "5-24 t"),
    ("Micro", 0, "under 5 t"),
]
MATRIX = {"Large": (40, 10), "Medium": (30, 6), "Small": (18, 3), "Micro": (8, 0)}

COMPANY_FIT = {"FMCG": 10, "Brand owner": 9, "Manufacturer": 8, "Retailer": 6, "Trader": 5}

DECISION_MAKER = re.compile(
    r"\b(founder|owner|ceo|chief|president|md|managing director|director|head|vp|vice president|partner)\b", re.I)
INFLUENCER = re.compile(r"\b(manager|buyer|procurement|purchase|purchasing|sourcing|category)\b", re.I)
AUTHORITY_POINTS = {"Decision-maker": 15, "Influencer": 9, "Other": 3}

SOURCE_POINTS = {"Inbound enquiry": 15, "Referral": 12, "Trade show": 8, "Cold outreach": 3}
SIGNAL_POINTS = {
    "stated_need": 8,
    "timeline": 7,
    "sample_request": 5,
    "competitor_contract": -10,
    "no_budget": -10,
}
INTENT_CAP = 35

SEGMENTS = [("Hot", 70), ("Warm", 45), ("Nurture", 25), ("Low", 0)]
SEGMENT_ORDER = {"Hot": 0, "Warm": 1, "Nurture": 2, "Low": 3}

ACTIONS = {
    "Hot": "Call within 24 hours and send samples with a spec sheet.",
    "Warm": "Send the outreach email and book a discovery call this week.",
    "Nurture": "Add to the nurture list and follow up in 30 days.",
    "Low": "No action this quarter. Keep on file.",
}
ESCALATE_ACTION = "Escalate to management for pricing approval before quoting."
INTRO_ACTION = "Ask for an introduction to the decision-maker before pitching."

# Email checks
EMAIL_MIN_WORDS, EMAIL_MAX_WORDS = 40, 180
CALL_TO_ACTION = re.compile(r"\b(call|meeting|meet|chat|samples?|schedule|book|arrange|set up)\b", re.I)
PRICE_TALK = re.compile(r"(\$|usd|₹|inr|€|per kg|/kg|discount)", re.I)


def volume_band(tonnes):
    for name, minimum, label in VOLUME_BANDS:
        if tonnes >= minimum:
            return name, label
    return "Micro", "under 5 t"


def authority_level(role):
    if DECISION_MAKER.search(role or ""):
        return "Decision-maker"
    if INFLUENCER.search(role or ""):
        return "Influencer"
    return "Other"


def segment_for(score):
    for name, minimum in SEGMENTS:
        if score >= minimum:
            return name
    return "Low"


# ---------- Email check and fallback ----------

def check_email(email, lead):
    """Return a list of problems with the AI's email (empty list means it passed)."""
    if not email:
        return ["no email returned by the AI"]
    body = email["body"]
    problems = []
    words = len(body.split())
    if not EMAIL_MIN_WORDS <= words <= EMAIL_MAX_WORDS:
        problems.append(f"length {words} words (needs {EMAIL_MIN_WORDS}-{EMAIL_MAX_WORDS})")
    if lead["company"].lower() not in (body + " " + email["subject"]).lower():
        problems.append("does not name the company")
    first_name = lead["contact_name"].split()[0].lower()
    if first_name not in body.lower():
        problems.append("does not address the contact by name")
    if not CALL_TO_ACTION.search(body):
        problems.append("no clear next step")
    if PRICE_TALK.search(body + " " + email["subject"]):
        problems.append("mentions price, which needs approval first")
    if re.search(r"\[[^\]]+\]", body):
        problems.append("contains a placeholder in brackets")
    return problems


def template_email(lead, segment):
    first = lead["contact_name"].split()[0]
    product = lead["product"].lower()
    if segment == "Hot":
        ask = f"Could we set up a short call this week? I can also send samples of our {product} coffee with a spec sheet."
    elif segment == "Warm":
        ask = "Would you be open to a 20-minute call this week to understand your requirements?"
    else:
        ask = "If it helps, I can share our product range and set up a short call whenever the timing suits you."
    body = (
        f"Hi {first},\n\n"
        f"Thank you for your interest in {product} instant coffee for {lead['company']}. "
        f"We manufacture spray-dried, agglomerated and freeze-dried coffee in bulk and as private label, "
        f"and work with {lead['company_type'].lower()} customers on blends, packing formats and consistent supply.\n\n"
        f"{ask}\n\n"
        f"Best regards,\nSales team"
    )
    return {"subject": f"{lead['product']} instant coffee for {lead['company']}", "body": body}


# ---------- Scoring ----------

def score_lead(lead, ai_result):
    """Score one validated lead using the rules plus the AI's verified signals."""
    parts = []

    # 1. Commercial value: volume x price matrix
    tonnes, price = lead["annual_volume_tonnes"], lead["indicative_price_usd_kg"]
    base = BASE_PRICES[lead["product"]]
    band, band_label = volume_band(tonnes)
    above_base = price >= base
    commercial = MATRIX[band][0 if above_base else 1]
    price_side = "at or above" if above_base else "below"
    parts.append({
        "part": "Commercial value", "source": "Rule", "points": commercial, "max": 40,
        "detail": f"{band} volume ({band_label}), price ${price:.2f}/kg is {price_side} the ${base:.2f} base",
    })

    # 2. Company fit
    fit = COMPANY_FIT[lead["company_type"]]
    parts.append({"part": "Company fit", "source": "Rule", "points": fit, "max": 10,
                  "detail": lead["company_type"]})

    # 3. Authority
    level = authority_level(lead["role"])
    parts.append({"part": "Authority", "source": "Rule", "points": AUTHORITY_POINTS[level], "max": 15,
                  "detail": f"{lead['role']} ({level.lower()})"})

    # 4. Intent: lead source + AI signals (capped)
    source_pts = SOURCE_POINTS[lead["lead_source"]]
    parts.append({"part": "Intent", "source": "Rule", "points": source_pts, "max": None,
                  "detail": f"Lead source: {lead['lead_source']}"})
    intent_total = source_pts
    signals = (ai_result or {}).get("signals", [])
    for s in signals:
        pts = SIGNAL_POINTS[s["type"]]
        if pts > 0:
            pts = min(pts, INTENT_CAP - intent_total)
            intent_total += pts
            part = "Intent"
        else:
            part = "Risk"
        parts.append({"part": part, "source": "AI", "points": pts, "max": None,
                      "detail": f"{s['label']}: “{s['quote']}”"})

    raw = sum(p["points"] for p in parts)
    score = max(0, min(100, raw))
    segment = segment_for(score)

    # Next action: segment default, then overrides (escalation is checked first)
    action, reasons = ACTIONS[segment], []
    is_active = segment in ("Hot", "Warm")
    if is_active and not above_base and band in ("Large", "Medium"):
        action = ESCALATE_ACTION
        reasons.append(f"{band} volume below the base price: discount needs approval")
    if is_active and level != "Decision-maker":
        if action == ACTIONS[segment]:
            action = INTRO_ACTION
        reasons.append("contact is not the decision-maker")

    # Email: keep the AI draft only if it passes every check
    ai_email = (ai_result or {}).get("email")
    problems = check_email(ai_email, lead) if ai_result and ai_result.get("checked") else ["AI not used"]
    if problems:
        email, email_source = template_email(lead, segment), "Template"
    else:
        email, email_source = ai_email, "AI draft"

    return {
        "lead": lead,
        "score": score,
        "segment": segment,
        "parts": parts,
        "next_action": action,
        "action_reasons": reasons,
        "email": email,
        "email_source": email_source,
        "email_problems": problems if ai_email else [],
        "ai_dropped": (ai_result or {}).get("dropped", []),
        "volume_band": band,
        "above_base": above_base,
        "authority": level,
    }


def score_batch(leads, ai_results):
    """Score every lead and sort: best segment first, then highest score."""
    results = [score_lead(l, ai_results.get(l["lead_id"])) for l in leads]
    results.sort(key=lambda r: (SEGMENT_ORDER[r["segment"]], -r["score"], r["lead"]["lead_id"]))
    return results
