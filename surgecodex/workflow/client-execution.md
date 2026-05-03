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

## Working Artefacts

Each ticket should get a work package under:

```text
runtime/<client>/tickets/<ticket-id>/
```

Recommended artefacts:

- `01-intake.md`
- `02-analysis.md`
- `03-dev-plan.md`
- `04-qa-report.md`
- `05-release.md`

## Preconditions

Before starting work on a ticket:

1. Identify the client.
2. Confirm no other ticket is active for that client.
3. Confirm the local repo path for that client.
4. Pull latest branch state.
5. Record the ticket as active.
