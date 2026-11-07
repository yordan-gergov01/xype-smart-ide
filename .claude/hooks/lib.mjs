// Shared helpers for Claude Code hooks. Node built-ins only (no dependencies, no jq).
import { readFileSync } from 'node:fs';
import path from 'node:path';

export const PROJECT_DIR = process.env.CLAUDE_PROJECT_DIR || process.cwd();

/** Read the hook's JSON payload from stdin; returns {} when stdin is empty or not JSON. */
export function readStdinJson() {
  try {
    const raw = readFileSync(0, 'utf8');
    return raw.trim() ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

/** Same glob subset as tools/check_size.py: `**\/`, `**`, `*`, `?`. */
export function globToRegex(glob) {
  let out = '';
  for (let i = 0; i < glob.length;) {
    if (glob.startsWith('**/', i)) {
      out += '(?:.*/)?';
      i += 3;
    } else if (glob.startsWith('**', i)) {
      out += '.*';
      i += 2;
    } else if (glob[i] === '*') {
      out += '[^/]*';
      i += 1;
    } else if (glob[i] === '?') {
      out += '[^/]';
      i += 1;
    } else {
      out += glob[i].replace(/[.+^${}()|[\]\\]/g, '\\$&');
      i += 1;
    }
  }
  return new RegExp(`^${out}$`);
}

/** Minimal reader for tools/size_limits.toml (integers and string arrays only). */
export function loadSizeLimits() {
  const text = readFileSync(path.join(PROJECT_DIR, 'tools', 'size_limits.toml'), 'utf8').replace(
    /#[^\n"]*$/gm,
    '',
  );
  const int = (key) => Number(new RegExp(`^${key}\\s*=\\s*(\\d+)`, 'm').exec(text)?.[1]);
  const arr = (key) => {
    const body = new RegExp(`^${key}\\s*=\\s*\\[([\\s\\S]*?)\\]`, 'm').exec(text)?.[1] ?? '';
    return [...body.matchAll(/"([^"]*)"/g)].map((m) => m[1]);
  };
  return {
    maxFileLines: int('max_file_lines'),
    extensions: new Set(arr('source_extensions')),
    exempt: arr('exempt').map(globToRegex),
  };
}

/** Paid API endpoints from tools/paid_api_hosts.txt as case-insensitive regexes. */
export function loadPaidApiPatterns() {
  return readFileSync(path.join(PROJECT_DIR, 'tools', 'paid_api_hosts.txt'), 'utf8')
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))
    .map((h) => new RegExp(h.replace(/[.+^${}()|[\]\\]/g, '\\$&'), 'i'));
}

/** Repo-relative POSIX path, or null when the file is outside the project. */
export function relToProject(filePath) {
  if (!filePath) return null;
  const rel = path
    .relative(PROJECT_DIR, path.resolve(PROJECT_DIR, filePath))
    .split(path.sep)
    .join('/');
  return rel.startsWith('..') ? null : rel;
}
