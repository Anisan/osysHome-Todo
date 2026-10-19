
from app.database import Column, SurrogatePK, db

class TodoTask(SurrogatePK, db.Model):
    __tablename__ = 'todo_tasks'
    list_id = Column(db.Integer)
    parent_id = Column(db.Integer)  # NULL = корневая задача
    title = Column(db.String(255))
    notes = Column(db.Text)
    extra = Column(db.Text)  # доп. данные (внешний id импорта и т.п.); вкладка Extra в UI
    tags = Column(db.Text)
    finished = Column(db.DateTime)
    priority = Column(db.Integer, default=1)
    started = Column(db.DateTime)
    all_day = Column(db.Boolean, default=False)
    created = Column(db.DateTime)
    updated = Column(db.DateTime)
    completed = Column(db.DateTime)
    settings = Column(db.Text)
    created_by = Column(db.String(64))
    assignee = Column(db.String(64))
    viewers = Column(db.Text)


