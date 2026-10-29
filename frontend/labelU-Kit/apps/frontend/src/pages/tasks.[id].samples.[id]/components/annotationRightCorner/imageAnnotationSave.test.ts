import { describe, expect, it } from 'vitest';

import { buildImageAnnotationSaveResult, exportImageAnnotations } from './imageAnnotationSave';

describe('imageAnnotationSave', () => {
  it('exports annotations by tool name without UI-only fields', () => {
    expect(
      exportImageAnnotations({
        box1: { id: 'box1', tool: 'rect', x: 10, y: 20, visible: true },
        tag1: { id: 'tag1', type: 'tag', label: 'car', visible: false },
      }),
    ).toEqual({
      rect: [{ id: 'box1', x: 10, y: 20 }],
      tag: [{ id: 'tag1', label: 'car', type: 'tag' }],
    });
  });

  it('restores saved image annotations back to original coordinates with the actual display size', () => {
    const result = buildImageAnnotationSaveResult({
      annotationsWithGlobal: {
        box1: {
          id: 'box1',
          tool: 'rect',
          x: 1456.0333333333333,
          y: 819.01875,
          width: 145.60333333333332,
          height: 81.901875,
        },
        tag1: {
          id: 'tag1',
          type: 'tag',
          label: 'done',
        },
      },
      originalWidth: 1920,
      originalHeight: 1080,
      displayWidth: 1456.0333333333333,
      displayHeight: 819.01875,
      rotate: 90,
    });

    expect(result.width).toBe(1920);
    expect(result.height).toBe(1080);
    expect(result.rotate).toBe(90);
    expect(result.rectTool?.toolName).toBe('rectTool');
    expect(result.rectTool?.result[0].id).toBe('box1');
    expect(result.rectTool?.result[0].x).toBeCloseTo(1920);
    expect(result.rectTool?.result[0].y).toBeCloseTo(1080);
    expect(result.rectTool?.result[0].width).toBeCloseTo(192);
    expect(result.rectTool?.result[0].height).toBeCloseTo(108);
    expect(result.tagTool).toEqual({
      toolName: 'tagTool',
      result: [
        {
          id: 'tag1',
          type: 'tag',
          label: 'done',
        },
      ],
    });
  });
});
