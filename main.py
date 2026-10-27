"""Lead Intelligence Tool - web app entry point.

Flow for each scoring run (POST /api/score):
  validation.py  -> rejects bad rows with a plain-English reason
  ai.py          -> quoted buying signals + outreach email draft (Groq)
  logic.py       -> score, segment, next action, email checks
  db.py          -> run, results and rejected rows saved in SQLite
The pages read everything back from the database; /dashboard is the admin view.
"""

import csv
import io
import os

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for

import ai
import db
import logic
from validation import COMPANY_TYPES, FIELDS, LEAD_SOURCES, PRODUCTS, clean_row, validate_batch

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-only-secret")

db.init_db()


def next_lead_id(leads):
    """Next free ID in the L001 pattern."""
    numbers = [int(l["lead_id"][1:]) for l in leads if l["lead_id"][1:].isdigit()]
    return f"L{(max(numbers, default=0) + 1):03d}"


# ---------- Leads waiting to be scored ----------

@app.route("/")
def index():
    leads = db.get_pending()
    return render_template(
        "index.html",
        leads=leads,
        company_types=COMPANY_TYPES,
        products=PRODUCTS,
        lead_sources=LEAD_SOURCES,
        next_id=next_lead_id(leads),
    )


@app.post("/leads")
def add_lead():
    lead = clean_row(request.form)
    if not lead["lead_id"]:
        lead["lead_id"] = next_lead_id(db.get_pending())
    db.add_pending(lead)
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
    db.replace_pending(rows)
    flash(f"Loaded {len(rows)} leads from {file.filename}.", "ok")
    return redirect(url_for("index"))


@app.post("/clear")
def clear_leads():
    db.clear_pending()
    flash("Cleared all leads.", "ok")
    return redirect(url_for("index"))


# ---------- Backend API ----------

@app.post("/api/score")
def api_score():
    """Score a batch of leads sent as JSON: {"leads": [ {...}, ... ]}."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("leads"), list):
        return jsonify({"error": 'Send JSON like {"leads": [ ... ]}.'}), 400
    if not body["leads"]:
        return jsonify({"error": "No leads to score. Add or upload leads first."}), 400

    valid, rejected = validate_batch(body["leads"])
    ai_results, ai_status = ai.generate(valid)
    scored = logic.score_batch(valid, ai_results)
    run_id = db.save_run(len(body["leads"]), scored, rejected, ai_status)

    return jsonify({
        "run_id": run_id,
        "received": len(body["leads"]),
        "valid_count": len(valid),
        "rejected_count": len(rejected),
        "ai_status": ai_status,
        "results": [{
            "lead_id": r["lead"]["lead_id"],
            "company": r["lead"]["company"],
            "score": r["score"],
            "segment": r["segment"],
            "next_action": r["next_action"],
            "email_source": r["email_source"],
        } for r in scored],
        "rejected": rejected,
        "results_url": url_for("view_run", run_id=run_id),
    })


@app.get("/api/runs/<int:run_id>")
def api_run(run_id):
    """Read a saved run back from the database as JSON."""
    run = db.load_run(run_id)
    if run is None:
        return jsonify({"error": f"Run {run_id} not found."}), 404
    return jsonify(run)


# ---------- Results and history pages ----------

@app.route("/results")
def results():
    run_id = db.latest_run_id()
    if run_id is None:
        return render_template("results.html", run=None, outcomes=db.OUTCOMES)
    return redirect(url_for("view_run", run_id=run_id))


@app.route("/runs/<int:run_id>")
def view_run(run_id):
    run = db.load_run(run_id)
    if run is None:
        abort(404)
    return render_template("results.html", run=run, outcomes=db.OUTCOMES)


@app.route("/history")
def history():
    return render_template("history.html", runs=db.list_runs())


@app.post("/results/<int:result_id>/outcome")
def record_outcome(result_id):
    run_id = db.run_id_for_result(result_id)
    if run_id is None:
        abort(404)
    outcome = request.form.get("outcome", "")
    if outcome == "clear":
        db.clear_outcome(result_id)
    elif outcome in db.OUTCOMES:
        db.set_outcome(result_id, outcome)
    else:
        flash("Pick one of the outcome buttons.", "error")
    return redirect(url_for("view_run", run_id=run_id) + f"#result-{result_id}")


# ---------- Admin ----------

@app.route("/dashboard")
def dashboard():
    return render_template(
        "dashboard.html", stats=db.dashboard_stats(), outcomes=db.OUTCOMES,
        base_prices=logic.BASE_PRICES, matrix=logic.MATRIX, bands=logic.VOLUME_BANDS,
        company_fit=logic.COMPANY_FIT, source_points=logic.SOURCE_POINTS,
        signal_points=logic.SIGNAL_POINTS, segments=logic.SEGMENTS,
    )


@app.post("/runs/<int:run_id>/delete")
def delete_run(run_id):
    if db.delete_run(run_id):
        flash(f"Deleted run #{run_id} and its results.", "ok")
    else:
        flash(f"Run #{run_id} was not found.", "error")
    return redirect(url_for("history"))


@app.get("/export.csv")
def export_csv():
    rows = db.export_rows()
    out = io.StringIO()
    columns = ["run_id", "created_at", "lead_id", "contact_name", "role", "company", "company_type",
               "product", "annual_volume_tonnes", "indicative_price_usd_kg", "lead_source",
               "score", "segment", "next_action", "email_source", "outcome"]
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=scored_leads.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
