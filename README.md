# job-radar

Python-first job search pipeline. Scan portals, dedup, screen, triage, apply,
track contacts, prep interviews, evaluate offers — with Claude only doing the
judgment work.

**Why:** hand-rolled LLM loops burn tokens re-reading the same CV and comp
data for every job. `job-radar` does the deterministic work in Python
(scanning, hashing, dedup, keyword screening, comp lookup, PDF rendering)
and reserves paid LLM calls for genuine judgment (archetype tagging, A–F
evaluation, interview prep, offer negotiation).

Both `job-radar` and `jr` are installed — use whichever is faster to type.

## Commands

### Setup
- `jr init` — seed `private/` from templates, migrate DB
- `jr import career-ops <path>` — one-shot migration from a career-ops install
- `jr migrate-portals <path>` — port 500+ company configs (auto-infers ATS source)
- `jr portals discover` — Playwright pass to detect ATS embeds on company careers pages

### Discovery (all zero-LLM)
- `jr scan [--portal X] [--limit N]` — pull portals, dedup by hash, save JDs, pre-screen
- `jr liveness` — detect 404/closed postings, auto-archive dead ones

### Add a single posting (zero-LLM)
- `jr add <url>` — fetch one JD URL, parse, screen, drop into review bucket

### Screening (Haiku)
- `jr triage` — Haiku pass on pre-screen review bucket (tier-0 auto-advances obvious pass/skip)
- `jr triage --batch submit` — queue triage via Messages Batch API at 50% cost (polls with `--batch poll`)
- `jr triage --rank debug` — preview the ranked list without spending tokens
- `jr show <job_id>` — print JD + screen + triage verdict

### Deep-dive (Sonnet)
- `jr eval <job_id>` — full A–F+G evaluation report
- `jr research <job_id>` — company scouting report (size, funding, signals)

### Applying
- `jr apply <job_id> [--referral <contact_id>]` — create application, branch resume + cover; cached `app_answers` reused
- `jr render <app_id>` — regenerate resume.pdf / cover.pdf
- `jr answers <app_id>` — draft answers to common application questions, cached per-app

### Interview & offer (Sonnet → Opus)
- `jr round add <app_id>` — log a scheduled interview round
- `jr round list <app_id>` — show interview timeline
- `jr round update <round_id>` — mark completed/cancelled/outcome
- `jr round questions <round_id>` — capture questions asked (zero LLM); reused as context by `jr interview`
- `jr interview <app_id>` — Sonnet interview prep report
- `jr thanks <round_id>` — draft thank-you note (Haiku); `jr thanks --due` to bulk-draft every completed-but-unsent round
- `jr offer <app_id>` — Opus offer eval + counter-script

### CRM / outreach
- `jr inbox paste [--file] [--app N] [--draft]` — paste LinkedIn/email thread; Haiku extracts + optionally drafts reply
- `jr inbox draft <touch_id>` — draft a reply for an existing touchpoint
- `jr inbox email <path>` — ingest .eml/.mbox file
- `jr call` — interactive recruiter-call logger
- `jr contact add|list|show` — contacts CRM
- `jr touch <app_id> --channel X --direction Y -m "..."` — log touchpoint manually
- `jr followup [--draft APP_ID | --draft-all]` — show follow-up queue; Haiku draft single or all due
- `jr outreach --contact N|--company X --kind ... --ask ...` — short cold-outbound DM/email draft

### Learning & reporting
- `jr patterns` — conversion analysis by archetype/remote/company; rejection-reason breakdown; ghost-company flags
- `jr learn keywords` — interactive keyword-learning loop from outcome history
- `jr learn rejections` — extract structured `rejection_reasons` (location/comp/level/...) from rejected app notes
- `jr brief` — morning report: due follow-ups, upcoming rounds, new passes, ghost companies, costs
- `jr status` — tracker overview
- `jr dash` — static HTML dashboard with click-through `file://` links to every artifact
- `jr costs [--since N]` — token + $ telemetry
- `jr export` — regenerate markdown views under `private/exports/`

