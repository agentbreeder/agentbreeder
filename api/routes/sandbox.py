"""Sandbox API routes — execute tools in isolated Docker containers."""

from __future__ import annotations

from fastapi import APIRouter

from api.models.schemas import ApiResponse, SandboxExecuteRequest, SandboxExecuteResponse
from api.services.sandbox_service import SandboxExecutionRequest, execute

router = APIRouter(prefix="/api/v1/tools/sandbox", tags=["sandbox"])


@router.post("/execute", response_model=ApiResponse[SandboxExecuteResponse])
async def execute_tool_in_sandbox(
    body: SandboxExecuteRequest,
) -> ApiResponse[SandboxExecuteResponse]:
    """Execute tool code in an ephemeral sandbox container.

    Runs the provided Python code in an isolated Docker container (or
    subprocess fallback) with the given input JSON. Returns stdout, stderr,
    structured output, exit code, and execution duration.
    """
    request = SandboxExecutionRequest(
        code=body.code,
        input_json=body.input_json,
        timeout_seconds=body.timeout_seconds,
        network_enabled=body.network_enabled,
        tool_id=body.tool_id,
    )

    result = await execute(request)

    return ApiResponse(
        data=SandboxExecuteResponse(
            execution_id=result.execution_id,
            output=result.output,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            error=result.error,
        )
    )
