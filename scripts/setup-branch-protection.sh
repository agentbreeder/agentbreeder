#!/usr/bin/env bash
# setup-branch-protection.sh
#
# Configures GitHub branch protection rules for agent-garden via the GitHub API.
# Run this ONCE after creating the repository. Requires gh CLI authenticated with
# an account that has Admin access to the repository.
#
# Usage:
#   ./scripts/setup-branch-protection.sh
#   ./scripts/setup-branch-protection.sh --repo owner/repo-name
#
# Requirements:
#   - gh CLI installed and authenticated (gh auth login)
#   - Admin access to the repository

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "open-agent-garden/agent-garden")}"
BRANCH="main"

# Only this GitHub user can merge PRs into main.
# restrictions.users limits who can push to the protected branch — merging a PR
# is a push, so only users listed here can click "Merge pull request".
MERGE_ACTOR="rajitsaha"

# These must match the exact job names in your workflows
REQUIRED_STATUS_CHECKS=(
  "Lint Python"
  "Lint Frontend"
  "Python Tests"
  "Integration Tests"
  "Python SAST (Bandit)"
  "Dependency Audit (pip-audit)"
  "Dependency Audit (npm audit)"
  "Secret Scan (Gitleaks)"
)

echo "Configuring branch protection for: $REPO ($BRANCH)"
echo ""

# ── Build required status checks JSON ────────────────────────────────────────
CHECKS_JSON="["
for check in "${REQUIRED_STATUS_CHECKS[@]}"; do
  CHECKS_JSON+="{\"context\": \"$check\"},"
done
CHECKS_JSON="${CHECKS_JSON%,}]"

# ── Apply branch protection rules ─────────────────────────────────────────────
# Note: heredoc is unquoted so shell variables expand inside it.
gh api \
  --method PUT \
  "repos/$REPO/branches/$BRANCH/protection" \
  --input - <<EOF
{
  "required_status_checks": {
    "strict": true,
    "checks": $CHECKS_JSON
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 2,
    "require_last_push_approval": true,
    "bypass_pull_request_allowances": {
      "users": [],
      "teams": [],
      "apps": []
    }
  },
  "restrictions": {
    "users": ["$MERGE_ACTOR"],
    "teams": [],
    "apps": []
  },
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
EOF

echo ""
echo "✓ Branch protection applied to $REPO/$BRANCH"
echo ""

# ── Configure repository settings ─────────────────────────────────────────────
echo "Configuring repository settings..."

gh api \
  --method PATCH \
  "repos/$REPO" \
  --input - <<EOF
{
  "has_issues": true,
  "has_projects": true,
  "has_wiki": false,
  "allow_squash_merge": true,
  "allow_merge_commit": false,
  "allow_rebase_merge": false,
  "squash_merge_commit_title": "PR_TITLE",
  "squash_merge_commit_message": "PR_BODY",
  "delete_branch_on_merge": true,
  "allow_auto_merge": false
}
EOF

echo "✓ Repository settings updated"
echo "  - Squash merges only (no merge commits, no rebase merges)"
echo "  - Delete branch on merge: enabled"
echo "  - Auto-merge: disabled (reviewers must explicitly approve)"
echo ""

# ── Verify branch protection ───────────────────────────────────────────────────
echo "Verifying branch protection rules..."
gh api "repos/$REPO/branches/$BRANCH/protection" \
  --jq '{
    required_reviews: .required_pull_request_reviews.required_approving_review_count,
    dismiss_stale: .required_pull_request_reviews.dismiss_stale_reviews,
    codeowner_reviews: .required_pull_request_reviews.require_code_owner_reviews,
    enforce_admins: .enforce_admins.enabled,
    linear_history: .required_linear_history.enabled,
    allow_force_push: .allow_force_pushes.enabled,
    merge_restricted_to: [.restrictions.users[].login],
    status_checks: [.required_status_checks.checks[].context]
  }'

echo ""
echo "✓ Branch protection setup complete!"
echo ""
echo "Merge access: only @$MERGE_ACTOR can merge PRs into main."
echo "All other contributors must submit PRs — they cannot merge them."
echo ""
echo "Next steps:"
echo "  1. Create teams in GitHub org: maintainers, backend-leads, frontend-leads, cli-leads, sdk-leads, engine-leads"
echo "  2. Add CODECOV_TOKEN secret: gh secret set CODECOV_TOKEN --repo $REPO"
echo "  3. Set up PyPI Trusted Publishing: https://pypi.org/manage/account/publishing/"
echo "  4. Configure Dependabot alerts: $REPO/settings/security_analysis"
