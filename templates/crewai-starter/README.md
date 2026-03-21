# CrewAI Starter

Sequential CrewAI crew with a researcher and writer agent. Use this as a starting point for building multi-agent CrewAI workflows with AgentBreeder.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Python 3.11+ with `crewai` installed
- Anthropic API key or OpenAI API key

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Run the crew:**
   ```bash
   agentbreeder chat crewai-starter-agent --message "Write an article about quantum computing"
   ```

## Architecture

```
Input Topic
    |
    v
[Researcher Agent] -- Searches and compiles research brief
    |
    v
[Writer Agent] -- Transforms research into polished article
    |
    v
Final Article
```

### Key Files

- `agent.yaml` -- AgentBreeder configuration
- `crew.py` -- CrewAI crew definition (entry point)

### Crew Structure

- **Researcher** -- finds and organizes information using the search tool
- **Writer** -- produces polished content from research findings

## Customization

### Add more agents

Define new agents and tasks in `crew.py`, then add them to the `Crew`:

```python
editor = Agent(
    role="Editor",
    goal="Review and polish the article for clarity and accuracy",
    backstory="You are a senior editor...",
)

editing_task = Task(
    description="Review the article...",
    agent=editor,
)

crew = Crew(
    agents=[researcher, writer, editor],
    tasks=[research_task, writing_task, editing_task],
    process=Process.sequential,
)
```

### Switch to hierarchical process

```python
crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    process=Process.hierarchical,
    manager_llm="claude-sonnet-4",
)
```
