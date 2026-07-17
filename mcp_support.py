"""MCP integration helpers for Todo plugin."""

from __future__ import annotations

from contextlib import contextmanager
from typing import List, Optional, Tuple

from sqlalchemy import or_

from app.core.lib.mcp_contract import (
    build_plugin_mcp_descriptors,
    revision_from_datetime,
    revision_from_dict,
    validate_entity_payload,
)

from plugins.Todo.models.List import TodoList
from plugins.Todo.models.Task import TodoTask
from plugins.Todo.services import list_service, notification_service, task_service

LISTS = "lists"
TASKS = "tasks"
HOOKS = "hooks"

_DATETIME_DESC = "ISO 8601 or 'YYYY-MM-DD HH:MM:SS' (naive datetime)"

_TASK_EVENTS = ("reminder", "start", "finish", "create", "delete", "notified")

_PRIORITY_LEVELS = {
    0: "low",
    1: "normal",
    2: "high",
    3: "urgent",
}


def _plugin_instance():
    try:
        from app.core.main.PluginsHelper import plugins
        return plugins.get("Todo", {}).get("instance")
    except Exception:
        return None


def _notification_settings_properties() -> dict:
    """JSON Schema properties for plugin notification hook codes (shared by config_schema and invoke)."""
    code_ctx = ["task", "task_id", "event", "params", "logger"]
    return {
        "default_reminder_code": {
            "type": "string",
            "description": "Default Python code for task reminder (scheduled before started)",
            "x-code-language": "python",
            "x-code-context": code_ctx,
        },
        "default_start_code": {
            "type": "string",
            "description": "Default Python code when task starts (scheduled at started)",
            "x-code-language": "python",
            "x-code-context": code_ctx,
        },
        "default_finish_code": {
            "type": "string",
            "description": "Default Python code when task finishes (scheduled at finished)",
            "x-code-language": "python",
            "x-code-context": code_ctx,
        },
        "code_on_create": {
            "type": "string",
            "description": "Python code run immediately when a scheduled task is created",
            "x-code-language": "python",
            "x-code-context": code_ctx,
        },
        "code_on_delete": {
            "type": "string",
            "description": "Python code run immediately when a scheduled task is deleted",
            "x-code-language": "python",
            "x-code-context": code_ctx,
        },
        "code_on_notified": {
            "type": "string",
            "description": "Python code run when task is marked as notified",
            "x-code-language": "python",
            "x-code-context": code_ctx,
        },
    }


def _notification_settings_schema() -> dict:
    props = _notification_settings_properties()
    return {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
        "description": "Plugin-level notification defaults and event hooks (subset of Plugin.config)",
    }


def _plugin_config() -> dict:
    instance = _plugin_instance()
    if instance and getattr(instance, "config", None):
        return instance.config
    return {}


@contextmanager
def _mcp_scope():
    from flask import g

    g._todo_mcp_unrestricted = True
    try:
        yield
    finally:
        g.pop("_todo_mcp_unrestricted", None)


def _task_event_schema() -> dict:
    return {
        "type": "string",
        "enum": list(_TASK_EVENTS),
        "description": (
            "Hook event: reminder (before started), start, finish (scheduled), "
            "create, delete, notified (immediate)"
        ),
    }


