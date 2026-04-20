# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| v0.1.x  | Yes (current) |
| < v0.1  | No |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

To report a vulnerability:

1. Use [GitHub's private vulnerability reporting](https://github.com/agentbreeder/agentbreeder/security/advisories/new)
2. Or email: **security@agentbreeder.com**

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **48 hours** — acknowledgment of your report
- **7 days** — initial assessment and severity classification
- **90 days** — coordinated disclosure window

We will credit reporters in security advisories unless they prefer anonymity.

## Security Considerations

### YAML Parsing
- Always uses `yaml.safe_load` — never `yaml.unsafe_load`
- All YAML inputs validated against JSON Schema before processing

### Secrets Management
- Secrets are never stored in config files or source code
- Agent secrets referenced via cloud secret managers (AWS Secrets Manager, GCP Secret Manager)
- Environment variables for local development only (`.env` is gitignored)

### Authentication & Authorization
- JWT + OAuth2 for API authentication
- Tokens expire (configurable, default 24 hours)
- RBAC enforced at deploy time, API level, and dashboard level

### Container Security
- Non-root users in all container images
- Read-only root filesystems where possible
- Minimal base images to reduce attack surface
- No secrets baked into container images

### Input Validation
- Pydantic models for all API inputs
- JSON Schema validation for YAML configs
- No raw SQL queries — SQLAlchemy ORM only

### Dependencies
- Dependabot enabled for automated dependency updates
- No dependencies with known critical vulnerabilities in releases

## CI Security Pipeline

Every pull request and every push to `main` runs the full security pipeline defined in `.github/workflows/security.yml`. The pipeline includes:

### Secret Scanning (Gitleaks)

[Gitleaks](https://github.com/gitleaks/gitleaks) scans every PR and push for accidentally committed secrets — API keys, tokens, credentials, and private keys.

- **Trigger:** All PRs and pushes to `main`
- **Scope:** Full git history (`fetch-depth: 0`) — not just the latest commit
- **Config:** `.gitleaks.toml` at the repo root defines an allowlist that covers test fixtures, example placeholder values, and known-safe patterns in `examples/` directories. If Gitleaks flags a false positive, add it to the allowlist with a comment explaining why it is safe.

**If you accidentally commit a secret:**
1. **Rotate it immediately** — treat the exposed credential as compromised, regardless of whether the commit is public. Removing it from history is not sufficient.
2. Revoke or regenerate the credential in the relevant service (AWS IAM, GCP, GitHub, etc.).
3. Remove the secret from git history using `git filter-repo` or BFG Repo Cleaner.
4. Force-push the cleaned history and notify the security team at **security@agentbreeder.com**.

> Removing a secret from git history does not un-expose it. Always rotate first.

### Python SAST (Bandit)

Bandit runs static analysis on all Python source files to detect common security anti-patterns (shell injection, use of `assert` for auth, weak cryptography, etc.).

### Dependency Auditing

- **pip-audit** — checks all Python dependencies against the PyPI advisory database
- **npm audit** — checks all Node.js dependencies in `dashboard/` against the npm advisory database

### Container Image Scanning (Trivy)

[Trivy](https://github.com/aquasecurity/trivy) scans all built Docker images for known CVEs in OS packages and language-level dependencies before images are pushed to Docker Hub.

## Security-Related Configuration

See the environment variables section in [CLAUDE.md](CLAUDE.md) for:
- `SECRET_KEY` — application secret (use a random 256-bit key)
- `JWT_SECRET_KEY` — JWT signing key
- `JWT_ALGORITHM` — default HS256
- `ACCESS_TOKEN_EXPIRE_MINUTES` — token lifetime

**Recommendations:**
- Rotate secrets regularly
- Use strong, randomly generated keys
- Enable MFA for all cloud provider accounts
- Review the `test:security` skill in [AGENT.md](AGENT.md) for the full security review checklist
