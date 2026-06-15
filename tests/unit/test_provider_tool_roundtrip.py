import json

from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.models import ProviderConfig, ProviderType
from engine.providers.openai_provider import OpenAIProvider


def _messages():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "build it"},
        {
            "role": "assistant",
            "content": "Creating agent.py",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function_name": "write_file",
                    "function_arguments": json.dumps({"path": "agent.py", "content": "x=1\n"}),
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "wrote agent.py (4 bytes)"},
    ]


def test_anthropic_builds_tool_use_and_tool_result_blocks():
    p = AnthropicProvider(ProviderConfig(provider_type=ProviderType.anthropic, api_key="sk-x"))
    payload = p._build_payload(_messages(), "claude-sonnet-4-6", None, 1024, None)
    msgs = payload["messages"]
    asst = next(m for m in msgs if m["role"] == "assistant")
    tu = [b for b in asst["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert tu
    assert tu[0]["id"] == "call_1"
    assert tu[0]["name"] == "write_file"
    assert tu[0]["input"] == {"path": "agent.py", "content": "x=1\n"}
    tr_turns = [
        m
        for m in msgs
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert tr_turns
    block = next(b for b in tr_turns[-1]["content"] if b.get("type") == "tool_result")
    assert block["tool_use_id"] == "call_1"


def test_openai_translates_toolcall_shape():
    p = OpenAIProvider(ProviderConfig(provider_type=ProviderType.openai, api_key="sk-x"))
    payload = p._build_payload(_messages(), "gpt-4o", None, None, None, False)
    asst = next(m for m in payload["messages"] if m["role"] == "assistant")
    fn = asst["tool_calls"][0]["function"]
    assert fn["name"] == "write_file"
    assert json.loads(fn["arguments"])["path"] == "agent.py"
    tool_msg = next(m for m in payload["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
