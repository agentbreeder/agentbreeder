"""Memory configuration for microlearning-ebook-agent.

Long-term PostgreSQL memory backs ADK session checkpoints (resume-from-failure)
and human-in-the-loop approval state (an outline can sit pending for hours).
"""

import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/agentdb")


def get_db_url() -> str:
    return DATABASE_URL
