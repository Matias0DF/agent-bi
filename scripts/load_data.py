import argparse
import io
import os
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

TABLES = {
    "olist_customers_dataset.csv": "customers",
    "olist_geolocation_dataset.csv": "geolocation",
    "olist_order_items_dataset.csv": "order_items",
    "olist_order_payments_dataset.csv": "order_payments",
    "olist_order_reviews_dataset.csv": "order_reviews",
    "olist_orders_dataset.csv": "orders",
    "olist_products_dataset.csv": "products",
    "olist_sellers_dataset.csv": "sellers",
    "product_category_name_translation.csv": "category_translation",
}


def sql_type(column: str, dtype) -> str:
    """Traduce el tipo de pandas a un tipo de Postgres."""
    if "timestamp" in column or "date" in column or column.endswith("_at"):
        return "TIMESTAMP"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "NUMERIC"
    return "TEXT"


def load_table(conn, csv_name: str, table: str) -> int:
    """Crea la tabla y copia el CSV adentro."""
    df = pd.read_csv(RAW_DIR / csv_name)
    df.columns = [c.lstrip("﻿") for c in df.columns]
    columns = ", ".join(f"{c} {sql_type(c, df[c].dtype)}" for c in df.columns)

    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False)
    buffer.seek(0)

    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE TABLE {table} ({columns})")
        with cur.copy(f"COPY {table} FROM STDIN WITH (FORMAT csv)") as copy:
            copy.write(buffer.read())
    return len(df)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip", nargs="*", default=[], help="Tablas a omitir, por ejemplo geolocation")
    args = parser.parse_args()

    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for csv_name, table in TABLES.items():
            if table in args.skip:
                continue
            rows = load_table(conn, csv_name, table)
            print(f"{table:<22} {rows:>9,} filas")


if __name__ == "__main__":
    main()
