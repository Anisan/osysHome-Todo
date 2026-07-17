"""Todo list persistence."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from sqlalchemy import delete

from app.database import db, get_now_to_utc, row2dict
from plugins.Todo.models.List import TodoList
from plugins.Todo.models.Task import TodoTask
from plugins.Todo.services import task_permissions
from plugins.Todo.services.task_permissions import NotFoundError, PermissionError


def _visible_list_ids() -> Set[int]:
    return task_permissions.visible_list_ids()


def list_to_dict(item: TodoList, visible_ids: Optional[Set[int]] = None) -> Dict[str, Any]:
    data = row2dict(item)
    if data.get("created") and hasattr(data["created"], "isoformat"):
        data["created"] = data["created"].isoformat(sep=" ", timespec="seconds")
    if data.get("updated") and hasattr(data["updated"], "isoformat"):
        data["updated"] = data["updated"].isoformat(sep=" ", timespec="seconds")
    data["created_by"] = item.created_by
    data["permissions"] = task_permissions.list_permissions(item, visible_ids=visible_ids)
    return data


def list_lists() -> list:
    visible_ids = _visible_list_ids()
    query = TodoList.query.order_by(TodoList.sort_order, TodoList.id)
    return task_permissions.filter_visible_lists(query, visible_ids=visible_ids).all()


def get_list(entity_id: int, username: Optional[str] = None) -> TodoList:
    item = TodoList.query.get(entity_id)
    if item is None:
        raise NotFoundError("List not found")
    visible_ids = _visible_list_ids()
    task_permissions.assert_can_view_list(item, username, visible_ids)
    return item


def save_list(payload: Dict[str, Any], entity_id: Optional[int] = None) -> TodoList:
    if not payload.get("title"):
        raise ValueError("title is required")

    username = task_permissions.current_username()

    if entity_id is not None:
        item = get_list(entity_id, username)
        task_permissions.assert_can_edit_list(item, username)
        task_permissions.assign_owner_if_missing(item, username)
        item.updated = get_now_to_utc()
    else:
        item = TodoList()
        item.created = get_now_to_utc()
        item.updated = item.created
        item.created_by = username
        max_order = db.session.query(db.func.max(TodoList.sort_order)).scalar()
        item.sort_order = (max_order or -1) + 1
        db.session.add(item)

    item.title = payload.get("title")
    if "tags" in payload:
        item.tags = payload.get("tags")
    if "sort_order" in payload:
        sort_order = payload.get("sort_order")
        item.sort_order = int(sort_order) if sort_order not in (None, "") else item.sort_order

    db.session.commit()
    db.session.refresh(item)
    return item


def delete_list(entity_id: int) -> bool:
    item = TodoList.query.get(entity_id)
    if item is None:
        raise NotFoundError("List not found")
    visible_ids = _visible_list_ids()
    task_permissions.assert_can_view_list(item, visible_ids=visible_ids)
    task_permissions.assert_can_delete_list(item)
    db.session.execute(delete(TodoTask).where(TodoTask.list_id == entity_id))
    db.session.delete(item)
    db.session.commit()
    return True


def reorder_lists(ordered_ids: list) -> list:
    username = task_permissions.current_username()
    visible_ids = {item.id for item in list_lists()}
    for list_id in ordered_ids:
        if int(list_id) not in visible_ids:
            raise NotFoundError("List not found")
    reordered_any = False
    for index, list_id in enumerate(ordered_ids):
        item = TodoList.query.get(int(list_id))
        if item is None:
            raise NotFoundError("List not found")
        if task_permissions.list_permissions(item)["can_reorder"]:
            task_permissions.assign_owner_if_missing(item, username)
            item.sort_order = index
            item.updated = get_now_to_utc()
            reordered_any = True
    if not reordered_any:
        raise PermissionError("Access denied")
    db.session.commit()
    return list_lists()
