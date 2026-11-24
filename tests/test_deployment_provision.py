from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from scripts.provision_deployment import check_database, provision_database, REQUIRED_TABLES


class ProvisionTests(unittest.TestCase):
    def test_missing_database_does_not_create_empty_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1] / "test_output") as directory:
            missing = Path(directory) / "missing.db"
            with self.assertRaises(sqlite3.OperationalError):
                check_database(missing)
            self.assertFalse(missing.exists())

    def test_seed_backup_is_valid_and_idempotent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1] / "test_output") as directory:
            seed = Path(directory) / "seed.db"
            target = Path(directory) / "data" / "energy.db"
            with closing(sqlite3.connect(seed)) as connection:
                for table in REQUIRED_TABLES:
                    connection.execute(f'CREATE TABLE {table}(value INTEGER)')
                    connection.execute(f'INSERT INTO {table} VALUES (7)')
                connection.commit()
            self.assertEqual(provision_database(seed, target), dict.fromkeys(REQUIRED_TABLES, 1))
            before = target.read_bytes()
            provision_database(Path(directory) / "absent.db", target)
            self.assertEqual(before, target.read_bytes())

    def test_bad_seed_not_copied(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1] / "test_output") as directory:
            seed = Path(directory) / "seed.db"
            seed.touch()
            target = Path(directory) / "target.db"
            with self.assertRaises(sqlite3.OperationalError):
                provision_database(seed, target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
