# Agents

Each role is defined by:

- `soul.md` for role identity and delivery posture
- `do.md` for required behaviour
- `do-not.md` for hard constraints

Current roles:

- `product-owner`
- `designer`
- `analyst`
- `developer`
- `peer-reviewer`
- `qa-tester`
- `release-manager`

How they fit together:

1. Product Owner validates the ticket.
2. Designer shapes approved change requests before analysis.
3. Analyst writes requirements and task decomposition.
4. Developer works one task at a time in an isolated worktree.
5. Peer Reviewer improves or blocks that same task.
6. The runner integrates approved task commits into the ticket branch.
7. QA verifies the integrated ticket.
8. Release Manager merges to a safe non-production branch and pushes.

These files are intentionally prescriptive so the workflow can evolve by changing role behaviour directly rather than rewriting the entire runner first.
