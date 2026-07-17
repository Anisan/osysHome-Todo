"""Todo task notifications via Scheduler and configurable Python hooks."""

from __future__ import annotations

import json
import ast
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.core.lib.common import addScheduledJob, clearScheduledJob
from app.core.lib.execute import execute_and_capture_output
from app.logging_config import getLogger
from app.database import convert_utc_to_local, get_now_to_utc, row2dict
from plugins.Todo.models.Task import TodoTask

JOB_PREFIX = "Todo.task."
_logger = getLogger("Todo")

DEFAULT_TASK_SETTINGS: Dict[str, Any] = {
    "reminder_enabled": False,
    "reminder_offset_minutes": 15,
    "reminder_code": "",
    "start_code": "",
    "finish_code": "",
    "notified": False,
}

DEFAULT_PLUGIN_SETTINGS: Dict[str, Any] = {
    "default_reminder_code": "",
    "default_start_code": "",
    "default_finish_code": "",
    "code_on_create": "",
    "code_on_delete": "",
    "code_on_notified": "",
}


def normalize_plugin_settings(config: Optional[dict]) -> Dict[str, Any]:
    data = dict(DEFAULT_PLUGIN_SETTINGS)
    if config:
        for key in DEFAULT_PLUGIN_SETTINGS:
            if key in config and config[key] is not None:
                data[key] = str(config[key])
    return data


def parse_task_settings(raw) -> Dict[str, Any]:
    data = dict(DEFAULT_TASK_SETTINGS)
    if not raw:
        return data
    if isinstance(raw, dict):
        source = raw
    else:
        try:
            source = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return data
    if not isinstance(source, dict):
        return data
    for key in DEFAULT_TASK_SETTINGS:
        if key not in source:
            continue
        value = source[key]
        if key == "reminder_enabled" or key == "notified":
            data[key] = bool(value)
        elif key == "reminder_offset_minutes":
            try:
                data[key] = max(0, int(value))
            except (TypeError, ValueError):
                data[key] = DEFAULT_TASK_SETTINGS["reminder_offset_minutes"]
        else:
            data[key] = "" if value is None else str(value)
    return data


def serialize_task_settings(settings: Dict[str, Any]) -> str:
    normalized = parse_task_settings(settings)
    return json.dumps(normalized, ensure_ascii=False)


def _base_task_dict(task: TodoTask) -> Dict[str, Any]:
    data = row2dict(task)
    for field in ("created", "updated", "completed", "finished", "started"):
        if data.get(field) and hasattr(data[field], "isoformat"):
            data[field] = data[field].isoformat(sep=" ", timespec="seconds")
    data["all_day"] = bool(data.get("all_day"))
    return data


def attach_settings(task: TodoTask) -> Dict[str, Any]:
    data = _base_task_dict(task)
    data["settings"] = parse_task_settings(getattr(task, "settings", None))
    return data


def has_scheduled_dates(task_or_dict) -> bool:
    """Task with a start date gets reminders and scheduled hooks; without — plain note."""
    if task_or_dict is None:
        return False
    if isinstance(task_or_dict, dict):
        return task_or_dict.get("started") not in (None, "", False)
    return getattr(task_or_dict, "started", None) is not None


def _execute_task_code(code: str, task_snapshot: dict, task_id: int, event: str) -> tuple:
    """Run hook code with task context in the execution environment."""
    params = {
        "task": task_snapshot,
        "task_id": task_id,
        "event": event,
    }
    variables = {
        "params": params,
        "task": task_snapshot,
        "task_id": task_id,
        "event": event,
        "logger": _logger,
    }
    output, error = execute_and_capture_output(code, variables)
    return output, not error


def validate_hook_code(code: str) -> dict:
    errors = []
    try:
        ast.parse(code or "")
    except SyntaxError as ex:
        errors.append({"message": str(ex), "line": ex.lineno, "column": ex.offset})
    return {"ok": len(errors) == 0, "errors": errors}


def run_hook_dry(
    code: str,
    task_id: int,
    event: str = "reminder",
    task_snapshot: Optional[dict] = None,
) -> dict:
    validation = validate_hook_code(code)
    if not validation.get("ok"):
        return {"ok": False, "validation": validation, "output": None, "success": False}
    if task_snapshot is None:
        task = TodoTask.query.get(int(task_id))
        if task is None:
            task_snapshot = {"id": int(task_id), "title": f"Task {task_id}"}
        else:
            task_snapshot = attach_settings(task)
    output, success = _execute_task_code(code, task_snapshot, int(task_id), str(event))
    return {
        "ok": bool(success),
        "validation": validation,
        "output": output,
        "success": bool(success),
    }


def _job_name(task_id: int, event: str) -> str:
    return f"{JOB_PREFIX}{int(task_id)}.{event}"


