# Pramaan AI — Project Documentation v1.1

This folder contains the Markdown source specifications for the Pramaan AI hackathon project.

## Files

- [`PRD.md`](PRD.md) — product definition, journeys, pages, requirements, edge cases, acceptance criteria, and hackathon demo.
- [`TECHNICAL_ARCHITECTURE.md`](TECHNICAL_ARCHITECTURE.md) — HLD/LLD, stack, database/ERD, API contracts, feature data flows, AI pipelines, guardrails, observability, evaluation, CI/CD, deployment.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — milestone order, terminal commands, module contracts, test gates, delivery dates, scope-cut policy.
- [`ANTIGRAVITY_IMPLEMENTATION_PROMPT.md`](ANTIGRAVITY_IMPLEMENTATION_PROMPT.md) — copyable master prompt and prompts for each subsequent phase.
- [`ANTIGRAVITY_FIRST_MILESTONE_PROMPT.md`](ANTIGRAVITY_FIRST_MILESTONE_PROMPT.md) — the shorter first-run prompt for Antigravity (M0 only).
- [`assets/`](assets/) — diagrams referenced by the documents.

## Source-of-truth rules

1. PRD defines **what** product behavior is required.
2. Technical Architecture defines **how** components, data, and controls should work.
3. Implementation Plan defines **when/in what order** to implement and verify.
4. Antigravity prompt defines **how the coding agent should work**: one milestone at a time, with tests and no unapproved live/cloud operations.

If an implementation discovery conflicts with these specs, record an Architecture Decision Record (ADR) and update relevant docs rather than silently drifting.

## Use in the repository

Copy this `docs/` directory into the root of the initialized `pramaan-ai` repository. Ensure that `docs/assets/` travels with it so diagrams render in GitHub.

## Important constraints

- Use only public/synthetic documents unless the rights and permissions are clear.
- No secrets in Git, frontend variables, logs, or AI traces.
- Free-tier hosting is a target, not a guarantee of zero charges.
- The hackathon demo must clearly identify functionality actually built during the event.
- Publish only real evaluation metrics and include test sample counts.
