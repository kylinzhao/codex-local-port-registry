import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "local-port-registry"
    / "scripts"
    / "port_registry.py"
)
SPEC = importlib.util.spec_from_file_location("port_registry", SCRIPT_PATH)
PORT_REGISTRY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PORT_REGISTRY)


class PortRegistryTests(unittest.TestCase):
    def test_detect_service_prefers_requested_script_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            package_path = project_root / "package.json"
            package_path.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "scripts": {
                            "audit:review": "PORT=12312 tsx audit.ts review",
                            "dev": "PORT=18082 next dev",
                        },
                    }
                ),
                encoding="utf-8",
            )

            service = PORT_REGISTRY.detect_service(project_root, command="npm run dev")

            self.assertEqual(service["current_port"], 18082)
            self.assertEqual(service["detected_sources"], ["package.json:scripts.dev"])
            self.assertEqual(
                service["suggested_patch"],
                {
                    "type": "package-script",
                    "file": str(package_path),
                    "script": "dev",
                },
            )

    def test_package_script_env_assignment_stays_package_script_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            package_path = project_root / "package.json"
            package_path.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "scripts": {
                            "audit:review": "PORT=12312 tsx audit.ts review",
                        },
                    }
                ),
                encoding="utf-8",
            )

            service = PORT_REGISTRY.detect_service(project_root)

            self.assertEqual(service["current_port"], 12312)
            self.assertEqual(service["preferred_env_var"], "PORT")
            self.assertEqual(
                service["suggested_patch"],
                {
                    "type": "package-script",
                    "file": str(package_path),
                    "script": "audit:review",
                },
            )

    def test_detect_service_uses_env_file_when_requested_script_has_no_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            package_path = project_root / "package.json"
            env_path = project_root / ".env"
            package_path.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "scripts": {
                            "audit:review": "PORT=12312 tsx audit.ts review",
                            "dev": "next dev",
                        },
                    }
                ),
                encoding="utf-8",
            )
            env_path.write_text("PORT=18083\n", encoding="utf-8")

            service = PORT_REGISTRY.detect_service(project_root, command="npm run dev")

            self.assertEqual(service["current_port"], 18083)
            self.assertEqual(service["detected_sources"], [".env:PORT"])
            self.assertEqual(
                service["suggested_patch"],
                {
                    "type": "env-file",
                    "file": str(env_path),
                    "env_var": "PORT",
                },
            )

    def test_apply_repair_rejects_package_json_as_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            package_path = project_root / "package.json"
            package_path.write_text('{"name":"demo"}\n', encoding="utf-8")

            service = {
                "project_root": str(project_root),
                "service_name": project_root.name,
                "assigned_port": 15878,
                "suggested_patch": {
                    "type": "env-file",
                    "file": str(package_path),
                    "env_var": "PORT",
                },
            }

            with self.assertRaisesRegex(RuntimeError, "package.json"):
                PORT_REGISTRY.apply_repair(service)

            with package_path.open("r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["name"], "demo")


if __name__ == "__main__":
    unittest.main()
