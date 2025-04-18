
from app.database import Column, SurrogatePK, db

class TodoTask(SurrogatePK, db.Model):
    __tablename__ = 'todo_tasks'
    list_id = Column(db.Integer)
    title = Column(db.String(255))
    notes = Column(db.Text)
    tags = Column(db.Text)
    created = Column(db.DateTime)
    updated = Column(db.DateTime)
    completed = Column(db.DateTime)
    
