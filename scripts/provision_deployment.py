"""Provision persistent model cache and a validated SQLite seed without replacing data."""
from contextlib import closing
import argparse
import os
from pathlib import Path
import sqlite3
import tempfile

REQUIRED_TABLES = ("company_finance", "capacity_stats", "price_index")


def check_database(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        counts = {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                  for table in REQUIRED_TABLES}
        if not all(counts.values()):
            raise ValueError(f"Database has empty required tables: {counts}")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database quick_check failed")
        return counts


def provision_database(seed: Path, destination: Path) -> dict[str, int]:
    if destination.exists():
        return check_database(destination)
    check_database(seed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        staged = Path(directory) / "energy.db"
        with closing(sqlite3.connect(seed.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(staged)) as target:
                source.backup(target)
        counts = check_database(staged)
        # Both paths share a filesystem; the hard link publishes the complete validated backup atomically.
        try:
            os.link(staged, destination)
        except FileExistsError:
            return check_database(destination)
        return counts


def provision_model() -> None:
    os.environ.setdefault("HF_HOME", "/models/cache")
    from huggingface_hub import HfApi, snapshot_download
    revision = os.getenv("EMBEDDING_REVISION")
    if not revision or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Set EMBEDDING_REVISION to the immutable 40-character model commit SHA")
    model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    files = {entry.rfilename for entry in HfApi().model_info(model, revision=revision).siblings}
    weights = "model.safetensors" if "model.safetensors" in files else "pytorch_model.bin"
    if weights not in files:
        raise ValueError("Model has no supported root weight file")
    snapshot_download(repo_id=model, revision=revision, local_files_only=False,
                      allow_patterns=["*.json", "*.txt", weights, "1_Pooling/*"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="store_true")
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--seed", type=Path, default=Path("resources/data/energy.db"))
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("DEPLOY_DATA_DIR", "/data")))
    args = parser.parse_args()
    if not (args.model or args.database):
        parser.error("Choose --model and/or --database")
    if args.model:
        provision_model()
    if args.database:
        print(provision_database(args.seed, args.data_dir / "energy.db"))


if __name__ == "__main__":
    main()
