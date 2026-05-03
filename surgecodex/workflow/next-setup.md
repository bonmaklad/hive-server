# Next Setup Items

This workflow scaffold is not the full operating system yet. The following pieces still need to be designed and implemented.

## 1. Ticket Intake Source

Decide where work arrives from:

- GitHub issues
- Jira
- Zendesk
- email
- manual file drop
- another system

The intake source must provide:

- ticket identifier
- raw description
- client hint if available
- priority
- attachments or links

## 2. Client Resolution Logic

Before Product Owner review, the system needs a deterministic way to map the ticket to the correct client folder.

Possible signals:

- source project mapping
- repository mapping
- email domain
- explicit client field
- keyword mapping

## 3. Orchestrator

Decide what starts and hands off each role.

Minimum orchestration responsibilities:

- resolve client
- enforce one-ticket-per-client lock
- load the correct agent instructions
- persist artefacts in the client work folder
- route failed QA back to Developer
- route passed QA to Release Manager

## 4. Client Repo Bootstrap

For each client, define:

- repo remote URL
- default local checkout path
- preferred branch policy
- environment setup steps
- test commands

## 5. Execution Logging

Store a simple audit trail for each ticket:

- who handled the stage
- when the stage started
- when the stage completed
- outcome
- links to artefacts

## 6. Git Safety Automation

Add automation later to:

- fetch and pull the latest code
- detect branch availability
- block pushes to `main` and `master`
- create safe branches when needed

## 7. Test Capability Registry

Per client, record:

- how automated tests are run
- which test frameworks exist
- whether sandboxed execution is possible
- when user testing is required

## 8. Definition Of Done

Standardize the minimum completion bar:

- Product Owner approved
- Analyst artefacts complete
- Developer implementation complete
- QA passed with evidence
- Release Manager pushed to safe branch
