# Triage (Haiku, ~1.5K tokens)

Input: a JD (already parsed to markdown), the pre-screen score + reasons, and
the user's profile/archetypes.

Output JSON exactly, no prose:

```json
{
  "verdict": "pass" | "review" | "skip",
  "score_0_5": 0.0,
  "archetype": "best-matching archetype name or null",
  "archetype_confidence": 0.0,
  "legitimacy": "high" | "medium" | "low",
  "legitimacy_reasons": ["short", "reasons"],
  "notes": "one sentence on what moved the score"
}
```

Rules:
- `verdict=skip` when the role clearly violates a dealbreaker or targets a
  level / stack / geo the user does not want. Be honest about "skip" — it's
  the point.
- `verdict=review` when the fit is real but something material is unclear
  (comp not stated, ambiguous remote policy, unusual level).
- `verdict=pass` only when the JD would merit a deep evaluation spend.
- `legitimacy=low` for ghost postings, reposts with no change, clearly
  scraped content, or >120-day-old reqs.
