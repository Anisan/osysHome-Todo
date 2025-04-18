"""
# Todo plugin

Plugin for managment tasks

Supports:

"""
from flask import render_template
from sqlalchemy import or_
from app.database import session_scope
from app.core.main.BasePlugin import BasePlugin
from app.api import api
from plugins.Todo.models.Task import TodoTask


class Todo(BasePlugin):

    def __init__(self, app):
        super().__init__(app, __name__)
        self.title = "Todo"
        self.description = """Managment tasks"""
        self.system = True
        self.actions = ['search','widget']
        self.category = "App"
        self.version = "0.1"
        
        from plugins.Todo.api import create_api_ns
        api_ns = create_api_ns()
        api.add_namespace(api_ns, path="/Todo")

    def initialization(self):
        pass

    def admin(self, request):
        return render_template("todo_tasks.html")

    def search(self, query: str) -> list:
        res = []
        tasks = TodoTask.query.filter(or_(TodoTask.title.contains(query),TodoTask.notes.contains(query))).all()
        for task in tasks:
            res.append({"url":'Todo', "title": f'{task.name}', "tags": [{"name":"TODO Task","color":"info"}]})
        return res

    def widget(self):
        content = {}
        with session_scope() as session:
            content['count'] = session.query(TodoTask).count()
        return render_template("widget_todo.html",**content)
