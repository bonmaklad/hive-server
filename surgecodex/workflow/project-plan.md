# Project Plan

## Goal

Build a cron-safe, Codex-driven ticket processor that pulls submitted tickets from Supabase, works one ticket at a time per client, writes auditable artefacts and notes, and advances each ticket toward `awaiting_client` or `closed`.

## Phase 1

Implement the minimum viable execution loop:

- poll `surge_devops`
- sort submitted tickets by priority then age
- resolve the client slug from `surge_clients`
- enforce one active ticket per client
- sync the client repo with a hard-coded script
- download ticket attachments
- create a ticket work package
- invoke Codex for Product Owner, Analyst, Developer, QA, and Release Manager
- write Supabase notes at each stage
- update final ticket status

## Phase 1 Constraints

- The workflow root is hard-coded to `/opt/surgecodex`.
- Client repos must exist at `/opt/surgecodex/clients/{slug}`.
- Git sync must fail fast on any issue.
- Video transcription and frame extraction are not available until media tooling is installed.
- A hard reset capability exists but is disabled by default.

## Execution Outcomes

### Product Owner

- `analysis` when the ticket is real and actionable
- `triaged` when more information is needed before automation can continue
- `closed` when the ticket is not valid or not appropriate

### Analyst, Developer, QA, Release

- run only if Product Owner sends the ticket into `analysis`
- leave notes and artefacts for the next stage
- final successful outcome is `awaiting_client`

## Major Risks

- Unknown enum coverage in database status values
- Missing git auth for push workflows
- Missing local media tooling for video analysis
- Long-running Codex steps inside cron windows

## Next Phase

- add richer status mapping if the full enum set is confirmed
- install media tooling
- add ticket retry policies
- add structured telemetry and failure dashboards
