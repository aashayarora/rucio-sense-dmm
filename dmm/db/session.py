from functools import wraps

from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy import create_engine
from threading import Lock

from dmm.utils.config import config_get

_MAKER, _ENGINE, _LOCK = None, None, Lock()

def get_engine():
    global _ENGINE
    if not _ENGINE:
        username = config_get("db", "username", default="dmm")
        password = config_get("db", "password", default="dmm")
        host = config_get("db", "db_host", default="localhost")
        port = config_get("db", "db_port", default="5432")
        db_name = config_get("db", "db_name", default="dmm")
        _ENGINE = create_engine('sqlite:///dmm.db')
    assert _ENGINE
    return _ENGINE

def get_maker():
    global _MAKER, _ENGINE
    assert _ENGINE
    if not _MAKER:
        _MAKER = sessionmaker(bind=_ENGINE)
    return _MAKER

def get_session():
    global _MAKER
    if not _MAKER:
        _LOCK.acquire()
        try:
            get_engine()
            get_maker()
        finally:
            _LOCK.release()
    assert _MAKER
    session = scoped_session(_MAKER)
    return session

def databased(function):
    @wraps(function)
    def new_funct(*args, **kwargs):
        if not kwargs.get('session'):
            session = get_session()
            try:
                kwargs['session'] = session
                result = function(*args, **kwargs)
                session.commit()
            except:
                session.rollback()
                raise
            finally:
                session.remove()
        else:
            result = function(*args, **kwargs)
        return result
    new_funct.__doc__ = function.__doc__
    return new_funct