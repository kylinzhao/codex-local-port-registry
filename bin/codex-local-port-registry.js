#!/usr/bin/env node
import { installSkill } from "../lib/install.js";

const args = process.argv.slice(2);
const force = args.includes("--force");
const dryRun = args.includes("--dry-run");

installSkill({ force, dryRun }).catch((error) => {
  console.error(`[codex-local-port-registry] ${error.message}`);
  process.exit(1);
});
