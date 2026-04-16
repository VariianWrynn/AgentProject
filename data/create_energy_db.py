"""
Create and seed data/energy.db with three energy-industry tables.

Run from the project root:
    python data/create_energy_db.py

Tables:
  company_finance  — quarterly financials for 10 major energy companies (120 rows)
  capacity_stats   — annual installed-capacity by energy type (180 rows)
  price_index      — monthly electricity-price snapshots (150 rows)
"""

import os
import random
import sqlite3

# ── path ──────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, "energy.db")

# ── seed for reproducibility ──────────────────────────────────────────────────
rng = random.Random(42)

# ── domain constants ──────────────────────────────────────────────────────────
COMPANIES = [
    ("国家电网",   "华北",  3000, 0.72),
    ("华能集团",   "华北",   600, 0.68),
    ("大唐发电",   "华东",   400, 0.71),
    ("三峡能源",   "华中",   350, 0.58),
    ("华电集团",   "华南",   450, 0.66),
    ("中国核电",   "华东",   700, 0.55),
    ("协鑫集成",   "华东",   120, 0.63),
    ("隆基绿能",   "西北",   400, 0.42),
    ("宁德时代",   "华南",   400, 0.48),
    ("比亚迪",     "华南",   500, 0.52),
]
# (company_name, region, base_revenue_billion, base_debt_ratio)

YEARS    = [2022, 2023, 2024]
QUARTERS = [1, 2, 3, 4]

ENERGY_TYPES = ["火电", "水电", "风电", "光伏", "核电", "储能"]

# province pools by region
PROVINCES = {
    "华北": ["北京", "天津", "河北", "山西", "内蒙古"],
    "华东": ["上海", "江苏", "浙江", "安徽", "山东"],
    "华南": ["广东", "广西", "海南", "福建"],
    "华中": ["湖北", "湖南", "河南", "江西"],
    "西北": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
}

# base installed-MW by energy type (realistic order-of-magnitude)
BASE_MW = {
    "火电":  50000,
    "水电":  30000,
    "风电":  20000,
    "光伏":  15000,
    "核电":  10000,
    "储能":   2000,
}

# which energy types each company operates (subset)
COMPANY_ENERGY_TYPES = {
    "国家电网":  ["火电", "水电", "风电", "光伏", "储能"],
    "华能集团":  ["火电", "风电", "光伏"],
    "大唐发电":  ["火电", "水电", "风电"],
    "三峡能源":  ["水电", "风电", "光伏"],
    "华电集团":  ["火电", "水电", "储能"],
    "中国核电":  ["核电", "风电"],
    "协鑫集成":  ["光伏", "储能"],
    "隆基绿能":  ["光伏"],
    "宁德时代":  ["储能"],
    "比亚迪":    ["储能", "光伏"],
}

REGIONS        = ["华东", "华南", "华北", "西北", "华中"]

# base price (yuan/kWh) by energy type
BASE_PRICE = {
    "火电":  0.42,
    "水电":  0.28,
    "风电":  0.35,
    "光伏":  0.32,
    "核电":  0.43,
    "储能":  0.55,
}


