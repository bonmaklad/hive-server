# Supabase Execution Model

## Queue Query

The runner reads tickets from `surge_devops` where:

- `status IN (submitted, analysis, development, testing)`

The runner orders work by:

1. `priority` using `high`, `medium`, `low`
2. `created_at` ascending

Only the first unlocked ticket per client is selected in a run.

## Client Resolution

`surge_devops.client_id` joins to `surge_clients.id`.

`surge_clients.slug` maps directly to:

```text
/opt/surgecodex/clients/{slug}
```

This mapping is hard-coded.

## Notes

Each stage writes a note into `surge_devops_notes`:

- Product Owner
- Analyst
- Developer
- QA/Tester
- Release Manager

Notes are internal by default, authored by the role-specific Supabase user, and include:

- stage
- short outcome
- short next step
- work package path

Notes must stay succinct.
They should read like brief delivery updates, not analysis dumps.
Use the prefix format `Name (Title):` before the comment body.

## Attachments

The runner downloads attachments from `surge_devops_attachments` into:

```text
runtime/{slug}/tickets/{ticket-number}/attachments/
```

Image attachments may be passed directly to Codex as prompt images.

Video and other attachments are downloaded and catalogued, but advanced media processing depends on external tooling not yet installed.

## Work Package

Each ticket creates:

```text
runtime/{slug}/tickets/{ticket-number}/
  ticket.json
  attachments.json
  media-summary.md
  01-intake.md
  02-design.md
  03-analysis.md
  04-dev-plan.md
  05-qa-report.md
  06-release.md
  responses/
```

## Client Lock

The runner creates:

```text
runtime/{slug}/state/active-ticket.json
```

If the file already exists for a different ticket, the client is skipped for that run.

## Status Flow

- `submitted` runs Product Owner
- `triaged` holds tickets that need clarification and returns them to the queue after a later human note/comment arrives
- `analysis` runs Analyst
- `development` runs Developer
- `testing` runs QA/Tester
- successful QA then runs Release Manager and moves the ticket to `awaiting_client`

If Product Owner determines the submitted `kind` is wrong, the runner updates `surge_devops.kind` to the normalized assessment before moving the ticket forward.

The runner must sync the client repo before any stage work starts.
If repo sync fails, the ticket must not advance to the next stage.
Exception: if a ticket is already in `development` or `testing` and the client repo has uncommitted changes from that active ticket, the runner reuses the dirty worktree and skips fetch/pull for that run.

## Failure Handling

### Normal Failure

- write an internal note
- clear the active ticket lock
- leave the DB status unchanged unless the stage explicitly moved the ticket
- QA `fail` returns the ticket to `development` so cron can retry it on a later run using the tester's note as the handoff

### Major Failure

If `SURGE_ALLOW_HARD_RESET=true`, the runner may perform:

- `git reset --hard HEAD`
- `git clean -fd`

This is intended only for client repos under `/opt/surgecodex/clients/{slug}`.
