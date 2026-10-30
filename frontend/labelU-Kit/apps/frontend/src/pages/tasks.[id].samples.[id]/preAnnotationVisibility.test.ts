import { describe, expect, it } from 'vitest';

import { SampleState } from '@/api/types';

import { getVisiblePreAnnotations, preAnnotationsHasGeometry } from './preAnnotationVisibility';

describe('preAnnotationsHasGeometry', () => {
  it('detects rect pre-annotations', () => {
    expect(preAnnotationsHasGeometry({ rect: [{ id: 'box-1' }] })).toBe(true);
    expect(preAnnotationsHasGeometry({ tag: [{ id: 'tag-1' }] })).toBe(false);
  });
});

describe('getVisiblePreAnnotations', () => {
  it('keeps all pre-annotations for new samples when global-only mode is off', () => {
    const preAnnotations = {
      rect: [{ id: 'box-1' }],
      tag: [{ id: 'tag-1' }],
    };

    expect(getVisiblePreAnnotations(SampleState.NEW, preAnnotations)).toBe(preAnnotations);
  });

  it('keeps geometry pre-annotations for new samples even in global-only mode', () => {
    const preAnnotations = {
      rect: [{ id: 'box-1' }],
      tag: [{ id: 'tag-1' }],
    };

    expect(getVisiblePreAnnotations(SampleState.NEW, preAnnotations, { globalOnlyForAnnotated: true })).toBe(
      preAnnotations,
    );
  });

  it('keeps only global pre-annotations for already annotated samples', () => {
    const preAnnotations = {
      rect: [{ id: 'box-1' }],
      polygon: [{ id: 'poly-1' }],
      tag: [{ id: 'tag-1' }],
      text: [{ id: 'text-1' }],
    };

    expect(getVisiblePreAnnotations(SampleState.DONE, preAnnotations, { globalOnlyForAnnotated: true })).toEqual({
      tag: [{ id: 'tag-1' }],
      text: [{ id: 'text-1' }],
    });
  });

  it('hides geometry pre-annotations for annotated samples even when the saved result has no geometry', () => {
    const preAnnotations = {
      rect: [{ id: 'box-1' }],
      tag: [{ id: 'tag-1' }],
    };

    expect(
      getVisiblePreAnnotations(SampleState.DONE, preAnnotations, {
        globalOnlyForAnnotated: true,
      }),
    ).toEqual({ tag: [{ id: 'tag-1' }] });
  });

  it('keeps an explicit detection preview beside existing formal geometry', () => {
    const preAnnotations = {
      rect: [{ id: 'ai-box-1' }],
      tag: [{ id: 'tag-1' }],
    };

    expect(
      getVisiblePreAnnotations(SampleState.DONE, preAnnotations, {
        globalOnlyForAnnotated: true,
        currentAnnotationsHaveGeometry: true,
        allowGeometryForAnnotated: true,
      }),
    ).toBe(preAnnotations);
  });

  it('does not expose pre-annotations for already annotated samples by default', () => {
    const preAnnotations = {
      tag: [{ id: 'tag-1' }],
    };

    expect(getVisiblePreAnnotations(SampleState.DONE, preAnnotations)).toBeUndefined();
  });

  it('keeps all pre-annotations for annotated samples when overwrite is requested', () => {
    const preAnnotations = {
      rect: [{ id: 'box-1' }],
      tag: [{ id: 'tag-1' }],
    };

    expect(
      getVisiblePreAnnotations(SampleState.DONE, preAnnotations, {
        globalOnlyForAnnotated: true,
        overwriteExistingAnnotations: true,
      }),
    ).toBe(preAnnotations);
  });
});