def mcp_capabilities() -> dict:
    return {
        "mcp_version": 1,
        "entities": True,
        "config_schema": True,
        "collections": [
            {
                "id": LISTS,
                "title": "Todo Lists",
                "binding_mode": "none",
                "writable": True,
                "has_code": False,
                "list_filters": [],
                "default_sort": "sort_order asc, id asc",
            },
            {
                "id": TASKS,
                "title": "Todo Tasks",
                "binding_mode": "none",
                "writable": True,
                "has_code": False,
                "list_filters": ["list_id", "query", "has_started", "completed_only"],
                "default_sort": "updated desc, id desc",
            },
            {
                "id": HOOKS,
                "title": "Todo Task Hooks",
                "binding_mode": "none",
                "writable": False,
                "has_code": True,
                "list_filters": [],
                "description": "Virtual collection for validate/run dry task notification Python code",
            },
        ],
        "operations": [
            "complete_task",
            "reorder_lists",
            "mark_notified",
            "sync_schedules",
            "run_task_event",
            "resolve_task_hook",
            "get_notification_settings",
            "save_notification_settings",
        ],
        "operation_schemas": {
            "complete_task": {
                "description": "Toggle task completed datetime (set to now or clear)",
                "params": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "Task id"},
                    },
                    "required": ["task_id"],
                },
            },
            "reorder_lists": {
                "description": "Reorder todo lists; array index becomes sort_order",
                "params": {
                    "type": "object",
                    "properties": {
                        "ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "List ids in desired order",
                        },
                        "order": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Alias for ids",
                        },
                    },
                    "required": ["ids"],
                },
            },
            "mark_notified": {
                "description": "Mark task as notified and run code_on_notified hook",
                "params": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "Task id"},
                    },
                    "required": ["task_id"],
                },
            },
            "sync_schedules": {
                "description": "Rebuild Scheduler jobs for one task or all tasks with a start date",
                "params": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "integer",
                            "description": "Optional task id; omit to resync all tasks",
                        },
                    },
                },
            },
            "run_task_event": {
                "description": "Run resolved hook code for a task event (reminder/start/finish/create/delete/notified)",
                "params": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "Task id"},
                        "event": _task_event_schema(),
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": "Run even for note tasks (without started)",
                        },
                    },
                    "required": ["task_id", "event"],
                },
            },
            "resolve_task_hook": {
                "description": "Resolve which Python code runs for a task event (task override vs plugin default)",
                "params": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "Task id"},
                        "event": _task_event_schema(),
                    },
                    "required": ["task_id", "event"],
                },
            },
            "get_notification_settings": {
                "description": "Get plugin-level notification code defaults and event hooks",
                "params": {"type": "object", "properties": {}},
            },
            "save_notification_settings": {
                "description": (
                    "Save plugin-level notification codes, persist Plugin.config, "
                    "and resync Scheduler jobs for all tasks"
                ),
                "params": {
                    "type": "object",
                    "properties": {
                        "settings": _notification_settings_schema(),
                    },
                    "required": ["settings"],
                },
            },
        },
        "notes": [
            "Discover module settings: capabilities.config_schema=true → action config_schema (full Plugin.config JSON Schema).",
            "Notification hooks subset: operation_schemas save_notification_settings / get_notification_settings; same fields in config_schema.",
            "Per-task settings schema: action entity_schema, collection=tasks, property settings.",
            "Task datetimes: started (work begin), finished/completed (actual end on complete_task).",
            "all_day=true stores dates at day start (started) and day end 23:59:59 (finished/completed).",
            "Without started the task is a plain note: no Scheduler jobs and no hooks (unless force=true).",
            "complete_task sets finished and completed, or clears both.",
            "Task settings.settings: reminder_enabled, reminder_offset_minutes, reminder/start/finish_code, notified.",
            "Plugin config: default_reminder/start/finish_code, code_on_create/delete/notified.",
            "Hook code runtime vars: task (dict with title, notes, ...), task_id, event, params, logger.",
            "Use collection hooks + validate_entity_code / run_entity_dry to test hook Python (context: task_id, event).",
            "MCP has full access to all lists and tasks (no per-user ACL).",
        ],
    }


def mcp_config_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "level_logging": {"type": "string", "description": "Plugin logger level (DEBUG, INFO, ...)"},
            **_notification_settings_properties(),
        },
    }


def _collection_meta(collection: str) -> dict:
    for item in mcp_capabilities()["collections"]:
        if item["id"] == collection:
            return item
    raise ValueError(f"Unsupported collection: {collection}")


