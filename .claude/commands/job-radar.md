---
description: job-radar — Python-first job pipeline. Scan, screen, triage, apply, track.
argument-hint: "[scan|triage|show ID|eval ID|apply ID|render ID|status|contact|touch|followup|jd|export|db|import|init] [...]"
---

Invoke the `job-radar` skill. Route the user's request to the right `jr`
subcommand:

- No args, or "help" → explain the CLI surface below and ask what they want
  to do.
- "scan" / "scan portals" → `jr scan` (optional `--portal NAME`)
- "triage" → `jr triage`
- "show 42" or "job 42" → `jr show 42`
- "eval 42" or "evaluate 42" → `jr eval 42`
- "apply 42" → `jr apply 42`
- "render 17" → `jr render 17`
- "status" → `jr status`
- "contacts" → `jr contact list`
- "add contact" → `jr contact add`
- "touch 17 email outbound 'note'" → `jr touch 17 --channel email --direction outbound -m 'note'`
- "followup" → `jr followup` (add `--draft APP_ID` for a Haiku draft)
- "archive old jds" → `jr jd archive --older-than 90`
- "export" → `jr export`
- "migrate db" → `jr db migrate`
- "import career-ops /path" → `jr import career-ops /path`

User's request: $ARGUMENTS
