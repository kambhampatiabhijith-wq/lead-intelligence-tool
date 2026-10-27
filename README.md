# Lead Intelligence Tool

A full-stack workflow tool for B2B instant coffee sales. A sales rep enters a lead (contact, company type, product, annual volume, indicative price, lead source and what the buyer said) and gets back a score out of 100, a segment, a next action and a personalised outreach email, with every point explained.

The AI reads the buyer's words and drafts the email. Fixed rules in a separate logic layer decide the score, so the same lead always gets the same result.

Full write-up: [`docs/documentation.pdf`](docs/documentation.pdf)

## System flow

```
Form or CSV  ->  Frontend (Flask pages)  ->  POST /api/score
             ->  validation.py   rejects bad rows with a plain-English reason
             ->  ai.py           Groq reads the context: quoted signals + email draft
             ->  logic.py        score, segment, next action, email checks
             ->  db.py           run, results, rejected rows, outcomes in SQLite
             ->  Results page, History page, Admin dashboard
```

Every AI signal must quote the buyer's words, and the code checks each quote against the original text. Claims it can't find are dropped. If there's no API key or the AI fails, the rules still score every lead and a template email is used.

## Scoring (out of 100)

| Part | Max | Based on |
|---|---|---|
| Commercial value | 40 | Volume x price matrix (below) |
| Company fit | 10 | FMCG 10, Brand owner 9, Manufacturer 8, Retailer 6, Trader 5 |
| Authority | 15 | Decision-maker 15, influencer 9, other 3 |
| Intent | 35 | Lead source (Inbound 15, Referral 12, Trade show 8, Cold 3) + AI signals: need +8, timeline within 6 months +7, sample request +5 |
| Risk | -20 | AI signals: tied to another supplier -10, no budget this year -10 |

### Volume x price matrix

| Annual volume | At or above base price | Below base price |
|---|---|---|
| Large (100 t+) | 40 | 10 |
| Medium (25-99 t) | 30 | 6 |
| Small (5-24 t) | 18 | 3 |
| Micro (under 5 t) | 8 | 0 |

Base prices are **illustrative placeholders** (spray-dried $8.00, agglomerated $9.00, freeze-dried $14.00 per kg), not real commercial prices.

**Segments:** Hot 70+, Warm 45-69, Nurture 25-44, Low under 25.

**Overrides:** a Hot or Warm lead that is Medium or Large and priced below base is escalated for pricing approval; a Hot or Warm lead whose contact isn't a decision-maker gets "ask for an introduction".

## Running it

1. Add a Groq API key in Replit **Secrets** as `GROQ_API_KEY` (or an OpenAI key as `OPENAI_API_KEY`).
2. Install Flask (`pip install -r requirements.txt`) and run `python main.py`. The app listens on port 8000.
3. Upload `sample_leads.csv` and click **Score leads**.

Optional settings: `GROQ_MODEL`, `DB_PATH` (default `leads.db`), `SESSION_SECRET`.

## Pages and API

| Path | What it is |
|---|---|
| `/` | Add leads by form or CSV, then score them |
| `/runs/<id>` | Results for one saved run: score breakdown, next action, email, outcome buttons |
| `/history` | Every saved run, delete a run, CSV export |
| `/dashboard` | Admin view: usage, conversion by segment, AI health, follow-ups, current rules |
| `POST /api/score` | Validate, run AI, score and save a batch of leads (JSON in, JSON out) |
| `GET /api/runs/<id>` | A saved run as JSON |

## Sample data

`sample_leads.csv` has seven leads, each built to test one rule:

| Lead | Tests | Expected result |
|---|---|---|
| L001 | Large volume above base, strong intent | Hot, 100 |
| L002 | Tied to another supplier | Warm, 48 |
| L003 | Medium volume below base price | Warm, 53, escalate for pricing approval |
| L004 | Contact is not the decision-maker | Warm, 65, ask for an introduction |
| L005 | Student enquiry, tiny volume | Low, 20 |
| L006 | Large volume below base, no budget | Nurture, 26 |
| L007 | Price missing | Rejected by validation |

These assume the AI finds the signals the context states. Without the AI the same file scores L001 80, L002 50, L004 50, L003 38, L006 36 and L005 20.

## Project files

| File | What it does |
|---|---|
| `main.py` | Flask routes: pages, API, admin actions |
| `validation.py` | Input checks |
| `ai.py` | Prompt, Groq call, retries, quote checks |
| `logic.py` | Scoring, segments, next actions, email checks |
| `db.py` | SQLite tables, saving and loading runs, dashboard numbers |
| `templates/`, `static/` | Pages and styles |
| `docs/documentation.pdf` | Architecture, frontend, backend and logic layer, database, dashboard, commit history |
