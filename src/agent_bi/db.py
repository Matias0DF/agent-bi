import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

MAX_ROWS = 200


def connect():
    """Abre una conexión a la base usando DATABASE_URL."""
    return psycopg.connect(os.environ["DATABASE_URL"])


def get_schema() -> str:
    """Devuelve las tablas y columnas de la base como texto."""
    query = """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """
    with connect() as conn:
        rows = conn.execute(query).fetchall()

    tables: dict[str, list[str]] = {}
    for table, column, dtype in rows:
        tables.setdefault(table, []).append(f"{column} ({dtype})")
    return "\n".join(f"{t}: {', '.join(cols)}" for t, cols in tables.items())


def run_query(sql: str) -> tuple[list[str], list[tuple]]:
    """Ejecuta una consulta de solo lectura y devuelve columnas y filas."""
    if not sql.lstrip().lower().startswith(("select", "with")):
        raise ValueError("Solo se permiten consultas SELECT.")

    with connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '10s'")
        cur = conn.execute(sql)
        columns = [d.name for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
    return columns, rows