# ── DDL ───────────────────────────────────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS company_finance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,
    revenue_billion REAL    NOT NULL,
    profit_billion  REAL    NOT NULL,
    debt_ratio      REAL    NOT NULL,
    region          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS capacity_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT    NOT NULL,
    energy_type  TEXT    NOT NULL,
    installed_mw REAL    NOT NULL,
    year         INTEGER NOT NULL,
    province     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS price_index (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT    NOT NULL,
    energy_type    TEXT    NOT NULL,
    region         TEXT    NOT NULL,
    price_yuan_kwh REAL    NOT NULL,
    spot_price     REAL    NOT NULL,
    forward_price  REAL    NOT NULL
);
"""


# ── seed helpers ──────────────────────────────────────────────────────────────

def _jitter(base: float, pct: float = 0.15) -> float:
    """Return base ± pct*base."""
    return round(base * (1 + rng.uniform(-pct, pct)), 3)


def seed_company_finance(conn: sqlite3.Connection) -> int:
    rows = []
    for (name, region, base_rev, base_debt) in COMPANIES:
        for year in YEARS:
            year_growth = 1 + (year - 2022) * rng.uniform(0.03, 0.12)
            for q in QUARTERS:
                q_factor = [0.22, 0.26, 0.28, 0.24][q - 1]
                rev  = round(base_rev * year_growth * q_factor * _jitter(1.0, 0.08), 2)
                margin = rng.uniform(0.04, 0.14)
                prof = round(rev * margin, 2)
                debt = round(base_debt + rng.uniform(-0.05, 0.05), 3)
                rows.append((name, year, q, rev, prof, debt, region))
    conn.executemany(
        "INSERT INTO company_finance (company_name,year,quarter,revenue_billion,"
        "profit_billion,debt_ratio,region) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def seed_capacity_stats(conn: sqlite3.Connection) -> int:
    rows = []
    _company_map = {c[0]: c for c in COMPANIES}
    for (name, region, _rev, _debt) in COMPANIES:
        all_provinces = PROVINCES.get(region, ["其他"])
        # Pick 2-3 provinces per company so we get more rows
        num_provinces = min(len(all_provinces), rng.randint(2, 3))
        selected_provs = rng.sample(all_provinces, num_provinces)
        for energy_type in COMPANY_ENERGY_TYPES.get(name, []):
            for year in YEARS:
                for prov in selected_provs:
                    growth = 1 + (year - 2022) * rng.uniform(0.05, 0.20)
                    base   = BASE_MW[energy_type]
                    rev_scale = _company_map[name][2] / 3000 if name in _company_map else 0.3
                    mw = round(base * rev_scale * growth * _jitter(1.0, 0.20), 1)
                    rows.append((name, energy_type, mw, year, prov))
    conn.executemany(
        "INSERT INTO capacity_stats (company_name,energy_type,installed_mw,year,province)"
        " VALUES (?,?,?,?,?)",
        rows,
    )
    return len(rows)


def seed_price_index(conn: sqlite3.Connection) -> int:
    rows = []
    # Generate monthly snapshots: Jan 2022 – Dec 2024 (36 months)
    months = []
    for year in [2022, 2023, 2024]:
        for m in range(1, 13):
            months.append(f"{year}-{m:02d}-01")

    for energy_type in ENERGY_TYPES:
        base = BASE_PRICE[energy_type]
        for region in REGIONS:
            # regional premium
            regional_premium = {"华东": 0.04, "华南": 0.03, "华北": 0.0,
                                 "西北": -0.03, "华中": 0.01}.get(region, 0.0)
            for date in months:
                year = int(date[:4])
                # slight annual trend: solar/storage falling, coal slightly rising
                trend = {"光伏": -0.02, "储能": -0.03, "火电": 0.01,
                         "风电": -0.01, "水电": 0.0, "核电": 0.005}.get(energy_type, 0.0)
                price = round(base + regional_premium + trend * (year - 2022)
                               + rng.uniform(-0.03, 0.03), 4)
                spot  = round(price * rng.uniform(0.85, 1.10), 4)
                fwd   = round(price * rng.uniform(0.95, 1.05), 4)
                rows.append((date, energy_type, region, price, spot, fwd))
    conn.executemany(
        "INSERT INTO price_index (date,energy_type,region,price_yuan_kwh,spot_price,forward_price)"
        " VALUES (?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Removed existing {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DDL)

    n1 = seed_company_finance(conn)
    n2 = seed_capacity_stats(conn)
    n3 = seed_price_index(conn)

    conn.commit()
    conn.close()

    print(f"Created {DB_PATH}")
    print(f"  company_finance : {n1} rows")
    print(f"  capacity_stats  : {n2} rows")
    print(f"  price_index     : {n3} rows")

    # Quick sanity check
    conn2 = sqlite3.connect(DB_PATH)
    for tbl in ("company_finance", "capacity_stats", "price_index"):
        cnt = conn2.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        assert cnt >= 100, f"{tbl} has only {cnt} rows (expected ≥100)"
        print(f"  [OK] {tbl}: {cnt} rows")
    conn2.close()
    print("All checks passed.")


if __name__ == "__main__":
    main()
