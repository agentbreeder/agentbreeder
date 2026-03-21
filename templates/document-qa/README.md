# Document QA Agent

RAG-based document question-answering agent that searches over indexed documents, retrieves relevant passages, and answers questions with citations and source references.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Documents indexed in an AgentBreeder knowledge base
- Anthropic API key (primary) and OpenAI API key (fallback + embeddings)

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

3. **Ask a question:**
   ```bash
   agentbreeder chat document-qa-agent --message "What is our company's remote work policy?"
   ```

## Architecture

```
User Question
    |
    v
[Search Documents] -- Semantic search over indexed docs
    |
    v
[Retrieve Passages] -- Top-K ranked passages with metadata
    |
    v
[Generate Answer] -- Claude Sonnet with cited evidence
    |
    v
Answer + Citations + Related Topics
```

### Supported Document Types

- PDF, DOCX, TXT, Markdown
- HTML pages (crawled)
- Confluence/Notion exports

### Response Format

Each answer includes:
- Direct answer (1-2 sentences)
- Supporting evidence (quoted passages with document name + page/section)
- Related topics for further exploration

## Customization

### Tune retrieval quality

```yaml
env_vars:
  SEARCH_TOP_K: "20"                  # Retrieve more passages
  CHUNK_SIZE: "1024"                   # Larger chunks for more context
  CHUNK_OVERLAP: "100"                 # More overlap to avoid splitting
  EMBEDDING_MODEL: text-embedding-3-large  # Higher quality embeddings
```

### Add more knowledge bases

```yaml
knowledge_bases:
  - ref: kb/company-documents
  - ref: kb/engineering-wiki
  - ref: kb/product-specs
```

### Use a different vector store

Update the vector store URL in `.env`. Supported backends include Qdrant, Pinecone, and Weaviate.
