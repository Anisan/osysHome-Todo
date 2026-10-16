"""Todo task persistence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from zoneinfo import ZoneInfo

from app.core.lib.crontab import nextStartCronJob, validate_cron_expression
from app.database import (
    convert_local_to_utc,
    convert_utc_to_local,
    db,
    get_now_to_utc,
    session_scope,
)
from app.logging_config import getLogger
from plugins.Todo.models.Task import TodoTask
from plugins.Todo.services import notification_service
from plugins.Todo.services import task_permissions

DEFAULT_TIMED_FINISH_MINUTES = 30
_logger = getLogger("Todo")


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
            ('extra', 'TEXT'),
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


def _parse_raw_datetime(value):
    """Return datetime as parsed (may be aware) or None."""
    if value in (None, "", False):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
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


def _coerce_to_utc(value, all_day: bool, boundary: str) -> Optional[datetime]:
    """Store datetimes as UTC.

    Naive strings from UI/API are local wall time.
    Timezone-aware values are treated as absolute instants.
    all_day boundaries are applied on the local calendar day.
    """
    parsed = _parse_raw_datetime(value)
    if parsed is None:
        return None

    if parsed.tzinfo is not None:
        utc = parsed.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        if not all_day:
            return utc
        local = convert_utc_to_local(utc)
    else:
        local = parsed

    if all_day:
        local = _all_day_start(local) if boundary == "start" else _all_day_finish(local)
    return convert_local_to_utc(local)


def _normalize_started(value, all_day: bool) -> Optional[datetime]:
    return _coerce_to_utc(value, all_day, "start")


def _normalize_finished(value, all_day: bool) -> Optional[datetime]:
    return _coerce_to_utc(value, all_day, "finish")


def _normalize_completed(value, all_day: bool) -> Optional[datetime]:
    return _coerce_to_utc(value, all_day, "finish")


def _ensure_all_day_finished(task: TodoTask) -> None:
    """If all_day has started but no finished, set finished to end of started's local day."""
    if not task.all_day or not task.started or task.finished:
        return
    started_local = convert_utc_to_local(task.started)
    task.finished = convert_local_to_utc(_all_day_finish(started_local))


def _ensure_timed_finished(task: TodoTask) -> None:
    """If a timed task has a start but no finish, default to start + DEFAULT_TIMED_FINISH_MINUTES.

    Applied on create only: on edit an empty finished is a deliberate way to keep a task open.
    """
    if task.all_day or not task.started or task.finished:
        return
    task.finished = task.started + timedelta(minutes=DEFAULT_TIMED_FINISH_MINUTES)


def _all_day_end_utc_from_utc(dt_utc: datetime) -> datetime:
    """End of the local calendar day that contains dt_utc, stored as UTC."""
    local = convert_utc_to_local(dt_utc)
    return convert_local_to_utc(_all_day_finish(local))


def task_to_dict(task: TodoTask, username: Optional[str] = None) -> Dict[str, Any]:
    data = notification_service.attach_settings(task)
    data["parent_id"] = task.parent_id
    data["created_by"] = task.created_by
    data["assignee"] = task.assignee
    data["viewers"] = task_permissions.parse_viewers(task.viewers)
    data["permissions"] = task_permissions.task_permissions(task, username)
    return data


def _parse_optional_int(value) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)


def _children_map(tasks: Optional[List[TodoTask]] = None) -> Dict[Optional[int], List[TodoTask]]:
    rows = tasks if tasks is not None else TodoTask.query.all()
    by_parent: Dict[Optional[int], List[TodoTask]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)
    return by_parent


def collect_descendant_ids(root_id: int, tasks: Optional[List[TodoTask]] = None) -> List[int]:
    """Все потомки root_id (без самого корня), BFS."""
    by_parent = _children_map(tasks)
    result: List[int] = []
    queue = list(by_parent.get(root_id, []))
    while queue:
        node = queue.pop(0)
        result.append(node.id)
        queue.extend(by_parent.get(node.id, []))
    return result


