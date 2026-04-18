---
name: local-port-registry
description: Use when working in local projects that may start dev servers, preview servers, backend APIs, or docker-compose services, especially when shared workspaces have recurring localhost port collisions and the project may need its local port config repaired before launch
---

# Local Port Registry

Reserve and repair local service ports before starting anything that binds `localhost`.

## When To Use

- Any task that may run `npm run dev`, `pnpm dev`, `vite`, `next dev`, `docker compose up`, backend watchers, previews, or local APIs
- Any project under your local workspace roots
- Any repo with `package.json`, `.env*`, `docker-compose.yml`, `go.mod`, or `Cargo.toml`
- Any case where `3000`, `3001`, `5173`, `8000`, `8080`, `10086`, or similar defaults may collide

## Rules

- Never assume a framework default port is free
- Always reserve or scan before launching a local service
- If the current project port conflicts with another project, tell the user which project conflicts and ask whether to switch to the suggested new port
- Update the central registry after every reserve or repair
- Never auto-apply a config rewrite without explicit user confirmation
- Only patch runtime config files; do not rewrite docs unless the user asks

## Commands

```bash
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" scan-root <workspace-root>
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" reserve --project "$PWD"
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" prompt --project "$PWD" --command "npm run dev"
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" repair-project --project "$PWD" --apply
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" repair-root <workspace-root> --apply
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" lookup --project "$PWD"
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" gc --root <workspace-root>
```

## Workflow

1. Before launching a project, run `prompt --project "$PWD" --command "<start command>"`.
2. If the result says `needs_repair=true`, send the returned `user_prompt` to the user as-is or with minimal trimming.
3. Only after approval, run the returned `apply_command`.
4. If there is no conflict, use the returned `recommended_command`.
5. For new workspaces or after many repo additions, run `scan-root` or `repair-root`.

## Repair Targets

- `.env`, `.env.local`, `.env.development`, `.env.development.local`
- `package.json` dev-like scripts
- `docker-compose.yml` or `docker-compose.yaml` host port mappings

## Notes

- The registry lives at `~/.codex/memories/local-port-registry.json`
- Assigned ports come from a stable high range to avoid repeated clashes with framework defaults
- For multi-service repos, reserve or repair each service separately when needed
- `prompt` is the preferred entrypoint for agent conversations because it already formats the conflict explanation and confirmation question
- Optional: set `LOCAL_PORT_REGISTRY_WORKSPACE_ROOTS` to a colon-separated list such as `/Users/me/work:/Users/me/side-projects` to get shorter relative labels in prompts
