# Focused rebuild implementation slice

1. Create branch and private backup of tracked working files and staged/unstaged diffs.
2. Record the authorized spec and explicit removal inventory.
3. Remove tracked legacy implementation, templates, generated knowledge maps, old tests, old documentation/site and obsolete CI/distribution scaffolding. Preserve user-modified paths, licensing/policies, root historical roadmap and local private material.
4. Create src/agentbreeder with strict manifest parsing and read-only diagnostics; add a small CLI and test-first contract cases.
5. Add the real sample HTTP service, honest onboarding documentation and focused CI/build configuration.
6. Run unit/integration checks, lint, package build and installed-package smoke tests. Review the diff for accidental removal of unrelated user files and private-data exposure.
7. Update private roadmap and resume cursor. Keep the branch uncommitted/unpublished until the repository release gates and any needed follow-up review are satisfied.

This slice completes the reset and validation foundation. It does not complete the roadmap's real deployment, access, recovery, UI or enterprise pilot milestones.