def _would_create_cycle(task_id: Optional[int], parent_id: Optional[int]) -> bool:
    if parent_id is None:
        return False
    if task_id is not None and parent_id == task_id:
        return True
    seen: Set[int] = set()
    current: Optional[int] = parent_id
    while current is not None:
        if task_id is not None and current == task_id:
            return True
        if current in seen:
            break
        seen.add(current)
        row = TodoTask.query.get(current)
        if row is None:
            break
        current = row.parent_id
    return False


def _validate_parent(
    task: TodoTask,
    parent_id: Optional[int],
    list_id: Optional[int],
    username: Optional[str],
) -> None:
    if parent_id is None:
        return
    parent = TodoTask.query.get(parent_id)
    if parent is None:
        raise ValueError("parent task not found")
    task_permissions.assert_can_view(parent, username)
    if list_id is not None and parent.list_id != list_id:
        raise ValueError("parent task must be in the same list")
    if _would_create_cycle(task.id, parent_id):
        raise ValueError("parent_id would create a cycle")


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

    old_list_id = task.list_id
    list_id_changed = False
    if "list_id" in payload:
        list_id = payload.get("list_id")
        new_list_id = int(list_id) if list_id not in (None, "") else None
        if new_list_id != task.list_id and new_list_id is not None:
            task_permissions.assert_can_create_task_in_list(new_list_id, username)
        list_id_changed = new_list_id != task.list_id
        task.list_id = new_list_id

    if "parent_id" in payload:
        new_parent_id = _parse_optional_int(payload.get("parent_id"))
        # При создании без list_id — взять список родителя
        if new_parent_id is not None and task.list_id is None and entity_id is None:
            parent_for_list = TodoTask.query.get(new_parent_id)
            if parent_for_list is not None:
                task_permissions.assert_can_create_task_in_list(parent_for_list.list_id, username)
                task.list_id = parent_for_list.list_id
        _validate_parent(task, new_parent_id, task.list_id, username)
        task.parent_id = new_parent_id

    # Если list сменился, а parent остался из старого списка — сбросить
    if task.parent_id is not None:
        parent = TodoTask.query.get(task.parent_id)
        if parent is None or parent.list_id != task.list_id:
            if "parent_id" in payload:
                raise ValueError("parent task must be in the same list")
            task.parent_id = None

    task.title = payload.get("title", task.title)
    if "notes" in payload:
        task.notes = payload.get("notes")
    if "extra" in payload:
        raw_extra = payload.get("extra")
        if raw_extra is None or raw_extra == "":
            task.extra = None
        elif isinstance(raw_extra, (dict, list)):
            task.extra = json.dumps(raw_extra, ensure_ascii=False)
        else:
            task.extra = str(raw_extra)
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
        task.completed = _normalize_completed(payload.get("completed"), bool(task.all_day))
    elif "complited" in payload:
        task.completed = _normalize_completed(payload.get("complited"), bool(task.all_day))

    if "assignee" in payload:
        assignee = payload.get("assignee")
        task.assignee = str(assignee).strip() if assignee not in (None, "") else None
    if "viewers" in payload:
        task.viewers = task_permissions.serialize_viewers(payload.get("viewers"))

    if "settings" in payload:
        parsed_settings = notification_service.parse_task_settings(payload.get("settings") or {})
        cron = (parsed_settings.get("recurrence_cron") or "").strip()
        if cron:
            validation = validate_cron_expression(cron)
            if not validation.get("ok"):
                messages = "; ".join(
                    err.get("message", str(err)) for err in (validation.get("errors") or [])
                )
                raise ValueError(f"Invalid recurrence_cron: {messages or cron}")
        task.settings = notification_service.serialize_task_settings(parsed_settings)

    _ensure_all_day_finished(task)
    if entity_id is None:
        _ensure_timed_finished(task)

    if task.started and task.finished and task.finished < task.started:
        raise ValueError("finished must be greater than or equal to started")

    if not notification_service.has_scheduled_dates(task):
        task.finished = None
        task.settings = notification_service.serialize_task_settings({
            **notification_service.parse_task_settings(task.settings),
            "reminder_enabled": False,
            "notified": False,
        })

    # Перенос всего поддерева в новый список
    if list_id_changed and entity_id is not None and task.list_id != old_list_id:
        descendant_ids = collect_descendant_ids(task.id)
        if descendant_ids:
            TodoTask.query.filter(TodoTask.id.in_(descendant_ids)).update(
                {TodoTask.list_id: task.list_id},
                synchronize_session=False,
            )

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
    plugin_config = _plugin_config()

    all_tasks = TodoTask.query.all()
    descendant_ids = collect_descendant_ids(entity_id, all_tasks)
    # Удаляем снизу вверх: сначала потомки, затем корень
    delete_ids = list(reversed(descendant_ids)) + [entity_id]
    id_set = set(delete_ids)
    by_id = {row.id: row for row in all_tasks if row.id in id_set}

    for tid in delete_ids:
        row = by_id.get(tid) or TodoTask.query.get(tid)
        if row is None:
            continue
        snapshot = notification_service.attach_settings(row)
        notification_service.clear_task_schedules(tid)
        if notification_service.has_scheduled_dates(snapshot):
            notification_service.run_task_event(
                tid, "delete", plugin_config=plugin_config, task_snapshot=snapshot
            )
        db.session.delete(row)

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

