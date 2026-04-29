# microlearning-ebook-agent — Claude Code Context

## What This Agent Does
Help corporate L&D teams produce bite-sized training material on demand without hiring instructional designers. User submits a topic; the agent researches it, structures the content into microlearning modules, generates quizzes, and renders a downloadable PDF/EPUB.

## Stack
- **Framework:** Google ADK (Full Code) — stateful multi-actor workflow with checkpoints, HITL approval, and parallel research branches
- **Model:** gemini-2.5-pro — strong reasoning for research synthesis + quiz generation; GCP-native
- **RAG:** None — live web research only, no internal corpus
- **Memory:** Long-term (PostgreSQL) — ADK session checkpoints + HITL approval state — see `memory/config.py`
- **Deploy:** GCP Cloud Run (us-central1, scale-to-zero)

## Rules for AI-Assisted Development
- New tools go in `tools/` as typed Python functions with docstrings
- Tests required for every new tool — see `tests/test_agent.py` for patterns
- Run `agentbreeder validate` before every commit
- Never modify `agent.yaml` model fields without re-running `tests/evals/`
- Never store PII in memory without a TTL
- Never bypass RBAC or eval gates
- The pipeline is research_web -> extract_concepts -> structure_modules -> generate_quiz -> [HITL pause] -> render_ebook. Do not reorder without updating evals.
