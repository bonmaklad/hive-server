# Operating Model

## Objective

Create a repeatable, auditable delivery workflow for multiple clients using Codex-driven specialist agents with clear role boundaries and handoffs.

## Core Principles

- Every ticket must be assigned to the correct client before work starts.
- One ticket at a time may be active for a single client.
- Different clients may progress at the same time.
- Every stage must produce an explicit artefact before handoff.
- Requirements must be written before coding starts.
- QA may reject work and force a rework loop.
- Release activity is separated from implementation activity.

## Delivery Stages

### 1. Product Owner

Purpose:

- Confirm the client.
- Confirm the work item is in scope.
- Classify the work item as bug, issue, or change request.
- Reject, defer, or pass the ticket onward.

Outputs:

- Intake decision
- Ticket classification
- Scope statement
- Initial assumptions and exclusions

### 2. Analyst

Purpose:

- Locate the relevant code area, feature, or requirement.
- Define current behaviour and desired behaviour.
- Produce quality requirements and BDD acceptance criteria.
- Decide whether validation should be automated or user-executed.

Outputs:

- Impacted code areas
- Relevant feature or requirement references
- As-is requirements
- To-be requirements
- BDD acceptance criteria
- Test strategy decision

### 3. Developer

Purpose:

- Turn the analyst package into an implementation job.
- Break the change into concrete tasks.
- Make the code changes.
- Add or update tests where appropriate.

Outputs:

- Development plan
- Code changes
- Test changes
- Handoff summary to QA

### 4. QA/Tester

Purpose:

- Review the code critically.
- Verify the implementation against requirements.
- Run or write targeted tests where possible.
- Decide pass or fail.

Outputs:

- QA findings
- Evidence of test execution
- Pass/fail decision
- Return-to-dev actions if failed

### 5. Release Manager

Purpose:

- Confirm target branch policy.
- Ensure the work is not pushed to `main` or `master`.
- Push to the correct branch or create an appropriate branch when needed.
- Prepare the branch for downstream merge or release handling.

Outputs:

- Release branch decision
- Push confirmation
- Release notes summary

## Lifecycle States

- `intake_received`
- `po_review`
- `po_rejected`
- `po_approved`
- `analysis_in_progress`
- `analysis_complete`
- `development_in_progress`
- `qa_in_progress`
- `qa_failed`
- `development_rework`
- `qa_passed`
- `release_in_progress`
- `released_to_branch`
- `blocked`

## Mandatory Handoff Rules

- Product Owner must not write requirements.
- Analyst must not write production code.
- Developer must not self-approve release readiness.
- QA must not rubber-stamp without evidence.
- Release Manager must not implement feature code.

## Client Locking Rule

Before work begins for a client:

1. Check `clients/<client>/state/active-ticket.md`.
2. If an active ticket exists, do not start another ticket for that client.
3. If no active ticket exists, create or update the lock file with the current ticket.

## Git Rule

Before Analyst or Developer work begins:

1. Confirm the client repo path.
2. Fetch and pull latest changes from the remote.
3. Confirm the working branch is not `main` or `master`.
4. If the expected branch exists, use `dev` unless there is a better ticket-specific branch policy.
5. If only `main` or `master` exists, Release Manager must create a new non-production branch before push.
