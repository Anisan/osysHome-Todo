
from app.database import Column, SurrogatePK, db

class TodoList(SurrogatePK, db.Model):
    __tablename__ = 'todo_lists'
    title = Column(db.String(100))
    created = Column(db.DateTime)
    updated = Column(db.DateTime)
    tags = Column(db.Text)
