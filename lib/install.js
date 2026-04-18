import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const packageRoot = path.resolve(__dirname, "..");
const sourceSkillDir = path.join(packageRoot, "skills", "local-port-registry");

function parseArgs() {
  return process.argv.slice(2);
}

function hasFlag(name) {
  return parseArgs().includes(name);
}

function codexHome() {
  return process.env.CODEX_HOME
    ? path.resolve(process.env.CODEX_HOME)
    : path.join(os.homedir(), ".codex");
}

function destinationSkillDir() {
  return path.join(codexHome(), "skills", "local-port-registry");
}

function ensureParentDir(target) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
}

function copyRecursive(source, target) {
  const stat = fs.statSync(source);
  if (stat.isDirectory()) {
    fs.mkdirSync(target, { recursive: true });
    for (const entry of fs.readdirSync(source)) {
      copyRecursive(path.join(source, entry), path.join(target, entry));
    }
    return;
  }
  ensureParentDir(target);
  fs.copyFileSync(source, target);
}

function removeRecursive(target) {
  fs.rmSync(target, { recursive: true, force: true });
}

export async function installSkill({ force = false, dryRun = false } = {}) {
  const dest = destinationSkillDir();
  const home = codexHome();

  if (!fs.existsSync(sourceSkillDir)) {
    throw new Error(`bundled skill not found: ${sourceSkillDir}`);
  }

  if (dryRun) {
    console.log(`Would install local-port-registry to ${dest}`);
    if (fs.existsSync(dest)) {
      console.log("Existing installation detected; use --force for overwrite in a real run.");
    }
    return;
  }

  if (fs.existsSync(dest) && !force) {
    console.log(`Skill already installed at ${dest}`);
    console.log("Use --force to overwrite it.");
    console.log("Restart Codex to reload skills if needed.");
    return;
  }

  fs.mkdirSync(path.join(home, "skills"), { recursive: true });
  if (fs.existsSync(dest)) {
    removeRecursive(dest);
  }
  copyRecursive(sourceSkillDir, dest);

  const pythonScript = path.join(dest, "scripts", "port_registry.py");
  if (fs.existsSync(pythonScript)) {
    fs.chmodSync(pythonScript, 0o755);
  }

  console.log(`Installed local-port-registry to ${dest}`);
  console.log("Restart Codex to pick up the new skill.");
  console.log("Optional: add the AGENTS.md snippet from the repository README to enable automatic pre-launch checks.");

  if (hasFlag("--print-agent-snippet")) {
    console.log("");
    console.log("## Local Dev Port Guard");
    console.log("");
    console.log("Before starting any local dev server, preview server, backend watcher, or `docker compose` service, run:");
    console.log("");
    console.log('```bash');
    console.log('python3 "$HOME/.codex/skills/local-port-registry/scripts/port_registry.py" prompt --project "$PWD" --command "<start command>"');
    console.log('```');
  }
}
