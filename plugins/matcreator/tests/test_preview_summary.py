from backend.plugins.loader import load_plugin_registry


def test_research_preview_projects_counts_without_task_content():
    definition = load_plugin_registry().node_type("matcreator.tasks").document
    action = definition.actions["summary"]
    assert action.read_only and action.project
    value = {"plans": [{"title": "Copper study", "tasks": [
        {"status": "done", "description": "Large scientific instructions", "depends_on": []},
        {"status": "pending", "description": "More instructions", "depends_on": ["first"]},
    ]}]}
    assert action.handler(value, {}) == {"total": 2, "done": 1, "latest_title": "Copper study"}