def _clone_recurring_task(
    source: TodoTask,
    plugin_config: dict,
    planned_started: Optional[datetime] = None,
    planned_finished: Optional[datetime] = None,
) -> Optional[TodoTask]:
    """Create next occurrence of a recurring task; returns the clone or None."""
    settings = notification_service.parse_task_settings(getattr(source, "settings", None))
    cron = (settings.get("recurrence_cron") or "").strip()
    base_started = planned_started if planned_started is not None else source.started
    base_finished = planned_finished if planned_finished is not None else source.finished
    if not cron or not base_started:
        return None

    validation = validate_cron_expression(cron)
    if not validation.get("ok"):
        _logger.error(
            "Todo task %s: invalid recurrence_cron %r: %s",
            source.id,
            cron,
            validation.get("errors"),
        )
        return None

    try:
        next_started_local = nextStartCronJob(cron)
    except Exception as ex:
        _logger.exception("Todo task %s: failed to compute next recurrence: %s", source.id, ex)
        return None

    next_started_utc = convert_local_to_utc(next_started_local)
    if source.all_day:
        next_started_utc = convert_local_to_utc(_all_day_start(next_started_local))
        next_finished_utc = convert_local_to_utc(_all_day_finish(next_started_local))
    elif base_finished and base_finished >= base_started:
        duration = base_finished - base_started
        next_finished_utc = next_started_utc + duration
    else:
        next_finished_utc = next_started_utc + timedelta(minutes=DEFAULT_TIMED_FINISH_MINUTES)

    clone_settings = dict(settings)
    clone_settings["notified"] = False

    now = get_now_to_utc()
    clone = TodoTask()
    clone.list_id = source.list_id
    clone.parent_id = source.parent_id
    clone.title = source.title
    clone.notes = source.notes
    clone.extra = source.extra
    clone.tags = source.tags
    clone.priority = source.priority
    clone.all_day = bool(source.all_day)
    clone.created_by = source.created_by
    clone.assignee = source.assignee
    clone.viewers = source.viewers
    clone.started = next_started_utc
    clone.finished = next_finished_utc
    clone.completed = None
    clone.created = now
    clone.updated = now
    clone.settings = notification_service.serialize_task_settings(clone_settings)
    db.session.add(clone)
    db.session.commit()
    db.session.refresh(clone)

    notification_service.sync_task_schedules(clone, plugin_config)
    if notification_service.has_scheduled_dates(clone):
        notification_service.run_immediate_event(clone, "create", plugin_config=plugin_config)
    return clone


