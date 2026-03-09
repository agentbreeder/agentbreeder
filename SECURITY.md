# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| v0.1.x  | Yes (current) |
| < v0.1  | No |

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

To report a vulnerability:

1. Use [GitHub's private vulnerability reporting](https://github.com/open-agent-garden/agent-garden/security/advisories/new)
2. Or email: **security@agent-garden.com**

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
