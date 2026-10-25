"""Input checks for leads. Bad rows are rejected with a plain-English reason."""

COMPANY_TYPES = ["FMCG", "Brand owner", "Manufacturer", "Retailer", "Trader"]
PRODUCTS = ["Spray-dried", "Agglomerated", "Freeze-dried"]
LEAD_SOURCES = ["Inbound enquiry", "Referral", "Trade show", "Cold outreach"]

FIELDS = [
    "lead_id",
    "contact_name",
    "role",
    "company",
    "company_type",
    "product",
    "annual_volume_tonnes",
    "indicative_price_usd_kg",
    "lead_source",
    "context",
]

REQUIRED_TEXT = {
    "lead_id": "Lead ID",
    "contact_name": "Contact name",
    "role": "Role",
    "company": "Company",
}

# Sanity limits. Instant coffee outside this price range is almost certainly a typo.
MIN_PRICE, MAX_PRICE = 2.0, 60.0
MAX_VOLUME = 10000
MAX_CONTEXT = 2000


def clean_row(raw):
    """Keep only the expected fields, as stripped strings."""
    return {field: str(raw.get(field) or "").strip() for field in FIELDS}


def _number(text):
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def validate_lead(raw):
    """Return (lead, errors). lead has numbers converted; errors is a list of strings."""
    lead = clean_row(raw)
    errors = []

    for field, label in REQUIRED_TEXT.items():
        if not lead[field]:
            errors.append(f"{label} is missing.")

    if lead["company_type"] not in COMPANY_TYPES:
        errors.append(f"Company type must be one of: {', '.join(COMPANY_TYPES)}.")
    if lead["product"] not in PRODUCTS:
        errors.append(f"Product must be one of: {', '.join(PRODUCTS)}.")
    if lead["lead_source"] not in LEAD_SOURCES:
        errors.append(f"Lead source must be one of: {', '.join(LEAD_SOURCES)}.")

    volume = _number(lead["annual_volume_tonnes"])
    if not lead["annual_volume_tonnes"]:
        errors.append("Annual volume is missing.")
    elif volume is None or volume <= 0:
        errors.append("Annual volume must be a number above 0 tonnes.")
    elif volume > MAX_VOLUME:
        errors.append(f"Annual volume above {MAX_VOLUME:,} tonnes looks like a typo.")

    price = _number(lead["indicative_price_usd_kg"])
    if not lead["indicative_price_usd_kg"]:
        errors.append("Indicative price is missing; ask the buyer for a target price per kg.")
    elif price is None:
        errors.append("Indicative price must be a number in USD per kg.")
    elif not MIN_PRICE <= price <= MAX_PRICE:
        errors.append(f"Indicative price must be between ${MIN_PRICE:.0f} and ${MAX_PRICE:.0f} per kg.")

    if len(lead["context"]) > MAX_CONTEXT:
        errors.append(f"Context is longer than {MAX_CONTEXT} characters; shorten it.")

    if not errors:
        lead["annual_volume_tonnes"] = volume
        lead["indicative_price_usd_kg"] = price
    return lead, errors


def validate_batch(raw_leads):
    """Validate a list of leads. Returns (valid, rejected)."""
    valid, rejected, seen = [], [], set()
    for raw in raw_leads:
        lead, errors = validate_lead(raw)
        if lead["lead_id"] and lead["lead_id"] in seen:
            errors.append(f"Duplicate lead ID {lead['lead_id']}.")
        if lead["lead_id"]:
            seen.add(lead["lead_id"])
        if errors:
            rejected.append({"lead": lead, "errors": errors})
        else:
            valid.append(lead)
    return valid, rejected
