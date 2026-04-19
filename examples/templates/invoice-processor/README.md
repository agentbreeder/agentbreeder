# Invoice Processor Agent

Processes vendor invoices end-to-end: extracts data from PDFs, validates against POs, routes for approval by amount, and posts to your accounting system.

## Use Case

Accounts payable teams manually process hundreds of invoices per month: opening email attachments, keying in vendor names, amounts, and GL codes, checking against purchase orders, routing to the right approver, and posting to the accounting system. This agent automates the full AP workflow. Invoices arrive by email, the agent extracts all structured data with high accuracy, validates against existing POs and business rules, and routes to the right tier of approver based on configurable thresholds. Small invoices below the auto-approve threshold are processed completely without human intervention. PII and financial data are protected by guardrails throughout.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Email service with API access (configured to forward vendor invoices to the AP inbox)
- Accounting system API access (QuickBooks, NetSuite, Xero, or similar)
- Anthropic API key

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | [console.anthropic.com](https://console.anthropic.com) |
| `EMAIL_SERVICE_API_KEY` | Email service API key (SendGrid, Postmark, etc.) | Your email service provider |
| `ACCOUNTING_API_KEY` | Accounting system API key | Your accounting system settings |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTO_APPROVE_THRESHOLD` | Max amount (USD) for auto-approval without human review | `500` |
| `MANAGER_APPROVAL_THRESHOLD` | Amount above which manager approval is required | `5000` |
| `DIRECTOR_APPROVAL_THRESHOLD` | Amount above which director approval is required | `25000` |
| `LOW_CONFIDENCE_THRESHOLD` | Extraction confidence below which manual review is flagged | `0.85` |
| `AP_EMAIL` | Accounts payable email address | `ap@company.com` |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use invoice-processor my-invoice-processor

# 2. Configure approval thresholds in agent.yaml env_vars to match your policy

# 3. Set credentials
agentbreeder secret set ANTHROPIC_API_KEY
agentbreeder secret set EMAIL_SERVICE_API_KEY
agentbreeder secret set ACCOUNTING_API_KEY

# 4. Configure your AP email to forward to the agent's webhook endpoint
# 5. Deploy
agentbreeder deploy --target aws
```

## Customization

- **Adjust approval tiers**: Modify the threshold env vars to match your company's approval policy
- **Add PO matching**: Connect to your procurement system to enable automatic PO matching validation
- **Multi-currency**: The agent handles currency detection — add exchange rate lookup for multi-currency invoice validation
- **Add Slack notifications**: Notify the AP team in Slack when high-priority invoices are queued
- **Add duplicate detection**: Extend `validate_invoice` with a database lookup to check for previously processed invoices with the same number and vendor
- **Customize GL codes**: Update the extraction prompt to suggest GL codes based on your chart of accounts

## Agent Behavior

1. Triggered by email webhook when a new invoice arrives at the AP inbox
2. Extracts the PDF or image attachment from the email
3. Calls `extract_invoice_data` to pull all structured fields with a confidence score
4. If `extraction_confidence < LOW_CONFIDENCE_THRESHOLD`: routes to human review queue instead of continuing
5. Calls `validate_invoice` to check for duplicates, PO mismatches, and amount discrepancies
6. If validation issues found: flags for AP team review with issue details
7. Determines approval tier based on total amount and thresholds
8. If `auto_approve`: posts directly to the accounting system
9. Otherwise: calls `route_for_approval` to email the appropriate approver with context
10. Logs every invoice and decision to the AgentBreeder audit trail (with PII stripped from logs)

## Cost Estimate

~$0.25–$0.50 per 100 invoices using `claude-sonnet-4-6` at 0.1 temperature. The very low temperature ensures highly consistent extraction and validation results.
