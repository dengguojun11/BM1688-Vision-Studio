import { describe, expect, it } from 'vitest';

import { hasRectAnnotations, isExplicitNegativeSample, withExplicitNegativeSample } from './negativeSample';

const sample = {
  inner_id: 1,
  is_pre_annotated: false,
  data: { result: '{"rectTool":{"result":[]}}' },
  file: { id: 1, url: '/image.jpg', filename: 'image.jpg' },
};

describe('negative sample metadata', () => {
  it('marks and unmarks a sample without changing its result', () => {
    const marked = withExplicitNegativeSample(sample, true);
    expect(marked.data?.training_sample_type).toBe('negative');
    expect(marked.data?.result).toBe(sample.data.result);
    expect(isExplicitNegativeSample(marked)).toBe(true);

    const unmarked = withExplicitNegativeSample(marked, false);
    expect(unmarked.data?.training_sample_type).toBeUndefined();
    expect(isExplicitNegativeSample(unmarked)).toBe(false);
  });

  it('detects both canvas and persisted rectangle shapes', () => {
    expect(hasRectAnnotations({ rect: [{ id: 'canvas-box' }] })).toBe(true);
    expect(hasRectAnnotations({ rectTool: { result: [{ id: 'stored-box' }] } })).toBe(true);
    expect(hasRectAnnotations({ rectTool: { result: [] } })).toBe(false);
  });
});
