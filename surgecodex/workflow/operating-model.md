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
- Classify the work item strictly as bug, issue, or change request.
- Reject disguised feature work that is presented as a bug or issue.
- Decide whether the work is real, valuable, viable, and safely bounded.
- Reject, triage, defer, or pass the ticket onward.

Outputs:

- Intake decision
- Ticket classification
- Scope statement
- Initial assumptions and exclusions

### 2. Designer

Purpose:

- Shape approved change requests before analysis starts.
- Align feature design with the existing product flows and design principles.
- Produce UI and UX requirements that are practical for the current codebase.

Outputs:

- Design requirements
- UI and UX constraints
- Interaction states and flow notes
- Design principles for the feature

### 3. Analyst

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

### 4. Developer Orchestrator

Purpose:

- Take the analyst task graph and coordinate task execution.
- Launch task-scoped developer work in isolated branches or worktrees.
- Keep task execution bounded and dependency-aware.
- Prepare integrated work for peer review and QA.

Outputs:

- Development plan
- Task state
- Integrated code changes
- Handoff summary to QA

### 5. Task Developer

Purpose:

- Implement one assigned task only.
- Stay inside the task boundary and target paths.
- Add or update task-scoped tests where appropriate.

Outputs:

- Task code changes
- Task test changes
- Task handoff summary

### 6. Peer Reviewer

Purpose:

- Review each task as a senior developer.
- Improve code directly when the fix is local and safe.
- Block only when the task cannot be corrected safely within scope.

Outputs:

- Peer review findings
- Improved task code
- Approval or block decision

### 7. QA/Tester

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

### 8. Release Manager

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
- `design_in_progress`
- `design_complete`
- `analysis_in_progress`
- `analysis_complete`
- `development_in_progress`
- `task_execution_in_progress`
- `qa_in_progress`
- `qa_failed`
- `development_rework`
- `qa_passed`
- `release_in_progress`
- `released_to_branch`
- `blocked`

## Mandatory Handoff Rules

- Product Owner must not write requirements.
- Designer must not skip codebase and flow review for change requests.
- Analyst must not write production code.
- Developer must not self-approve release readiness.
- Peer Reviewer must not widen scope beyond the assigned task.
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
4. Create a ticket integration branch for the active ticket.
5. Run task work on isolated task branches or worktrees derived from the ticket integration branch.
6. Release Manager merges the integration branch into the chosen non-production release branch before push.
