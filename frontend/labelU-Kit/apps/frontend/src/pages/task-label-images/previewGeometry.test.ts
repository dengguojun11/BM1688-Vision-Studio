import { describe, expect, it } from 'vitest';

import { fitContainSize } from './previewGeometry';

describe('fitContainSize', () => {
  it('fits a landscape image to the available width', () => {
    expect(fitContainSize(800, 600, 1600, 900)).toEqual({ width: 800, height: 450 });
  });

  it('fits a portrait image to the available height', () => {
    expect(fitContainSize(800, 600, 900, 1600)).toEqual({ width: 337.5, height: 600 });
  });

  it('rejects invalid container or image dimensions', () => {
    expect(fitContainSize(800, 0, 1600, 900)).toEqual({ width: 0, height: 0 });
    expect(fitContainSize(800, 600, Number.NaN, 900)).toEqual({ width: 0, height: 0 });
  });
});
