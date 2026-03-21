# AgentBreeder -- GitHub Issues for Build Recommendations

> This document contains GitHub issues to be created for AgentBreeder's growth, technical build, legal/IP, and business tracks. Each issue includes title, labels, priority, description, acceptance criteria, and estimated effort.

---

## Growth & Launch Issues

---

### Issue 1: Create 20+ Agent Templates for `agentbreeder init --template`

**Priority:** P0
**Labels:** `enhancement`, `templates`, `launch`

**Description:**
Create a library of high-quality, production-ready starter templates that users can scaffold with `agentbreeder init --template <name>`. Templates are the single most important driver of time-to-first-deploy. Every template must work on the first try with zero configuration beyond API keys.

Required templates:
- `customer-support` -- Zendesk MCP integration + RAG over product docs
- `code-review` -- GitHub MCP + Claude for PR review and suggestions
- `research-agent` -- Web search + summarization pipeline
- `data-analyst` -- SQL tool + chart generation
- `slack-bot` -- Slack MCP + conversational agent
- `email-triage` -- Email parsing + classification + routing
- `document-qa` -- RAG-based document question answering
- `sales-qualification` -- CRM integration + lead scoring
- `multi-agent-pipeline` -- Triage agent -> Specialist agent -> QA agent orchestration
- One template per supported framework: LangGraph, CrewAI, OpenAI Agents, Claude SDK, Google ADK

Each template must include:
- Working `agent.yaml` with sensible defaults
- README with setup instructions and architecture diagram
- At least one unit test
- Example `.env.example` with required secrets documented
- Screenshot or terminal output showing the template in action

**Acceptance Criteria:**
- [ ] All 20+ templates pass `agentbreeder validate` without errors
- [ ] All templates deploy successfully with `agentbreeder deploy --target local`
- [ ] Each template has a README with < 5 minute setup instructions
- [ ] Each template includes at least one passing test
- [ ] `agentbreeder init --template` lists all templates with descriptions
- [ ] Templates are indexed in the registry for `agentbreeder search`

**Estimated Effort:** 3-4 weeks (2 engineers)

---

### Issue 2: Record "5-Minute Demo" Video

**Priority:** P0
**Labels:** `marketing`, `launch`

**Description:**
Create two demo assets that show AgentBreeder's end-to-end workflow:

1. **Terminal GIF** (asciinema or VHS): A 60-second recording showing:
   - `agentbreeder up` starting the local platform
   - `agentbreeder init --template customer-support` scaffolding an agent
   - `agentbreeder deploy --target local` deploying it
   - The agent responding to a test query
   - The dashboard showing governance (RBAC, cost, audit trail)

2. **YouTube video** (2 minutes): Narrated walkthrough covering the same flow with dashboard UI shown side-by-side. End with the three-tier visual (No Code / Low Code / Full Code).

The terminal GIF goes in the README hero section. The YouTube video is linked from the landing page and README.

**Acceptance Criteria:**
- [ ] Terminal GIF is < 5 MB and renders correctly on GitHub
- [ ] YouTube video is uploaded and public
- [ ] Both assets show a complete init -> deploy -> running agent flow
- [ ] Dashboard governance features (RBAC, cost, audit) are visible
- [ ] No secrets or credentials visible in recordings
- [ ] GIF is embedded in README.md hero section

**Estimated Effort:** 3-5 days (1 person)

---

### Issue 3: Write "Migrate from X" Guides

**Priority:** P0
**Labels:** `docs`, `seo`, `launch`

**Description:**
Write four migration guides targeting developers who already have agents deployed using other tools or ad-hoc methods. These guides serve dual purpose: SEO content and genuine onboarding help.

Guides to write:

1. **Migrate from LangChain Deployment** -- Show how to wrap an existing LangChain/LangGraph agent in `agent.yaml`, replace custom deployment scripts with `agentbreeder deploy`, and gain governance automatically. Include before/after code comparison.

2. **Deploy CrewAI Agents with Governance** -- Take a working CrewAI crew definition and show how AgentBreeder adds RBAC, cost tracking, and audit trail without changing the crew logic. Emphasize what they gain vs. running CrewAI standalone.

3. **Replace Custom Docker+Terraform with agent.yaml** -- For teams that built their own deployment pipeline. Show the lines-of-code reduction, the governance features they get for free, and the multi-cloud portability.

4. **Add RBAC to Existing OpenAI Agents** -- For teams using the OpenAI Agents SDK directly. Show how to wrap their agent, deploy it, and immediately get team-based access control and cost attribution.

Each guide should include:
- A realistic "before" scenario (with code)
- Step-by-step migration process
- "After" state showing AgentBreeder benefits
- Common pitfalls and troubleshooting
- Time estimate for migration

**Acceptance Criteria:**
- [ ] All four guides are published in `docs/guides/`
- [ ] Each guide includes working code examples that pass `agentbreeder validate`
- [ ] Each guide has been tested end-to-end by someone other than the author
- [ ] Guides are linked from the main documentation index
- [ ] Each guide targets relevant SEO keywords in the title and headings

**Estimated Effort:** 2 weeks (1 person)

---

### Issue 4: Create Comparison Pages

**Priority:** P1
**Labels:** `docs`, `seo`

**Description:**
Create honest, factual comparison pages for SEO and developer decision-making. These pages should acknowledge competitors' strengths while clearly articulating AgentBreeder's differentiation.

Comparisons to write:

1. **AgentBreeder vs LangChain** -- Focus on: deployment (LangChain has none built-in), governance (none), multi-framework support (LangChain is LangChain-only), registry/discovery.

2. **AgentBreeder vs CrewAI** -- Focus on: deployment to production (CrewAI is dev-focused), governance, multi-cloud, MCP integration, registry.

3. **AgentBreeder vs DIY (Docker+Terraform)** -- Focus on: lines of code, time to deploy, governance out of the box, framework agnosticism, team collaboration.

4. **AgentBreeder vs Vellum** -- Focus on: open source vs proprietary, framework freedom, self-hosted option, no vendor lock-in, full code access.

Each comparison page should include:
- Feature matrix table
- Honest assessment of when the competitor is a better fit
- Code/config comparison where applicable
- "When to choose AgentBreeder" section

