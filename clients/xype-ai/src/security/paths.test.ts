import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { PathError } from './obfuscation';
import { fileText, isDeniedPath, parsePathRules, PathRulesError } from './paths';

const rules = parsePathRules(
  JSON.parse(
    readFileSync(new URL('../../../../packages/scrub-rules/rules.json', import.meta.url), 'utf8'),
  ),
);
const utf8 = (text: string) => new TextEncoder().encode(text);

describe('path exclusion (ADR-0019)', () => {
  it.each([
    '.env',
    'api/.env.local',
    '.ENV.Production',
    'certs/server.pem',
    'deploy/tls.KEY',
    'home/id_rsa.pub',
    'id_ed25519',
    '.npmrc',
    'kubeconfig',
    'ops/.kube/config',
    'a/.aws/credentials',
    '.ssh/known_hosts',
    'x/.docker/config.json',
    'svc/credentials.json',
    'infra/terraform.tfstate',
    'infra/terraform.tfstate.backup',
    'charts\\secrets.yaml',
  ])('denies %s', (path) => {
    expect(isDeniedPath(rules, path)).toBe(true);
  });

  it.each([
    '.env.example',
    'api/.env.example',
    'src/main.py',
    'docs/keys.md',
    'src/pem.ts',
    'docker/config.json',
    'environment.ts',
    'aws/handler.py',
    'notes/a:b.txt',
  ])('allows %s', (path) => {
    expect(isDeniedPath(rules, path)).toBe(false);
  });

  it('refuses a glob it would match only literally', () => {
    for (const pattern of ['*.egg-info/', 'config/*.secret']) {
      expect(() => parsePathRules({ denied_paths: [pattern], max_file_bytes: 1 })).toThrow(
        PathRulesError,
      );
    }
  });

  it('refuses paths outside the repository', () => {
    for (const path of ['../x.py', '/etc/passwd', 'a//b.py', 'C:/x.py']) {
      expect(() => isDeniedPath(rules, path)).toThrow(PathError);
    }
  });
});

describe('file text', () => {
  it('normalises line endings to LF', () => {
    expect(fileText(rules, 'a.py', utf8('x = 1\r\ny = 2\r\n'))).toEqual({
      kind: 'text',
      text: 'x = 1\ny = 2\n',
    });
  });

  it.each([
    ['bad_path', '../outside.py', utf8('x = 1\n')],
    ['denied_path', '.env', utf8('A=1\n')],
    ['too_large', 'a.py', new Uint8Array(rules.maxFileBytes + 1).fill(0x61)],
    ['binary', 'a.bin', Uint8Array.of(0x61, 0, 0x62)],
    ['not_utf8', 'a.py', Uint8Array.of(0x61, 0xff, 0x62)],
  ] as const)('excludes %s', (reason, path, bytes) => {
    expect(fileText(rules, path, bytes)).toEqual({ kind: 'excluded', reason });
  });
});