def _normalize_task_payload(payload: dict) -> dict:
    """Map legacy typo field names before persistence."""
    data = dict(payload)
    if "complited" in data and "completed" not in data:
        data["completed"] = data.pop("complited")
    elif "complited" in data:
        data.pop("complited", None)
    return data


def mcp_entity_schema(collection: str) -> dict:
    _collection_meta(collection)
    if collection == LISTS:
        return {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "readOnly": True,
                    "description": "List id (set by server on create)",
                },
                "title": {
                    "type": "string",
                    "description": "List title",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional tags (free text)",
                },
                "sort_order": {
                    "type": "integer",
                    "description": "Display order (0-based). Prefer reorder_lists for batch reorder.",
                },
                "created": {
                    "type": "string",
                    "readOnly": True,
                    "description": f"Creation timestamp. {_DATETIME_DESC}",
                },
                "updated": {
                    "type": "string",
                    "readOnly": True,
                    "description": f"Last update timestamp. {_DATETIME_DESC}",
                },
                "created_by": {
                    "type": ["string", "null"],
                    "readOnly": True,
                    "description": "Username of the list owner (set on create)",
                },
            },
            "required": ["title"],
        }
    if collection == TASKS:
        return {
            "type": "object",
            "description": (
                "Todo task. Datetime semantics: started=work begin, finished/completed=actual end "
                "(set together by complete_task)."
            ),
            "properties": {
                "id": {
                    "type": "integer",
                    "readOnly": True,
                    "description": "Task id (set by server on create)",
                },
                "list_id": {
                    "type": ["integer", "null"],
                    "description": "Parent list id",
                },
                "title": {
                    "type": "string",
                    "description": "Task title",
                },
                "notes": {
                    "type": "string",
                    "description": "Task description",
                },
                "tags": {
                    "type": "string",
                    "description": "Optional tags (free text)",
                },
                "started": {
                    "type": ["string", "null"],
                    "description": f"Work start datetime. {_DATETIME_DESC}. Omit for a note without scheduling.",
                },
                "all_day": {
                    "type": "boolean",
                    "default": False,
                    "description": "Date-only mode: started at 00:00:00, finished/completed at 23:59:59",
                },
                "finished": {
                    "type": ["string", "null"],
                    "description": (
                        f"Planned or actual finish datetime. {_DATETIME_DESC}. "
                        "Set with completed by complete_task; optional on upsert for scheduling finish hook."
                    ),
                },
                "priority": {
                    "type": "integer",
                    "enum": [0, 1, 2, 3],
                    "default": 1,
                    "description": "Importance: 0=low, 1=normal, 2=high, 3=urgent",
                    "x-enum-labels": _PRIORITY_LEVELS,
                },
                "completed": {
                    "type": ["string", "null"],
                    "description": (
                        f"Actual completion datetime. {_DATETIME_DESC}. "
                        "Set via upsert or complete_task toggle."
                    ),
                },
                "created": {
                    "type": "string",
                    "readOnly": True,
                    "description": f"Record creation timestamp. {_DATETIME_DESC}",
                },
                "updated": {
                    "type": "string",
                    "readOnly": True,
                    "description": f"Last update timestamp. {_DATETIME_DESC}",
                },
                "created_by": {
                    "type": ["string", "null"],
                    "readOnly": True,
                    "description": "Username of the task creator (set on create)",
                },
                "assignee": {
                    "type": ["string", "null"],
                    "description": "Username of the executor; can mark the task completed",
                },
                "viewers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Usernames with read-only access to the task",
                },
                "settings": {
                    "type": "object",
                    "description": "Notification and automation settings for the task",
                    "properties": {
                        "reminder_enabled": {"type": "boolean", "default": False},
                        "reminder_offset_minutes": {
                            "type": "integer",
                            "default": 15,
                            "description": "Minutes before started to fire reminder",
                        },
                        "reminder_code": {
                            "type": "string",
                            "description": "Python code for reminder; empty uses plugin default_reminder_code",
                        },
                        "start_code": {
                            "type": "string",
                            "description": "Python code at started; empty uses plugin default_start_code",
                        },
                        "finish_code": {
                            "type": "string",
                            "description": "Python code at finished; empty uses plugin default_finish_code",
                        },
                        "notified": {
                            "type": "boolean",
                            "default": False,
                            "description": "Whether notification was acknowledged",
                        },
                    },
                },
            },
            "required": ["title"],
        }
    if collection == HOOKS:
        return {
            "type": "object",
            "description": "Virtual schema for task hook code validation and dry-run",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python hook code to validate or execute",
                },
                "task_id": {
                    "type": "integer",
                    "description": "Task id used to build task context (required for run_entity_dry)",
                },
                "event": _task_event_schema(),
                "task": {
                    "type": "object",
                    "description": "Optional task dict override for dry-run instead of loading from DB",
                },
            },
            "required": ["code"],
        }
    raise ValueError(f"Unsupported collection: {collection}")


