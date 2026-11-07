// Stop hook: report-only fast check of changed files (size limits). Never blocks (always exit 0);
// findings are surfaced to the user as a system message.
import { spawnSync } from "node:child_process";
import { PROJECT_DIR, readStdinJson } from "./lib.mjs";

readStdinJson();
const python = process.platform === "win32" ? "py" : "python3";
const args = process.platform === "win32" ? ["-3", "tools/check_size.py", "--changed"] : ["tools/check_size.py", "--changed"];
const r = spawnSync(python, args, { cwd: PROJECT_DIR, encoding: "utf8", timeout: 20_000 });
const findings = (r.stderr ?? "").trim();
if (r.status === 1 && findings) {
  process.stdout.write(JSON.stringify({ systemMessage: `Size check on changed files:\n${findings}` }));
}
process.exit(0);
