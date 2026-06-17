"""LangGraph agent targeting AWS App Runner — serverless containers.

This is the same shape as ``examples/langgraph-agent`` but the ``agent.yaml``
sets ``deploy.cloud: aws`` + ``deploy.runtime: app-runner`` so ``agentbreeder
deploy`` builds the image, pushes it to ECR, and creates an App Runner
service. No VPC / ALB / NAT gateway required.

Export ``graph`` — the AgentBreeder server wrapper looks for this symbol.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import StateGraph


class AgentState(TypedDict):
    message: str
    response: str


def respond(state: AgentState) -> AgentState:
    message = state.get("message", "")
    return {
        "message": message,
        "response": f"Hello from AgentBreeder on AWS App Runner! You said: {message}",
    }


builder = StateGraph(AgentState)
builder.add_node("respond", respond)
builder.set_entry_point("respond")
builder.set_finish_point("respond")

graph = builder.compile()