def mcp_list_entities(
    collection: str,
    query: str = None,
    limit: int = 100,
    list_id: Optional[int] = None,
    has_started: Optional[bool] = None,
    completed_only: Optional[bool] = None,
) -> List[dict]:
    with _mcp_scope():
        limit = max(1, min(int(limit or 100), 5000))
        if collection == LISTS:
            rows = TodoList.query.order_by(TodoList.sort_order, TodoList.id).all()
            if query:
                like = f"%{query}%"
                rows = [
                    row for row in rows
                    if like.lower() in (row.title or "").lower()
                    or like.lower() in (row.tags or "").lower()
                ]
            return [list_service.list_to_dict(row) for row in rows[:limit]]
        if collection == TASKS:
            q = TodoTask.query
            if list_id is not None:
                q = q.filter(TodoTask.list_id == int(list_id))
            if has_started is True:
                q = q.filter(TodoTask.started.isnot(None))
            elif has_started is False:
                q = q.filter(TodoTask.started.is_(None))
            if completed_only is True:
                q = q.filter(TodoTask.completed.isnot(None))
            elif completed_only is False:
                q = q.filter(TodoTask.completed.is_(None))
            if query:
                like = f"%{query}%"
                q = q.filter(or_(TodoTask.title.ilike(like), TodoTask.notes.ilike(like), TodoTask.tags.ilike(like)))
            rows = q.order_by(TodoTask.updated.desc(), TodoTask.id.desc()).limit(limit).all()
            return [task_service.task_to_dict(row) for row in rows]
        if collection == HOOKS:
            return []
        raise ValueError(f"Unsupported collection: {collection}")


def mcp_get_entity(collection: str, entity_id) -> dict:
    with _mcp_scope():
        if collection == HOOKS:
            raise ValueError(f"Collection '{collection}' does not support get_entity")
        if collection == LISTS:
            row = TodoList.query.get(entity_id)
            if row is None:
                raise ValueError(f"List not found: {entity_id}")
            return list_service.list_to_dict(row)
        if collection == TASKS:
            row = TodoTask.query.get(entity_id)
            if row is None:
                raise ValueError(f"Task not found: {entity_id}")
            return task_service.task_to_dict(row)
        raise ValueError(f"Unsupported collection: {collection}")


def mcp_upsert_entity(collection: str, payload: dict, entity_id=None) -> dict:
    meta = _collection_meta(collection)
    if not meta.get("writable"):
        raise ValueError(f"Collection '{collection}' is read-only")
    with _mcp_scope():
        if collection == LISTS:
            row = list_service.save_list(payload, entity_id=entity_id)
            return list_service.list_to_dict(row)
        if collection == TASKS:
            row = task_service.save_task(_normalize_task_payload(payload), entity_id=entity_id)
            return task_service.task_to_dict(row)
        raise ValueError(f"Unsupported collection: {collection}")


