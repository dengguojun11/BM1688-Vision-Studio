import { describe, expect, it } from 'vitest';

import { isPreannotationCapabilityVisible, normalizePreannotationCapabilities } from './preannotationCapabilities';

describe('preannotation capability visibility', () => {
  it('normalizes the API envelope and preserves hidden providers', () => {
    const capabilities = normalizePreannotationCapabilities({
      data: {
        items: [
          { key: 'yolo', visible: true, available: true },
          { key: 'tagtool', visible: false, available: true },
          { key: 'vlx_seek', visible: true, available: false },
        ],
      },
    });

    expect(isPreannotationCapabilityVisible(capabilities.yolo)).toBe(true);
    expect(isPreannotationCapabilityVisible(capabilities.tagtool)).toBe(false);
    expect(isPreannotationCapabilityVisible(capabilities.vlx_seek)).toBe(false);
  });
});
