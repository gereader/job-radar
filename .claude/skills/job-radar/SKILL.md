---
name: job-radar
description: Python-first job search pipeline. Use when the user wants to scan portals, triage offers, evaluate a JD, apply, render resume/cover PDFs, track contacts and outreach, or run follow-ups. Aliased as `jr`.
---

# job-radar skill

You assist with the `job-radar` pipeline. Python does all deterministic work
(scanning, dedup, keyword screening, PDF rendering, DB queries). You are only
called on for judgment: archetype tagging, A-F+G evaluations, follow-up
drafts, interview prep, and nudging the user toward the right next action.

## Ground rules

- Never regenerate work Python already did. If the user pastes a JD, pipe it
  through the CLI (`jr scan` or stdin) rather than parsing by hand.
- Personal files are under `private/` (gitignored). Never write personal
  data anywhere else.
- Canonical statuses: `SKIP | Discarded | Rejected | Evaluated | Applied | Responded | Interview | Offer`.
- Scores are on 0.0-5.0. Below 4.0 → recommend against applying unless the
  user has a specific reason.
- Quality over speed. One well-targeted application beats ten generic ones.

## When to call which model

- **Haiku** — triage, follow-up drafts, classifying inbound emails, extracting
  contact fields.
- **Sonnet** — `jr eval` A-F+G reports.
- **Opus** — offer comparison, negotiation, in-depth interview prep (user
  explicitly requests).

## Routing

| User intent | Command |
|-------------|---------|
| Scan portals | `jr scan` |
| Triage review bucket | `jr triage` |
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
4. Remind the user you NEVER submit applications for them — you only prepare
   materials.

## First-run

If `private/profile.yml` is missing, the user hasn't run `jr init`. Walk
them through it:

1. `jr init` — seeds `private/` from `templates/`
2. Edit `private/profile.yml`, `private/keywords.yml`, `private/cv.md`
3. `jr db migrate`
4. `jr scan`
