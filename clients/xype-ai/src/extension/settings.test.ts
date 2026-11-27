import { describe, expect, it } from 'vitest';

import { userValue } from './settings';

describe('userValue', () => {
  it('takes only the user setting, never what a workspace or folder sets', () => {
    expect(userValue({ globalValue: 'https://xype.local' })).toBe('https://xype.local');
    expect(
      userValue({ workspaceValue: 'https://attacker.example', workspaceFolderValue: 'https://x' }),
    ).toBe('');
    expect(
      userValue({ globalValue: 'https://xype.local', workspaceValue: 'https://attacker.example' }),
    ).toBe('https://xype.local');
    expect(userValue(undefined)).toBe('');
  });
});
