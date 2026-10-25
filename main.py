"""Lead Intelligence Tool - web app entry point.

Stage 4: AI generation. POST /api/score validates the leads, then the AI
reads each lead's context for buying signals and drafts an outreach email.
Leads and the last result are held in memory until the database stage.
"""

import csv
import io
import os

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

import ai
from validation import COMPANY_TYPES, FIELDS, LEAD_SOURCES, PRODUCTS, clean_row, validate_batch

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-only-secret")

# Leads waiting to be scored. Moves to SQLite in the database stage.
LEADS = []
# Result of the last scoring run. Moves to SQLite in the database stage.
LAST_RUN = None


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


# ---------- Backend API ----------

@app.post("/api/score")
def api_score():
    """Validate a batch of leads sent as JSON: {"leads": [ {...}, ... ]}."""
    global LAST_RUN
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("leads"), list):
        return jsonify({"error": 'Send JSON like {"leads": [ ... ]}.'}), 400
    if not body["leads"]:
        return jsonify({"error": "No leads to score. Add or upload leads first."}), 400

    valid, rejected = validate_batch(body["leads"])
    ai_results, ai_status = ai.generate(valid)
    LAST_RUN = {
        "received": len(body["leads"]),
        "valid": valid,
        "rejected": rejected,
        "ai": ai_results,
        "ai_status": ai_status,
    }
    return jsonify({
        "received": LAST_RUN["received"],
        "valid_count": len(valid),
        "rejected_count": len(rejected),
        "ai_status": ai_status,
        "leads": [dict(lead, ai=ai_results[lead["lead_id"]]) for lead in valid],
        "rejected": rejected,
        "results_url": url_for("results"),
    })


@app.route("/results")
def results():
    return render_template("results.html", run=LAST_RUN)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
