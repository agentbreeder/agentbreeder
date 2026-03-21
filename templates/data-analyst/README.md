# Data Analyst Agent

SQL-powered data analysis agent that queries databases, performs statistical analysis, and generates visualizations. Designed for business users who need data insights without writing SQL.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Database with read-only credentials (PostgreSQL, MySQL, or SQLite)
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your database URL and API keys
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Ask a question:**
   ```bash
   agentbreeder chat data-analyst-agent --message "What were our top 10 products by revenue last quarter?"
   ```

## Architecture

```
Business Question
    |
    v
[Understand] -- Clarify the question
    |
    v
[Explore] -- Discover table schemas
    |
    v
[Query] -- Execute read-only SQL
    |
    v
[Analyze] -- Statistical summary
    |
    v
[Visualize] -- Generate charts
    |
    v
Plain-language explanation with charts
```

### Safety

- **Read-only mode** -- only SELECT queries are allowed
- **Query timeout** -- 30-second default to prevent runaway queries
- **Row limits** -- max 10,000 rows per query
- **PII detection** -- sensitive data is filtered from responses

## Customization

### Connect to a different database

Update `DATABASE_URL` in your `.env` file:
```bash
# PostgreSQL
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# MySQL
DATABASE_URL=mysql://user:pass@host:3306/dbname
```

### Adjust query limits

```yaml
env_vars:
  SQL_TIMEOUT_SECONDS: "60"
  MAX_RESULT_ROWS: "50000"
```

### Allow access for additional teams

```yaml
access:
  allowed_callers:
    - team:data-engineering
    - team:finance          # Add more teams
    - team:marketing
```
