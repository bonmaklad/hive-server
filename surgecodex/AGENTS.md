# SurgeCodex Agents

This repository uses a role-based delivery system for one active ticket per client.

## Roles

### Product Owner

- Confirms the client and classifies the ticket.
- Stops vague, invalid, low-value, non-viable, or oversized work before delivery starts.
- Reclassifies disguised feature requests out of bug or issue categories.
- Sends approved bugs and issues to the Analyst.
- Sends approved change requests to the Designer.

### Designer

- Works only on approved change requests.
- Reads the existing code and flows to keep the feature aligned with the product.
- Produces practical UI and UX requirements before analysis.

### Analyst

- Finds the relevant code area and current behaviour.
- Writes as-is and to-be requirements.
- Produces BDD acceptance criteria and test strategy.
- Splits the change into the smallest practical task graph.

### Developer

- Works on one assigned task only.
- Changes code only inside the task boundary and target paths.
- Adds or updates task-scoped tests when practical.

### Peer Reviewer

- Reviews the developer's task as a senior engineer.
- Improves correctness, maintainability, and tests when the fix is local and safe.
- Approves or blocks the task before integration.

### QA Tester

- Verifies the integrated ticket result against the analysis and acceptance criteria.
- Runs targeted checks and records pass, fail, awaiting-client, or closed outcomes.

### Release Manager

- Keeps all outbound Git operations off `main` and `master`.
- Merges the ticket integration branch into the chosen non-production release branch.
- Pushes the release branch and records the branch outcome.

## Execution Model

- One active ticket per client.
- Many clients may run concurrently.
- Many independent tasks may run concurrently inside one active ticket.
- `triaged` is the clarification and human-review state for blocked automatic work.
- The Analyst produces the task graph.
- The runner creates a ticket integration branch automatically.
- Each ready task gets its own isolated Git worktree and task branch.
- Developer and Peer Reviewer both run inside that worktree.
- Approved task commits are cherry-picked back into the ticket integration branch.
- QA runs after all task work is integrated.
- Release merges the integration branch into `dev` or another safe non-production branch automatically.

## Artifacts

Ticket artifacts live outside the client repo under:

```text
runtime/<client>/tickets/<ticket-number>/
```

Common artifacts:

- `01-intake.md`
- `02-design.md`
- `03-analysis.md`
- `04-dev-plan.md`
- `05-qa-report.md`
- `06-release.md`
- `tasks.json`
- `tasks/<task-id>/developer.md`
- `tasks/<task-id>/peer-review.md`

## Constraints

- Never push directly to `main` or `master`.
- Do not run more than one active ticket for the same client at once.
- Keep workflow state under `runtime/`, not inside client repositories.
- Keep role prompts narrow and task-scoped wherever possible.
- Use machine-readable task state to coordinate parallel execution.
- Do not let new functionality pass as a bug or issue.
