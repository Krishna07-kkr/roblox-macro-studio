"""Tool registry: the one place a tool is added (Constitution I, SC-002)."""

import pytest

from macro_studio import registry
from macro_studio.registry import ToolSpec


def _spec(tool_id, order):
    return ToolSpec(id=tool_id, title=tool_id.title(), order=order, factory=lambda s, c: None)


def test_tools_come_back_in_sidebar_order():
    specs = [_spec("b", 20), _spec("a", 10), _spec("c", 30)]
    assert [s.id for s in registry.ordered(specs)] == ["a", "b", "c"]


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="dup"):
        registry.ordered([_spec("dup", 1), _spec("dup", 2)])


def test_registered_tools_are_valid_and_macro_comes_first():
    tools = registry.ordered(registry.TOOLS)
    assert tools[0].id == "macro"
    for spec in tools:
        assert spec.id.replace("_", "").isalnum() and spec.id == spec.id.lower()
        assert spec.title and callable(spec.factory)


def test_registry_entries_load_tool_code_lazily():
    # Importing the registry must stay cheap and Tk-free: each entry's factory is a
    # small function in the registry that imports its tool only when the shell builds it.
    for spec in registry.TOOLS:
        assert spec.factory.__module__ == "macro_studio.registry", spec.id