def _job_pattern(task_id: int) -> str:
    return f"{JOB_PREFIX}{int(task_id)}.%"


def _scheduler_code(task_id: int, event: str) -> str:
    return (
        'from app.core.lib.common import callPluginFunction; '
        f'callPluginFunction("Todo", "run_task_event", '
        f'{{"task_id": {int(task_id)}, "event": "{event}"}})'
    )


def _resolve_code(event: str, task_settings: dict, plugin_settings: dict) -> str:
    if event == "reminder":
        return (
            task_settings.get("reminder_code")
            or plugin_settings.get("default_reminder_code")
            or ""
        ).strip()
    if event == "start":
        return (task_settings.get("start_code") or plugin_settings.get("default_start_code") or "").strip()
    if event == "finish":
        return (task_settings.get("finish_code") or plugin_settings.get("default_finish_code") or "").strip()
    if event == "create":
        return (plugin_settings.get("code_on_create") or "").strip()
    if event == "delete":
        return (plugin_settings.get("code_on_delete") or "").strip()
    if event == "notified":
        return (plugin_settings.get("code_on_notified") or "").strip()
    return ""


def resolve_hook_code(event: str, task_settings: dict, plugin_settings: dict) -> str:
    return _resolve_code(event, task_settings, plugin_settings)


def _reminder_datetime(task: TodoTask, settings: dict) -> Optional[datetime]:
    if not settings.get("reminder_enabled"):
        return None
    if not task.started:
        return None
    offset = int(settings.get("reminder_offset_minutes") or 0)
    started_local = convert_utc_to_local(task.started)
    return started_local - timedelta(minutes=offset)


def _schedule_at(task_id: int, event: str, dt: Optional[datetime]) -> None:
    name = _job_name(task_id, event)
    if dt is None:
        clearScheduledJob(name)
        return
    now_local = convert_utc_to_local(get_now_to_utc())
    if dt <= now_local:
        clearScheduledJob(name)
        return
    addScheduledJob(name, _scheduler_code(task_id, event), dt)


def clear_task_schedules(task_id: int) -> None:
    clearScheduledJob(_job_pattern(task_id))


def sync_task_schedules(task: TodoTask, plugin_config: Optional[dict] = None) -> None:
    """Create or update Scheduler jobs for reminder/start/finish."""
    if task.id is None:
        return
    clear_task_schedules(task.id)
    if not has_scheduled_dates(task):
        return
    settings = parse_task_settings(getattr(task, "settings", None))
    plugin_settings = normalize_plugin_settings(plugin_config)

    reminder_dt = _reminder_datetime(task, settings)
    if reminder_dt and _resolve_code("reminder", settings, plugin_settings):
        _schedule_at(task.id, "reminder", reminder_dt)

    if _resolve_code("start", settings, plugin_settings):
        _schedule_at(task.id, "start", convert_utc_to_local(task.started))

    if task.finished and _resolve_code("finish", settings, plugin_settings):
        _schedule_at(task.id, "finish", convert_utc_to_local(task.finished))


def run_task_event(
    task_id: int,
    event: str,
    plugin_config: Optional[dict] = None,
    task_snapshot: Optional[dict] = None,
    force: bool = False,
) -> dict:
    """Execute hook code for a task event."""
    task = TodoTask.query.get(task_id)
    settings = parse_task_settings(getattr(task, "settings", None) if task else None)
    plugin_settings = normalize_plugin_settings(plugin_config)

    if task_snapshot is None and task is not None:
        task_snapshot = attach_settings(task)
    elif task_snapshot is None:
        task_snapshot = {"id": task_id}

    if not force and not (has_scheduled_dates(task_snapshot) or has_scheduled_dates(task)):
        return {"ok": True, "skipped": True, "event": event, "task_id": task_id, "reason": "note"}

    code = _resolve_code(event, settings, plugin_settings)
    if not code:
        return {"ok": True, "skipped": True, "event": event, "task_id": task_id}

    output, success = _execute_task_code(code, task_snapshot, task_id, event)
    return {
        "ok": bool(success),
        "skipped": False,
        "event": event,
        "task_id": task_id,
        "output": output,
    }


def run_immediate_event(task: TodoTask, event: str, plugin_config: Optional[dict] = None) -> dict:
    snapshot = attach_settings(task) if task else {"id": None}
    task_id = task.id if task else snapshot.get("id")
    return run_task_event(task_id, event, plugin_config=plugin_config, task_snapshot=snapshot)


def mark_task_notified(task: TodoTask, plugin_config: Optional[dict] = None) -> TodoTask:
    if not has_scheduled_dates(task):
        raise ValueError("Task has no start date; notifications are not applicable")
    settings = parse_task_settings(getattr(task, "settings", None))
    settings["notified"] = True
    task.settings = serialize_task_settings(settings)
    run_immediate_event(task, "notified", plugin_config=plugin_config)
    return task
