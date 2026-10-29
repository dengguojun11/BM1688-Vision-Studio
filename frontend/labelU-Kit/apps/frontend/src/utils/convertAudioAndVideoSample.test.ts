import { describe, expect, it } from 'vitest';

import { MediaType, type SampleResponse } from '@/api/types';

import { convertAudioAndVideoSample } from './convertAudioAndVideoSample';

const sample: SampleResponse = {
  id: 42,
  inner_id: 7,
  is_pre_annotated: false,
  file: {
    id: 1,
    url: '/api/v1/attachment/video.mp4',
    filename: 'video.mp4',
  },
  data: {
    result: JSON.stringify({
      videoSegmentTool: { result: [{ id: 'segment-1', start: 0, end: 1 }] },
      tagTool: { result: [{ id: 'tag-1', value: 'car' }] },
    }),
  },
};

describe('convertAudioAndVideoSample', () => {
  it('maps persisted tool names to annotator media keys and rewrites media URLs', () => {
    const converted = convertAudioAndVideoSample(sample, MediaType.VIDEO);

    expect(converted).toMatchObject({
      id: 42,
      url: '/api/v1/partial/video.mp4',
      data: {
        segment: [{ id: 'segment-1', start: 0, end: 1, type: 'segment' }],
        tag: [{ id: 'tag-1', value: 'car', type: 'tag' }],
      },
    });
  });

  it('returns an empty annotation map when a sample has no persisted result', () => {
    const converted = convertAudioAndVideoSample({ ...sample, data: undefined }, MediaType.AUDIO);

    expect(converted?.url).toBe('/api/v1/partial/video.mp4');
    expect(converted?.data).toEqual({ segment: [], frame: [], text: [], tag: [] });
  });
});
