"""
# Todo plugin

Plugin for managment tasks

Supports:

"""
from flask import redirect, render_template, request
from sqlalchemy import or_, func
from app.authentication.handlers import handle_user_required
from app.database import session_scope
from app.core.main.BasePlugin import BasePlugin
from app.api import api
from plugins.Todo.models.Task import TodoTask


class Todo(BasePlugin):

    def __init__(self, app):
        super().__init__(app, "Todo")
        self.title = "Todo"
        self.description = """Managment tasks"""
        self.system = True
        self.actions = ['search','widget','page']
        self.category = "App"
        self.version = "0.1"
        
        from plugins.Todo.api import create_api_ns
        api_ns = create_api_ns()
        api.add_namespace(api_ns, path="/Todo")

        with app.app_context():
            from plugins.Todo.services.task_service import migrate_deadline_to_finished
            migrate_deadline_to_finished()

    def initialization(self):
        from plugins.Todo.services import task_service
        with self._app.app_context():
            task_service.resync_all_schedules(self.config)

    def run_task_event(self, task_id, event):
        from plugins.Todo.services import notification_service
        return notification_service.run_task_event(
            int(task_id), str(event), plugin_config=self.config
        )

    def get_notification_settings(self):
        from plugins.Todo.services.notification_service import normalize_plugin_settings
        return normalize_plugin_settings(self.config)

    def save_notification_settings(self, payload: dict):
        from plugins.Todo.services.notification_service import (
            DEFAULT_PLUGIN_SETTINGS,
            normalize_plugin_settings,
        )
        from plugins.Todo.services import task_service

        current = normalize_plugin_settings(self.config)
        for key in DEFAULT_PLUGIN_SETTINGS:
            if key in payload:
                current[key] = "" if payload[key] is None else str(payload[key])
        self.config.update(current)
        self.saveConfig()
        task_service.resync_all_schedules(self.config)
        return current

    def admin(self, request):
        tab = request.args.get("tab", "")
        if tab == "settings":
            return self._admin_settings(request)
        if tab == "calendar":
            return render_template("todo_calendar.html", tab="calendar")
        return render_template("todo_tasks.html", tab="")

    def _admin_settings(self, request):
        message = None
        message_type = "success"
        settings_stab = request.args.get("stab", "reminder")
        if settings_stab not in ("reminder", "schedule", "events"):
            settings_stab = "reminder"

        if request.method == "POST":
            from plugins.Todo.services.notification_service import DEFAULT_PLUGIN_SETTINGS
            settings_stab = request.form.get("settings_stab", settings_stab)
            if settings_stab not in ("reminder", "schedule", "events"):
                settings_stab = "reminder"
            payload = {
                key: request.form.get(key, "") or ""
                for key in DEFAULT_PLUGIN_SETTINGS
            }
            self.save_notification_settings(payload)
            return redirect(f"Todo?tab=settings&stab={settings_stab}&saved=1")

        if request.args.get("saved"):
            message = "Settings saved"

        return self.render(
            "todo_settings.html",
            {
                "tab": "settings",
                "settings_stab": settings_stab,
                "settings": self.get_notification_settings(),
                "message": message,
                "message_type": message_type,
            },
        )

    def route_page(self):
        @self.blueprint.route("/page/" + self.name, methods=["GET", "POST"])
        @handle_user_required
        def module_page():
            return self.page(request)

    def page(self, request):
        return render_template("todo_tasks_page.html")

    def search(self, query: str) -> list:
        if not query or len(query.strip()) < 1:
            return []

        from plugins.Todo.services import task_permissions

        q = query.strip()
        pattern = f"%{q}%"
        tasks_query = TodoTask.query.filter(
            or_(
                TodoTask.title.ilike(pattern),
                func.coalesce(TodoTask.notes, "").ilike(pattern),
            )
        )
        tasks_query = task_permissions.filter_visible_tasks(tasks_query)
        tasks = (
            tasks_query
            .order_by(TodoTask.updated.desc().nullslast(), TodoTask.id.desc())
            .limit(100)
            .all()
        )

        res = []
        for task in tasks:
            title = (task.title or "").strip()
            if not title:
                title = f"Task #{task.id}"
            res.append(
                {
                    "url": "Todo",
                    "title": title,
                    "tags": [{"name": "Task", "color": "primary"}],
                }
            )
        return res

    def widget(self):
        from plugins.Todo.services import task_permissions
        content = {}
        with session_scope() as session:
            query = task_permissions.filter_visible_tasks(session.query(TodoTask))
            content['count'] = query.count()
        return render_template("widget_todo.html", **content)

    # --- MCP integration ---

    def mcp_capabilities(self):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_capabilities()

    def mcp_config_schema(self):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_config_schema()

    def mcp_entity_schema(self, collection: str):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_entity_schema(collection)

    def mcp_list_entities(
        self,
        collection: str,
        query: str = None,
        limit: int = 100,
        list_id=None,
        has_started=None,
        completed_only=None,
    ):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_list_entities(
            collection,
            query=query,
            limit=limit,
            list_id=list_id,
            has_started=has_started,
            completed_only=completed_only,
        )

    def mcp_get_entity(self, collection: str, entity_id):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_get_entity(collection, entity_id)

    def mcp_upsert_entity(self, collection: str, payload: dict, entity_id=None):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_upsert_entity(collection, payload, entity_id=entity_id)

    def mcp_delete_entity(self, collection: str, entity_id):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_delete_entity(collection, entity_id)

    def mcp_validate_entity_code(self, collection: str, code: str):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_validate_entity_code(collection, code)

    def mcp_run_entity_dry(self, collection: str, code: str, context: dict = None):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_run_entity_dry(collection, code, context=context)

    def mcp_invoke(self, operation: str, params: dict = None):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_invoke(operation, params or {})

    def mcp_entity_revision(self, collection: str, entity_id):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_entity_revision(collection, entity_id)

    def mcp_validate_entity(self, collection: str, payload: dict, entity_id=None):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_validate_entity(collection, payload, entity_id=entity_id)

    def mcp_tools(self):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_descriptors()[0]

    def mcp_resources(self):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_descriptors()[1]

    def mcp_prompts(self):
        from plugins.Todo import mcp_support
        return mcp_support.mcp_descriptors()[2]
