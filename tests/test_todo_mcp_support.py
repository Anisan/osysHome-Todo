"""Tests for Todo MCP support."""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from plugins.Todo import mcp_support
from plugins.Todo.services import notification_service


@contextmanager
def _noop_mcp_scope():
    yield


class TestTodoMcpSupport(unittest.TestCase):
    def test_capabilities(self):
        caps = mcp_support.mcp_capabilities()
        self.assertTrue(caps.get("entities"))
        collection_ids = {item["id"] for item in caps.get("collections", [])}
        self.assertEqual(collection_ids, {"lists", "tasks", "hooks"})
        ops = caps.get("operations", [])
        self.assertIn("complete_task", ops)
        self.assertIn("sync_schedules", ops)
        self.assertIn("run_task_event", ops)
        self.assertIn("get_notification_settings", ops)
        self.assertIn("save_notification_settings", ops)

    def test_notification_settings_schema(self):
        schema = mcp_support.mcp_config_schema()
        for key in notification_service.DEFAULT_PLUGIN_SETTINGS:
            self.assertIn(key, schema.get("properties", {}))
        caps = mcp_support.mcp_capabilities()
        save_schema = caps["operation_schemas"]["save_notification_settings"]["params"]
        self.assertIn("settings", save_schema["properties"])
        self.assertIn("default_reminder_code", save_schema["properties"]["settings"]["properties"])

    def test_save_notification_settings_requires_settings(self):
        with patch.object(mcp_support, "_mcp_scope", _noop_mcp_scope):
            with self.assertRaises(ValueError):
                mcp_support.mcp_invoke("save_notification_settings", {})

    def test_entity_schemas(self):
        lists_schema = mcp_support.mcp_entity_schema("lists")
        self.assertIn("title", lists_schema.get("properties", {}))
        tasks_schema = mcp_support.mcp_entity_schema("tasks")
        self.assertIn("list_id", tasks_schema.get("properties", {}))
        self.assertIn("extra", tasks_schema.get("properties", {}))
        self.assertIn("settings", tasks_schema.get("properties", {}))
        hooks_schema = mcp_support.mcp_entity_schema("hooks")
        self.assertIn("code", hooks_schema.get("properties", {}))

    def test_validate_hook_code(self):
        ok = mcp_support.mcp_validate_entity_code("hooks", "print(task['title'])")
        self.assertTrue(ok.get("ok"))
        bad = mcp_support.mcp_validate_entity_code("hooks", "def (")
        self.assertFalse(bad.get("ok"))

    def test_run_hook_dry(self):
        result = mcp_support.mcp_run_entity_dry(
            "hooks",
            "print(task['title'])",
            context={"task_id": 1, "event": "reminder", "task": {"id": 1, "title": "Test"}},
        )
        self.assertTrue(result.get("success"))
        self.assertIn("Test", result.get("output") or "")

    def test_get_notification_settings_invoke(self):
        with patch.object(mcp_support, "_mcp_scope", _noop_mcp_scope):
            result = mcp_support.mcp_invoke("get_notification_settings")
        self.assertTrue(result.get("ok"))
        self.assertIn("default_reminder_code", result.get("settings", {}))

    def test_task_code_receives_task_context(self):
        from plugins.Todo.services.notification_service import _execute_task_code

        task = {"id": 42, "title": "Buy milk", "notes": "2%"}
        output, ok = _execute_task_code("print(task['title'])", task, 42, "reminder")
        self.assertTrue(ok)
        self.assertIn("Buy milk", output)

    def test_mcp_descriptors(self):
        tools, resources, prompts = mcp_support.mcp_descriptors()
        self.assertTrue(any(item.get("kind") == "plugin_surface" for item in tools))
        self.assertTrue(any(item.get("collection") == "tasks" for item in tools))
        self.assertTrue(any(item.get("uri") == "osys://plugin/Todo/schema/tasks" for item in resources))
        self.assertTrue(any("entity_authoring" in item.get("name", "") for item in prompts))

    def test_validate_entity_tasks_requires_title(self):
        result = mcp_support.mcp_validate_entity("tasks", {"list_id": 1})
        self.assertFalse(result.get("ok"))
        self.assertTrue(any(err.get("field") == "title" for err in result.get("errors", [])))

    def test_validate_entity_hooks_requires_code(self):
        result = mcp_support.mcp_validate_entity("hooks", {})
        self.assertFalse(result.get("ok"))

if __name__ == "__main__":
    unittest.main()
