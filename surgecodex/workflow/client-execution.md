# Client Execution Rules

## Client Resolution

Every ticket must resolve to exactly one client subfolder before any analysis or development begins.

The client subfolder is the root operating boundary for:

- repository checkout
- branch activity

Working artefacts and ticket state are intentionally kept outside the client repository.

## Concurrency Rule

- One active ticket per client.
- Many clients may run concurrently.
- Many independent tasks may run concurrently inside one active ticket.
- `triaged` tickets pause automation while waiting for clarification, but the runner can re-queue them automatically after a later human note/comment answers the blocking question.

This prevents branch conflicts, state corruption, and ambiguous handoffs inside a single client codebase.

## Standard Client Folder Layout

```text
clients/<client>/

runtime/<client>/
  tickets/
  state/
```

## State Files

- `runtime/<client>/state/active-ticket.json` current ticket lock
- `runtime/<client>/state/history.md` optional audit trail
- `runtime/<client>/tickets/<ticket-id>/tasks.json` machine-readable task graph and execution state

## Working Artefacts

Each ticket should get a work package under:

```text
runtime/<client>/tickets/<ticket-id>/
```

Recommended artefacts:

- `01-intake.md`
- `02-design.md`
- `03-analysis.md`
- `04-dev-plan.md`
- `05-qa-report.md`
- `06-release.md`
- `tasks.json`
- `tasks/<task-id>/developer.md`
- `tasks/<task-id>/peer-review.md`

## Preconditions

Before starting work on a ticket:

1. Identify the client.
2. Confirm no other ticket is active for that client.
3. Confirm the local repo path for that client.
4. Pull latest branch state.
5. Create the ticket integration branch.
6. Record the ticket as active.
