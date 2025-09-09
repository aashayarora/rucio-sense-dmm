from functools import wraps
from inspect import iscoroutinefunction

from sqlmodel import create_engine, Session

from dmm.core.config import config_get

_ENGINE = None

def get_engine():
    global _ENGINE
    if not _ENGINE:
        db_type = config_get("db", "db_type", default="postgresql")
        if db_type == "postgresql":
            username = config_get("db", "username", default="dmm")
            password = config_get("db", "password", default="dmm")
            host = config_get("db", "db_host", default="localhost")
            port = config_get("db", "db_port", default="5432")
            db_name = config_get("db", "db_name", default="dmm")
            _ENGINE = create_engine(
                f"postgresql+psycopg2://{username}:{password}@{host}:{port}/{db_name}",
                pool_size=1000, max_overflow=0
            )
        elif db_type == "sqlite":
            db_file = config_get("db", "db_file", default="dmm.db")
            _ENGINE = create_engine(
                f"sqlite:///{db_file}"
            )
        else:
            raise ValueError(f"Unknown database type: {db_type}, supported types are 'postgresql' and 'sqlite'")
    assert _ENGINE
    return _ENGINE

def get_session():
    get_engine()
    return Session(_ENGINE)

def databased(function):
    if iscoroutinefunction(function):
        @wraps(function)
        async def new_funct(*args, **kwargs):
            if not kwargs.get('session'):
                with get_session() as session:
                    try:
                        kwargs['session'] = session
                        result = await function(*args, **kwargs)
                        session.commit()
                    except:
                        session.rollback()
                        raise
            else:
                result = await function(*args, **kwargs)
            return result
    else:
        @wraps(function)
        def new_funct(*args, **kwargs):
            if not kwargs.get('session'):
                with get_session() as session:
                    try:
                        kwargs['session'] = session
                        result = function(*args, **kwargs)
                        session.commit()
                    except:
                        session.rollback()
                        raise
            else:
                result = function(*args, **kwargs)
            return result
    return new_funct