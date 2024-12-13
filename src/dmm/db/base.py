from datetime import datetime

from sqlmodel import SQLModel, Field

class ModelBase(SQLModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now())
    updated_at: datetime = Field(default_factory=lambda: datetime.now())

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