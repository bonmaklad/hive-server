# Branch Policy

## Non-Negotiable Rules

- Never push directly to `main`.
- Never push directly to `master`.
- Default pull target is `dev` in 9 out of 10 cases.
- Default push target is `dev` unless ticket workflow requires a dedicated branch.
- Branch creation and push decisions belong to Release Manager.

## Branch Selection Order

1. Existing ticket branch explicitly provided by process
2. Existing `dev` branch
3. Existing `develop` branch
4. New ticket branch created from the safest non-production base
5. If only `main` or `master` exists, create a new non-production branch from it and push there

## New Branch Naming

Use a clear prefix and ticket identifier:

- `feature/<ticket-id>-<slug>`
- `bugfix/<ticket-id>-<slug>`
- `chore/<ticket-id>-<slug>`
- `release/<date>-<ticket-id>-<slug>`

Example:

- `bugfix/abc-142-fix-null-customer-id`

## Pull and Push Protocol

1. Fetch all remotes.
2. Inspect available branches.
3. Select the safest valid non-production branch.
4. Pull latest changes before starting work.
5. Push only after QA has passed.

## Release Manager Decision Notes

Release Manager records:

- chosen base branch
- chosen push branch
- why the branch was selected
- whether the branch was created during this run
