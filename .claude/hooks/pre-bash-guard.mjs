// PreToolUse(Bash|PowerShell) guard: blocks destructive commands, hook bypasses, force pushes,
// shell reads of secret files and network calls to paid LLM APIs.
// Exit 2 = block (stderr is shown to Claude).
import { loadPaidApiPatterns, readStdinJson } from './lib.mjs';

const PAID_API = loadPaidApiPatterns();
const NETWORK_TOOL =
  /\b(curl|wget|httpie|Invoke-WebRequest|Invoke-RestMethod|iwr|irm|nc|ncat)\b|https?:\/\//i;

// Secret files: .env, .env.<anything except "example">, keys, certificates, secrets/ dirs.
const SECRET_FILE =
  /(^|[\s"'=\\/])(\.env(\.(?!example\b)[\w.-]+)?|[\w.-]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)\w*|secrets[\\/]\S*)(?=$|[\s"';|&)])/i;
const FILE_READER =
  /(^|[;&|(\n]\s*)(cat|type|less|more|head|tail|bat|nl|grep|rg|sed|awk|cp|copy|mv|source|\.|Get-Content|gc|Select-String|Copy-Item)\b/i;

/**
 * Remove quoted strings so messages like -m "why force push is banned" don't trigger rules.
 * @param {string} c
 */
const unquote = (c) => c.replace(/"(?:[^"\\]|\\.)*"|'[^']*'/g, '""');

/** @type {{ test: (command: string) => boolean, reason: string }[]} */
const RULES = [
  {
    test: (c) =>
      /\bgit\b[^\n;&|]*\bpush\b[^\n;&|]*(\s--force(-with-lease|-if-includes)?\b|\s-f\b|\s--mirror\b|\s\+\S+|\s:\S*master\b|\s--delete\b[^\n;&|]*\bmaster\b|\s-d\b[^\n;&|]*\bmaster\b)/.test(
        unquote(c),
      ),
    reason: 'Force push, mirror push and deleting master are blocked. Ask the owner.',
  },
  {
    test: (c) => {
      const u = unquote(c);
      return (
        /\bgit\b[^\n;&|]*\b(commit|push|merge|rebase|am|cherry-pick)\b[^\n;&|]*\s--no-verify\b/.test(
          u,
        ) ||
        /\bgit\b[^\n;&|]*\bcommit\b[^\n;&|]*\s-[a-zA-Z]*n[a-zA-Z]*\b/.test(u) ||
        /\bgit\b[^\n;&|]*-c\s+core\.hooksPath\b/i.test(u) ||
        /\bgit\s+config\b[^\n;&|]*\bcore\.hooksPath\b/i.test(u) ||
        /(^|[;&|\n]\s*)(SKIP|PRE_COMMIT_ALLOW_NO_CONFIG|HUSKY)=\S*\s+git\b/.test(u)
      );
    },
    reason:
      'Bypassing git hooks is blocked (--no-verify, -n, core.hooksPath, SKIP=). Fix the failing check instead.',
  },
  {
    test: (c) => {
      const u = unquote(c);
      const target = String.raw`("|')?(\/|~\/?|\$HOME\/?|\*|\.\/?|\.\.\/?|[A-Za-z]:[\\/]?)("|')?(\s|$)`;
      const rmRecursive = new RegExp(
        String.raw`\brm\s+((-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|-[a-zA-Z]+|--force)\s+)+` + target,
      );
      const psRecursive = new RegExp(
        String.raw`\b(Remove-Item|rm|ri|del|rd|rmdir)\b[^\n;&|]*-Recurse\b[^\n;&|]*\s` + target,
        'i',
      );
      const psTargetFirst = new RegExp(
        String.raw`\b(Remove-Item|ri)\s+(-Path\s+|-LiteralPath\s+)?` +
          target.replace(String.raw`(\s|$)`, String.raw`[^\n;&|]*-Recurse\b`),
        'i',
      );
      return rmRecursive.test(u) || psRecursive.test(u) || psTargetFirst.test(u);
    },
    reason: 'Recursive delete of /, ~, ., .., * or a drive root is blocked.',
  },
  {
    // Only as a command (start of line or after a separator/sudo), so words like "graceful shutdown"
    // in commit messages stay allowed.
    test: (c) =>
      /(^|[;&|\n(]\s*|\bsudo\s+)(mkfs(\.\w+)?|format\s+[A-Za-z]:|diskpart|shutdown|reboot|Stop-Computer|Restart-Computer)\b/i.test(
        c,
      ) || /:\(\)\s*\{\s*:\|:&\s*\};:/.test(c),
    reason: 'System-level destructive command is blocked.',
  },
  {
    test: (c) => FILE_READER.test(c) && SECRET_FILE.test(c),
    reason:
      'Reading or copying secret files (.env, keys, certificates, secrets/) is blocked. Use deploy/env/.env.example.',
  },
  {
    test: (c) => NETWORK_TOOL.test(c) && PAID_API.some((p) => p.test(c)),
    reason:
      'Network call to a paid LLM API is blocked (cost rule). Use the mock provider or Ollama.',
  },
];

const input = readStdinJson();
const command = String(input?.tool_input?.command ?? '');
const hit = RULES.find((r) => r.test(command));
if (hit) {
  process.stderr.write(`Blocked by .claude/hooks/pre-bash-guard.mjs: ${hit.reason}\n`);
  process.exit(2);
}
process.exit(0);
