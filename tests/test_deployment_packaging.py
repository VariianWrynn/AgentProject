import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_deployment", ROOT / "scripts/verify_deployment.py")
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class DeploymentConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(subprocess.check_output(
            ["docker", "compose", "--env-file", ".env.deploy.example", "config", "--format", "json"],
            cwd=ROOT, text=True, encoding="utf-8"))

    def test_default_and_explicit_entry_resolve_same_production_services(self):
        alias = json.loads(subprocess.check_output(
            ["docker", "compose", "-f", "docker-compose.yml", "--env-file", ".env.deploy.example",
             "config", "--format", "json"], cwd=ROOT, text=True, encoding="utf-8"))
        self.assertEqual(VERIFY.validate_config(alias), VERIFY.validate_config(self.config))
        self.assertEqual(VERIFY.validate_config(self.config)["memory_limit_bytes"], 6_106_906_624)
        self.assertEqual(VERIFY.validate_config(self.config)["published_bind"], "127.0.0.1")

    def test_rejects_accidental_internal_port_publication(self):
        config = copy.deepcopy(self.config)
        config["services"]["milvus"]["ports"] = [{"target": 19530, "published": "19530"}]
        with self.assertRaisesRegex(ValueError, "Internal service"):
            VERIFY.validate_config(config)

    def test_rejects_budget_overallocation(self):
        config = copy.deepcopy(self.config)
        config["services"]["api-server"]["mem_limit"] = 8_000_000_000
        with self.assertRaisesRegex(ValueError, "Aggregate caps"):
            VERIFY.validate_config(config)

    def test_rejects_cpu_overallocation(self):
        config = copy.deepcopy(self.config)
        config["services"]["api-server"]["cpus"] = 4
        with self.assertRaisesRegex(ValueError, "Aggregate caps"):
            VERIFY.validate_config(config)

    def test_rejects_missing_limits_and_container_swap(self):
        config = copy.deepcopy(self.config)
        del config["services"]["api-server"]["cpus"]
        with self.assertRaisesRegex(ValueError, "explicit CPU"):
            VERIFY.validate_config(config)
        config = copy.deepcopy(self.config)
        config["services"]["api-server"]["memswap_limit"] *= 2
        with self.assertRaisesRegex(ValueError, "disable container swap"):
            VERIFY.validate_config(config)


if __name__ == "__main__":
    unittest.main()
