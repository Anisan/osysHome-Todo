import datetime
from flask import request
from flask_restx import Namespace, Resource
from app.api.decorators import api_key_required
from app.authentication.handlers import handle_user_required
from app.api.models import model_404, model_result
from plugins.Todo.services import list_service, task_service, task_permissions as todo_perms
from plugins.Todo.services.task_permissions import NotFoundError, PermissionError, list_users

_api_ns = Namespace(name="Todo", description="Todo namespace", validate=True)

response_result = _api_ns.model("Result", model_result)
response_404 = _api_ns.model("Error", model_404)


def create_api_ns():
    return _api_ns

@_api_ns.route("/lists", endpoint="todo_lists")
class GetLists(Resource):
    @api_key_required
    @handle_user_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "List lists", response_result)
    def get(self):
        """
        Get lists visible to the current user
        """
        visible_ids = todo_perms.visible_list_ids()
        lists = list_service.list_lists()
        result = [list_service.list_to_dict(item, visible_ids=visible_ids) for item in lists]
        return {"success": True, "result": result}, 200
        
@_api_ns.route("/list/<list_id>", endpoint="todo_list")
class EndpointList(Resource):
    @api_key_required
    @handle_user_required
    def get(self,list_id: int):
        """ Get list """
        try:
            item = list_service.get_list(int(list_id))
        except NotFoundError:
            return {"success": False, "msg": "List not found"}, 404
        visible_ids = todo_perms.visible_list_ids()
        return {"success": True, "result": list_service.list_to_dict(item, visible_ids=visible_ids)}, 200

    @api_key_required
    @handle_user_required
    def post(self,list_id):
        """ Create/update list """
        data = request.get_json() or {}
        entity_id = int(list_id) if data.get("id") else None
        try:
            list_rec = list_service.save_list(data, entity_id=entity_id)
            visible_ids = todo_perms.visible_list_ids()
            return {"success": True, "result": list_service.list_to_dict(list_rec, visible_ids=visible_ids)}, 200
        except NotFoundError:
            return {"success": False, "msg": "List not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403
        except ValueError as exc:
            return {"success": False, "msg": str(exc)}, 400

    @api_key_required
    @handle_user_required
    def delete(self,list_id):
        """ Delete list """
        try:
            list_service.delete_list(int(list_id))
            return {"success": True}, 200
        except NotFoundError:
            return {"success": False, "msg": "List not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403

@_api_ns.route("/lists/reorder", endpoint="todo_lists_reorder")
class ReorderLists(Resource):
    @api_key_required
    @handle_user_required
    def post(self):
        """Reorder lists by id array"""
        data = request.get_json() or {}
        ordered_ids = data.get("ids") or data.get("order")
        if not ordered_ids or not isinstance(ordered_ids, list):
            return {"success": False, "msg": "ids array is required"}, 400
        try:
            lists = list_service.reorder_lists(ordered_ids)
        except NotFoundError:
            return {"success": False, "msg": "List not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403
        except ValueError as exc:
            return {"success": False, "msg": str(exc)}, 400
        visible_ids = todo_perms.visible_list_ids()
        result = [list_service.list_to_dict(item, visible_ids=visible_ids) for item in lists]
        return {"success": True, "result": result}, 200

@_api_ns.route("/tasks", endpoint="todo_tasks")
class GetTasks(Resource):
    @api_key_required
    @handle_user_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "List tasks", response_result)
    def get(self):
        """
        Get tasks visible to the current user
        """
        tasks = task_service.list_tasks()
        result = [task_service.task_to_dict(task) for task in tasks]
        return {"success": True, "result": result}, 200


@_api_ns.route("/users", endpoint="todo_users")
class GetTodoUsers(Resource):
    @api_key_required
    @handle_user_required
    def get(self):
        """List usernames for assignee and viewer selectors."""
        return {"success": True, "result": list_users()}, 200


@_api_ns.route("/task/<task_id>", endpoint="todo_task")
class EndpointTask(Resource):
    @api_key_required
    @handle_user_required
    def get(self,task_id: int):
        """ Get task """
        try:
            task = task_service.get_task(int(task_id))
        except NotFoundError:
            return {"success": False, "msg": "Task not found"}, 404
        return {"success": True, "result": task_service.task_to_dict(task)}, 200

    @api_key_required
    @handle_user_required
    def post(self,task_id):
        """ Create/update task """
        data = request.get_json() or {}
        entity_id = int(task_id) if data.get("id") else None
        try:
            task = task_service.save_task(data, entity_id=entity_id)
            return {"success": True, "result": task_service.task_to_dict(task)}, 200
        except NotFoundError:
            return {"success": False, "msg": "Not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403
        except ValueError as exc:
            return {"success": False, "msg": str(exc)}, 400

    @api_key_required
    @handle_user_required
    def delete(self,task_id):
        """ Delete task """
        try:
            task_service.delete_task(int(task_id))
            return {"success": True}, 200
        except NotFoundError:
            return {"success": False, "msg": "Task not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403

@_api_ns.route("/task/<task_id>/complete", endpoint="todo_task_complete")
class EndpointTaskComplete(Resource):
    @api_key_required
    @handle_user_required
    def get(self,task_id: int):
        """ Switch task complited"""
        try:
            task = task_service.toggle_task_complete(int(task_id))
            return {"success": True, "result": task_service.task_to_dict(task)}, 200
        except NotFoundError:
            return {"success": False, "msg": "Task not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403


@_api_ns.route("/task/<task_id>/notified", endpoint="todo_task_notified")
class EndpointTaskNotified(Resource):
    @api_key_required
    @handle_user_required
    def post(self, task_id: int):
        """Mark task as notified and run notification hook."""
        try:
            task = task_service.mark_task_notified(int(task_id))
            return {"success": True, "result": task_service.task_to_dict(task)}, 200
        except NotFoundError:
            return {"success": False, "msg": "Task not found"}, 404
        except PermissionError:
            return {"success": False, "msg": "Access denied"}, 403


@_api_ns.route("/settings", endpoint="todo_settings")
class EndpointTodoSettings(Resource):
    @api_key_required
    @handle_user_required
    def get(self):
        """Get Todo notification default settings."""
        from app.core.main.PluginsHelper import plugins
        instance = plugins.get("Todo", {}).get("instance")
        if not instance:
            return {"success": False, "msg": "Todo plugin not loaded"}, 503
        return {"success": True, "result": instance.get_notification_settings()}, 200

    @api_key_required
    @handle_user_required
    def post(self):
        """Update Todo notification default settings."""
        from app.core.main.PluginsHelper import plugins
        instance = plugins.get("Todo", {}).get("instance")
        if not instance:
            return {"success": False, "msg": "Todo plugin not loaded"}, 503
        data = request.get_json() or {}
        result = instance.save_notification_settings(data)
        return {"success": True, "result": result}, 200
