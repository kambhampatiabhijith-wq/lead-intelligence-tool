# Lead Intelligence Tool

A workflow tool for B2B instant coffee sales. A sales rep enters a lead (who they are, their company, product, volume, price and what they said), and the tool returns a lead score, a segment, a personalised outreach message and the next action, with the reasons shown.

## System flow

1. **Frontend:** a form or CSV upload sends the lead to the backend API.
2. **Backend API:** validates the input.
3. **AI generation (Groq):** reads the free-text context, pulls out buying signals (each backed by a quote from the context) and drafts an outreach email.
4. **Smart logic layer:** scores the lead out of 100, assigns a segment, picks the next action and checks the AI's email.
5. **Database (SQLite):** stores every lead, result and outcome.
6. **Admin dashboard:** monitors volumes, segment mix, AI health and whether Hot leads convert.
7. **Structured output:** results shown as cards, never raw JSON.

If the AI is unavailable, the logic layer still scores every lead and a template email is used.

## Scoring (out of 100)

| Part | Max | Based on |
|---|---|---|
| Commercial value | 40 | Volume x price matrix (below) |
| Company fit | 10 | Company type: FMCG 10, Brand owner 9, Manufacturer 8, Retailer 6, Trader 5 |
| Authority | 15 | Contact's role |
| Intent | 35 | Lead source + AI signals: stated need, timeline within 6 months, sample request |
| Risk | -20 | AI signals: locked into a competitor's contract, no budget this year |

### Volume x price matrix

| Annual volume | At or above base price | Below base price |
|---|---|---|
| Large (100 t+) | 40 | 10 |
| Medium (25-99 t) | 30 | 6 |
| Small (5-24 t) | 18 | 3 |
| Micro (under 5 t) | 8 | 0 |

Base prices per product are **illustrative placeholders** for this prototype, not real commercial prices.

**Segments:** Hot 70+, Warm 45-69, Nurture 25-44, Low under 25.

## Sample data

`sample_leads.csv` has seven leads, each built to test one part of the logic.

| Lead | Company type | Tests | Expected result |
|---|---|---|---|
| L001 | FMCG | Large volume above base, strong intent | Hot, 100 |
| L002 | Brand owner | Competitor contract penalty | Warm, 48 |
| L003 | Trader | Medium volume below base price | Warm, 53, escalate for pricing approval |
| L004 | Manufacturer | Contact is not the decision-maker | Warm, 65, ask for an introduction |
| L005 | Retailer | Student enquiry, tiny volume | Low, 20 |
| L006 | Brand owner | Large volume below base, no budget | Nurture, 26 |
| L007 | FMCG | Price missing | Rejected by validation |

Without the AI, L003 drops to 38 (Nurture) because its need and timeline signals come from the context text.

## Build stages

1. README and sample leads
2. Input form and CSV upload
3. Backend API and validation
4. AI generation
5. Logic layer
6. Database and history
7. Admin dashboard
8. Documentation