**Acceptance Criteria:**
- [ ] Four comparison pages published in `docs/comparisons/`
- [ ] Each page has a feature matrix table
- [ ] Pages are factually accurate (no straw-man arguments)
- [ ] Each page includes a "When to choose [competitor]" section for honesty
- [ ] Pages are linked from main docs navigation

**Estimated Effort:** 1 week (1 person)

---

### Issue 5: Landing Page & README Redesign

**Priority:** P0
**Labels:** `marketing`, `launch`

**Description:**
Redesign the README.md and (if applicable) the landing page to maximize conversion from visitor to first deploy. The current README is developer-oriented but does not sell the value proposition quickly enough.

New structure:
- **Hero section:** Animated terminal GIF (from Issue 2), one-liner tagline ("Define Once. Deploy Anywhere. Govern Automatically."), copy-pasteable get-started block (`pip install agentbreeder && agentbreeder init --template customer-support && agentbreeder deploy`)
- **Three-tier visual:** A clean graphic showing No Code / Low Code / Full Code tiers with arrows showing tier mobility
- **Template gallery:** Grid of 6-8 featured templates with icons and one-line descriptions
- **Feature highlights:** 4 panels -- Framework Agnostic, Multi-Cloud, Governance Built-In, Agent Registry
- **Social proof section:** GitHub stars badge, contributor count, "Used by" logos (when available)
- **Getting started:** 3-step quick start with terminal screenshots
- **Architecture diagram:** High-level system diagram

**Acceptance Criteria:**
- [ ] README renders correctly on GitHub (no broken images, proper formatting)
- [ ] Hero section includes working copy-paste commands
- [ ] Three-tier visual is an SVG or high-quality PNG
- [ ] Template gallery links to actual template directories
- [ ] Page loads in < 3 seconds (no oversized images)
- [ ] Mobile-responsive if landing page (README inherently is)

**Estimated Effort:** 1 week (1 designer + 1 engineer)

---

### Issue 6: Launch Discord Community

**Priority:** P0
**Labels:** `community`, `launch`

**Description:**
Set up a Discord server as the primary community hub for AgentBreeder users, contributors, and maintainers.

Required channels:
- `#general` -- General discussion
- `#support` -- Help with setup, deployment, troubleshooting
- `#show-and-tell` -- Share agents, templates, and deployments
- `#feature-requests` -- Community feature suggestions
- `#contributors` -- For people contributing to the codebase
- `#announcements` -- Release notes, breaking changes, events (read-only for non-admins)

Additional setup:
- Welcome message with links to docs, quickstart, and GitHub
- Role-based access (Maintainer, Contributor, Community)
- Bot for GitHub notifications (new releases, important PRs)
- Community guidelines / Code of Conduct pinned
- Link from README, docs, and GitHub repo

**Acceptance Criteria:**
- [ ] Discord server is created and configured with all channels
- [ ] Welcome message and community guidelines are posted
- [ ] GitHub notification bot is connected
- [ ] Invite link is added to README.md and docs
- [ ] At least 2 maintainers have admin access
- [ ] Server has a custom icon and banner

**Estimated Effort:** 2-3 days (1 person)

---

### Issue 7: Show HN Launch Preparation

**Priority:** P1
**Labels:** `marketing`, `launch`

**Description:**
Prepare a structured Hacker News Show HN launch to maximize visibility and engagement. HN is the single highest-leverage launch channel for developer tools.

Preparation steps:

1. **Draft 3 title options** (pick the best one on launch day):
   - "Show HN: AgentBreeder -- Deploy AI agents to any cloud with one YAML file"
   - "Show HN: AgentBreeder -- Open-source platform for deploying and governing AI agents"
   - "Show HN: AgentBreeder -- Define once, deploy anywhere, govern automatically"

2. **Prepare FAQ document** for rapid comment response:
   - "How is this different from LangChain?" -- prepared answer
   - "Why not just use Docker?" -- prepared answer
   - "What about vendor lock-in?" -- prepared answer (Apache 2.0, self-hosted)
   - "Does this work with [framework]?" -- prepared answer per framework
   - "What's the business model?" -- prepared answer (open core)

3. **Coordination plan:**
   - Designate 2-3 team members to monitor and respond to comments for first 6 hours
   - Pre-write 5-6 "deep dive" comments about architecture decisions
   - Have demo video and template gallery ready as links for comments

4. **Timing:** Post between 8-10 AM ET on a Tuesday or Wednesday

**Acceptance Criteria:**
- [ ] Three title options drafted and reviewed
- [ ] FAQ document with 10+ prepared answers
- [ ] Comment response team identified with 6-hour coverage plan
- [ ] 5+ deep-dive comment drafts ready
- [ ] Demo video (Issue 2) and templates (Issue 1) are live before launch
- [ ] README (Issue 5) is finalized before launch

**Estimated Effort:** 3-5 days (2 people)

---

### Issue 8: Weekly Content Calendar

**Priority:** P1
**Labels:** `marketing`, `content`

**Description:**
Establish a recurring content calendar to build organic developer mindshare. Consistency matters more than virality.

Schedule:
- **Blog post every Tuesday** -- Publish on the project blog (or dev.to / Hashnode). Topics rotate between: technical deep dives, tutorials, architecture decisions, community spotlights, and industry commentary.
- **YouTube video every other Thursday** -- Short (5-10 min) videos covering: template walkthroughs, feature demos, architecture explainers, "build with me" sessions.
- **Daily Twitter/X post** -- Mix of: feature tips, community highlights, memes/humor, thread deep dives, engagement questions.

First month content plan:
- Week 1: "Why We Built AgentBreeder" (blog) + launch video (YouTube)
- Week 2: "agent.yaml Deep Dive" (blog)
- Week 3: "Building a Customer Support Agent in 5 Minutes" (blog) + template walkthrough (YouTube)
- Week 4: "The Three-Tier Builder Model" (blog)

**Acceptance Criteria:**
- [ ] Content calendar spreadsheet/doc covering first 8 weeks
- [ ] Blog platform selected and configured (dev.to, Hashnode, or self-hosted)
- [ ] YouTube channel created with branding
- [ ] Twitter/X account active with bio linking to repo
- [ ] First 4 blog posts drafted
- [ ] First 2 YouTube videos scripted

**Estimated Effort:** Ongoing (2-4 hours/week after initial setup of 1 week)

