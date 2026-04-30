# AgentBreeder Project Governance

This document describes how decisions are made on the AgentBreeder open-source
project (`agentbreeder/agentbreeder` and `agentbreeder/agentbreeder-showcase`).

## Current model: BDFL

AgentBreeder is currently led by a Benevolent Dictator For Life (BDFL):

- **Project Lead:** Rajit Saha (saha.rajit@gmail.com)

The BDFL has final authority on all project decisions: roadmap, architecture,
release timing, license posture, contributor admission, and code-of-conduct
enforcement. The BDFL delegates day-to-day decisions to maintainers and the
community whenever practical.

This is the standard model for early-stage projects. As the project grows it
will evolve — see [Future evolution](#future-evolution) below.

## How decisions get made

### Day-to-day technical decisions

- **Single-area changes** (one runtime, one deployer, one connector, doc
  fixes): one maintainer approval is sufficient. The project lead may waive
  this for trivial fixes.
- **Cross-area changes** (anything touching `engine/`, the deploy pipeline,
  the registry schema, or the public CLI surface): two maintainer approvals,
  one of whom must be the project lead or a delegated reviewer.
- **Schema changes** to `agent.yaml`, `orchestration.yaml`, or any registry
  table: project lead must approve. Schema changes require a corresponding
  doc update in `website/content/docs/agent-yaml.mdx` or equivalent in the
  same PR.

### Roadmap decisions

The roadmap is public — see
[`ROADMAP.md`](ROADMAP.md) and the
[GitHub Project board](https://github.com/orgs/agentbreeder/projects).
Anyone can propose a feature by opening an issue tagged `type:feature`. The
project lead triages and assigns a milestone (or rejects with a reason).

### License and trademark decisions

The project license (Apache 2.0) and trademark policy
([TRADEMARK.md](TRADEMARK.md)) are stable commitments. They will not change
without:

1. Public discussion in GitHub Discussions for at least 30 days.
2. A blog post on agentbreeder.io explaining the rationale.
3. The project lead's signed-off proposal.

This is intentional — predictable license posture is part of the contract
with contributors.

## Maintainer ladder

Anyone in the community can climb the ladder. The criteria are public and
applied consistently.

| Tier | Requirements | What you can do |
|---|---|---|
| **Contributor** | Signed [CLA](CLA.md) and merged at least one PR | Open issues and PRs |
| **Triager** | 5+ merged PRs over at least 30 days, sustained activity, demonstrated good judgment in issue triage | Apply labels, close stale issues, assign milestones, request changes on PRs |
| **Maintainer** | 15+ merged PRs over 90+ days, deep expertise in at least one subsystem (`engine/`, `cli/`, `dashboard/`, `sdk/`, etc.), proven ability to mentor newer contributors | Approve and merge PRs in their area, run release builds for their area, vote on RFCs |
| **Project Lead** | One person at a time. Currently Rajit Saha. | All maintainer rights, plus license/trademark/governance authority |

Promotions are decided by the existing maintainers and the project lead. We
expect to promote the first non-founder triagers and maintainers within
6 months of public launch.

## Conflict resolution

1. **Technical disagreement:** discuss in the relevant PR or issue. If no
   consensus is reached within 7 days, escalate to a maintainer in the
   affected area. If still unresolved, the project lead makes the call.
2. **Code of Conduct issues:** see
   [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Report to
   **saha.rajit@gmail.com**. The project lead investigates within 48 hours
   and follows the Contributor Covenant 2.1 enforcement guidelines.
3. **Disputes over license, trademark, or project direction:** the project
   lead has final authority. The lead is expected to explain reasoning
   publicly when overruling community consensus.

## Future evolution

This governance model will evolve as the project grows. The intended path:

| Trigger | Change |
|---|---|
| First Cloud paying customer | Form an internal AgentBreeder maintainer team (employees + key external maintainers) |
| 50+ active external contributors | Form a public **Steering Committee** — 5–7 members, including at least 2 non-employee maintainers, elected annually. Steering Committee decisions can override the project lead by 2/3 vote, except on license/trademark. |
| 1000+ active monthly contributors or significant enterprise dependency | Consider transferring stewardship to a neutral foundation (Apache Software Foundation, Linux Foundation, or Open Source Initiative) |

These triggers are guideposts, not commitments. The project lead may move
faster or slower depending on community needs.

## Communication channels

| Channel | Purpose |
|---|---|
| [GitHub Issues](https://github.com/agentbreeder/agentbreeder/issues) | Bug reports, feature requests, tracked work |
| [GitHub Discussions](https://github.com/agentbreeder/agentbreeder/discussions) | Open-ended questions, RFCs, roadmap input |
| Discord *(coming soon)* | Real-time chat: `#announcements`, `#help`, `#contributors`, `#cloud` |
| security@agentbreeder.com | Security disclosures (see [SECURITY.md](SECURITY.md)) |
| saha.rajit@gmail.com | Code of Conduct, license, trademark, governance, partnerships |

## Maintainer responsibilities

Maintainers commit to:

- **48-hour PR triage** — every PR gets a first response within 48 hours of
  opening, even if it's only "thanks, will review by EOW."
- **7-day review** — PRs from external contributors get a full review and a
  merge-or-changes decision within 7 days unless explicitly deferred.
- **Public reasoning** — when rejecting a PR or design proposal, explain
  why in writing.
- **Mentor newer contributors** — make time to help triagers and contributors
  level up.

Failure to uphold these responsibilities is grounds for stepping down from
or being removed from a maintainer role.

---

*Document version: 1.0 — adopted 2026-04-30.*
