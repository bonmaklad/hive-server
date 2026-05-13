# SurgeCodex

SurgeCodex is a multi-client agentic delivery system built around a strict workflow:

- one active ticket per client
- many clients in parallel
- many task workers in parallel inside one active ticket
- no direct pushes to `main` or `master`
- workflow state stored outside client repos

It is designed to let you keep branch safety and auditability while still getting parallel development throughput inside a single ticket.

## How It Works

1. Product Owner validates the ticket and confirms the client.
2. If the ticket is a change request and is viable, Designer shapes it into the current product before analysis.
3. Analyst writes the requirements and splits the change into a task graph.
4. The runner creates a ticket integration branch automatically.
5. Ready tasks run in parallel in isolated Git worktrees.
6. Each task goes through Developer, then Peer Reviewer.
7. Approved task commits are integrated back into the ticket branch automatically.
8. QA verifies the full ticket result.
9. Release Manager merges the ticket branch into `dev` or another safe non-production branch and pushes it.

You do not need to manually manage the internal task branches. The system does that.

## Features

- Supabase-backed queue runner
- one-ticket-per-client locking
- strict intake classification for bugs, issues, and change requests
- `triaged` clarification and human-review state for blocked automatic work
- automatic QA retry cap with `triaged` fallback for stuck tickets
- designer stage for approved feature work
- analyst-driven task decomposition
- machine-readable task state in `tasks.json`
- isolated task worktrees per worker
- peer review pass before integration
- automatic integration back into the ticket branch
- automatic merge into a safe release branch
- attachment download and media summary support
- role-specific prompts and schemas

## Repo Layout

- `AGENTS.md`
  concise explanation of the agent roles and execution model
- `agents/`
  role definitions for Product Owner, Designer, Analyst, Developer, Peer Reviewer, QA Tester, and Release Manager
- `workflow/`
  operating model, branch policy, client execution rules, and cron setup
- `schemas/`
  JSON schemas for structured role outputs
- `templates/`
  markdown templates for artifacts and reporting
- `scripts/`
  queue runner and Git helper scripts
- `clients/`
  client repository checkouts only
- `runtime/`
  ticket artifacts, locks, attachments, task state, and worktrees

## Runtime Layout

```text
clients/<client>/

runtime/<client>/
  state/
    active-ticket.json
  tickets/<ticket-number>/
    01-intake.md
    02-design.md
    03-analysis.md
    04-dev-plan.md
    05-qa-report.md
    06-release.md
    tasks.json
    tasks/<task-id>/
    worktrees/<task-id>/
```

## Main Entry Points

- `scripts/run_surge_queue.py`
  main queue runner
- `scripts/run_hourly_queue.sh`
  cron-friendly wrapper
- `scripts/git_sync_client.sh`
  safe client repo sync helper

## Required Configuration

Provide these through `.env`, `.env.local`, or environment variables:

- `NEXT_PUBLIC_SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SURGE_PRODUCT_OWNER_USER_ID`
- `SURGE_DESIGNER_USER_ID`
- `SURGE_ANALYST_USER_ID`
- `SURGE_DEVELOPER_USER_ID`
- `SURGE_PEER_REVIEW_USER_ID`
- `SURGE_QA_USER_ID`
- `SURGE_RELEASE_USER_ID`

Useful controls:

- `SURGE_MAX_PARALLEL_CLIENTS`
- `SURGE_MAX_PARALLEL_TASKS`
- `SURGE_MAX_QA_FAILURES`
- `SURGE_QUEUE_POLL_SECONDS`
- `SURGE_ALLOW_HARD_RESET`
- `SURGE_CODEX_SANDBOX`

## Current Assumptions

- client repos already exist under `clients/<slug>`
- GitHub auth is already configured for those repos
- workflow artifacts stay out of client repos and live under `runtime/`
- QA still happens after full task integration, not per task
- the Product Owner is the main scope-control gate for feature creep