def mcp_delete_entity(collection: str, entity_id) -> bool:
    meta = _collection_meta(collection)
    if not meta.get("writable"):
        raise ValueError(f"Collection '{collection}' is read-only")
    with _mcp_scope():
        if collection == LISTS:
            item = TodoList.query.get(entity_id)
            if item is None:
                raise ValueError(f"List not found: {entity_id}")
            return list_service.delete_list(int(entity_id))
        if collection == TASKS:
            task = TodoTask.query.get(entity_id)
            if task is None:
                raise ValueError(f"Task not found: {entity_id}")
            return task_service.delete_task(int(entity_id))
        raise ValueError(f"Unsupported collection: {collection}")


def mcp_validate_entity_code(collection: str, code: str) -> dict:
    if collection != HOOKS:
        raise ValueError(f"Collection '{collection}' does not support code validation")
    return notification_service.validate_hook_code(code)


def mcp_run_entity_dry(collection: str, code: str, context: dict = None) -> dict:
    if collection != HOOKS:
        raise ValueError(f"Collection '{collection}' does not support dry-run code")
    context = context or {}
    task_id = context.get("task_id")
    if task_id in (None, ""):
        raise ValueError("context.task_id is required for hooks dry-run")
    event = str(context.get("event") or "reminder")
    if event not in _TASK_EVENTS:
        raise ValueError(f"Unsupported event: {event}")
    task_snapshot = context.get("task")
    if task_snapshot is not None and not isinstance(task_snapshot, dict):
        raise ValueError("context.task must be an object when provided")
    return notification_service.run_hook_dry(
        code,
        int(task_id),
        event=event,
        task_snapshot=task_snapshot,
    )


def _resolve_hook_source(event: str, task_settings: dict, plugin_settings: dict) -> str:
    if event == "reminder":
        if (task_settings.get("reminder_code") or "").strip():
            return "task"
        if (plugin_settings.get("default_reminder_code") or "").strip():
            return "plugin"
        return "none"
    if event in ("start", "finish"):
        field = f"{event}_code"
        if (task_settings.get(field) or "").strip():
            return "task"
        default_key = f"default_{event}_code"
        if (plugin_settings.get(default_key) or "").strip():
            return "plugin"
        return "none"
    key = f"code_on_{event}" if event in ("create", "delete", "notified") else ""
    if key and (plugin_settings.get(key) or "").strip():
        return "plugin"
    return "none"


