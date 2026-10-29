import type { SampleResponse } from '@/api/types';

export const TRAINING_SAMPLE_TYPE_NEGATIVE = 'negative';

export function isExplicitNegativeSample(sample: Pick<SampleResponse, 'data'> | undefined): boolean {
  return sample?.data?.training_sample_type === TRAINING_SAMPLE_TYPE_NEGATIVE;
}

export function hasRectAnnotations(result: unknown): boolean {
  if (!result || typeof result !== 'object') {
    return false;
  }
  const value = result as Record<string, any>;
  const rectBlock = value.rectTool ?? value.rect;
  if (Array.isArray(rectBlock)) {
    return rectBlock.length > 0;
  }
  return Array.isArray(rectBlock?.result) && rectBlock.result.length > 0;
}

export function withExplicitNegativeSample<T extends SampleResponse>(sample: T, negative: boolean): T {
  const data = { ...(sample.data || { result: '{}' }) };
  if (negative) {
    data.training_sample_type = TRAINING_SAMPLE_TYPE_NEGATIVE;
  } else {
    delete data.training_sample_type;
  }
  return { ...sample, data };
}
