"""Todo task persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.database import db, get_now_to_utc, session_scope
from plugins.Todo.models.Task import TodoTask
from plugins.Todo.services import notification_service
from plugins.Todo.services import task_permissions


def migrate_deadline_to_finished() -> None:
    """Copy legacy deadline column into finished; ensure settings and access columns exist."""
    from sqlalchemy import inspect, text

    engine = db.engine
    inspector = inspect(engine)
    if not inspector.has_table('todo_tasks'):
        return
    cols = {col['name'] for col in inspector.get_columns('todo_tasks')}
    dialect = engine.dialect.name
    with engine.connect() as conn:
        if 'deadline' in cols:
            if 'finished' not in cols:
                if dialect == 'sqlite':
                    conn.execute(text('ALTER TABLE todo_tasks ADD COLUMN finished DATETIME'))
                elif dialect == 'postgresql':
                    conn.execute(text('ALTER TABLE "todo_tasks" ADD COLUMN "finished" TIMESTAMP'))
                else:
                    conn.execute(text('ALTER TABLE `todo_tasks` ADD COLUMN `finished` DATETIME'))
                conn.commit()
            conn.execute(
                text('UPDATE todo_tasks SET finished = deadline WHERE finished IS NULL AND deadline IS NOT NULL')
            )
            conn.commit()
        for column_name, column_type in (
            ('settings', 'TEXT'),
            ('created_by', 'VARCHAR(64)'),
            ('assignee', 'VARCHAR(64)'),
            ('viewers', 'TEXT'),
        ):
            if column_name in cols:
                continue
            if dialect == 'sqlite':
                conn.execute(text(f'ALTER TABLE todo_tasks ADD COLUMN {column_name} {column_type}'))
            elif dialect == 'postgresql':
                conn.execute(text(f'ALTER TABLE "todo_tasks" ADD COLUMN "{column_name}" {column_type}'))
            else:
                conn.execute(text(f'ALTER TABLE `todo_tasks` ADD COLUMN `{column_name}` {column_type}'))
            conn.commit()

    if not inspector.has_table('todo_lists'):
        return
    list_cols = {col['name'] for col in inspector.get_columns('todo_lists')}
    if 'created_by' not in list_cols:
        with engine.connect() as conn:
            if dialect == 'sqlite':
                conn.execute(text('ALTER TABLE todo_lists ADD COLUMN created_by VARCHAR(64)'))
            elif dialect == 'postgresql':
                conn.execute(text('ALTER TABLE "todo_lists" ADD COLUMN "created_by" VARCHAR(64)'))
            else:
                conn.execute(text('ALTER TABLE `todo_lists` ADD COLUMN `created_by` VARCHAR(64)'))
            conn.commit()


def _parse_datetime(value) -> Optional[datetime]:
    if value in (None, "", False):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False"):
        return False
    return bool(value)


def _all_day_start(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _all_day_finish(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def _normalize_started(value, all_day: bool) -> Optional[datetime]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return _all_day_start(parsed) if all_day else parsed


def _normalize_finished(value, all_day: bool) -> Optional[datetime]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return _all_day_finish(parsed) if all_day else parsed


def task_to_dict(task: TodoTask, username: Optional[str] = None) -> Dict[str, Any]:
    data = notification_service.attach_settings(task)
    data["created_by"] = task.created_by
    data["assignee"] = task.assignee
    data["viewers"] = task_permissions.parse_viewers(task.viewers)
    data["permissions"] = task_permissions.task_permissions(task, username)
    return data


def filter_tasks_for_user(query, username: Optional[str] = None):
    return task_permissions.filter_visible_tasks(query, username)


def list_tasks(username: Optional[str] = None):
    query = TodoTask.query
    query = filter_tasks_for_user(query, username)
    return query.order_by(TodoTask.updated.desc(), TodoTask.id.desc()).all()


def get_task(entity_id: int, username: Optional[str] = None) -> TodoTask:
    task = TodoTask.query.get(entity_id)
    if task is None:
        raise task_permissions.NotFoundError("Task not found")
    task_permissions.assert_can_view(task, username)
    return task


def _plugin_config() -> dict:
    try:
        from app.core.main.PluginsHelper import plugins
        instance = plugins.get("Todo", {}).get("instance")
        if instance and getattr(instance, "config", None):
            return instance.config
    except Exception:
        pass
    return {}


def save_task(
    payload: Dict[str, Any],
    entity_id: Optional[int] = None,
    username: Optional[str] = None,
) -> TodoTask:
    if not payload.get("title"):
        raise ValueError("title is required")

    username = username or task_permissions.current_username()

    if entity_id is not None:
        task = get_task(entity_id, username)
        task_permissions.assert_can_edit(task, username)
        task_permissions.assign_owner_if_missing(task, username)
        task.updated = get_now_to_utc()
    else:
        task = TodoTask()
        task.created = get_now_to_utc()
        task.updated = task.created
        task.created_by = username
        db.session.add(task)
        if payload.get("list_id") not in (None, ""):
            task_permissions.assert_can_create_task_in_list(payload.get("list_id"), username)

    if "list_id" in payload:
        list_id = payload.get("list_id")
        new_list_id = int(list_id) if list_id not in (None, "") else None
        if new_list_id != task.list_id and new_list_id is not None:
            task_permissions.assert_can_create_task_in_list(new_list_id, username)
        task.list_id = new_list_id
    task.title = payload.get("title", task.title)
    if "notes" in payload:
        task.notes = payload.get("notes")
    if "tags" in payload:
        task.tags = payload.get("tags")
    if "all_day" in payload:
        task.all_day = _as_bool(payload.get("all_day"))
    if "priority" in payload:
        priority = payload.get("priority")
        task.priority = int(priority) if priority not in (None, "") else 1
    if "started" in payload:
        task.started = _normalize_started(payload.get("started"), bool(task.all_day))
    if "finished" in payload:
        task.finished = _normalize_finished(payload.get("finished"), bool(task.all_day))
    if "completed" in payload:
        completed = _parse_datetime(payload.get("completed"))
        task.completed = _all_day_finish(completed) if completed and task.all_day else completed
    elif "complited" in payload:
        completed = _parse_datetime(payload.get("complited"))
        task.completed = _all_day_finish(completed) if completed and task.all_day else completed

    if "assignee" in payload:
        assignee = payload.get("assignee")
        task.assignee = str(assignee).strip() if assignee not in (None, "") else None
    if "viewers" in payload:
        task.viewers = task_permissions.serialize_viewers(payload.get("viewers"))

    if "settings" in payload:
        task.settings = notification_service.serialize_task_settings(payload.get("settings") or {})

    if not notification_service.has_scheduled_dates(task):
        task.finished = None
        task.settings = notification_service.serialize_task_settings({
            **notification_service.parse_task_settings(task.settings),
            "reminder_enabled": False,
            "notified": False,
        })

    is_new = entity_id is None
    db.session.commit()
    db.session.refresh(task)

    plugin_config = _plugin_config()
    notification_service.sync_task_schedules(task, plugin_config)
    if is_new and notification_service.has_scheduled_dates(task):
        notification_service.run_immediate_event(task, "create", plugin_config=plugin_config)

    return task


def delete_task(entity_id: int, username: Optional[str] = None) -> bool:
    task = TodoTask.query.get(entity_id)
    if task is None:
        raise task_permissions.NotFoundError("Task not found")
    task_permissions.assert_can_delete(task, username)
    snapshot = notification_service.attach_settings(task)
    plugin_config = _plugin_config()
    notification_service.clear_task_schedules(entity_id)
    if notification_service.has_scheduled_dates(snapshot):
        notification_service.run_task_event(
            entity_id, "delete", plugin_config=plugin_config, task_snapshot=snapshot
        )
    db.session.delete(task)
    db.session.commit()
    return True


def mark_task_notified(entity_id: int, username: Optional[str] = None) -> TodoTask:
    task = get_task(entity_id, username)
    task_permissions.assert_can_notify(task, username)
    plugin_config = _plugin_config()
    notification_service.mark_task_notified(task, plugin_config=plugin_config)
    task.updated = get_now_to_utc()
    db.session.commit()
    db.session.refresh(task)
    return task


def resync_all_schedules(plugin_config: Optional[dict] = None) -> int:
    config = plugin_config if plugin_config is not None else _plugin_config()
    count = 0
    with session_scope() as session:
        tasks = session.query(TodoTask).all()
        for task in tasks:
            notification_service.sync_task_schedules(task, config)
            count += 1
    return count

def toggle_task_complete(entity_id: int, username: Optional[str] = None) -> TodoTask:
    task = get_task(entity_id, username)
    task_permissions.assert_can_complete(task, username)
    if task.completed:
        task.completed = None
        task.finished = None
    else:
        now = get_now_to_utc()
        if task.all_day:
            end = _all_day_finish(now)
            task.completed = end
            task.finished = end
        else:
            task.completed = now
            task.finished = now
    task.updated = get_now_to_utc()
    db.session.commit()
    db.session.refresh(task)
    notification_service.sync_task_schedules(task, _plugin_config())
    return task
