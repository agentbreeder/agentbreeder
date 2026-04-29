# AGENTS.md — AI Skills for microlearning-ebook-agent

## add-tool
Add a new tool to this agent.
Read `tools/research_web.py` first for patterns.
Files: `tools/`, `agent.yaml` (tools section), `tests/test_agent.py`

## update-prompt
Revise the system prompt. Read `ARCHITECT_NOTES.md` for original intent.
Files: `agent.yaml` (prompts.system)

## add-eval
Add a new eval test case. Read `tests/evals/criteria.md` for dimensions.
Files: `tests/evals/eval_runner.py`, `tests/evals/promptfooconfig.yaml`, `tests/evals/criteria.md`

## deploy
Validate and deploy.
Commands: `agentbreeder validate && agentbreeder deploy --target gcp`
