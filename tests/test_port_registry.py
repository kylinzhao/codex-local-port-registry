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
    def test_same_repo_worktree_conflict_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            main_repo = base / "repo-main"
            worktree_repo = base / "repo-worktree"
            main_repo.mkdir()
            worktree_repo.mkdir()

            (main_repo / ".git").mkdir()
            worktree_gitdir = main_repo / ".git" / "worktrees" / "feature-a"
            worktree_gitdir.mkdir(parents=True)
            (worktree_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
            (worktree_repo / ".git").write_text(f"gitdir: {worktree_gitdir}\n", encoding="utf-8")

            registry = {
                "version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "entries": [
                    {
                        "key": f"{main_repo}::web",
                        "project_root": str(main_repo),
                        "service_name": "web",
                        "framework": "next",
                        "current_port": 3000,
                        "assigned_port": 3000,
                    }
                ],
            }
            service = {
                "project_root": str(worktree_repo),
                "service_name": "web",
                "framework": "next",
                "current_port": 3000,
                "detected_ports": [3000],
                "detected_sources": [],
                "detected_env_var": "PORT",
                "preferred_env_var": "PORT",
                "suggested_patch": None,
            }

            enriched = PORT_REGISTRY.enrich_with_registry(service, registry)
            self.assertFalse(enriched["needs_repair"])
            self.assertEqual(enriched["assigned_port"], 3000)
            self.assertEqual(enriched["conflicts_with"], [])

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
