import { describe, expect, it, vi } from 'vitest';

import { readPersistedAnnotationConfig } from './annotationConfigPersistence';

describe('readPersistedAnnotationConfig', () => {
  it('preserves preset identity while removing client-only preset fields', () => {
    const getFieldsValue = vi.fn(() => ({
      media_type: 'IMAGE',
      defaultRectPresetId: 'garbageExposure',
      selectedRectPresetId: 2,
      rectLabelPresets: [{ id: 'garbageExposure', presetDbId: 2 }],
      tools: [{ tool: 'rectTool', config: { attributes: [] } }],
    }));

    expect(readPersistedAnnotationConfig({ getFieldsValue } as any)).toEqual({
      defaultRectPresetId: 'garbageExposure',
      tools: [{ tool: 'rectTool', config: { attributes: [] } }],
    });
    expect(getFieldsValue).toHaveBeenCalledWith(true);
  });
});
