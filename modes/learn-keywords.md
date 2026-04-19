# Learn Keywords (Haiku)

You are proposing new `keywords.yml` rules based on outcome history.

Input: two corpora.
- `NEG` — titles + short body excerpts from jobs the user SKIPped / Rejected.
- `POS` — titles + short body excerpts from jobs the user Applied to,
  interviewed for, or got an offer from.

Output JSON only, exactly this shape:

```
{
  "add_negative": [
    {"term": "lowercase phrase", "field": "title|description|location|any",
     "weight": 4, "evidence": "short reason — which corpus / frequency"}
  ],
  "add_positive": [
    {"term": "lowercase phrase", "field": "any|title|description|location",
     "weight": 4, "evidence": "..."}
  ],
  "add_dealbreaker": [
    {"term": "lowercase phrase", "field": "description|location|any",
     "evidence": "..."}
  ],
  "retire": [
    {"term": "existing rule term", "reason": "never fired / misfires"}
  ],
  "notes": "one sentence on the biggest signal you saw."
}
```

Rules:
- Propose at most 5 in each list. Quality over quantity.
- A term qualifies as negative only if it appears in ≥3 NEG entries and
  absent from POS (or vice versa for positive).
- Dealbreakers are reserved for patterns that should *always* skip — think
  "security clearance required", "100% travel", specific geo restrictions.
- Terms must be short phrases (≤4 words). No sentences.
- If there isn't enough signal, return empty arrays. Don't invent.
