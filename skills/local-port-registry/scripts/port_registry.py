#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

MIN_PORT = 12000
MAX_PORT = 19999
ROOT_SCAN_DEPTH = 4
IGNORE_DIRS = {
    ".git",
    ".next",
    ".turbo",
    ".worktrees",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "tmp",
    ".tmp",
}
ENV_FILE_CANDIDATES = [
    ".env.local",
    ".env.development.local",
    ".env.development",
    ".env",
]
FRAMEWORK_DEFAULT_PORTS = {
    "next": 3000,
    "vite": 5173,
    "taro-h5": 10086,
    "node-backend": 3001,
    "docker-compose": 3000,
    "go": 8080,
    "rust": 3000,
    "unknown": 3000,
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def registry_path() -> Path:
    return codex_home() / "memories" / "local-port-registry.json"


def registry_lock_path() -> Path:
    return codex_home() / "memories" / "local-port-registry.lock"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def configured_workspace_roots() -> list[Path]:
    raw = os.environ.get("LOCAL_PORT_REGISTRY_WORKSPACE_ROOTS", "").strip()
    if not raw:
        return []
    roots = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if item:
            roots.append(Path(item).expanduser().resolve())
    return roots


def workspace_root_for(path: Path) -> Path | None:
    resolved = path.resolve()
    candidates = configured_workspace_roots()
    for parent in resolved.parents:
        if parent.name.lower() in {"work", "workspace", "workspaces", "projects", "repos", "repo"}:
            candidates.append(parent)
    best = None
    for candidate in candidates:
        if str(resolved).startswith(str(candidate)):
            if best is None or len(str(candidate)) > len(str(best)):
                best = candidate
    if best is not None:
        return best
    for parent in resolved.parents:
        if parent == Path.home():
            return parent
    return None


class RegistryLock:
    def __init__(self) -> None:
        self.path = registry_lock_path()
        self.handle = None

    def __enter__(self) -> "RegistryLock":
        ensure_parent(self.path)
        self.handle = self.path.open("a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def default_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": utc_now(),
        "entries": [],
    }


def load_registry_unlocked() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return default_registry()
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_registry_unlocked(data: dict[str, Any]) -> None:
    path = registry_path()
    ensure_parent(path)
    data["updated_at"] = utc_now()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def entry_key(project_root: str, service_name: str) -> str:
    return f"{project_root}::{service_name}"


def hash_base(project_root: str, service_name: str) -> int:
    digest = hashlib.sha1(entry_key(project_root, service_name).encode("utf-8")).hexdigest()
    span = MAX_PORT - MIN_PORT + 1
    return MIN_PORT + (int(digest[:8], 16) % span)


def is_port_bindable(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).lower() in {"1", "true", "yes", "y", "on"}


def package_json_path(project_root: Path) -> Path | None:
    path = project_root / "package.json"
    return path if path.exists() else None


def find_project_root(path: Path) -> Path:
    current = path.resolve()
    if current.is_file():
        current = current.parent
    while True:
        if any(
            (current / marker).exists()
            for marker in (
                "package.json",
                "docker-compose.yml",
                "docker-compose.yaml",
                "go.mod",
                "Cargo.toml",
            )
        ):
            return current
        if current.parent == current:
            return path.resolve() if path.exists() else current
        current = current.parent


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_package_json(project_root: Path) -> dict[str, Any] | None:
    pkg = package_json_path(project_root)
    if not pkg:
        return None
    with pkg.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def infer_framework(project_root: Path, package: dict[str, Any] | None) -> str:
    if (project_root / "docker-compose.yml").exists() or (project_root / "docker-compose.yaml").exists():
        if package is None:
            return "docker-compose"
    if (project_root / "go.mod").exists():
        return "go"
    if (project_root / "Cargo.toml").exists():
        return "rust"
    if not package:
        return "unknown"
    deps: dict[str, Any] = {}
    deps.update(package.get("dependencies", {}))
    deps.update(package.get("devDependencies", {}))
    scripts = package.get("scripts", {})
    if "@tarojs/cli" in deps or any("taro build --type h5" in str(value) for value in scripts.values()):
        return "taro-h5"
    if "next" in deps or any("next dev" in str(value) for value in scripts.values()):
        return "next"
    if "vite" in deps or any(re.search(r"\bvite\b", str(value)) for value in scripts.values()):
        return "vite"
    if "fastify" in deps:
        return "node-backend"
    if scripts:
        return "node-backend"
    return "unknown"


def infer_service_name(project_root: Path) -> str:
    return project_root.name


def env_files(project_root: Path) -> list[Path]:
    return [project_root / name for name in ENV_FILE_CANDIDATES if (project_root / name).exists()]


def parse_env_file_ports(path: Path) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key.endswith("PORT"):
            continue
        if value.isdigit():
            matches.append(
                {
                    "port": int(value),
                    "env_var": key,
                    "source": f"{path.name}:{key}",
                    "file": str(path),
                    "kind": "env-file",
                }
            )
    return matches


def parse_package_script_ports(package: dict[str, Any], package_path: Path) -> list[dict[str, Any]]:
    scripts = package.get("scripts", {})
    matches: list[dict[str, Any]] = []
    for name, raw_command in scripts.items():
        command = str(raw_command)
        for regex in (
            re.compile(r"(?:^|\s)--port[=\s]+(\d{2,5})(?:\s|$)"),
            re.compile(r"(?:^|\s)-p\s+(\d{2,5})(?:\s|$)"),
            re.compile(r"(?:^|\s)([A-Z_]*PORT)=(\d{2,5})(?:\s|$)"),
        ):
            match = regex.search(command)
            if not match:
                continue
            if len(match.groups()) == 1:
                port = int(match.group(1))
                env_var = None
            else:
                env_var = match.group(1)
                port = int(match.group(2))
            matches.append(
                {
                    "port": port,
                    "env_var": env_var,
                    "source": f"package.json:scripts.{name}",
                    "file": str(package_path),
                    "script": name,
                    "kind": "package-script",
                }
            )
            break
    return matches


def detect_compose_services(project_root: Path) -> list[dict[str, Any]]:
    compose_path = None
    for name in ("docker-compose.yml", "docker-compose.yaml"):
        candidate = project_root / name
        if candidate.exists():
            compose_path = candidate
            break
    if compose_path is None:
        return []

    lines = read_text(compose_path).splitlines()
    services: list[dict[str, Any]] = []
    in_services = False
    current: dict[str, Any] | None = None
    section = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            services.append(current)
            current = None

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "services:" and indent == 0:
            in_services = True
            continue
        if not in_services:
            continue
        if indent == 0:
            flush()
            break
        service_match = re.match(r"^\s{2}([A-Za-z0-9_.-]+):\s*$", line)
        if service_match:
            flush()
            current = {
                "service_name": f"compose:{service_match.group(1)}",
                "compose_service_name": service_match.group(1),
                "framework": "docker-compose",
                "project_root": str(project_root),
                "detected_ports": [],
                "detected_sources": [],
                "current_port": None,
                "detected_env_var": None,
                "preferred_env_var": None,
                "suggested_patch": {
                    "type": "compose-port",
                    "file": str(compose_path),
                    "service_name": service_match.group(1),
                },
            }
            section = None
            continue
        if current is None:
            continue
        section_match = re.match(r"^\s{4}([A-Za-z0-9_.-]+):\s*$", line)
        if section_match:
            section = section_match.group(1)
            continue
        if section == "ports":
            port_match = re.search(r"['\"]?(\d{2,5}):(\d{2,5})['\"]?", stripped)
            if port_match:
                host_port = int(port_match.group(1))
                current["detected_ports"].append(host_port)
                current["detected_sources"].append(f"{compose_path.name}:{current['compose_service_name']}:ports")
                current["current_port"] = current["current_port"] or host_port
            var_match = re.search(r"\$\{([A-Z0-9_]+):-?(\d{2,5})\}:(\d{2,5})", stripped)
            if var_match:
                env_var = var_match.group(1)
                host_port = int(var_match.group(2))
                current["detected_ports"].append(host_port)
                current["detected_env_var"] = env_var
                current["preferred_env_var"] = env_var
                current["detected_sources"].append(f"{compose_path.name}:{current['compose_service_name']}:ports:{env_var}")
                current["current_port"] = current["current_port"] or host_port
                current["suggested_patch"] = {
                    "type": "env-file",
                    "file": str(project_root / ".env"),
                    "env_var": env_var,
                }
            continue
        if section == "environment":
            env_match = re.search(r"([A-Z0-9_]*PORT)=(\d{2,5})", stripped)
            if env_match:
                env_var = env_match.group(1)
                env_port = int(env_match.group(2))
                current["detected_ports"].append(env_port)
                current["detected_env_var"] = current["detected_env_var"] or env_var
                current["preferred_env_var"] = current["preferred_env_var"] or env_var
                current["detected_sources"].append(f"{compose_path.name}:{current['compose_service_name']}:environment:{env_var}")
    flush()

    for service in services:
        if not service["detected_ports"]:
            service["current_port"] = FRAMEWORK_DEFAULT_PORTS["docker-compose"]
        if service["preferred_env_var"] is None:
            service["preferred_env_var"] = f"{service['compose_service_name'].upper()}_PORT"
    return services


def detect_service(project_root: Path, service_name: str | None = None) -> dict[str, Any]:
    package = load_package_json(project_root)
    framework = infer_framework(project_root, package)
    service_name = service_name or infer_service_name(project_root)

    detections: list[dict[str, Any]] = []
    if package is not None:
        detections.extend(parse_package_script_ports(package, package_json_path(project_root)))
    for env_path in env_files(project_root):
        detections.extend(parse_env_file_ports(env_path))

    current_port = None
    preferred_env_var = None
    patch_target: dict[str, Any] | None = None
    detected_sources: list[str] = []

    if detections:
        current_port = detections[0]["port"]
        detected_sources = [item["source"] for item in detections]
        for item in detections:
            if item.get("env_var"):
                preferred_env_var = item["env_var"]
                patch_target = {
                    "type": "env-file",
                    "file": item["file"],
                    "env_var": item["env_var"],
                }
                break
        if patch_target is None:
            for item in detections:
                if item["kind"] == "package-script":
                    patch_target = {
                        "type": "package-script",
                        "file": item["file"],
                        "script": item["script"],
                    }
                    break
    if current_port is None:
        current_port = FRAMEWORK_DEFAULT_PORTS.get(framework, 3000)
    if preferred_env_var is None:
        if framework == "docker-compose":
            preferred_env_var = f"{service_name.replace(':', '_').upper()}_PORT"
        elif framework == "vite" or framework == "taro-h5":
            preferred_env_var = None
        else:
            preferred_env_var = "PORT"
    if patch_target is None:
        if framework in {"next", "vite", "taro-h5", "node-backend"} and package_json_path(project_root):
            patch_target = {
                "type": "package-json-auto",
                "file": str(package_json_path(project_root)),
            }
        elif preferred_env_var:
            target_file = str((project_root / ".env").resolve())
            patch_target = {
                "type": "env-file",
                "file": target_file,
                "env_var": preferred_env_var,
            }

    return {
        "project_root": str(project_root),
        "service_name": service_name,
        "framework": framework,
        "current_port": current_port,
        "detected_ports": sorted({item["port"] for item in detections}) if detections else [current_port],
        "detected_sources": detected_sources,
        "detected_env_var": preferred_env_var,
        "preferred_env_var": preferred_env_var,
        "suggested_patch": patch_target,
    }


def detect_services_for_root(project_root: Path) -> list[dict[str, Any]]:
    services = detect_compose_services(project_root)
    package = load_package_json(project_root)
    if package is not None or (project_root / "go.mod").exists() or (project_root / "Cargo.toml").exists():
        services.append(detect_service(project_root))
    return services


def candidate_project_roots(root: Path) -> list[Path]:
    roots: set[Path] = set()
    for current_root, dirs, files in os.walk(root):
        current = Path(current_root)
        depth = len(current.relative_to(root).parts)
        dirs[:] = [name for name in dirs if name not in IGNORE_DIRS]
        if depth > ROOT_SCAN_DEPTH:
            dirs[:] = []
            continue
        file_set = set(files)
        if file_set & {"package.json", "docker-compose.yml", "docker-compose.yaml", "go.mod", "Cargo.toml"}:
            roots.add(current)
    return sorted(roots)


def allocate_port(project_root: str, service_name: str, used_ports: set[int], existing_port: int | None = None) -> int:
    if existing_port is not None:
        return existing_port
    span = MAX_PORT - MIN_PORT + 1
    start = hash_base(project_root, service_name)
    for offset in range(span):
        candidate = MIN_PORT + ((start - MIN_PORT + offset) % span)
        if candidate in used_ports:
            continue
        if not is_port_bindable(candidate):
            continue
        return candidate
    raise RuntimeError("No free port available in the registry range")


def claimed_ports(registry: dict[str, Any], exclude_key: str | None = None) -> set[int]:
    ports: set[int] = set()
    for entry in registry.get("entries", []):
        if exclude_key and entry["key"] == exclude_key:
            continue
        for field in ("current_port", "assigned_port"):
            value = entry.get(field)
            if isinstance(value, int):
                ports.add(value)
    return ports


def conflict_keys_from_registry(service: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    key = entry_key(service["project_root"], service["service_name"])
    current_port = service["current_port"]
    keys = []
    for entry in registry.get("entries", []):
        if entry["key"] == key:
            continue
        if entry.get("current_port") == current_port:
            keys.append(entry["key"])
    return sorted(keys)


def launch_strategy(service: dict[str, Any]) -> str:
    framework = service.get("framework")
    if framework in {"vite", "taro-h5"}:
        return "cli-arg"
    env_var = service.get("preferred_env_var")
    if env_var and env_var.endswith("PORT"):
        return "inline-env"
    return "inline-env"


def decorate_command(command: str, service: dict[str, Any]) -> str:
    port = service["assigned_port"]
    strategy = launch_strategy(service)
    env_var = service.get("preferred_env_var") or "PORT"
    if re.search(r"(?:^|\s)(?:docker compose|docker-compose)\b", command):
        return f"{env_var}={port} {command}"
    if strategy == "cli-arg":
        if re.search(r"(?:^|\s)--port(?:=|\s)", command) or re.search(r"(?:^|\s)-p\s+\d{2,5}", command):
            command = re.sub(r"(--port(?:=|\s+))\d{2,5}", rf"\g<1>{port}", command)
            command = re.sub(r"(-p\s+)\d{2,5}", rf"\g<1>{port}", command)
            return command
        return f"{command} -- --port {port}"
    if re.search(rf"(?:^|\s){re.escape(env_var)}=\d{{2,5}}(?:\s|$)", command):
        return re.sub(rf"({re.escape(env_var)}=)\d{{2,5}}", rf"\g<1>{port}", command)
    return f"{env_var}={port} {command}"


def registry_entry_from_service(service: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    entry = copy.deepcopy(existing) if existing else {}
    entry.update(
        {
            "key": entry_key(service["project_root"], service["service_name"]),
            "project_root": service["project_root"],
            "service_name": service["service_name"],
            "framework": service["framework"],
            "current_port": service["current_port"],
            "assigned_port": service["assigned_port"],
            "detected_ports": service.get("detected_ports", []),
            "detected_sources": service.get("detected_sources", []),
            "detected_env_var": service.get("detected_env_var"),
            "preferred_env_var": service.get("preferred_env_var"),
            "suggested_patch": service.get("suggested_patch"),
            "needs_repair": service.get("needs_repair", False),
            "conflicts_with": service.get("conflicts_with", []),
            "reasons": service.get("reasons", []),
            "last_seen_at": utc_now(),
        }
    )
    return entry


def index_entries(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["key"]: entry for entry in registry.get("entries", [])}


def enrich_with_registry(service: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    enriched = copy.deepcopy(service)
    existing = index_entries(registry).get(entry_key(service["project_root"], service["service_name"]))
    key = entry_key(service["project_root"], service["service_name"])
    conflicts = conflict_keys_from_registry(service, registry)
    enriched["conflicts_with"] = conflicts
    if conflicts:
        existing_alt = None
        if existing and isinstance(existing.get("assigned_port"), int) and existing["assigned_port"] != service["current_port"]:
            existing_alt = existing["assigned_port"]
        used_ports = claimed_ports(registry, exclude_key=key)
        enriched["assigned_port"] = allocate_port(service["project_root"], service["service_name"], used_ports, existing_alt)
        enriched["needs_repair"] = True
    else:
        enriched["assigned_port"] = service["current_port"]
        enriched["needs_repair"] = False
    reasons: list[str] = []
    if existing and existing.get("current_port") != enriched["current_port"]:
        reasons.append("project-port-changed")
    if conflicts:
        reasons.append("conflicts-with-registered-projects")
    enriched["reasons"] = reasons
    return enriched


def annotate_services_for_scan(services: list[dict[str, Any]], registry: dict[str, Any]) -> list[dict[str, Any]]:
    indexed = index_entries(registry)
    groups: dict[int, list[dict[str, Any]]] = {}
    for service in services:
        groups.setdefault(service["current_port"], []).append(service)

    result: list[dict[str, Any]] = []
    reserved_ports = claimed_ports(registry)

    for current_port, group in sorted(groups.items(), key=lambda item: item[0]):
        sorted_group = sorted(group, key=lambda item: entry_key(item["project_root"], item["service_name"]))
        registry_keeper = None
        for service in sorted_group:
            key = entry_key(service["project_root"], service["service_name"])
            existing = indexed.get(key)
            if existing and existing.get("assigned_port") == current_port:
                registry_keeper = key
                break
        keeper_key = registry_keeper or entry_key(sorted_group[0]["project_root"], sorted_group[0]["service_name"])

        for service in sorted_group:
            enriched = copy.deepcopy(service)
            key = entry_key(service["project_root"], service["service_name"])
            peers = [entry_key(item["project_root"], item["service_name"]) for item in sorted_group if item is not service]
            registry_peers = [item for item in conflict_keys_from_registry(service, registry) if item not in peers]
            conflicts = sorted(peers + registry_peers)
            enriched["conflicts_with"] = conflicts
            existing = indexed.get(key)

            if not conflicts or key == keeper_key:
                enriched["assigned_port"] = current_port
                enriched["needs_repair"] = False
                enriched["reasons"] = []
                reserved_ports.add(current_port)
            else:
                existing_alt = None
                if existing and isinstance(existing.get("assigned_port"), int) and existing["assigned_port"] != current_port:
                    existing_alt = existing["assigned_port"]
                enriched["assigned_port"] = allocate_port(service["project_root"], service["service_name"], reserved_ports, existing_alt)
                enriched["needs_repair"] = True
                enriched["reasons"] = ["conflicts-with-projects"]
                reserved_ports.add(enriched["assigned_port"])
            result.append(enriched)
    return sorted(result, key=lambda item: entry_key(item["project_root"], item["service_name"]))


def detect_conflicts(services: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for service in services:
        grouped.setdefault(service["current_port"], []).append(service)
    return {port: items for port, items in grouped.items() if len(items) > 1}


def update_env_file(path: Path, env_var: str, port: int) -> None:
    ensure_parent(path)
    if path.exists():
        lines = read_text(path).splitlines()
    else:
        lines = []
    updated = False
    pattern = re.compile(rf"^\s*{re.escape(env_var)}\s*=")
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line) and not updated:
            new_lines.append(f"{env_var}={port}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{env_var}={port}")
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def update_package_json_scripts(path: Path, framework: str, port: int, preferred_script: str | None = None) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        package = json.load(handle)
    scripts = package.setdefault("scripts", {})
    changed: list[str] = []
    keys = list(scripts.keys())
    if preferred_script and preferred_script in scripts:
        keys = [preferred_script] + [key for key in keys if key != preferred_script]

    def patch_command(command: str, key: str) -> str:
        if re.search(r"(?:^|\s)--port(?:=|\s+)\d{2,5}", command):
            return re.sub(r"(--port(?:=|\s+))\d{2,5}", rf"\g<1>{port}", command, count=1)
        if re.search(r"(?:^|\s)-p\s+\d{2,5}", command):
            return re.sub(r"(-p\s+)\d{2,5}", rf"\g<1>{port}", command, count=1)
        if re.search(r"(?:^|\s)([A-Z_]*PORT)=\d{2,5}", command):
            return re.sub(r"((?:^|\s)[A-Z_]*PORT=)\d{2,5}", rf"\g<1>{port}", command, count=1)
        if framework == "next" and "next dev" in command:
            return command.replace("next dev", f"next dev -p {port}", 1)
        if framework == "next" and "next start" in command:
            return command.replace("next start", f"next start -p {port}", 1)
        if framework == "vite" and re.search(r"\bvite\b", command):
            return f"{command} --port {port}"
        if framework == "taro-h5" and "--watch" in command:
            return f"{command} --port {port}"
        if framework == "node-backend":
            return f"PORT={port} {command}"
        return command

    for key in keys:
        if not (key == "dev" or key.startswith("dev:") or key == "preview"):
            continue
        original = str(scripts[key])
        patched = patch_command(original, key)
        if patched != original:
            scripts[key] = patched
            changed.append(key)
    if changed:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(package, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return changed


def update_compose_literal_port(path: Path, service_name: str, port: int) -> bool:
    lines = read_text(path).splitlines()
    changed = False
    in_service = False
    section = None
    for index, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if re.match(rf"^\s{{2}}{re.escape(service_name)}:\s*$", line):
            in_service = True
            section = None
            continue
        if in_service and len(line) - len(line.lstrip(" ")) == 2 and re.match(r"^\s{2}[A-Za-z0-9_.-]+:\s*$", line):
            break
        if not in_service:
            continue
        section_match = re.match(r"^\s{4}([A-Za-z0-9_.-]+):\s*$", line)
        if section_match:
            section = section_match.group(1)
            continue
        if section != "ports":
            continue
        updated = re.sub(r"(['\"]?)(\d{2,5})(:(\d{2,5})['\"]?)", rf"\g<1>{port}\3", line, count=1)
        if updated != line:
            lines[index] = updated
            changed = True
            break
    if changed:
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed


def apply_repair(service: dict[str, Any]) -> dict[str, Any]:
    patch = service.get("suggested_patch")
    result = {
        "project_root": service["project_root"],
        "service_name": service["service_name"],
        "assigned_port": service["assigned_port"],
        "patched_files": [],
        "strategy": None,
    }
    if not patch:
        raise RuntimeError("No patch strategy available for this service")

    patch_type = patch["type"]
    if patch_type == "env-file":
        path = Path(patch["file"]).expanduser()
        env_var = patch["env_var"]
        update_env_file(path, env_var, service["assigned_port"])
        result["patched_files"].append(str(path))
        result["strategy"] = f"env-file:{env_var}"
        return result
    if patch_type == "package-script":
        path = Path(patch["file"]).expanduser()
        changed = update_package_json_scripts(path, service["framework"], service["assigned_port"], patch.get("script"))
        if not changed:
            raise RuntimeError(f"No script was updated in {path}")
        result["patched_files"].append(str(path))
        result["strategy"] = f"package-script:{','.join(changed)}"
        return result
    if patch_type == "package-json-auto":
        path = Path(patch["file"]).expanduser()
        changed = update_package_json_scripts(path, service["framework"], service["assigned_port"])
        if not changed:
            raise RuntimeError(f"No dev-like script could be patched in {path}")
        result["patched_files"].append(str(path))
        result["strategy"] = f"package-json-auto:{','.join(changed)}"
        return result
    if patch_type == "compose-port":
        path = Path(patch["file"]).expanduser()
        changed = update_compose_literal_port(path, patch["service_name"], service["assigned_port"])
        if not changed:
            raise RuntimeError(f"No compose port mapping was updated in {path}")
        result["patched_files"].append(str(path))
        result["strategy"] = f"compose-port:{patch['service_name']}"
        return result
    raise RuntimeError(f"Unsupported patch type: {patch_type}")


def upsert_registry_entries(registry: dict[str, Any], services: list[dict[str, Any]]) -> dict[str, Any]:
    indexed = index_entries(registry)
    for service in services:
        key = entry_key(service["project_root"], service["service_name"])
        indexed[key] = registry_entry_from_service(service, indexed.get(key))
    registry["entries"] = sorted(indexed.values(), key=lambda item: item["key"])
    return registry


def refresh_entries_under_root(registry: dict[str, Any], root: Path, services: list[dict[str, Any]]) -> dict[str, Any]:
    root_str = str(root)
    keep_keys = {entry_key(service["project_root"], service["service_name"]) for service in services}
    kept_entries = []
    for entry in registry.get("entries", []):
        if str(entry["project_root"]).startswith(root_str) and entry["key"] not in keep_keys:
            continue
        kept_entries.append(entry)
    registry["entries"] = kept_entries
    return registry


def serializable_service(service: dict[str, Any], command: str | None = None) -> dict[str, Any]:
    data = copy.deepcopy(service)
    if command:
        data["recommended_command"] = decorate_command(command, service)
    return data


def parse_entry_key(key: str) -> tuple[str, str]:
    project_root, service_name = key.rsplit("::", 1)
    return project_root, service_name


def normalize_service_name(service_name: str) -> str:
    return service_name.split(":", 1)[-1]


def compact_project_label(project_root: str, service_name: str) -> str:
    path = Path(project_root)
    workspace_root = workspace_root_for(path)
    if workspace_root is not None:
        try:
            label = str(path.resolve().relative_to(workspace_root))
        except ValueError:
            label = path.name
    else:
        label = path.name
    if service_name.startswith("compose:"):
        return f"{label}#{normalize_service_name(service_name)}"
    if normalize_service_name(service_name) != path.name:
        return f"{label}#{normalize_service_name(service_name)}"
    return label


def is_self_reference_conflict(service: dict[str, Any], conflict_project_root: str, conflict_service_name: str) -> bool:
    current_root = Path(service["project_root"]).resolve()
    other_root = Path(conflict_project_root).resolve()
    current_service = normalize_service_name(service["service_name"])
    other_service = normalize_service_name(conflict_service_name)
    if current_service != other_service:
        return False
    return (
        current_root == other_root
        or str(current_root).startswith(str(other_root) + os.sep)
        or str(other_root).startswith(str(current_root) + os.sep)
    )


def conflict_details(conflict_keys: list[str], registry: dict[str, Any]) -> list[dict[str, Any]]:
    indexed = index_entries(registry)
    details: list[dict[str, Any]] = []
    for key in conflict_keys:
        entry = indexed.get(key)
        if entry:
            details.append(
                {
                    "key": key,
                    "project_root": entry["project_root"],
                    "service_name": entry["service_name"],
                    "current_port": entry.get("current_port"),
                    "assigned_port": entry.get("assigned_port"),
                }
            )
            continue
        project_root, service_name = parse_entry_key(key)
        details.append(
            {
                "key": key,
                "project_root": project_root,
                "service_name": service_name,
                "current_port": None,
                "assigned_port": None,
            }
        )
    return details


def prompt_payload(service: dict[str, Any], registry: dict[str, Any], command: str | None = None) -> dict[str, Any]:
    payload = serializable_service(service, command)
    raw_conflict_items = conflict_details(service.get("conflicts_with", []), registry)
    conflict_items = [
        item
        for item in raw_conflict_items
        if not is_self_reference_conflict(service, item["project_root"], item["service_name"])
    ]
    for item in conflict_items:
        item["display_label"] = compact_project_label(item["project_root"], item["service_name"])
    payload["conflict_projects"] = conflict_items
    service_label = f"{service['project_root']}::{service['service_name']}"
    apply_command = [
        "python3",
        str(Path(__file__).resolve()),
        "repair-project",
        "--project",
        service["project_root"],
    ]
    if service["service_name"] != Path(service["project_root"]).name:
        apply_command.extend(["--service", service["service_name"]])
    apply_command.append("--apply")
    payload["apply_command"] = " ".join(apply_command)

    if service["needs_repair"]:
        labels = [item["display_label"] for item in conflict_items]
        preview = labels[:4]
        if len(labels) > 4:
            preview.append(f"另外 {len(labels) - 4} 个项目")
        conflict_paths = "、".join(preview)
        payload["user_prompt"] = (
            f"项目 {compact_project_label(service['project_root'], service['service_name'])} 当前端口 {service['current_port']} 与 {conflict_paths} 冲突。"
            f"建议改用新端口 {service['assigned_port']}。是否应用这个新端口？"
        )
    else:
        command_text = payload.get("recommended_command")
        if command_text:
            payload["user_prompt"] = (
                f"项目 {compact_project_label(service['project_root'], service['service_name'])} 当前端口 {service['current_port']} 没有登记冲突。"
                f"可直接使用 {command_text} 启动。"
            )
        else:
            payload["user_prompt"] = (
                f"项目 {compact_project_label(service['project_root'], service['service_name'])} 当前端口 {service['current_port']} 没有登记冲突，可以直接启动。"
            )
    return payload


def print_output(payload: Any, json_output: bool) -> None:
    if json_output:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return
    if isinstance(payload, list):
        for item in payload:
            print(f"{item['service_name']}: {item['assigned_port']} ({item['project_root']})")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def handle_scan_root(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    raw_services: list[dict[str, Any]] = []
    with RegistryLock():
        registry = load_registry_unlocked()
        for project_root in candidate_project_roots(root):
            for service in detect_services_for_root(project_root):
                raw_services.append(service)
        services = annotate_services_for_scan(raw_services, registry)
        registry = refresh_entries_under_root(registry, root, services)
        registry = upsert_registry_entries(registry, services)
        save_registry_unlocked(registry)
    conflicts = detect_conflicts(services)
    payload = {
        "root": str(root),
        "registry": str(registry_path()),
        "service_count": len(services),
        "conflicts": {
            str(port): [entry_key(item["project_root"], item["service_name"]) for item in items]
            for port, items in conflicts.items()
        },
        "services": [serializable_service(service) for service in services],
    }
    print_output(payload, args.json)
    return 0


def resolve_service_for_project(project: Path, service_name: str | None) -> dict[str, Any]:
    project_root = find_project_root(project)
    if service_name and service_name.startswith("compose:"):
        for service in detect_compose_services(project_root):
            if service["service_name"] == service_name:
                return service
        raise RuntimeError(f"Compose service not found: {service_name}")
    return detect_service(project_root, service_name)


def handle_reserve(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser()
    with RegistryLock():
        registry = load_registry_unlocked()
        service = resolve_service_for_project(project, args.service)
        service = enrich_with_registry(service, registry)
        registry = upsert_registry_entries(registry, [service])
        save_registry_unlocked(registry)
    print_output(serializable_service(service, args.command), args.json)
    return 0


def repair_services(services: list[dict[str, Any]], apply_changes: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for service in services:
        item = serializable_service(service)
        if apply_changes:
            item["repair"] = apply_repair(service)
        results.append(item)
    return results


def handle_repair_project(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser()
    with RegistryLock():
        registry = load_registry_unlocked()
        service = resolve_service_for_project(project, args.service)
        service = enrich_with_registry(service, registry)
        registry = upsert_registry_entries(registry, [service])
        if args.apply and service["needs_repair"]:
            repair = apply_repair(service)
            service["repair"] = repair
            service["current_port"] = service["assigned_port"]
            service["detected_ports"] = [service["assigned_port"]]
            service["needs_repair"] = False
            service["reasons"] = []
            registry = upsert_registry_entries(registry, [service])
        save_registry_unlocked(registry)
    print_output(serializable_service(service, args.command), args.json)
    return 0


def handle_repair_root(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    with RegistryLock():
        registry = load_registry_unlocked()
        raw_services: list[dict[str, Any]] = []
        for project_root in candidate_project_roots(root):
            raw_services.extend(detect_services_for_root(project_root))
        conflicts = detect_conflicts(raw_services)
        conflict_keys = {
            entry_key(item["project_root"], item["service_name"])
            for items in conflicts.values()
            for item in items
        }
        enriched = [
            service
            for service in annotate_services_for_scan(raw_services, registry)
            if entry_key(service["project_root"], service["service_name"]) in conflict_keys
        ]
        registry = upsert_registry_entries(registry, enriched)
        if args.apply:
            repaired: list[dict[str, Any]] = []
            for service in enriched:
                if service["needs_repair"]:
                    apply_repair(service)
                    service["current_port"] = service["assigned_port"]
                    service["detected_ports"] = [service["assigned_port"]]
                    service["needs_repair"] = False
                    service["reasons"] = []
                repaired.append(service)
            registry = upsert_registry_entries(registry, repaired)
        save_registry_unlocked(registry)
    payload = {
        "root": str(root),
        "registry": str(registry_path()),
        "conflict_ports": sorted(conflicts.keys()),
        "services": [serializable_service(service) for service in enriched],
    }
    print_output(payload, args.json)
    return 0


def handle_lookup(args: argparse.Namespace) -> int:
    with RegistryLock():
        registry = load_registry_unlocked()
    entries = registry.get("entries", [])
    if args.project:
        project_root = str(find_project_root(Path(args.project).expanduser()))
        entries = [entry for entry in entries if entry["project_root"] == project_root]
    if args.service:
        entries = [entry for entry in entries if entry["service_name"] == args.service]
    payload = {
        "registry": str(registry_path()),
        "count": len(entries),
        "entries": entries,
    }
    print_output(payload, args.json)
    return 0


def handle_prompt(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser()
    with RegistryLock():
        registry = load_registry_unlocked()
        service = resolve_service_for_project(project, args.service)
        service = enrich_with_registry(service, registry)
        registry = upsert_registry_entries(registry, [service])
        save_registry_unlocked(registry)
    payload = prompt_payload(service, registry, args.command)
    print_output(payload, args.json)
    return 0


def handle_gc(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve() if args.root else None
    with RegistryLock():
        registry = load_registry_unlocked()
        before = len(registry.get("entries", []))
        kept = []
        removed = []
        for entry in registry.get("entries", []):
            project_root = Path(entry["project_root"])
            exists = project_root.exists()
            if root is not None and not str(project_root).startswith(str(root)):
                kept.append(entry)
                continue
            if exists:
                kept.append(entry)
            else:
                removed.append(entry["key"])
        registry["entries"] = kept
        save_registry_unlocked(registry)
    payload = {
        "registry": str(registry_path()),
        "before": before,
        "after": len(kept),
        "removed": removed,
    }
    print_output(payload, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reserve and repair local project ports.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    scan_root = subparsers.add_parser("scan-root", help="Scan a workspace root and register detected services.")
    scan_root.add_argument("root")
    scan_root.add_argument("--json", action="store_true", default=True)
    scan_root.set_defaults(func=handle_scan_root)

    reserve = subparsers.add_parser("reserve", help="Reserve a stable port for a project or service.")
    reserve.add_argument("--project", required=True)
    reserve.add_argument("--service")
    reserve.add_argument("--command")
    reserve.add_argument("--json", action="store_true", default=True)
    reserve.set_defaults(func=handle_reserve)

    repair_project = subparsers.add_parser("repair-project", help="Repair one project if its current port conflicts.")
    repair_project.add_argument("--project", required=True)
    repair_project.add_argument("--service")
    repair_project.add_argument("--command")
    repair_project.add_argument("--apply", action="store_true")
    repair_project.add_argument("--json", action="store_true", default=True)
    repair_project.set_defaults(func=handle_repair_project)

    repair_root = subparsers.add_parser("repair-root", help="Repair all conflicting projects under a root.")
    repair_root.add_argument("root")
    repair_root.add_argument("--apply", action="store_true")
    repair_root.add_argument("--json", action="store_true", default=True)
    repair_root.set_defaults(func=handle_repair_root)

    prompt = subparsers.add_parser("prompt", help="Generate a user-facing prompt for port conflict confirmation.")
    prompt.add_argument("--project", required=True)
    prompt.add_argument("--service")
    prompt.add_argument("--command")
    prompt.add_argument("--json", action="store_true", default=True)
    prompt.set_defaults(func=handle_prompt)

    lookup = subparsers.add_parser("lookup", help="Show registry entries.")
    lookup.add_argument("--project")
    lookup.add_argument("--service")
    lookup.add_argument("--json", action="store_true", default=True)
    lookup.set_defaults(func=handle_lookup)

    gc = subparsers.add_parser("gc", help="Remove registry entries for missing projects.")
    gc.add_argument("--root")
    gc.add_argument("--json", action="store_true", default=True)
    gc.set_defaults(func=handle_gc)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
