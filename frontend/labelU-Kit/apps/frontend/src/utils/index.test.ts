import { describe, expect, it } from 'vitest';

import { jsonParse } from './index';

describe('jsonParse', () => {
  it('returns objects as-is when the backend already provided parsed JSON', () => {
    const value = {
      rectTool: {
        toolName: 'rectTool',
        result: [{ id: 'box-1', attribute: 'person' }],
      },
    };

    expect(jsonParse(value)).toBe(value);
  });

  it('returns an empty object for nullish values', () => {
    expect(jsonParse(null)).toEqual({});
    expect(jsonParse(undefined)).toEqual({});
    expect(jsonParse('')).toEqual({});
  });
});