---

## Technical Build Issues

---

### Issue 9: Implement CrewAI Runtime

**Priority:** P1
**Labels:** `enhancement`, `engine`

**Description:**
Add a CrewAI runtime builder at `engine/runtimes/crewai.py` that implements the `RuntimeBuilder` abstract interface. This enables users to deploy CrewAI-based agents through the standard `agentbreeder deploy` pipeline.

The runtime must:
- Parse CrewAI-specific configuration from `agent.yaml` (crew definition, agent roles, task definitions)
- Generate a working Dockerfile that installs `crewai` and dependencies
- Produce a server entrypoint that exposes the crew as an HTTP endpoint
- Support CrewAI's sequential and hierarchical process types
- Handle tool injection from the AgentBreeder tool registry into CrewAI's tool format

Reference the existing `engine/runtimes/langgraph.py` and `engine/runtimes/openai_agents.py` for implementation patterns.

**Acceptance Criteria:**
- [ ] `engine/runtimes/crewai.py` implements all `RuntimeBuilder` methods
- [ ] `validate()` checks for valid CrewAI configuration in `agent.yaml`
- [ ] `build()` produces a working container image
- [ ] `get_entrypoint()` returns a valid server entrypoint
- [ ] `get_requirements()` includes `crewai` and dependencies
- [ ] Server template added at `engine/runtimes/templates/crewai_server.py`
- [ ] Unit tests with > 80% coverage on the runtime
- [ ] Integration test: deploy a CrewAI agent locally and verify it responds
- [ ] `examples/crewai-agent/` directory with working example

**Estimated Effort:** 1-2 weeks (1 engineer)

---

### Issue 10: Implement Claude SDK Runtime

**Priority:** P1
**Labels:** `enhancement`, `engine`

**Description:**
Add a Claude SDK runtime builder at `engine/runtimes/claude_sdk.py` that implements the `RuntimeBuilder` abstract interface. This enables users to deploy agents built with Anthropic's Claude SDK (tool use, multi-turn conversations) through the standard `agentbreeder deploy` pipeline.

The runtime must:
- Parse Claude SDK-specific configuration from `agent.yaml` (model selection, tool definitions, system prompt)
- Generate a Dockerfile that installs `anthropic` SDK
- Produce a server entrypoint that wraps Claude's Messages API with tool use in an HTTP endpoint
- Support Claude's native tool use format and convert AgentBreeder tool registry entries to Claude tool schemas
- Handle streaming responses

**Acceptance Criteria:**
- [ ] `engine/runtimes/claude_sdk.py` implements all `RuntimeBuilder` methods
- [ ] Supports Claude's native tool use and multi-turn conversation
- [ ] Server template at `engine/runtimes/templates/claude_sdk_server.py`
- [ ] Streaming response support
- [ ] Unit tests with > 80% coverage
- [ ] Integration test: deploy a Claude SDK agent locally
- [ ] `examples/claude-sdk-agent/` directory with working example

**Estimated Effort:** 1-2 weeks (1 engineer)

---

### Issue 11: Implement Google ADK Runtime

**Priority:** P1
**Labels:** `enhancement`, `engine`

**Description:**
Add a Google ADK (Agent Development Kit) runtime builder at `engine/runtimes/google_adk.py` that implements the `RuntimeBuilder` abstract interface. This enables users to deploy agents built with Google's ADK through the standard `agentbreeder deploy` pipeline.

The runtime must:
- Parse Google ADK-specific configuration from `agent.yaml`
- Generate a Dockerfile that installs `google-adk` and dependencies
- Produce a server entrypoint compatible with ADK's agent serving patterns
- Support ADK's tool integration and convert AgentBreeder tool registry entries to ADK format
- Handle Gemini model configuration and authentication

**Acceptance Criteria:**
- [ ] `engine/runtimes/google_adk.py` implements all `RuntimeBuilder` methods
- [ ] Supports Google ADK agent patterns and tool format
- [ ] Server template at `engine/runtimes/templates/google_adk_server.py`
- [ ] Handles Google Cloud authentication (service account, ADC)
- [ ] Unit tests with > 80% coverage
- [ ] Integration test: deploy a Google ADK agent locally
- [ ] `examples/google-adk-agent/` directory with working example

**Estimated Effort:** 1-2 weeks (1 engineer)

---

### Issue 12: AWS ECS Deployer

**Priority:** P0
**Labels:** `enhancement`, `engine`, `cloud`

**Description:**
Implement the AWS ECS Fargate deployer at `engine/deployers/aws_ecs.py`. This is a critical path item -- AWS is the most requested cloud target and ECS Fargate is the default runtime for `deploy.cloud: aws`.

The deployer must:
- Implement the `Deployer` abstract interface from `engine/deployers/base.py`
- Create or update an ECS service with Fargate launch type
- Handle ECR image push (build locally or in CI, push to ECR)
- Configure ALB/target group for HTTP endpoint
- Set up CloudWatch log group for agent logs
- Inject secrets from AWS Secrets Manager as environment variables
- Configure autoscaling based on `deploy.scaling` config
- Set resource limits based on `deploy.resources` config
- Support rollback on health check failure
- Register the deployed endpoint in the AgentBreeder registry

Infrastructure provisioning should use Pulumi (Python) consistent with the project's IaC choice.

**Acceptance Criteria:**
- [ ] `engine/deployers/aws_ecs.py` implements full `Deployer` interface
- [ ] Deploys create ECS service with Fargate launch type
- [ ] Container images are pushed to ECR
- [ ] ALB + target group configured for HTTP traffic
- [ ] CloudWatch logs configured
- [ ] Secrets injected from AWS Secrets Manager
- [ ] Autoscaling configured per `deploy.scaling`
- [ ] Health check endpoint verified post-deploy
- [ ] Rollback works on failed health check
- [ ] Agent endpoint registered in registry after successful deploy
- [ ] `agentbreeder teardown` removes all created resources
- [ ] Unit tests with > 80% coverage
- [ ] Integration test with real AWS account (can be in CI with credentials)

**Estimated Effort:** 2-3 weeks (1 senior engineer)

---

### Issue 13: Kubernetes Deployer

**Priority:** P1
**Labels:** `enhancement`, `engine`, `cloud`

