"""AI generation: reads each lead's context and drafts outreach.

For every lead the AI returns:
  - buying signals, each backed by words quoted exactly from the context
  - a short outreach email
The code checks every quote against the original context and drops any
signal it can't find. Scoring is done by the logic layer, not the AI.
If no API key is set or the call fails, the app carries on without AI.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date

# Groq's API uses the same format as OpenAI's, so either provider works.
# Set GROQ_API_KEY (free tier) or OPENAI_API_KEY in Replit Secrets.
PROVIDERS = {
    "Groq": {
        "key_env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
    },
    "OpenAI": {
        "key_env": "OPENAI_API_KEY",
        "url": "https://api.openai.com/v1/chat/completions",
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    },
}
TEMPERATURE = 0.2
TIMEOUT_SECONDS = 60
# Small batches keep each reply short, so it is less likely to be cut off
# (which breaks the JSON) and stays inside free-tier token limits.
BATCH_SIZE = 3
MAX_ATTEMPTS = 3

SIGNALS = {
    "stated_need": "States a need",
    "timeline": "Buying within 6 months",
    "sample_request": "Asked for samples",
    "competitor_contract": "Tied to another supplier",
    "no_budget": "No budget this year",
}

SYSTEM_PROMPT = """You help a B2B sales rep at an instant coffee manufacturer (spray-dried, agglomerated and freeze-dried coffee, sold in bulk and as private label). For each lead you get the buyer's details and a free-text context of what they said.

For each lead return:
1. signals: facts the context states directly. Allowed types:
   - stated_need: the buyer says they need, want, are looking for or are launching something we could supply
   - timeline: the buyer gives a time to buy, launch or switch that falls within 6 months of today's date
   - sample_request: the buyer asks for samples
   - competitor_contract: the buyer is currently under contract with, or committed to, another supplier
   - no_budget: the buyer says there is no budget, or buying is on hold this year
   Each signal needs "quote": words copied exactly from the context (3 to 20 words). Report only what the context states. Never infer. A date that only says when a competitor contract ends is not a timeline. If the context is empty or neutral, return an empty list.
2. email: a first outreach email to the contact, with "subject" and "body". The body is 60 to 140 words, addresses the contact by first name, names their company, refers to the product and what they said, and ends with one clear next step (a call or sending samples). Do not quote prices, discounts or delivery dates. Sign off as "Sales team". Plain text, no placeholders in brackets.

Reply with JSON only, in this shape:
{"leads":[{"lead_id":"L001","signals":[{"type":"stated_need","quote":"..."}],"email":{"subject":"...","body":"..."}}]}"""


class AIError(Exception):
    """Raised when the LLM call fails or returns something unusable."""


def pick_provider():
    """Return (name, settings, api_key) for the first provider with a key set."""
    for name, settings in PROVIDERS.items():
        key = os.environ.get(settings["key_env"], "").strip()
        if key:
            return name, settings, key
    return None, None, ""


def build_payload(leads):
    return [{
        "lead_id": l["lead_id"],
        "contact_name": l["contact_name"],
        "role": l["role"],
        "company": l["company"],
        "company_type": l["company_type"],
        "product": l["product"],
        "annual_volume_tonnes": l["annual_volume_tonnes"],
        "lead_source": l["lead_source"],
        "context": l["context"],
    } for l in leads]


def _error_message(raw):
    try:
        return json.loads(raw)["error"]["message"][:200]
    except (ValueError, KeyError, TypeError):
        return raw[:200]


def _extract_json(text):
    """Pull the JSON object out of a reply that may have extra text around it."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


