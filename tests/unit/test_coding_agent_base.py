from engine.coding_agent.base import (
    CODING_TOOLS,
    TOOL_NAMES,
    AgentBounds,
    AgentEvent,
)


def test_agent_event_token_defaults():
    e = AgentEvent(type="token", text="hi")
    assert e.path == ""
    assert e.diff == ""
    assert e.error == ""


def test_coding_tools_cover_fs_surface():
    assert TOOL_NAMES == {"write_file", "read_file", "list_files", "run_command"}
    assert all(t.function.name in TOOL_NAMES for t in CODING_TOOLS)


def test_bounds_defaults_are_sane():
    b = AgentBounds()
    assert b.max_turns >= 1
    assert b.wall_clock_s > 0
    assert b.max_tokens > 0
