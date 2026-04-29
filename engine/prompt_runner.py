"""Render a registered prompt by sending it to a real LLM.

Used by the prompt registry's "Try it" UI panel and the
``agentbreeder registry prompt try`` CLI command.

Currently supports the Google AI Studio API (any ``gemini-*`` model) using
``GOOGLE_API_KEY``. Other providers can be added behind the same
``run_prompt`` interface.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


@dataclass
class PromptRunResult:
    output: str
    model: str
    duration_ms: int
    error: str | None = None


async def run_prompt(
    system_prompt: str,
    user_message: str,
    model: str = "gemini-2.5-flash",
    temperature: float = 0.4,
) -> PromptRunResult:
    """Send the (system, user) pair to an LLM and return the response text.

    The model string determines the provider:
      * ``gemini-*``  -> Google AI Studio (requires GOOGLE_API_KEY)
      * other         -> raises NotImplementedError until a provider is wired

    A failure to call the model returns a ``PromptRunResult`` with ``error``
    set rather than raising — the caller surfaces it to the UI.
    """
    started = time.perf_counter()

    if not model.startswith("gemini"):
        return PromptRunResult(
            output="",
            model=model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=(
                f"Provider for model '{model}' is not yet wired. Currently "
                f"only Google AI Studio (gemini-*) is supported in the "
                f"prompt-render endpoint."
            ),
        )

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return PromptRunResult(
            output="",
            model=model,
            duration_ms=0,
            error="GOOGLE_API_KEY is not set on the API server environment.",
        )

    try:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=api_key)
        contents: list[genai_types.Content] = []
        if user_message.strip():
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=user_message)],
                )
            )
        else:
            contents.append(
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text="(no input provided)")],
                )
            )

        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=2048,
        )
        resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        text_out = ""
        for cand in resp.candidates or []:
            if cand.content and cand.content.parts:
                for p in cand.content.parts:
                    text = getattr(p, "text", None)
                    if text:
                        text_out += text
        duration_ms = int((time.perf_counter() - started) * 1000)
        return PromptRunResult(output=text_out, model=model, duration_ms=duration_ms)
    except Exception as exc:  # noqa: BLE001 — surface exact error to the UI
        return PromptRunResult(
            output="",
            model=model,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )
