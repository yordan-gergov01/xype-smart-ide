import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// Size and complexity limits from .claude/rules/02-code-quality.md.
/** @type {import('eslint').Linter.RulesRecord} */
const limits = {
  complexity: ['error', 10],
  'max-lines': ['error', { max: 500, skipBlankLines: false, skipComments: false }],
  'max-lines-per-function': ['error', { max: 50, skipBlankLines: true, skipComments: true }],
  'no-console': 'error',
};

export default tseslint.config(
  {
    ignores: ['**/node_modules', '**/dist', '**/coverage', 'server', 'fork/vscode', 'docs/drafts'],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: limits,
  },
  {
    files: ['**/*.{js,mjs}'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
    rules: limits,
  },
  {
    // Claude Code hooks talk to the harness through stdout and stderr.
    files: ['.claude/hooks/**/*.mjs'],
    rules: { 'no-console': 'off' },
  },
  {
    files: ['**/*.test.{ts,tsx,mjs}'],
    rules: { 'max-lines-per-function': 'off' },
  },
);
