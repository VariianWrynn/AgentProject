"""
Create sales.db with 10 products and 100 sales rows (seed=42).

Run:
    python data/create_db.py
"""

import sqlite3
import random
from datetime import date, timedelta
from pathlib import Path

PRODUCTS = [
    (1,  "笔记本电脑", "电子产品", 5999.0),
    (2,  "手机",       "电子产品", 3999.0),
    (3,  "冰箱",       "家电",     2999.0),
    (4,  "洗衣机",     "家电",     1999.0),
    (5,  "外套",       "服装",      599.0),
    (6,  "衬衫",       "服装",      299.0),
    (7,  "零食",       "食品",       49.0),
    (8,  "饮料",       "食品",       29.0),
    (9,  "打印机",     "办公",     1499.0),
    (10, "文具",       "办公",       39.0),
]

REGIONS = ["华东", "华南", "华北"]

DB_PATH = Path(__file__).parent / "sales.db"


def main() -> None:
    rng = random.Random(42)
    today = date.today()

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.executescript("""
        DROP TABLE IF EXISTS sales;
        DROP TABLE IF EXISTS products;

        CREATE TABLE products (
            id           INTEGER PRIMARY KEY,
            product_name TEXT    NOT NULL,
            category     TEXT    NOT NULL,
            unit_price   REAL    NOT NULL
        );

        CREATE TABLE sales (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            product   TEXT NOT NULL,
            region    TEXT NOT NULL,
            amount    REAL NOT NULL,
            sale_date TEXT NOT NULL
        );
    """)

    cur.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)",
        PRODUCTS,
    )

    rows = []
    for _ in range(100):
        pid, pname, _cat, unit_price = rng.choice(PRODUCTS)
        region = rng.choice(REGIONS)
        offset = rng.randint(0, 180)
        sale_date = (today - timedelta(days=offset)).isoformat()
        amount = round(unit_price * rng.uniform(0.8, 3.0), 2)
        rows.append((pname, region, amount, sale_date))

    cur.executemany(
        "INSERT INTO sales (product, region, amount, sale_date) VALUES (?, ?, ?, ?)",
        rows,
    )

    conn.commit()
    conn.close()
    print(f"Created {DB_PATH} with {len(PRODUCTS)} products and {len(rows)} sales rows.")


if __name__ == "__main__":
    main()
