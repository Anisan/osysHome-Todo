"""Todo task access control."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from flask import g
from flask_login import current_user

from plugins.Todo.models.Task import TodoTask


class PermissionError(ValueError):
    """Raised when the current user cannot perform an operation."""


class NotFoundError(ValueError):
    """Raised when entity is missing or not visible to the current user."""


def mcp_unrestricted() -> bool:
    return bool(getattr(g, "_todo_mcp_unrestricted", False))


def current_username() -> Optional[str]:
    user = getattr(g, "current_user", None)
    if user and getattr(user, "username", None):
        return str(user.username)
    if current_user.is_authenticated:
        return str(current_user.username)
    return None


def _role_for_username(username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    from app.core.lib.object import getObject
    from app.utils import User

    obj = getObject(str(username))
    if obj is None:
        return None
    return getattr(User(obj), "role", None)


def is_admin() -> bool:
    if mcp_unrestricted():
        return True
    user = getattr(g, "current_user", None)
    if user and getattr(user, "role", None) == "admin":
        return True
    if current_user.is_authenticated and getattr(current_user, "role", None) == "admin":
        return True
    return _role_for_username(current_username()) == "admin"


def parse_viewers(raw) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    try:
        data = json.loads(str(raw))
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return []


def serialize_viewers(viewers) -> Optional[str]:
    names = parse_viewers(viewers)
    if not names:
        return None
    return json.dumps(names, ensure_ascii=False)


def task_role(task: TodoTask, username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    if task.created_by and task.created_by == username:
        return "creator"
    if task.assignee and task.assignee == username:
        return "assignee"
    if username in parse_viewers(task.viewers):
        return "viewer"
    return None


def owned_list_ids(username: Optional[str] = None) -> set:
    from plugins.Todo.models.List import TodoList

    if is_admin():
        return set()
    username = username or current_username()
    if not username:
        return set()
    rows = (
        TodoList.query.filter(TodoList.created_by == username)
        .with_entities(TodoList.id)
        .all()
    )
    return {int(row[0]) for row in rows if row[0] is not None}


def is_list_owner_of_task(task: TodoTask, username: Optional[str] = None) -> bool:
    if not task.list_id:
        return False
    return int(task.list_id) in owned_list_ids(username)


def can_view_task(task: TodoTask, username: Optional[str] = None) -> bool:
    if is_admin():
        return True
    username = username or current_username()
    if not username:
        return False
    if task_role(task, username) is not None:
        return True
    return is_list_owner_of_task(task, username)


def task_permissions(task: TodoTask, username: Optional[str] = None) -> Dict[str, Any]:
    if mcp_unrestricted():
        return {
            "role": "admin",
            "can_view": True,
            "can_edit": True,
            "can_complete": True,
            "can_delete": True,
            "can_notify": True,
        }
    username = username or current_username()
    role = task_role(task, username) if username else None
    legacy = list_has_no_owner(task.created_by)
    list_owner = is_list_owner_of_task(task, username)
    can_view = can_view_task(task, username)
    admin = is_admin()
    return {
        "role": role or ("owner" if list_owner and can_view else ("legacy" if legacy and can_view else None)),
        "can_view": can_view,
        "can_edit": admin or role == "creator" or list_owner,
        "can_complete": admin or role in ("creator", "assignee"),
        "can_delete": admin or role == "creator" or list_owner,
        "can_notify": admin or role == "creator" or list_owner,
    }


def assert_can_view(task: TodoTask, username: Optional[str] = None) -> None:
    if not can_view_task(task, username):
        raise NotFoundError("Task not found")


def assert_can_edit(task: TodoTask, username: Optional[str] = None) -> None:
    if not task_permissions(task, username)["can_edit"]:
        raise PermissionError("Access denied")


def assert_can_complete(task: TodoTask, username: Optional[str] = None) -> None:
    if not task_permissions(task, username)["can_complete"]:
        raise PermissionError("Access denied")


def assert_can_delete(task: TodoTask, username: Optional[str] = None) -> None:
    if not task_permissions(task, username)["can_delete"]:
        raise PermissionError("Access denied")


def assert_can_notify(task: TodoTask, username: Optional[str] = None) -> None:
    if not task_permissions(task, username)["can_notify"]:
        raise PermissionError("Access denied")


def filter_visible_tasks(query, username: Optional[str] = None):
    from sqlalchemy import or_

    from plugins.Todo.models.List import TodoList

    if is_admin():
        return query
    username = username or current_username()
    if not username:
        return query.filter(False)
    viewers_like = f'%"{username}"%'
    owned_subq = TodoList.query.filter(TodoList.created_by == username).with_entities(TodoList.id)
    return query.filter(
        or_(
            TodoTask.created_by == username,
            TodoTask.assignee == username,
            TodoTask.viewers.like(viewers_like),
            TodoTask.list_id.in_(owned_subq),
        )
    )


def list_users() -> List[Dict[str, str]]:
    from app.core.lib.object import getObjectsByClass

    users = getObjectsByClass("Users")
    result = []
    for user in users:
        name = getattr(user, "name", None)
        if not name:
            continue
        avatar_url = "/Users/static/Users.png"
        props = getattr(user, "__dict__", {}).get("properties", {})
        if "image" in props:
            image = props["image"]._PropertyManager__value
            if image:
                avatar_url = str(image)
        result.append({"username": str(name), "avatar_url": avatar_url})
    return sorted(result, key=lambda item: item["username"].lower())


def list_usernames() -> List[str]:
    return [item["username"] for item in list_users()]


def list_has_no_owner(created_by) -> bool:
    return created_by in (None, "")


def assign_owner_if_missing(entity, username: Optional[str] = None) -> bool:
    """Assign current user as owner when entity has no owner."""
    username = username or current_username()
    if not username or not list_has_no_owner(getattr(entity, "created_by", None)):
        return False
    entity.created_by = username
    return True


def visible_list_ids(username: Optional[str] = None) -> set:
    if is_admin():
        return set()
    username = username or current_username()
    if not username:
        return set()
    rows = (
        filter_visible_tasks(
            TodoTask.query.with_entities(TodoTask.list_id).filter(TodoTask.list_id.isnot(None)),
            username,
        )
        .distinct()
        .all()
    )
    return {int(row[0]) for row in rows if row[0] is not None}


def can_view_list(list_item, username: Optional[str] = None, visible_ids: Optional[set] = None) -> bool:
    from plugins.Todo.models.List import TodoList

    if not isinstance(list_item, TodoList):
        return False
    if is_admin():
        return True
    username = username or current_username()
    if not username:
        return False
    if list_item.created_by == username:
        return True
    if visible_ids is None:
        visible_ids = visible_list_ids(username)
    return list_item.id in visible_ids


def list_permissions(
    list_item,
    username: Optional[str] = None,
    visible_ids: Optional[set] = None,
) -> Dict[str, Any]:
    if mcp_unrestricted():
        return {
            "role": "owner",
            "can_view": True,
            "can_edit": True,
            "can_delete": True,
            "can_reorder": True,
            "can_create_task": True,
        }
    username = username or current_username()
    is_owner = bool(list_item.created_by and list_item.created_by == username)
    legacy = list_has_no_owner(list_item.created_by)
    can_view = can_view_list(list_item, username, visible_ids)
    admin = is_admin()
    can_manage = admin or is_owner or (legacy and can_view)
    return {
        "role": "owner" if is_owner else ("legacy" if legacy and can_view else None),
        "can_view": can_view,
        "can_edit": can_manage,
        "can_delete": can_manage,
        "can_reorder": can_manage,
        "can_create_task": can_manage,
    }


def assert_can_view_list(list_item, username: Optional[str] = None, visible_ids: Optional[set] = None) -> None:
    if not can_view_list(list_item, username, visible_ids):
        raise NotFoundError("List not found")


def assert_can_edit_list(list_item, username: Optional[str] = None) -> None:
    if not list_permissions(list_item, username)["can_edit"]:
        raise PermissionError("Access denied")


def assert_can_delete_list(list_item, username: Optional[str] = None) -> None:
    if not list_permissions(list_item, username)["can_delete"]:
        raise PermissionError("Access denied")


def assert_can_reorder_list(list_item, username: Optional[str] = None) -> None:
    if not list_permissions(list_item, username)["can_reorder"]:
        raise PermissionError("Access denied")


def assert_can_create_task_in_list(list_id: int, username: Optional[str] = None) -> None:
    from plugins.Todo.models.List import TodoList

    if list_id in (None, ""):
        return
    list_item = TodoList.query.get(int(list_id))
    if list_item is None or not can_view_list(list_item, username):
        raise NotFoundError("List not found")
    if not list_permissions(list_item, username)["can_create_task"]:
        raise PermissionError("Access denied")


def filter_visible_lists(query, username: Optional[str] = None, visible_ids: Optional[set] = None):
    from sqlalchemy import or_

    from plugins.Todo.models.List import TodoList

    if is_admin():
        return query
    username = username or current_username()
    if not username:
        return query.filter(False)
    if visible_ids is None:
        visible_ids = visible_list_ids(username)
    filters = [TodoList.created_by == username]
    if visible_ids:
        filters.append(TodoList.id.in_(visible_ids))
    return query.filter(or_(*filters))