def call_llm(items, today, api_key, provider, name, json_mode=True):
    body = {
        "model": provider["model"],
        "temperature": TEMPERATURE,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"today": today.isoformat(), "leads": items})},
        ],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if "gpt-oss" in provider["model"]:
        body["reasoning_effort"] = "low"  # less hidden reasoning, more room for the answer
    request = urllib.request.Request(
        provider["url"],
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "lead-intelligence-tool/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        raise AIError(f"{name} returned error {e.code}: "
                      f"{_error_message(e.read().decode('utf-8', 'ignore'))}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError(f"couldn't reach {name} ({e})")

    try:
        parsed = _extract_json(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise AIError("the reply wasn't valid JSON")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("leads"), list):
        raise AIError("the reply didn't contain a list of leads")
    return parsed


# ---------- Evidence checks ----------

def _normalise(text):
    text = re.sub(r"[\"'“”‘’]", "", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_context(quote, context):
    """True only if the quote really appears in the lead's context."""
    q = _normalise(quote).rstrip(".")
    return len(q) >= 3 and q in _normalise(context)


def parse_entry(lead, entry):
    """Keep only verified signals and a well-formed email from one lead's AI output."""
    out = {"checked": entry is not None, "signals": [], "dropped": [], "email": None}
    if entry is None:
        return out

    seen = set()
    for s in entry.get("signals") or []:
        if not isinstance(s, dict):
            continue
        kind, quote = s.get("type"), str(s.get("quote") or "").strip()
        if kind not in SIGNALS or kind in seen:
            continue
        if not quote_in_context(quote, lead["context"]):
            out["dropped"].append(f"{SIGNALS[kind]}: quote not found in the context")
            continue
        seen.add(kind)
        out["signals"].append({"type": kind, "label": SIGNALS[kind], "quote": quote})

    email = entry.get("email")
    if isinstance(email, dict):
        subject = str(email.get("subject") or "").strip()
        body = str(email.get("body") or "").strip()
        if subject and body:
            out["email"] = {"subject": subject, "body": body}
    return out


def generate(leads, today=None):
    """Run the AI over valid leads. Returns (results_by_lead_id, status)."""
    today = today or date.today()
    status = {"used": False, "provider": None, "model": None, "message": ""}
    results = {l["lead_id"]: parse_entry(l, None) for l in leads}
    if not leads:
        status["message"] = "No valid leads to send to the AI."
        return results, status

    name, provider, key = pick_provider()
    if not key:
        status["message"] = "No AI key set (add GROQ_API_KEY in Secrets). Signals and emails were skipped."
        return results, status
    status.update(provider=name, model=provider["model"])

    by_id = {l["lead_id"]: l for l in leads}
    failed, last_error = [], None
    for start in range(0, len(leads), BATCH_SIZE):
        batch = leads[start:start + BATCH_SIZE]
        reply = None
        for attempt in range(MAX_ATTEMPTS):
            # If strict JSON mode was rejected, the next try asks without it
            # and the reply is parsed leniently.
            json_mode = not (last_error and "json" in str(last_error).lower() and attempt > 0)
            try:
                reply = call_llm(build_payload(batch), today, key, provider, name, json_mode=json_mode)
                break
            except AIError as e:
                last_error = e
                if " 429" in str(e):  # rate limit: wait a moment before retrying
                    time.sleep(3 * (attempt + 1))
        if reply is None:
            failed.extend(l["lead_id"] for l in batch)
            continue

        returned = {str(e.get("lead_id")): e for e in reply["leads"] if isinstance(e, dict)}
        for lead in batch:
            results[lead["lead_id"]] = parse_entry(by_id[lead["lead_id"]], returned.get(lead["lead_id"]))

    if len(failed) == len(leads):
        status["message"] = f"AI unavailable: {last_error}. Signals and emails were skipped."
        return results, status

    status["used"] = True
    dropped = sum(len(r["dropped"]) for r in results.values())
    status["message"] = f"AI read {len(leads) - len(failed)} of {len(leads)} leads with {name} ({provider['model']})."
    if failed:
        status["message"] += f" Not checked by the AI: {', '.join(failed)} ({last_error})."
    if dropped:
        status["message"] += f" {dropped} unsupported claim(s) dropped."
    status["failed"] = failed
    return results, status