**Description:**
Implement a Kubernetes deployer at `engine/deployers/kubernetes.py`. This deployer targets any Kubernetes cluster (EKS, GKE, self-hosted) and enables `agentbreeder deploy --target kubernetes`.

The deployer must:
- Implement the `Deployer` abstract interface
- Generate and apply Kubernetes manifests (Deployment, Service, Ingress, HPA)
- Support image pull from any container registry (ECR, GCR, Docker Hub)
- Configure Kubernetes secrets from the cluster's secret store
- Set up Horizontal Pod Autoscaler based on `deploy.scaling` config
- Support both ClusterIP and LoadBalancer service types
- Handle health check probes (liveness, readiness)

Consider integration with Google's Kubernetes Agent Sandbox (gVisor, WarmPools) for enhanced agent isolation (see Issue 19 for research).

**Acceptance Criteria:**
- [ ] `engine/deployers/kubernetes.py` implements full `Deployer` interface
- [ ] Generates valid Kubernetes YAML manifests
- [ ] Deploys to any conformant Kubernetes cluster
- [ ] HPA configured per `deploy.scaling`
- [ ] Secrets mounted from Kubernetes secrets
- [ ] Health probes configured
- [ ] `agentbreeder teardown` deletes all created resources
- [ ] Unit tests with > 80% coverage
- [ ] Integration test with kind or minikube in CI

**Estimated Effort:** 2-3 weeks (1 engineer)

---

### Issue 14: AgentBreeder Cloud Platform (Managed Service)

**Priority:** P0
**Labels:** `epic`, `cloud`, `revenue`

**Description:**
Build the managed deployment service that enables `agentbreeder deploy --target cloud`. This is the primary revenue driver for AgentBreeder -- users get one-command deployment without managing their own cloud infrastructure.

Components:

1. **User accounts and authentication:**
   - Email/password + OAuth (GitHub, Google)
   - Organization and team management
   - API key generation for CLI authentication

2. **Cloud deployment orchestration:**
   - Accept deploy requests from CLI
   - Provision isolated compute per agent (ECS Fargate or Cloud Run under the hood)
   - Manage networking, load balancing, TLS termination
   - Provide `*.agentbreeder.dev` subdomains for deployed agents

3. **Usage metering and billing:**
   - Track compute hours, LLM API calls (if proxied), storage
   - Stripe integration for subscription billing
   - Usage dashboard in the web UI

4. **Pricing tiers:**
   - **Free:** 3 agents, shared compute, community support
   - **Pro ($49/agent/month):** Dedicated compute, custom domains, priority support, advanced governance
   - **Enterprise (custom):** SSO/SAML, SLA, dedicated infrastructure, compliance exports

**Acceptance Criteria:**
- [ ] User signup and login flow works (email + GitHub OAuth)
- [ ] `agentbreeder login` authenticates CLI with cloud platform
- [ ] `agentbreeder deploy --target cloud` deploys an agent to managed infrastructure
- [ ] Deployed agent is accessible at `*.agentbreeder.dev` with TLS
- [ ] Usage metering tracks compute hours accurately
- [ ] Stripe billing integration processes payments
- [ ] Free tier enforces 3-agent limit
- [ ] Dashboard shows deployed agents, usage, and billing
- [ ] 99.9% uptime SLA infrastructure is in place for Pro tier

**Estimated Effort:** 8-12 weeks (2-3 engineers + 1 designer)

---

### Issue 15: Implement OTel GenAI Semantic Conventions

**Priority:** P1
**Labels:** `enhancement`, `observability`

**Description:**
Adopt the OpenTelemetry GenAI Semantic Conventions in AgentBreeder's tracing and observability layer. This ensures that traces from AgentBreeder agents are interoperable with any OTel-compatible observability backend (Datadog, Honeycomb, Grafana, etc.).

The implementation should:
- Use the `gen_ai.*` semantic convention attributes for LLM calls (model, provider, token counts, latency)
- Add `gen_ai.request.*` and `gen_ai.response.*` attributes per the spec
- Instrument tool calls with appropriate span attributes
- Add agent-level spans that group LLM calls and tool calls into agent "turns"
- Update the tracing API (`api/routes/tracing.py`) to expose OTel-formatted traces
- Prepare for sidecar injection (Architecture Principle 3) by making instrumentation modular

Reference: https://opentelemetry.io/docs/specs/semconv/gen-ai/

**Acceptance Criteria:**
- [ ] All LLM calls emit spans with `gen_ai.*` attributes
- [ ] Token counts (`gen_ai.response.input_tokens`, `gen_ai.response.output_tokens`) are captured
- [ ] Tool calls are instrumented with tool name and duration
- [ ] Agent turns are represented as parent spans
- [ ] Traces are exportable to any OTel-compatible backend
- [ ] Tracing API returns OTel-formatted data
- [ ] Unit tests verify span attributes
- [ ] Documentation for configuring OTel exporters

**Estimated Effort:** 1-2 weeks (1 engineer)

---

### Issue 16: Auto-generate AGENTS.md

**Priority:** P2
**Labels:** `enhancement`, `standards`

**Description:**
Automatically generate an `AGENTS.md` file (following the OpenAI AGENTS.md specification) for every agent deployed through AgentBreeder. This file describes the agent's capabilities, tools, and interaction patterns in a standardized format that other AI systems can consume.

The generator should:
- Extract agent metadata from `agent.yaml` (name, description, capabilities, tools)
- Format it according to the AGENTS.md specification
- Include tool schemas, supported interaction patterns, and rate limits
- Generate the file during the deploy pipeline (after successful deploy)
- Serve the AGENTS.md at a well-known endpoint (`/.well-known/agents.md`) on deployed agents

**Acceptance Criteria:**
- [ ] `AGENTS.md` is auto-generated during deploy for every agent
- [ ] Output conforms to the AGENTS.md specification
- [ ] Includes tool descriptions and schemas
- [ ] Served at `/.well-known/agents.md` on deployed agents
- [ ] Regenerated on redeploy if agent.yaml changes
- [ ] Unit tests for the generator
- [ ] Example AGENTS.md in documentation

**Estimated Effort:** 3-5 days (1 engineer)

---

### Issue 17: Implement A2A Agent Cards for Registry

**Priority:** P1
**Labels:** `enhancement`, `registry`, `standards`

