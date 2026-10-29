// @ts-nocheck
import { describe, expect, it } from 'vitest';
import {
  mergePreAnnotationsIntoSample,
  preserveGeometryAnnotations,
  preserveGlobalAnnotations,
} from './mergePreAnnotations';

describe('preserveGeometryAnnotations', () => {
  it('keeps existing geometry when the next merge only has global pre-annotations', () => {
    const preserved = preserveGeometryAnnotations(
      {
        'ai-weather': {
          id: 'ai-weather',
          type: 'tag',
          tool: 'tag',
          value: { weather: ['sunny'] },
        },
      } as any,
      {
        'user-box': {
          id: 'user-box',
          order: 0,
          label: 'old',
          tool: 'rect',
          x: 1,
          y: 1,
          width: 10,
          height: 10,
        },
      } as any,
    );

    expect(preserved).toEqual({
      'ai-weather': {
        id: 'ai-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['sunny'] },
      },
      'user-box': {
        id: 'user-box',
        order: 0,
        label: 'old',
        tool: 'rect',
        x: 1,
        y: 1,
        width: 10,
        height: 10,
      },
    });
  });
});

describe('mergePreAnnotationsIntoSample', () => {
  it('uses global pre-annotations when the saved global field is empty', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        rect: [{ id: 'box-1', order: 0, label: 'car', x: 1, y: 1, width: 10, height: 10 }],
        tag: [{ id: 'empty-weather', type: 'tag', value: { weather: [] } }],
      } as any,
      {
        tag: [{ id: 'pre-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      false,
    );

    expect(merged).toEqual({
      'pre-weather': {
        id: 'pre-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['sunny'] },
      },
      'box-1': {
        id: 'box-1',
        order: 0,
        label: 'car',
        tool: 'rect',
        x: 1,
        y: 1,
        width: 10,
        height: 10,
      },
    });
  });

  it('keeps saved global values instead of overwriting user annotations', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        tag: [{ id: 'user-weather', type: 'tag', value: { weather: ['rainy'] } }],
      } as any,
      {
        tag: [{ id: 'pre-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      false,
    );

    expect(merged).toEqual({
      'user-weather': {
        id: 'user-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['rainy'] },
      },
    });
  });

  it('treats blank global array entries as empty', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        tag: [{ id: 'blank-weather', type: 'tag', value: { weather: [''] } }],
      } as any,
      {
        tag: [{ id: 'pre-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      false,
    );

    expect(merged).toEqual({
      'pre-weather': {
        id: 'pre-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['sunny'] },
      },
    });
  });

  it('overwrites saved geometry and global annotations when requested', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        rect: [{ id: 'user-box', order: 0, label: 'old', x: 1, y: 1, width: 10, height: 10 }],
        tag: [{ id: 'user-weather', type: 'tag', value: { weather: ['rainy'] } }],
      } as any,
      {
        rect: [{ id: 'ai-box', order: 0, label: 'car', x: 2, y: 2, width: 20, height: 20 }],
        tag: [{ id: 'ai-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      false,
      { overwriteExistingAnnotations: true },
    );

    expect(merged).toEqual({
      'ai-box': {
        id: 'ai-box',
        order: 0,
        label: 'car',
        tool: 'rect',
        x: 2,
        y: 2,
        width: 20,
        height: 20,
      },
      'ai-weather': {
        id: 'ai-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['sunny'] },
      },
    });
  });

  it('keeps saved geometry when overwrite is requested but only global pre-annotations are present', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        rect: [{ id: 'user-box', order: 0, label: 'old', x: 1, y: 1, width: 10, height: 10 }],
        tag: [{ id: 'user-weather', type: 'tag', value: { weather: ['rainy'] } }],
      } as any,
      {
        tag: [{ id: 'ai-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      false,
      { overwriteExistingAnnotations: true },
    );

    expect(merged).toEqual({
      'ai-weather': {
        id: 'ai-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['sunny'] },
      },
      'user-box': {
        id: 'user-box',
        order: 0,
        label: 'old',
        tool: 'rect',
        x: 1,
        y: 1,
        width: 10,
        height: 10,
      },
    });
  });

  it('removes saved geometry when that tool is marked for replacement but has no pre-annotations', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        rect: [{ id: 'user-box', order: 0, label: 'old', x: 1, y: 1, width: 10, height: 10 }],
        tag: [{ id: 'user-weather', type: 'tag', value: { weather: ['rainy'] } }],
      } as any,
      {
        tag: [{ id: 'ai-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      false,
      { overwriteExistingAnnotations: true, replaceTools: ['rect'] },
    );

    expect(merged).toEqual({
      'ai-weather': {
        id: 'ai-weather',
        type: 'tag',
        tool: 'tag',
        value: { weather: ['sunny'] },
      },
    });
  });

  it('replaces geometry while preserving saved global annotations', () => {
    const merged = mergePreAnnotationsIntoSample(
      {
        rect: [{ id: 'old-box', order: 0, label: 'old', x: 1, y: 1, width: 10, height: 10 }],
        tag: [{ id: 'saved-weather', type: 'tag', value: { weather: ['sunny'] } }],
      } as any,
      {
        rect: [{ id: 'yolo-box', order: 0, label: 'new', x: 2, y: 2, width: 20, height: 20 }],
      } as any,
      false,
      { overwriteExistingAnnotations: true, replaceTools: ['rect', 'polygon', 'point', 'line'] },
    );

    expect(Object.keys(merged)).toEqual(['yolo-box', 'saved-weather']);
    expect(merged['yolo-box'].tool).toBe('rect');
    expect(merged['saved-weather'].tool).toBe('tag');
  });
});

describe('preserveGlobalAnnotations', () => {
  it('keeps the current global field while accepting refreshed geometry', () => {
    const merged = preserveGlobalAnnotations(
      {
        'rex-box': {
          id: 'rex-box',
          order: 0,
          label: 'vehicle_motorbike',
          tool: 'rect',
          x: 2,
          y: 2,
          width: 20,
          height: 20,
        },
        'old-weather': {
          id: 'old-weather',
          type: 'tag',
          tool: 'tag',
          value: { weather: ['sunny'] },
        },
      } as any,
      {
        'current-weather': {
          id: 'current-weather',
          type: 'tag',
          tool: 'tag',
          value: { weather: ['rainy'] },
        },
        'current-scene': {
          id: 'current-scene',
          type: 'tag',
          tool: 'tag',
          value: { scene: ['road'] },
        },
      } as any,
    );

    expect(merged['rex-box'].tool).toBe('rect');
    expect(merged['old-weather']).toBeUndefined();
    expect(merged['current-weather'].value).toEqual({ weather: ['rainy'] });
    expect(merged['current-scene'].value).toEqual({ scene: ['road'] });
  });
});
