# SQL Analyst Agent

Translates natural language questions into SQL, executes queries, and returns results with plain-language explanations.

## Use Case

Non-technical stakeholders in product, marketing, and operations often have data questions that require waiting for a data engineer to write a query. This agent enables self-service analytics by accepting natural language questions ("What were our top 10 customers by revenue last quarter?"), generating the correct SQL using knowledge of your schema and data dictionary, executing it safely against BigQuery or Postgres, and returning the results with an explanation and a chart recommendation. Write queries are blocked by default, and row limits prevent runaway scans.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- BigQuery project with service account credentials, or Postgres/Snowflake connection string
- Data dictionary registered in the AgentBreeder registry as `kb/data-dictionary`
- Schema documentation registered as `kb/schema-docs`

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `BIGQUERY_CREDENTIALS` | GCP service account JSON key | Google Cloud Console → IAM → Service Accounts |
| `DATABASE_URL` | Postgres/Snowflake connection string (if not BigQuery) | Your DBA or cloud console |
| `ANTHROPIC_API_KEY` | Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use sql-analyst my-sql-analyst

# 2. Register your data dictionary and schema docs
agentbreeder register kb data-dictionary ./docs/data-dictionary.yaml
agentbreeder register kb schema-docs ./docs/schema/
agentbreeder register kb sql-examples ./docs/example-queries/

# 3. Set credentials
agentbreeder secret set BIGQUERY_CREDENTIALS
agentbreeder secret set ANTHROPIC_API_KEY

# 4. Configure the database in agent.yaml env_vars (DEFAULT_DATABASE)
# 5. Deploy
agentbreeder deploy --target aws

# 6. Test it
curl -X POST https://your-agent-url/query \
  -d '{"question": "What were our top 10 customers by revenue last month?"}'
```

## Customization

- **Enable write queries**: Set `ALLOW_WRITE_QUERIES: "true"` and add guardrails carefully — not recommended for production
- **Add more data sources**: Extend `database-mcp` to support multiple connections (e.g., BigQuery for analytics, Postgres for transactional data)
- **Increase row limit**: Change `MAX_QUERY_ROWS` for use cases where full result sets are needed
- **Add result caching**: Deploy a Redis layer to cache frequent queries — significant cost savings for dashboards
- **Embed in Slack**: Add `ref: tools/slack-mcp` and listen for `@analyst <question>` mentions in data channels
- **Connect to BI tools**: Use the agent's API as a backend for Retool, Metabase, or a custom dashboard

## Agent Behavior

1. Receives a natural language question via API or Slack
2. Fetches relevant schema context from `kb/data-dictionary` and `kb/schema-docs`
3. Calls `generate_sql` to produce a validated SQL query with explanation
4. If `estimated_row_scan: very_large`, asks for user confirmation before executing
5. Calls `execute_query` with row limit and timeout safeguards
6. Returns results with the plain-language explanation
7. Calls `suggest_visualization` to recommend how to chart the results
8. Logs the query, execution time, and rows scanned to the audit trail

## Cost Estimate

~$0.30–$0.80 per 100 queries using `claude-sonnet-4-6` at 0.2 temperature. Schema context is prompt-cached for significant savings on repeated queries.