**Description:**
Use the Google A2A (Agent-to-Agent) Agent Card format as the standard discovery mechanism in AgentBreeder's agent registry. Agent Cards provide a machine-readable description of an agent's capabilities, endpoint, and authentication requirements.

The implementation should:
- Generate an A2A Agent Card for every deployed agent based on `agent.yaml` metadata
- Serve Agent Cards at the standard `/.well-known/agent.json` endpoint
- Index Agent Cards in the registry for discovery via `agentbreeder search` and the registry API
- Support both AgentBreeder-internal and external A2A agents in the registry
- Enable inter-agent discovery (Agent A can find and call Agent B via registry)

This builds on the existing A2A implementation in `engine/a2a/` and `api/routes/a2a.py`.

**Acceptance Criteria:**
- [ ] Every deployed agent has an A2A Agent Card generated
- [ ] Agent Cards are served at `/.well-known/agent.json`
- [ ] Registry indexes Agent Cards for search
- [ ] `agentbreeder search` returns Agent Card data
- [ ] External A2A agents can be registered in the registry
- [ ] Agent-to-agent discovery works through the registry
- [ ] Unit and integration tests
- [ ] Documentation for A2A Agent Card format in AgentBreeder

**Estimated Effort:** 1-2 weeks (1 engineer)

---

### Issue 18: MCP Registry Standard Alignment

**Priority:** P2
**Labels:** `enhancement`, `registry`, `standards`

**Description:**
Align AgentBreeder's MCP server registry (`registry/mcp_servers.py`, `api/routes/mcp_servers.py`) with the official MCP Registry API specification being developed under AAIF (AI Alliance Infrastructure Foundation). As the MCP ecosystem matures, having a standards-compliant registry will enable interoperability with other MCP-aware platforms.

The implementation should:
- Monitor the MCP Registry API specification as it evolves
- Adapt the internal MCP server registry schema to match the standard
- Expose a standards-compliant registry API endpoint
- Support importing MCP servers from external registries
- Support exporting AgentBreeder MCP servers to external registries

**Acceptance Criteria:**
- [ ] MCP registry schema aligns with the latest MCP Registry API draft
- [ ] Standards-compliant API endpoint exposed
- [ ] Import from external MCP registries works
- [ ] Export to external MCP registries works
- [ ] Backward compatibility maintained with existing AgentBreeder MCP server entries
- [ ] Unit tests for schema conversion
- [ ] Documentation for MCP registry interoperability

**Estimated Effort:** 1 week (1 engineer), plus ongoing alignment as spec evolves

---

### Issue 19: Kubernetes Agent Sandbox Integration Research

**Priority:** P2
**Labels:** `research`, `engine`, `cloud`

**Description:**
Research Google's Kubernetes Agent Sandbox technology (based on gVisor and WarmPools) for potential integration into AgentBreeder's Kubernetes deployer (Issue 13). Agent sandboxing provides enhanced isolation for AI agents executing arbitrary code or tools, which is critical for enterprise security.

Research areas:
- gVisor integration with Kubernetes pods for agent isolation
- WarmPool patterns for reducing cold start latency on sandboxed agents
- Security implications for tool execution (file system, network, process isolation)
- Performance overhead of gVisor vs standard container isolation
- Compatibility with AgentBreeder's sidecar pattern (Architecture Principle 3)
- Cost implications for enterprise deployments

**Acceptance Criteria:**
- [ ] Research document published in `docs/research/` covering all areas above
- [ ] Proof-of-concept demonstrating gVisor-isolated agent deployment
- [ ] Performance benchmarks: cold start, request latency, throughput (gVisor vs standard)
- [ ] Security analysis: threat model for agent tool execution with/without sandboxing
- [ ] Recommendation: adopt, defer, or reject, with justification
- [ ] If adopt: implementation plan with effort estimate for Issue 13 integration

**Estimated Effort:** 1-2 weeks (1 senior engineer)

---

### Issue 20: Enterprise SSO/SAML

**Priority:** P1
**Labels:** `enhancement`, `enterprise`, `revenue`

**Description:**
Add SSO (Single Sign-On) and SAML 2.0 integration for enterprise customers. This is a table-stakes requirement for enterprise sales -- no enterprise with > 100 employees will adopt a tool that requires separate credentials.

The implementation should:
- Support SAML 2.0 identity providers (Okta, Azure AD, OneLogin, Google Workspace)
- Support OIDC (OpenID Connect) as an alternative to SAML
- Map IdP groups/roles to AgentBreeder teams and RBAC roles
- Support Just-In-Time (JIT) user provisioning from IdP
- Support SCIM 2.0 for user/group synchronization
- Integrate with the existing JWT-based auth system (`api/auth.py`)

**Acceptance Criteria:**
- [ ] SAML 2.0 SSO flow works with Okta (primary test IdP)
- [ ] OIDC flow works with Azure AD
- [ ] IdP groups map to AgentBreeder teams
- [ ] JIT provisioning creates users on first login
- [ ] SCIM endpoint for user/group sync
- [ ] Admin UI for SSO configuration (upload metadata XML, configure mappings)
- [ ] Existing JWT auth continues to work (SSO is additive, not replacing)
- [ ] Unit and integration tests
- [ ] Documentation for SSO setup with Okta, Azure AD, and Google Workspace

**Estimated Effort:** 3-4 weeks (1 senior engineer)

---

### Issue 21: Compliance Export (SOC 2, HIPAA)

**Priority:** P1
**Labels:** `enhancement`, `enterprise`, `revenue`

**Description:**
Add audit log export functionality in formats required for SOC 2 Type II and HIPAA compliance. Enterprise customers in regulated industries (healthcare, finance) need to demonstrate that AI agent access and actions are auditable.

The implementation should:
- Export audit logs from `api/routes/audit.py` in compliance-ready formats
- SOC 2: Export access logs, configuration changes, deployment events, data access patterns
- HIPAA: Export PHI access logs, user authentication events, system access logs with timestamps
- Support scheduled exports (daily/weekly) to customer-owned S3/GCS buckets
- Support on-demand export via API and CLI (`agentbreeder audit export`)
- Include tamper-evident log signing (hash chain or similar)
- Retention policy configuration (e.g., 7 years for HIPAA)

