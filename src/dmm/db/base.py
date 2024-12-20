from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy import text, or_
from datetime import datetime

BASE = declarative_base()

class ModelBase:
    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()
    
    @declared_attr
    def created_at(cls):
        return Column(DateTime, default=datetime.utcnow)
    
    @declared_attr
    def updated_at(cls):
        return Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        attrs = {k: getattr(self, k) for k in vars(self) if not k.startswith('_')}
        return f"<{self.__class__.__name__}({attrs})>"

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __getitem__(self, key):
        return getattr(self, key)
    
    def save(self, session=None):
        session.add(self)
        session.commit()

    def delete(self, session=None):
        session.delete(self)

    def update(self, values, session=None):
        for k, v in values.items():
            self[k] = v

    @classmethod
    def get_all(cls, session=None):
        return [obj for obj in session.query(cls).all()]