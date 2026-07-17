
from app.database import Column, SurrogatePK, db

class TodoList(SurrogatePK, db.Model):
    __tablename__ = 'todo_lists'
    title = Column(db.String(100))
    sort_order = Column(db.Integer, default=0)
    created = Column(db.DateTime)
    updated = Column(db.DateTime)
    tags = Column(db.Text)
    created_by = Column(db.String(64))