**Acceptance Criteria:**
- [ ] SOC 2 audit export produces a report accepted by auditors
- [ ] HIPAA audit export includes all required access log fields
- [ ] Scheduled exports to S3/GCS work reliably
- [ ] On-demand export via API and `agentbreeder audit export` CLI command
- [ ] Log entries are tamper-evident (hash chain verification)
- [ ] Retention policy is configurable per organization
- [ ] Export formats: JSON, CSV, and PDF summary report
- [ ] Unit tests for export formatting and hash chain verification
- [ ] Documentation for compliance setup

**Estimated Effort:** 2-3 weeks (1 engineer)

---

### Issue 22: Cost Dashboard

**Priority:** P0
**Labels:** `enhancement`, `dashboard`

**Description:**
Build a comprehensive cost tracking dashboard in the web UI. Cost opacity is the #2 developer pain point with AI agents (after deployment complexity). AgentBreeder's cost tracking is a key differentiator and must be surfaced prominently.

The dashboard should show:
- **Per-agent cost:** Total cost, cost trend (7d/30d/90d), cost per request
- **Per-model cost:** Breakdown by model (Claude, GPT-4, etc.), input vs output tokens
- **Per-team cost:** Team-level aggregation for budget management
- **Cost alerts:** Configurable thresholds with email/Slack notifications
- **Cost anomaly detection:** Flag agents with sudden cost spikes
- **Budget tracking:** Set monthly budgets per team/agent, show burn rate
- **Export:** CSV/JSON export for finance teams

Data sources: the existing cost tracking in `api/routes/costs.py` and `api/services/`.

**Acceptance Criteria:**
- [ ] Dashboard page at `/costs` with all views listed above
- [ ] Per-agent, per-model, and per-team cost breakdowns are accurate
- [ ] Cost trend charts render for 7d, 30d, and 90d windows
- [ ] Cost alerts are configurable and trigger notifications
- [ ] Anomaly detection flags agents with > 2x cost increase
- [ ] Budget tracking shows burn rate and projected overage
- [ ] CSV/JSON export works
- [ ] Dashboard loads in < 2 seconds for orgs with 100+ agents
- [ ] Unit tests for cost calculation logic
- [ ] E2E tests for dashboard rendering

**Estimated Effort:** 2-3 weeks (1 frontend + 1 backend engineer)

---

### Issue 23: Marketplace MVP

**Priority:** P1
**Labels:** `epic`, `marketplace`, `revenue`

**Description:**
Build the community marketplace for sharing and discovering agent templates. The marketplace enables a network effect: every template published makes AgentBreeder more valuable for all users.

MVP features:

1. **Publishing:** `agentbreeder publish` CLI command packages an agent template (agent.yaml + code + README + tests) and publishes it to the marketplace. Requires a verified account.

2. **Browsing:** Web UI and CLI (`agentbreeder search --marketplace`) for discovering templates. Categories, tags, search, and sorting (stars, downloads, recency).

3. **Installing:** `agentbreeder init --template marketplace:<author>/<name>` installs a marketplace template locally.

4. **Featured templates:** Curated section of high-quality templates maintained by the AgentBreeder team.

5. **Quality gates:** Published templates must pass `agentbreeder validate`, include a README, and include at least one test.

Future (not MVP): ratings/reviews, paid templates, verified publishers.

**Acceptance Criteria:**
- [ ] `agentbreeder publish` packages and uploads a template
- [ ] Marketplace web UI shows browsable template catalog
- [ ] Search works by name, tag, framework, and description
- [ ] `agentbreeder init --template marketplace:<author>/<name>` installs a template
- [ ] Featured templates section is curated
- [ ] Quality gates enforce validation, README, and test requirements
- [ ] Published templates display download count and star count
- [ ] API endpoints in `api/routes/marketplace.py` are functional
- [ ] Unit and integration tests
- [ ] Documentation for publishing and consuming marketplace templates

**Estimated Effort:** 4-6 weeks (2 engineers + 1 designer)

---

## Legal & IP Issues

---

### Issue 24: File Patent Provisionals

**Priority:** P0
**Labels:** `legal`, `ip`

**Description:**
File three provisional patent applications to protect AgentBreeder's core innovations. Provisional patents establish a priority date and provide 12 months to file full utility patents. This is critical IP protection before public launch.

Patent applications:

1. **Three-Tier Builder with Bidirectional Ejection**
   - System and method for a three-tier agent development interface (No Code, Low Code, Full Code) where all tiers compile to the same internal representation
   - Bidirectional tier mobility: No Code generates editable YAML (Low Code), Low Code ejects to SDK code (Full Code), and all tiers share the same deploy pipeline
   - The deploy pipeline is tier-agnostic

2. **MCP Sidecar Injection**
   - System and method for automatically injecting a sidecar container alongside deployed AI agents
   - The sidecar intercepts all MCP (Model Context Protocol) tool calls for observability, cost attribution, and guardrail enforcement
   - Automatic injection without agent code modification

3. **Automatic Inter-Agent Tool Synthesis**
   - System and method for automatically generating tool interfaces between agents in a multi-agent orchestration
   - Given two agent definitions, the system synthesizes the API contract, authentication, and data transformation needed for one agent to call another as a tool
   - Uses A2A Agent Cards for discovery and contract generation

Budget: $10,000-$16,000 (approximately $3,500-$5,500 per provisional, including patent attorney fees).

**Acceptance Criteria:**
- [ ] Patent attorney engaged and retained
- [ ] Technical disclosure documents written for all three inventions
- [ ] Prior art search completed for each invention
- [ ] Three provisional patent applications filed with USPTO
- [ ] Filing receipts and serial numbers documented
- [ ] 12-month deadline for full utility patent filing calendared
- [ ] Budget tracked and within $10K-$16K range

**Estimated Effort:** 2-4 weeks (founder + patent attorney)

---

### Issue 25: Publish Defensive Blog Posts

**Priority:** P1
**Labels:** `legal`, `content`

**Description:**
Publish detailed technical blog posts for six innovations to establish publicly dated prior art. These posts serve as defensive publications -- if AgentBreeder cannot or chooses not to patent an innovation, the blog post prevents others from patenting it.

Blog posts to write:

1. **agent.yaml Schema Design** -- Detailed explanation of the agent.yaml specification, design decisions, and the philosophy of declarative agent configuration. Include the full schema with rationale for each field.

