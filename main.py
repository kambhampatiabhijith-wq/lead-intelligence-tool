"""Lead Intelligence Tool - web app entry point.

Stage 2: input form and CSV upload. Leads are held in memory for now;
the database arrives in a later stage.
"""

import csv
import io
import os

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-only-secret")

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

# Leads waiting to be scored. Moves to SQLite in the database stage.
LEADS = []


def clean_row(raw):
    """Keep only the expected fields and strip stray spaces."""
    return {field: (raw.get(field) or "").strip() for field in FIELDS}


def next_lead_id():
    """Next free ID in the L001 pattern."""
    numbers = [int(l["lead_id"][1:]) for l in LEADS if l["lead_id"][1:].isdigit()]
    return f"L{(max(numbers, default=0) + 1):03d}"


@app.route("/")
def index():
    return render_template(
        "index.html",
        leads=LEADS,
        company_types=COMPANY_TYPES,
        products=PRODUCTS,
        lead_sources=LEAD_SOURCES,
        next_id=next_lead_id(),
    )


@app.post("/leads")
def add_lead():
    lead = clean_row(request.form)
    if not lead["lead_id"]:
        lead["lead_id"] = next_lead_id()
    LEADS.append(lead)
    flash(f"Added lead {lead['lead_id']} ({lead['company'] or 'no company'}).", "ok")
    return redirect(url_for("index"))


@app.post("/upload")
def upload_csv():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a CSV file first.", "error")
        return redirect(url_for("index"))
    if not file.filename.lower().endswith(".csv"):
        flash("That file is not a CSV. Export your leads as .csv and try again.", "error")
        return redirect(url_for("index"))

    try:
        text = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("Could not read the file. Save it as UTF-8 CSV and try again.", "error")
        return redirect(url_for("index"))

    reader = csv.DictReader(io.StringIO(text))
    missing = [f for f in FIELDS if f not in (reader.fieldnames or [])]
    if missing:
        flash("The CSV is missing these columns: " + ", ".join(missing), "error")
        return redirect(url_for("index"))

    rows = [clean_row(r) for r in reader if any((v or "").strip() for v in r.values())]
    LEADS.clear()
    LEADS.extend(rows)
    flash(f"Loaded {len(rows)} leads from {file.filename}.", "ok")
    return redirect(url_for("index"))


@app.post("/clear")
def clear_leads():
    LEADS.clear()
    flash("Cleared all leads.", "ok")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
