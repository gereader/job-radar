# job-radar — rejection-reasons mode

You're normalizing free-text rejection notes (or final email bodies)
from rejected applications into one or more structured `category +
detail` rows.

## Categories

Use exactly one of these per row. Pick the most specific:

- `location` — geographic mismatch (visa, time zone, on-site requirement
  the candidate can't meet).
- `comp` — salary, equity, or total comp mismatch.
- `level` — too senior or too junior for the role.
- `stack` — missing a specific required technology.
- `culture` — vibes, values, team-fit feedback.
- `timing` — role pulled, frozen, paused, headcount cut.
- `fit` — generic "not a fit" without a clearer reason.
- `other` — anything that doesn't slot in.

## Inputs you'll see

For each application:
- The candidate's archetype targets (for context only — don't blame them).
- The application status, applied_at date.
- The free-text `notes` field plus any saved touchpoint summaries.

## Output

A JSON object with one or more rows:

```json
{
  "rows": [
    {"category": "comp", "detail": "Their max was $190k; candidate target band starts at $220k."},
    {"category": "timing", "detail": "Role frozen for Q2."}
  ],
  "notes": "anything that didn't slot in"
}
```

Empty `rows` is allowed if the notes really say nothing actionable.
Detail is a single short sentence — it's the explanation a future
analytics view will show.
