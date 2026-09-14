import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from dmm.models.base import *
from dmm.models.request import Request
from dmm.models.site import Site
from dmm.models.endpoint import Endpoint
from dmm.models.mesh import Mesh

from dmm.db.session import get_engine


def sync_columns(engine):
    """
    Add model columns that the live database is missing.

    create_all() only ever creates whole tables, so a column added to a model is
    invisible to an existing deployment and every SELECT naming it fails. This
    closes that gap for the only case that is safe to automate: adding a nullable
    column. Nothing is ever dropped, renamed, or retyped - those still need a
    hand-written migration.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all() will make it, columns and all

        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable or column.primary_key:
                logging.error(
                    f"Column {table.name}.{column.name} is missing from the database and "
                    "cannot be added automatically because it is not nullable - "
                    "add it by hand"
                )
                continue
            col_type = column.type.compile(engine.dialect)
            logging.warning(f"Adding missing column {table.name}.{column.name} ({col_type})")
            try:
                with engine.begin() as connection:
                    connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
            except SQLAlchemyError as e:
                # Most likely another instance starting at the same moment already
                # added it. Anything else is for an operator to look at - startup
                # continues either way, and the queries touching the column will say
                # plainly if it really is absent.
                logging.warning(f"Could not add {table.name}.{column.name}: {e}")


engine = get_engine()
SQLModel.metadata.create_all(engine)
sync_columns(engine)
