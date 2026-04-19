---
name: job-radar
description: Python-first job search pipeline. Use when the user wants to scan portals, triage offers, evaluate a JD, apply, render resume/cover PDFs, track contacts and outreach, or run follow-ups. Aliased as `jr`. Also performs Claude-Code-side LLM inference for queue dirs at `private/llm-queue/`.
---

# job-radar skill

You assist with the `job-radar` pipeline. Python does all deterministic
work (scanning, dedup, keyword screening, PDF rendering, DB queries). You
are called on for two things:

1. **Judgment** — archetype tagging, A-F+G evaluations, follow-up drafts,
   interview prep, nudging the user toward the right next action.
2. **Inference for queued packets** — when the user runs a `jr` command
   without an `ANTHROPIC_API_KEY`, Python writes prompt packets to
   `private/llm-queue/{operation}-{ts}/` and exits. You read those
   packets, perform the inference yourself (paid for by the Max plan),
   and write `result-{id}.json` files next to them. The user then re-runs
   `jr <op> --ingest <dir>` to fold the results into SQLite.

## Ground rules

- Never regenerate work Python already did. If the user pastes a JD, pipe
  it through the CLI (`jr scan` or stdin) rather than parsing by hand.
- Personal files are under `private/` (gitignored). Never write personal
  data anywhere else.
- Canonical statuses: `SKIP | Discarded | Rejected | Evaluated | Applied | Responded | Interview | Offer`.
- Scores are on 0.0-5.0. Below 4.0 → recommend against applying unless
  the user has a specific reason.
- Quality over speed. One well-targeted application beats ten generic ones.

## LLM plane: queue/ingest pattern

Every LLM-consuming `jr` command (`triage`, `eval`, `interview`,
`research`, `offer`, `thanks`, `inbox paste --draft`, `learn keywords`,
`followup --draft`, `outreach`, `answers`, learn-rejections) supports
two backends:

- **Direct** — `ANTHROPIC_API_KEY` set → Anthropic SDK call, written
  back to DB inline.
- **Queue** — no API key (or `--prepare`) → writes a queue dir, exits,
  waits for you to consume it.

A queue dir contains:

```
private/llm-queue/{operation}-{ts}/
    manifest.json    # operation, model_hint, result_schema, items[]
    system.md        # cached system prompt (read this once)
    packet-{id}.md   # one user prompt per item
    result-{id}.json # ← you write these
    consumed.flag    # written by --ingest after the user folds results in
```

`manifest.json["items"]` looks like:

```json
{
  "id": "42",
  "packet": "packet-42.md",
  "result": "result-42.json",
  "meta": {"job_id": 42, "company": "Foo"},
  "max_tokens": 512
}
```

`manifest.json["result_schema"]` (when present) is the JSON shape your
output must match — validate against it before writing.

### How to consume a queue (the `/jr consume` flow)

When the user invokes `/jr consume <queue_dir>` (or asks you to "process
the queue"), do this:

1. **Read** `<queue_dir>/manifest.json` and `<queue_dir>/system.md`.
2. For **each item** that doesn't already have its `result-{id}.json`:
   1. Read `<queue_dir>/<item.packet>`.
   2. Treat `system.md` as the system prompt and the packet body as the
      user message. Use the operation's natural model
      (`manifest.model_hint` is a suggestion, not a requirement).
   3. Produce a JSON object that satisfies `manifest.result_schema` (if
      set). When the schema demands fields you can't infer, return your
      best guess plus a `"notes"` field — don't refuse.
   4. Write the JSON to `<queue_dir>/<item.result>`. Pretty-print is
      fine; the ingester strips fences.
3. **Tell the user** when you're done: print the `--ingest` command
   they should run next.

If a result file already exists, skip that item — don't overwrite.

### Operation cheat sheet (what to put in result JSONs)

| Operation | Required result keys |
|-----------|----------------------|
| `triage` | `verdict` (`pass`/`review`/`skip`), `score_0_5`, `rationale`, optional `archetype` |
| `evaluate` | `report_md` (full A-F+G markdown), `score_0_5`, `archetype`, `comp_band_fit` |
| `research` | `funding`, `headcount`, `signals[]`, `risks[]`, optional `summary_md` |
| `interview` | `prep_md`, `topics[]`, `recent_questions[]`, `red_flags[]` |
| `offer` | `strengths[]`, `risks[]`, `counter_script_md`, optional `compare_to` |
| `thanks` | `subject`, `body_md` |
| `inbox.paste` (draft) | `draft_md`, `intent`, `next_action` |
| `outreach` | `subject`, `body_md`, `tone` |
| `answers` | `answers` (object keyed by question_id, each `{question, answer_md}`) |
| `rejection_reason` | `category` (one of: location, comp, level, stack, culture, timing, fit), `detail` |
| `learn_keywords` | `add_positive[]`, `add_negative[]`, `rationale` |

If the schema in `manifest.json` differs, the schema wins.

### Smoke test

`jr echo "hello"` is a tiny round-trip: it queues one `echo` operation
expecting `{"echo": "<text>"}`. Useful for testing the loop.

## When to call which model

- **Haiku** — triage, follow-up drafts, classifying inbound emails,
  extracting contact fields, rejection-reason normalization.
- **Sonnet** — `jr eval` A-F+G reports, interview prep, research.
- **Opus** — offer comparison, negotiation, in-depth interview prep
  (user explicitly requests).

## Routing

| User intent | Command |
|-------------|---------|
| Scan portals | `jr scan` |
| Triage review bucket | `jr triage` |
| Process a queue dir | `/jr consume <dir>` (you do the inference) |
| Inspect queues | `jr queue ls` / `jr queue show <dir>` |
| Smoke-test queue plane | `jr echo "hi"` |
| Evaluate a specific job | `jr eval <job_id>` |
| Start an application | `jr apply <job_id>` |
| Rebuild PDFs | `jr render <app_id>` |
| Log a touchpoint | `jr touch <app_id> --channel X --direction Y -m "..."` |
| Show follow-up queue | `jr followup` |
| Draft a follow-up | `jr followup --draft <app_id>` |
| Export markdown views | `jr export` |
| Migrate from career-ops | `jr import career-ops <path>` |

## Before recommending "apply"

1. Confirm the job's triage verdict is `pass` or `review` (not `skip`).
2. Confirm legitimacy is not `low`.
3. Check comp against the user's band.
4. Remind the user you NEVER submit applications for them — you only
   prepare materials.

## First-run

If `private/profile.yml` is missing, the user hasn't run `jr init`. Walk
them through it:

1. `jr init` — seeds `private/` from `templates/`
2. Edit `private/profile.yml`, `private/keywords.yml`, `private/cv.md`
3. `jr db migrate`
4. `jr scan`

## Worked example: triage queue

```text
$ jr triage --prepare
queued 10 packets → private/llm-queue/triage-20260418-103015/
sliced top 10 of 47 — rerun with --limit 25 for more, or --all to include everything.
Next: ask Claude Code to run /jr consume private/llm-queue/triage-20260418-103015/, then jr triage --ingest private/llm-queue/triage-20260418-103015/.

$ # user → /jr consume private/llm-queue/triage-20260418-103015/
$ # you → for each packet, write result-{id}.json with {verdict, score_0_5, rationale}
$ # tell user when done → "10 results written; run jr triage --ingest <dir>"

$ jr triage --ingest private/llm-queue/triage-20260418-103015/
Ingested 10 triage results
ingest complete — private/llm-queue/triage-20260418-103015/
```