def mcp_invoke(operation: str, params: dict = None) -> dict:
    params = params or {}
    with _mcp_scope():
        if operation == "complete_task":
            task_id = params.get("task_id")
            if task_id in (None, ""):
                raise ValueError("task_id is required")
            task = task_service.toggle_task_complete(int(task_id))
            return {"ok": True, "operation": operation, "task": task_service.task_to_dict(task)}
        if operation == "reorder_lists":
            ordered_ids = params.get("ids") or params.get("order")
            if not ordered_ids or not isinstance(ordered_ids, list):
                raise ValueError("ids array is required")
            lists = list_service.reorder_lists(ordered_ids)
            return {
                "ok": True,
                "operation": operation,
                "lists": [list_service.list_to_dict(item) for item in lists],
            }
        if operation == "mark_notified":
            task_id = params.get("task_id")
            if task_id in (None, ""):
                raise ValueError("task_id is required")
            task = task_service.mark_task_notified(int(task_id))
            return {"ok": True, "operation": operation, "task": task_service.task_to_dict(task)}
        if operation == "sync_schedules":
            config = _plugin_config()
            task_id = params.get("task_id")
            if task_id not in (None, ""):
                row = TodoTask.query.get(int(task_id))
                if row is None:
                    raise ValueError(f"Task not found: {task_id}")
                notification_service.sync_task_schedules(row, config)
                return {"ok": True, "operation": operation, "synced": 1, "task_id": int(task_id)}
            synced = task_service.resync_all_schedules(config)
            return {"ok": True, "operation": operation, "synced": synced}
        if operation == "run_task_event":
            task_id = params.get("task_id")
            event = params.get("event")
            if task_id in (None, "") or event in (None, ""):
                raise ValueError("task_id and event are required")
            event = str(event)
            if event not in _TASK_EVENTS:
                raise ValueError(f"Unsupported event: {event}")
            if TodoTask.query.get(int(task_id)) is None:
                raise ValueError(f"Task not found: {task_id}")
            result = notification_service.run_task_event(
                int(task_id),
                event,
                plugin_config=_plugin_config(),
                force=bool(params.get("force")),
            )
            return {"ok": True, "operation": operation, **result}
        if operation == "resolve_task_hook":
            task_id = params.get("task_id")
            event = params.get("event")
            if task_id in (None, "") or event in (None, ""):
                raise ValueError("task_id and event are required")
            event = str(event)
            if event not in _TASK_EVENTS:
                raise ValueError(f"Unsupported event: {event}")
            row = TodoTask.query.get(int(task_id))
            if row is None:
                raise ValueError(f"Task not found: {task_id}")
            task_settings = notification_service.parse_task_settings(row.settings)
            plugin_settings = notification_service.normalize_plugin_settings(_plugin_config())
            code = notification_service.resolve_hook_code(event, task_settings, plugin_settings)
            return {
                "ok": True,
                "operation": operation,
                "task_id": int(task_id),
                "event": event,
                "source": _resolve_hook_source(event, task_settings, plugin_settings),
                "code": code,
                "has_code": bool(code),
            }
        if operation == "get_notification_settings":
            settings = notification_service.normalize_plugin_settings(_plugin_config())
            return {"ok": True, "operation": operation, "settings": settings}
        if operation == "save_notification_settings":
            raw = params.get("settings")
            if not isinstance(raw, dict):
                raise ValueError("settings object is required")
            instance = _plugin_instance()
            if instance is None:
                raise ValueError("Todo plugin not loaded")
            saved = instance.save_notification_settings(raw)
            return {"ok": True, "operation": operation, "settings": saved}
        raise ValueError(f"Unsupported operation: {operation}")


def mcp_descriptors() -> Tuple[list, list, list]:
    return build_plugin_mcp_descriptors("Todo", mcp_capabilities())


def mcp_entity_revision(collection: str, entity_id) -> str:
    if collection == HOOKS:
        raise ValueError(f"Collection '{collection}' does not support entity revision")
    entity = mcp_get_entity(collection, entity_id)
    updated = revision_from_datetime(entity.get("updated"))
    if updated:
        return updated
    if collection == LISTS:
        return revision_from_dict(entity, keys=["id", "title", "tags", "sort_order"])
    return revision_from_dict(
        entity,
        keys=["id", "list_id", "title", "notes", "tags", "started", "finished", "priority", "completed", "settings"],
    )


def mcp_validate_entity(collection: str, payload: dict, entity_id=None) -> dict:
    if collection == HOOKS:
        if not isinstance(payload, dict):
            return {"ok": False, "errors": [{"field": "_", "message": "payload must be an object"}]}
        code = str(payload.get("code") or "").strip()
        if not code:
            return {"ok": False, "errors": [{"field": "code", "message": "required"}]}
        return mcp_validate_entity_code(collection, code)

    schema = mcp_entity_schema(collection)
    result = validate_entity_payload(payload, schema)
    if not result.get("ok"):
        return result

    if collection == TASKS:
        list_id = payload.get("list_id")
        if list_id not in (None, ""):
            row = TodoList.query.get(int(list_id))
            if row is None:
                return {"ok": False, "errors": [{"field": "list_id", "message": f"list not found: {list_id}"}]}
        if entity_id not in (None, ""):
            row = TodoTask.query.get(int(entity_id))
            if row is None:
                return {"ok": False, "errors": [{"field": "id", "message": f"task not found: {entity_id}"}]}

    if collection == LISTS and entity_id not in (None, ""):
        row = TodoList.query.get(int(entity_id))
        if row is None:
            return {"ok": False, "errors": [{"field": "id", "message": f"list not found: {entity_id}"}]}

    return {"ok": True, "errors": []}
