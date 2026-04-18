# Codex Local Port Registry

Codex skill for reserving, auditing, and repairing local dev ports before starting project services.

## Install

```bash
npx github:kylinzhao/codex-local-port-registry
```

Restart Codex after installing.

To overwrite an existing install:

```bash
npx github:kylinzhao/codex-local-port-registry -- --force
```

## Optional

Set workspace roots for shorter conflict labels:

```bash
export LOCAL_PORT_REGISTRY_WORKSPACE_ROOTS="$HOME/work:$HOME/projects"
```

## Global Agent Rule

Add this to your global `AGENTS.md` if you want startup commands to be checked automatically:

~~~md
## Local Dev Port Guard

Before starting any local dev server, preview server, backend watcher, or `docker compose` service, run:

```bash
python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" prompt --project "$PWD" --command "<start command>"
```

If `needs_repair=true`, show `user_prompt`, wait for approval, then run `apply_command`.
If `needs_repair=false`, use `recommended_command` when present.
~~~
