import datetime
from flask import request
from flask_restx import Namespace, Resource
from sqlalchemy import delete
from app.api.decorators import api_key_required
from app.authentication.handlers import handle_user_required
from app.api.models import model_404, model_result
from plugins.Todo.models.Task import TodoTask
from plugins.Todo.models.List import TodoList
from app.database import row2dict, session_scope, get_now_to_utc

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
        Get lists
        """
        with session_scope() as session:
            lists = session.query(TodoList).all()
            result = [row2dict(item) for item in lists]
            return {"success": True, "result": result}, 200
        
@_api_ns.route("/list/<list_id>", endpoint="todo_list")
class EndpointList(Resource):
    @api_key_required
    @handle_user_required
    def get(self,list_id: int):
        """ Get list """
        with session_scope() as session:
            task = session.query(TodoList).filter(TodoList.id == list_id).one_or_none()
            if task:
                result = row2dict(task)
                return {"success": True, "result": result}, 200
            return {"success": False, "msg": "List not found"}, 404
    @api_key_required
    @handle_user_required
    def post(self,list_id):
        """ Create/update task """
        with session_scope() as session:
            data = request.get_json()
            if data.get("id"):
                list_rec = session.query(TodoList).filter(TodoList.id == list_id).one()
            else:
                list_rec = TodoList()
                list_rec.created = get_now_to_utc()
                session.add(list_rec)
            list_rec.title = data.get('title')
            list_rec.updated = data.get('updated')
            list_rec.tags = data.get('tags')
            session.commit()
            return {"success": True, "result": row2dict(list_rec)}, 200
    @api_key_required
    @handle_user_required
    def delete(self,list_id):
        """ Delete list """
        with session_scope() as session:
            sql = delete(TodoTask).where(TodoTask.list_id == list_id)
            session.execute(sql)
            sql = delete(TodoList).where(TodoList.id == list_id)
            session.execute(sql)
            session.commit()
            return {"success": True}, 200

@_api_ns.route("/tasks", endpoint="todo_tasks")
class GetTasks(Resource):
    @api_key_required
    @handle_user_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "List tasks", response_result)
    def get(self):
        """
        Get tasks
        """
        with session_scope() as session:
            tasks = session.query(TodoTask).all()
            result = [row2dict(task) for task in tasks]
            return {"success": True, "result": result}, 200


@_api_ns.route("/task/<task_id>", endpoint="todo_task")
class EndpointTask(Resource):
    @api_key_required
    @handle_user_required
    def get(self,task_id: int):
        """ Get task """
        with session_scope() as session:
            task = session.query(TodoTask).filter(TodoTask.id == task_id).one_or_none()
            if task:
                result = row2dict(task)
                return {"success": True, "result": result}, 200
            return {"success": False, "msg": "Task not found"}, 404
    @api_key_required
    @handle_user_required
    def post(self,task_id):
        """ Create/update task """
        with session_scope() as session:
            data = request.get_json()
            if data.get("id"):
                task = session.query(TodoTask).filter(TodoTask.id == task_id).one()
                task.updated = get_now_to_utc()
            else:
                task = TodoTask()
                task.created = get_now_to_utc()
                session.add(task)
            task.list_id = data.get('list_id')
            task.title = data.get('title')
            task.notes = data.get('notes')
            task.updated = data.get('updated')
            task.completed = data.get('complited')
            task.tags = data.get('tags')
            session.commit()
            return {"success": True, "result": row2dict(task)}, 200
    @api_key_required
    @handle_user_required
    def delete(self,task_id):
        """ Delete task """
        with session_scope() as session:
            sql = delete(TodoTask).where(TodoTask.id == task_id)
            session.execute(sql)
            session.commit()
            return {"success": True}, 200

@_api_ns.route("/task/<task_id>/complete", endpoint="todo_task_complete")
class EndpointTaskComplete(Resource):
    @api_key_required
    @handle_user_required
    def get(self,task_id: int):
        """ Switch task complited"""
        with session_scope() as session:
            task = session.query(TodoTask).filter(TodoTask.id == task_id).one_or_none()
            if task:
                if task.completed:
                    task.completed = None
                else:
                    task.completed = get_now_to_utc()
                session.commit()
                result = row2dict(task)
                return {"success": True, "result": result}, 200
            return {"success": False, "msg": "Task not found"}, 404
