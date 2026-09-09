import pytest

from app.plugin_storage import PluginStorage


@pytest.mark.anyio
async def test_plugin_state_keeps_project_and_conversation_scopes_separate(tmp_path):
    state = PluginStorage(tmp_path / "plugins.sqlite3").for_plugin("demo", "conv-1")

    await state.set("phase", "exercise")
    await state.set("summary", "shared note", scope="project")

    assert await state.get("phase") == "exercise"
    assert await state.get("summary") is None
    assert await state.get("summary", scope="project") == "shared note"


@pytest.mark.anyio
async def test_plugin_state_rejects_unknown_scope(tmp_path):
    state = PluginStorage(tmp_path / "plugins.sqlite3").for_plugin("demo", "conv-1")
    with pytest.raises(ValueError):
        await state.get("x", scope="other")
