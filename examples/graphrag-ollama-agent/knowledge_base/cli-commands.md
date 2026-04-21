# AgentBreeder CLI Reference

## Core Commands

### agentbreeder init
Scaffold a new agent project in the current directory. Generates agent.yaml, Dockerfile, and example code.

```bash
agentbreeder init --framework langgraph --cloud aws
```

### agentbreeder deploy
Deploy an agent from an agent.yaml file. This is the primary command.

```bash
agentbreeder deploy                      # deploy from current directory
agentbreeder deploy --target aws         # explicit cloud target
agentbreeder deploy --target local       # deploy locally via Docker Compose
agentbreeder deploy agent.yaml           # explicit file path
```

Governance is automatic: RBAC check, container build, deploy, registry registration.

### agentbreeder validate
Validate an agent.yaml against the JSON Schema and registry refs. Does not deploy.

```bash
agentbreeder validate agent.yaml
```

### agentbreeder chat
Open an interactive chat session with a deployed agent.

```bash
agentbreeder chat --agent my-agent
agentbreeder chat --agent my-agent "What can you help with?"
```

### agentbreeder eject
Eject from No Code to YAML, or from YAML to Full Code (Python SDK).

```bash
agentbreeder eject --to yaml              # generates agent.yaml from visual builder state
agentbreeder eject --to code              # generates Python SDK code from agent.yaml
```

### agentbreeder eval
Run evaluations on a deployed agent.

```bash
agentbreeder eval --agent my-agent --dataset evals/qa.jsonl
```

## Registry Commands

### agentbreeder search
Search the org registry for agents, tools, prompts, models.

```bash
agentbreeder search "customer support"
agentbreeder search --type tool zendesk
```

### agentbreeder list
List registered agents.

```bash
agentbreeder list
agentbreeder list --team engineering
```

## Secrets Management

### agentbreeder secret
Manage secrets across backends (env, AWS Secrets Manager, GCP Secret Manager, Vault).

```bash
agentbreeder secret set OPENAI_API_KEY --backend aws
agentbreeder secret get OPENAI_API_KEY
agentbreeder secret list
```
