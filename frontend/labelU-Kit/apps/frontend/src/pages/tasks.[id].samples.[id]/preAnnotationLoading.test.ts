import { describe, expect, it } from 'vitest';

import { SampleState } from '@/api/types';

import {
  sampleHasGeometryAnnotations,
  shouldAutoLoadCanvasPreAnnotationsForSample,
  shouldLoadCanvasPreAnnotations,
} from './preAnnotationLoading';

describe('shouldLoadCanvasPreAnnotations', () => {
  it('auto-loads pre-annotations only for new samples', () => {
    expect(shouldLoadCanvasPreAnnotations(SampleState.NEW)).toBe(true);
    expect(shouldLoadCanvasPreAnnotations(SampleState.DONE)).toBe(false);
    expect(shouldLoadCanvasPreAnnotations(SampleState.SKIPPED)).toBe(false);
  });

  it('allows manual pre-annotation refresh for annotated samples', () => {
    expect(shouldLoadCanvasPreAnnotations(SampleState.DONE, { manual: true })).toBe(true);
  });
});

describe('shouldAutoLoadCanvasPreAnnotationsForSample', () => {
  it('keeps auto-loading for new samples', () => {
    expect(
      shouldAutoLoadCanvasPreAnnotationsForSample({
        state: SampleState.NEW,
        data: { result: null } as any,
        is_pre_annotated: false,
        is_yolo_pre_annotated: false,
        is_experiment_dataset_pre_annotated: false,
      }),
    ).toBe(true);
  });

  it('auto-loads geometry pre-annotations for annotated samples when current result has no geometry', () => {
    expect(
      shouldAutoLoadCanvasPreAnnotationsForSample({
        state: SampleState.DONE,
        data: {
          result: JSON.stringify({
            tagTool: { toolName: 'tagTool', result: [{ id: 'tag-1' }] },
          }),
        } as any,
        is_pre_annotated: false,
        is_yolo_pre_annotated: false,
        is_experiment_dataset_pre_annotated: true,
      }),
    ).toBe(true);
  });

  it('auto-loads VLX geometry pre-annotations for annotated samples', () => {
    expect(
      shouldAutoLoadCanvasPreAnnotationsForSample({
        state: SampleState.DONE,
        data: { result: null } as any,
        is_pre_annotated: false,
        is_yolo_pre_annotated: false,
        is_experiment_dataset_pre_annotated: false,
        is_vlx_pre_annotated: true,
      }),
    ).toBe(true);
  });

  it('does not auto-load when annotated sample already has geometry', () => {
    expect(
      shouldAutoLoadCanvasPreAnnotationsForSample({
        state: SampleState.DONE,
        data: {
          result: JSON.stringify({
            rectTool: { toolName: 'rectTool', result: [{ id: 'box-1' }] },
          }),
        } as any,
        is_pre_annotated: false,
        is_yolo_pre_annotated: false,
        is_experiment_dataset_pre_annotated: true,
      }),
    ).toBe(false);
  });

  it('loads VLX preview even when the saved sample already has geometry', () => {
    expect(
      shouldAutoLoadCanvasPreAnnotationsForSample({
        state: SampleState.DONE,
        data: {
          result: JSON.stringify({
            rectTool: { toolName: 'rectTool', result: [{ id: 'saved-box-1' }] },
          }),
        } as any,
        is_pre_annotated: false,
        is_yolo_pre_annotated: false,
        is_experiment_dataset_pre_annotated: false,
        is_vlx_pre_annotated: true,
      }),
    ).toBe(true);
  });

  it('loads YOLO preview even when the saved sample already has geometry', () => {
    expect(
      shouldAutoLoadCanvasPreAnnotationsForSample({
        state: SampleState.DONE,
        data: {
          result: JSON.stringify({
            rectTool: { toolName: 'rectTool', result: [{ id: 'saved-box-1' }] },
          }),
        } as any,
        is_pre_annotated: false,
        is_yolo_pre_annotated: true,
        is_experiment_dataset_pre_annotated: false,
        is_vlx_pre_annotated: false,
      }),
    ).toBe(true);
  });
});

describe('sampleHasGeometryAnnotations', () => {
  it('detects geometry from stringified sample results', () => {
    expect(
      sampleHasGeometryAnnotations({
        data: {
          result: JSON.stringify({
            rectTool: { toolName: 'rectTool', result: [{ id: 'box-1' }] },
          }),
        } as any,
      }),
    ).toBe(true);
  });

  it('returns false for tag-only results', () => {
    expect(
      sampleHasGeometryAnnotations({
        data: {
          result: JSON.stringify({
            tagTool: { toolName: 'tagTool', result: [{ id: 'tag-1' }] },
          }),
        } as any,
      }),
    ).toBe(false);
  });
});
