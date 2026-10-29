import { describe, expect, it } from 'vitest';

import { findTaskLabelTool, getTaskLabelIds } from './taskLabelTool';

describe('findTaskLabelTool', () => {
  it('prefers rectTool when both rectangle and polygon tools exist', () => {
    expect(
      findTaskLabelTool([
        { tool: 'polygonTool', config: { attributes: [{ value: 'polygon' }] } },
        { tool: 'rectTool', config: { attributes: [{ value: 'rect' }] } },
      ])?.tool,
    ).toBe('rectTool');
  });

  it('uses polygonTool when rectTool was removed', () => {
    expect(findTaskLabelTool([{ tool: 'polygonTool', config: { attributes: [{ value: 'polygon' }] } }])?.tool).toBe(
      'polygonTool',
    );
  });

  it('uses the active polygon tool even when rectTool also exists', () => {
    const tools = [
      { tool: 'rectTool', config: { attributes: [{ labelId: 1 }] } },
      { tool: 'polygonTool', config: { attributes: [{ labelId: 10 }] } },
    ];

    expect(findTaskLabelTool(tools, 'polygonTool')?.tool).toBe('polygonTool');
    expect(getTaskLabelIds(tools, 'polygonTool')).toEqual([10]);
    expect(getTaskLabelIds(tools, 'rectTool')).toEqual([1]);
  });
});
