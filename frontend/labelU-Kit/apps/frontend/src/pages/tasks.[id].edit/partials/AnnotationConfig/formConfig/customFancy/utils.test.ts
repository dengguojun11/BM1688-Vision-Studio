import { describe, expect, it, vi } from 'vitest';

import { duplicatedValueValidator } from './utils';

vi.mock('@labelu/i18n', () => ({
  i18n: { t: (key: string) => key },
}));

describe('duplicatedValueValidator', () => {
  it('accepts a field path that is temporarily absent during tool replacement', async () => {
    const getFieldValue = vi.fn(() => undefined);
    const rule = duplicatedValueValidator(
      ['tools', 0, 'config', 'attributes'],
      0,
    )({
      getFieldValue,
    } as any);

    await expect(rule.validator(undefined, 'label')).resolves.toBeUndefined();
  });
});