2. **secret:// URI Resolution** -- Technical deep dive into the `secret://` URI scheme for resolving secrets from multiple backends (env, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) at deploy time.

3. **Multi-Cloud Deployer Abstraction** -- Architecture of the deployer interface and how a single `agent.yaml` deploys to Docker Compose, ECS Fargate, Cloud Run, or Kubernetes without changes.

4. **Provider Fallback Chains** -- How AgentBreeder implements model provider fallback (primary -> fallback -> gateway) with automatic failover, health checking, and cost-aware routing.

5. **RFC 8594 API Versioning** -- Implementation of API versioning with deprecation headers following RFC 8594, enabling smooth API evolution without breaking clients.

6. **Org-Wide Registry Architecture** -- Design of the unified registry for agents, prompts, tools, MCP servers, models, and knowledge bases, enabling org-wide discovery and dependency resolution.

Each post must include: publication date, detailed technical description, code examples, architecture diagrams, and design rationale.

**Acceptance Criteria:**
- [ ] All six blog posts published on a publicly accessible, date-stamped platform
- [ ] Each post includes sufficient technical detail to serve as prior art
- [ ] Each post includes code examples and/or architecture diagrams
- [ ] Posts are archived on the Wayback Machine (archive.org) for permanence
- [ ] Cross-linked from AgentBreeder documentation
- [ ] Publication dates are verifiable (platform timestamps)

**Estimated Effort:** 2-3 weeks (1-2 engineers writing, 1 person reviewing)

---

### Issue 26: Apache 2.0 License + CLA Bot

**Priority:** P0
**Labels:** `legal`, `community`

**Description:**
Formalize AgentBreeder's open-source licensing and contributor agreement before public launch. This must be done before accepting any external contributions.

Steps:

1. **Add Apache 2.0 LICENSE file** to the repository root. Apache 2.0 is chosen because it provides patent protection (contributors grant patent license), is permissive (enterprise-friendly), and is compatible with most other open-source licenses.