def _mark_task_completed(task: TodoTask) -> Tuple[bool, Optional[datetime], Optional[datetime]]:
    """Пометить задачу выполненной. Возвращает (changed, planned_started, planned_finished)."""
    if task.completed:
        return False, None, None
    planned_started = task.started
    planned_finished = task.finished
    now = get_now_to_utc()
    if task.all_day:
        end = _all_day_end_utc_from_utc(now)
        task.completed = end
        if not task.finished:
            task.finished = end
    else:
        task.completed = now
        if not task.finished:
            task.finished = now
    task.updated = now
    return True, planned_started, planned_finished


def _mark_task_uncompleted(task: TodoTask) -> bool:
    """Снять отметку выполнения. True если состояние изменилось."""
    if not task.completed:
        return False
    completed_at = task.completed
    task.completed = None
    if task.all_day and task.started:
        if task.finished is None or task.finished == completed_at:
            task.finished = _all_day_end_utc_from_utc(task.started)
    elif task.finished == completed_at:
        task.finished = None
    task.updated = get_now_to_utc()
    return True


def toggle_task_complete(
    entity_id: int,
    username: Optional[str] = None,
) -> Tuple[TodoTask, List[TodoTask]]:
    """Переключить выполнение. Каскад: дети при завершении родителя; родитель, если все дети готовы.

    Returns:
        (корневая переключённая задача, все затронутые задачи включая её)
    """
    task = get_task(entity_id, username)
    task_permissions.assert_can_complete(task, username)
    becoming_completed = not task.completed

    all_tasks = TodoTask.query.all()
    by_id = {row.id: row for row in all_tasks}
    by_parent = _children_map(all_tasks)

    newly_completed: List[Tuple[TodoTask, Optional[datetime], Optional[datetime]]] = []
    newly_uncompleted: List[TodoTask] = []
    affected_ids: List[int] = []

    def _track(row: TodoTask) -> None:
        if row.id not in affected_ids:
            affected_ids.append(row.id)

    if becoming_completed:
        # Завершить задачу и всех потомков
        for tid in [task.id] + collect_descendant_ids(task.id, all_tasks):
            row = by_id.get(tid)
            if row is None:
                continue
            changed, planned_started, planned_finished = _mark_task_completed(row)
            if changed:
                newly_completed.append((row, planned_started, planned_finished))
                _track(row)
        # Вверх: если все дети родителя завершены — завершить родителя
        current = task
        while current.parent_id:
            parent = by_id.get(current.parent_id)
            if parent is None:
                break
            children = by_parent.get(parent.id, [])
            if not children or not all(child.completed for child in children):
                break
            changed, planned_started, planned_finished = _mark_task_completed(parent)
            if changed:
                newly_completed.append((parent, planned_started, planned_finished))
                _track(parent)
            current = parent
    else:
        # Снять завершение с задачи и потомков; также с предков
        for tid in [task.id] + collect_descendant_ids(task.id, all_tasks):
            row = by_id.get(tid)
            if row is None:
                continue
            if _mark_task_uncompleted(row):
                newly_uncompleted.append(row)
                _track(row)
        current = task
        while current.parent_id:
            parent = by_id.get(current.parent_id)
            if parent is None:
                break
            if _mark_task_uncompleted(parent):
                newly_uncompleted.append(parent)
                _track(parent)
            current = parent

    db.session.commit()

    plugin_config = _plugin_config()
    affected: List[TodoTask] = []
    for tid in affected_ids:
        row = TodoTask.query.get(tid)
        if row is None:
            continue
        db.session.refresh(row)
        notification_service.sync_task_schedules(row, plugin_config)
        affected.append(row)

    for row, planned_started, planned_finished in newly_completed:
        # После commit/refresh брать актуальный объект
        fresh = by_id.get(row.id) or row
        fresh = next((item for item in affected if item.id == row.id), fresh)
        notification_service.run_immediate_event(fresh, "completed", plugin_config=plugin_config)
        _clone_recurring_task(
            fresh,
            plugin_config,
            planned_started=planned_started,
            planned_finished=planned_finished,
        )

    # Клоны recurrence могли добавить задачи — корневую вернуть свежей
    db.session.refresh(task)
    primary = TodoTask.query.get(entity_id) or task
    if not any(item.id == primary.id for item in affected):
        affected.insert(0, primary)
    return primary, affected
