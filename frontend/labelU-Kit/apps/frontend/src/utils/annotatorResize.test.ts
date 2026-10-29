import { Annotator } from '@labelu/image';
import { describe, expect, it, vi } from 'vitest';

function createResizeHarness(dataByTool: Record<string, unknown[]>) {
  const load = vi.fn();
  const clear = vi.fn();
  const render = vi.fn();
  const rendererResize = vi.fn();
  const backgroundResize = vi.fn();
  const container = {
    clientWidth: 640,
    clientHeight: 480,
    style: { width: '', height: '' },
  };
  const tool = { name: 'rect', clear, load };
  const annotator = {
    backgroundRenderer: { resize: backgroundResize },
    renderer: { resize: rendererResize },
    container,
    tools: new Map([['rect', tool]]),
    getDataByTool: () => dataByTool,
    render,
  };

  Annotator.prototype.resize.call(annotator as unknown as Annotator, {
    width: 800,
    height: 600,
  });

  return { backgroundResize, clear, container, load, render, rendererResize };
}

describe('Annotator.resize', () => {
  it('reloads an empty tool with an empty data array', () => {
    const harness = createResizeHarness({});

    expect(harness.clear).toHaveBeenCalledOnce();
    expect(harness.load).toHaveBeenCalledWith([]);
    expect(harness.render).toHaveBeenCalledOnce();
  });

  it('preserves existing tool data while resizing', () => {
    const rectData = [{ id: 'rect-1', x: 10, y: 20, width: 30, height: 40 }];
    const harness = createResizeHarness({ rect: rectData });

    expect(harness.load).toHaveBeenCalledWith(rectData);
    expect(harness.rendererResize).toHaveBeenCalledWith(800, 600);
    expect(harness.backgroundResize).toHaveBeenCalledWith(800, 600);
    expect(harness.container.style).toEqual({ width: '800px', height: '600px' });
  });
});