2. **Set up CLA Assistant bot** (https://github.com/cla-assistant/cla-assistant or similar). The CLA ensures that all contributors grant AgentBreeder sufficient rights to use their contributions, including the right to relicense under a commercial license for the enterprise product (open core model).

3. **Add CONTRIBUTING.md** with:
   - How to set up the development environment
   - Code style and testing requirements
   - PR process and review expectations
   - CLA signing instructions
   - Code of Conduct reference

4. **Add CODE_OF_CONDUCT.md** adopting the Contributor Covenant.

**Acceptance Criteria:**
- [ ] `LICENSE` file with Apache 2.0 text at repository root
- [ ] CLA Assistant bot configured and active on the repository
- [ ] CLA text reviewed by legal counsel
- [ ] `CONTRIBUTING.md` with development setup and contribution guidelines
- [ ] `CODE_OF_CONDUCT.md` with Contributor Covenant
- [ ] Bot blocks PR merge until CLA is signed
- [ ] All existing contributors have signed the CLA

**Estimated Effort:** 3-5 days (1 person + legal review)

---

### Issue 27: Trademark Registration

**Priority:** P1
**Labels:** `legal`

**Description:**
Register "AgentBreeder" as a trademark with the United States Patent and Trademark Office (USPTO) to protect the brand name from unauthorized use by competitors.

Steps:

1. **Trademark search:** Conduct a comprehensive search to ensure "AgentBreeder" is not already registered or in use for similar goods/services (software, developer tools, AI platforms).

2. **File trademark application** in the relevant classes:
   - Class 9: Computer software for building, deploying, and managing AI agents
   - Class 42: Software as a service (SaaS) for AI agent deployment and governance

3. **Monitor application** through examination, publication, and registration phases.

Consider also filing for the AgentBreeder logo (design mark) once finalized.

**Acceptance Criteria:**
- [ ] Trademark search completed with no blocking conflicts
- [ ] Trademark application filed with USPTO
- [ ] Application serial number documented
- [ ] Filing covers Class 9 and Class 42
- [ ] Monitoring process established for office actions
- [ ] Budget: approximately $1,000-$2,500 (filing fees + attorney if needed)

**Estimated Effort:** 1-2 weeks for filing (attorney handles ongoing prosecution)

---

## Business Issues

---

### Issue 28: Incorporate Delaware C-Corp

**Priority:** P0
**Labels:** `legal`, `business`

**Description:**
Incorporate AgentBreeder as a Delaware C-Corporation. Delaware C-Corp is the standard structure for venture-backed startups due to well-established corporate law, investor familiarity, and flexibility for equity compensation.

Steps:

1. **Incorporation:** Use Stripe Atlas ($500) or Clerky ($799+) for streamlined incorporation. Include:
   - Certificate of Incorporation with standard protective provisions
   - Bylaws
   - Initial board consent
   - 83(b) elections for founders

2. **Equity structure:**
   - Authorize 10,000,000 shares of common stock
   - Standard 4-year vesting with 1-year cliff for all founders
   - Set aside 10-15% option pool for future employees

3. **Post-incorporation:**
   - Obtain EIN from IRS
   - Open business bank account (Mercury or similar)
   - Register as foreign corporation in states where founders reside (if not Delaware)
   - Set up cap table management (Carta, Pulley, or AngelList)

**Acceptance Criteria:**
- [ ] Delaware C-Corp incorporated with Certificate of Incorporation
- [ ] Bylaws and initial board consent adopted
- [ ] Founder stock issued with 4-year vesting, 1-year cliff
- [ ] 83(b) elections filed within 30 days of stock grant
- [ ] EIN obtained
- [ ] Business bank account opened
- [ ] Cap table management tool set up
- [ ] Option pool reserved (10-15%)
- [ ] Budget: $500-$2,000 for incorporation + $500-$1,000 for legal review

**Estimated Effort:** 1-2 weeks (founder, mostly waiting for processing)

---

### Issue 29: Prepare Data Room for Fundraising

**Priority:** P1
**Labels:** `business`, `fundraising`

**Description:**
Prepare a comprehensive data room for investor due diligence. Even if not raising immediately, having a ready data room demonstrates professionalism and enables opportunistic fundraising conversations.

Data room contents:

1. **Pitch deck** (15-20 slides):
   - Problem: AI agents are hard to deploy, impossible to govern
   - Solution: AgentBreeder -- define once, deploy anywhere, govern automatically
   - Demo: screenshot or GIF of the deploy flow
   - Market: TAM/SAM/SOM for AI agent infrastructure
   - Traction: GitHub stars, users, agents deployed, template downloads
   - Business model: Open core (free OSS + paid cloud + enterprise)
   - Competition: landscape with AgentBreeder's positioning
   - Team: founder backgrounds
   - Ask: amount, use of funds, milestones

2. **Financial model** (3-year projection):
   - Revenue streams: Cloud platform, Enterprise licenses
   - Unit economics: CAC, LTV, gross margin
   - Growth assumptions tied to product milestones

3. **Cap table:** Current ownership, option pool, any SAFEs/convertible notes

4. **Legal documents:**
   - Certificate of Incorporation
   - Bylaws
   - IP assignment agreements
   - Any existing investor agreements

5. **Technical architecture:** High-level system diagram, technology choices, scalability plan

**Acceptance Criteria:**
- [ ] Pitch deck completed and reviewed by 2+ advisors
- [ ] Financial model with 3-year projections and sensitivity analysis
- [ ] Cap table is accurate and up to date
- [ ] All legal documents organized and accessible
- [ ] Technical architecture document with scalability narrative
- [ ] Data room hosted on a secure platform (DocSend, Google Drive, or Notion)
- [ ] All documents are up to date as of launch date

**Estimated Effort:** 2-3 weeks (founder + advisors)

---

### Issue 30: YC Application (if applicable)

**Priority:** P1
**Labels:** `business`, `fundraising`

**Description:**
Apply to Y Combinator with AgentBreeder. YC provides funding ($500K standard deal), mentorship, network access, and significant credibility for developer tool companies. The application should be submitted for the next available batch.

Application preparation:

1. **Application form:**
   - Company description (clear, jargon-free explanation of AgentBreeder)
   - Problem statement (why deploying and governing AI agents is painful today)
   - Solution (how AgentBreeder solves it, with specific examples)
   - Traction metrics (GitHub stars, users, agents deployed, growth rate)
   - Market size (AI agent infrastructure TAM)
   - Team (why this team is uniquely qualified)
   - Technical insight (what do we understand that others do not)

2. **Demo video** (1 minute):
   - Show `agentbreeder deploy` end-to-end
   - Show governance features in dashboard
   - Show three-tier builder (No Code, Low Code, Full Code)

3. **Supporting materials:**
   - GitHub repository (clean, well-documented, active)
   - Live demo instance (if possible)
   - Usage metrics dashboard

4. **Interview preparation** (if invited):
   - Practice 10-minute pitch with Q&A
   - Prepare for common YC questions: "Why now?", "What's your moat?", "How do you make money?", "What's your growth rate?"
   - Have specific customer/user stories ready

**Acceptance Criteria:**
- [ ] YC application submitted before deadline
- [ ] Application reviewed by at least 2 YC alumni or advisors
- [ ] 1-minute demo video recorded and polished
- [ ] GitHub repo is in presentable state (README, docs, examples, CI green)
- [ ] Interview prep doc with answers to 20 common questions
- [ ] Team available for interview dates

**Estimated Effort:** 1-2 weeks (founder + team input)

---

## Summary

| # | Issue | Priority | Category | Effort |
|---|-------|----------|----------|--------|
| 1 | Create 20+ Agent Templates | P0 | Growth | 3-4 weeks |
| 2 | Record "5-Minute Demo" Video | P0 | Growth | 3-5 days |
| 3 | Write "Migrate from X" Guides | P0 | Growth | 2 weeks |
| 4 | Create Comparison Pages | P1 | Growth | 1 week |
| 5 | Landing Page & README Redesign | P0 | Growth | 1 week |
| 6 | Launch Discord Community | P0 | Growth | 2-3 days |
| 7 | Show HN Launch Preparation | P1 | Growth | 3-5 days |
| 8 | Weekly Content Calendar | P1 | Growth | Ongoing |
| 9 | Implement CrewAI Runtime | P1 | Technical | 1-2 weeks |
| 10 | Implement Claude SDK Runtime | P1 | Technical | 1-2 weeks |
| 11 | Implement Google ADK Runtime | P1 | Technical | 1-2 weeks |
| 12 | AWS ECS Deployer | P0 | Technical | 2-3 weeks |
| 13 | Kubernetes Deployer | P1 | Technical | 2-3 weeks |
| 14 | AgentBreeder Cloud Platform | P0 | Technical | 8-12 weeks |
| 15 | OTel GenAI Semantic Conventions | P1 | Technical | 1-2 weeks |
| 16 | Auto-generate AGENTS.md | P2 | Technical | 3-5 days |
| 17 | A2A Agent Cards for Registry | P1 | Technical | 1-2 weeks |
| 18 | MCP Registry Standard Alignment | P2 | Technical | 1 week |
| 19 | K8s Agent Sandbox Research | P2 | Technical | 1-2 weeks |
| 20 | Enterprise SSO/SAML | P1 | Technical | 3-4 weeks |
| 21 | Compliance Export (SOC 2, HIPAA) | P1 | Technical | 2-3 weeks |
| 22 | Cost Dashboard | P0 | Technical | 2-3 weeks |
| 23 | Marketplace MVP | P1 | Technical | 4-6 weeks |
| 24 | File Patent Provisionals | P0 | Legal | 2-4 weeks |
| 25 | Publish Defensive Blog Posts | P1 | Legal | 2-3 weeks |
| 26 | Apache 2.0 License + CLA Bot | P0 | Legal | 3-5 days |
| 27 | Trademark Registration | P1 | Legal | 1-2 weeks |
| 28 | Incorporate Delaware C-Corp | P0 | Legal/Business | 1-2 weeks |
| 29 | Prepare Data Room | P1 | Business | 2-3 weeks |
| 30 | YC Application | P1 | Business | 1-2 weeks |

**P0 items (launch blockers):** Issues 1, 2, 3, 5, 6, 12, 14, 22, 24, 26, 28
**P1 items (high priority):** Issues 4, 7, 8, 9, 10, 11, 13, 15, 17, 20, 21, 23, 25, 27, 29, 30
**P2 items (important but deferrable):** Issues 16, 18, 19
