# Branch Policy

## Non-Negotiable Rules

- Never push directly to `main`.
- Never push directly to `master`.
- Default pull target is `dev` in 9 out of 10 cases.
- Task branches are internal execution branches and should not require manual management.
- Default push target is `dev` unless ticket workflow requires a dedicated branch.
- Branch creation and push decisions belong to Release Manager.
- Customer-facing intake classification must happen before branch work begins.

## Branch Selection Order

1. Existing ticket integration branch for the active ticket
2. Existing `dev` branch for release target
3. Existing `develop` branch for release target
4. New non-production release branch created from the safest production base
5. If only `main` or `master` exists, create `dev` from it and push there

## New Branch Naming

Use a clear prefix and ticket identifier:

- `feature/<ticket-id>-<slug>`
- `bugfix/<ticket-id>-<slug>`
- `chore/<ticket-id>-<slug>`
- `release/<date>-<ticket-id>-<slug>`
- `feature/ticket-<ticket-id>-integration`
- `bugfix/ticket-<ticket-id>-integration`
- `chore/ticket-<ticket-id>-integration`

Example:

- `bugfix/abc-142-fix-null-customer-id`

## Pull and Push Protocol

1. Fetch all remotes.
2. Create or update the ticket integration branch.
3. Create isolated task branches or worktrees from the integration branch.
4. Integrate approved task commits back into the integration branch.
5. Merge the integration branch into the chosen release branch.
6. Push only after QA has passed.

## Release Manager Decision Notes

Release Manager records:

- chosen base branch
- chosen push branch
- why the branch was selected
- whether the branch was created during this run