### Portals admin
- `jr portals ls` — list portals with enabled / ghosted status
- `jr portals disable <name>` / `jr portals enable <name>`
- `jr portals ghost-cooldown <name> [--days 180]` — temporarily skip a 0%-conversion portal; auto-re-enables on date

### LLM queue / Claude Code Max plan
- `jr queue ls` — list pending and consumed queue dirs
- `jr queue show <dir>` — pretty-print a manifest
- `jr echo "text"` — smoke-test the queue/ingest round-trip
- `<op> --prepare` — force queue mode (write packets, exit)
- `<op> --ingest <dir>` — fold result-*.json files back into the DB

### Lifecycle
- `jr jd list [--state active|archived|applied]`
- `jr jd archive [--older-than 90]`
- `jr jd purge [--older-than 365]`
- `jr db migrate|backup|query "<sql>"`

## LLM plane: API key vs Claude Code Max plan

Every LLM-emitting `jr` command supports two backends, picked automatically:

- **Direct API** — when `ANTHROPIC_API_KEY` is set, calls go to the Anthropic
  SDK and write back inline. Same as before.
- **Queue / Max plan** — without an API key, `jr <op>` writes a queue
  directory under `private/llm-queue/{operation}-{timestamp}/` containing a
  manifest, a cached system prompt, and one user-prompt packet per item.
  You run `/jr consume <dir>` from Claude Code (paid for by the Max plan),
  which writes a `result-{id}.json` next to each packet. Then `jr <op> --ingest <dir>`
  folds the structured results back into SQLite.

You can force the queue path with `--prepare` even when you have an API
key, e.g. when you want to inspect or replay packets. Pre-ranking is
mandatory: every command pre-ranks candidates in pure Python (`screen_score`,
keyword positives, recency) and slices to `--limit 10` by default — pass
`--all` to opt into everything, `--rank debug` to preview the rank.

## Cost strategy (why this beats hand-rolled LLM loops)

| Layer | Who does it | Tokens |
|---|---|---|
| Scan portals (Greenhouse/Ashby/Lever/Workable) | Python | 0 |
| URL + content-hash dedup | Python | 0 |
| HTML → markdown + JD field extraction | Python | 0 |
| Keyword + dealbreaker pre-screen | Python | 0 |
| Tier-0 auto-advance (high-confidence pass/skip) | Python | 0 |
| Haiku triage (ambiguous middle) | Haiku | ~1.5K/job |
| Batch triage via Messages Batch API | Haiku | ~0.75K/job (50% off) |
| A–F+G deep eval | Sonnet | ~8K/job, only on advanced jobs |
| Interview prep | Sonnet | once per app, before loops |
| Company research | Sonnet | auto-offered ≥4.0 triage |
| Offer eval + counter | Opus | rare, high-value |
| Thank-you / reply drafts | Haiku | ~400 tok |

System prompts are marked `cache_control: ephemeral` so a batch of triages
pays full input cost once and cache-read rate (~10×) after that.

## Architecture

- **SQLite** at `private/data/career.db` — structured data
- **Markdown exports** under `private/exports/` — human/agent-readable views, regenerated on demand
- **Per-application dir** at `private/applications/{id}-{slug}/` — resume, cover, PDFs, JD freeze, report, interview prep, offer eval, notes
- **All personal data** lives under `private/` — one `.gitignore` line covers everything

## Install

```
pip install -e .
# for playwright-based scanners (Workable, deep-crawl, portals discover):
pip install -e '.[playwright]'
playwright install chromium
```

Then `jr init` to set up `private/` from the examples.

## Migrating from career-ops

```
jr init
jr import career-ops /path/to/career-ops
jr migrate-portals /path/to/career-ops
jr portals discover       # optional: upgrades manual entries to ATS sources
jr db migrate
jr scan --limit 20        # first live scan
jr triage                 # tier-0 auto-advances most; Haiku picks up the ambiguous
jr dash                   # open the dashboard
```

After that you can delete the career-ops checkout — all applications, JDs,
reports, and the portal list have been ported.

## Credit

Inspired by [career-ops](https://github.com/santifer/career-ops) by santifer.
This is a rebuild focused on cutting Claude token cost and adding first-class
contacts CRM, interview-round tracking, offer negotiation, and a static
dashboard.
