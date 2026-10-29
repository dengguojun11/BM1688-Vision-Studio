import { describe, expect, it } from 'vitest';

import {
  isUploadedPreAnnotationRow,
  selectCanvasPreAnnotationRow,
  type PreAnnotationListRow,
} from './selectCanvasPreAnnotation';

describe('selectCanvasPreAnnotationRow', () => {
  const uploaded: PreAnnotationListRow = {
    id: 1,
    file: { id: 10, filename: 'pre.json' },
    data: JSON.stringify({
      sample_name: 'a.jpg',
      annotations: { rectTool: { toolName: 'rectTool', result: [{ id: 'u1' }] } },
    }),
  };

  const aiOnly: PreAnnotationListRow = {
    id: 2,
    data: JSON.stringify({
      meta: { source_type: 'ai_generated' },
      annotations: { tagTool: { toolName: 'tagTool', result: [] } },
    }),
  };

  const experimentDatasetRect: PreAnnotationListRow = {
    id: 3,
    data: JSON.stringify({
      meta: { source_type: 'experiment_dataset_metadata' },
      annotations: { rectTool: { toolName: 'rectTool', result: [{ id: 'r1' }] } },
    }),
  };

  const tagtoolOnly: PreAnnotationListRow = {
    id: 5,
    data: JSON.stringify({
      meta: { source_type: 'tagtool_cls_generated' },
      annotations: {
        tagTool: {
          toolName: 'tagTool',
          result: [{ id: 'tag-1', type: 'tag', value: { scene: ['illegal'] } }],
        },
      },
    }),
  };

  const vlxRect: PreAnnotationListRow = {
    id: 6,
    data: JSON.stringify({
      meta: { source_type: 'vlx_seek_generated', preserve_global_annotations: true },
      annotations: { rectTool: { toolName: 'rectTool', result: [{ id: 'box-1' }] } },
    }),
  };

  const explicitYoloOverwrite: PreAnnotationListRow = {
    id: 4,
    data: JSON.stringify({
      meta: { source_type: 'yolo_generated', apply_to_canvas_overwrite: true },
      annotations: {},
    }),
  };

  const explicitRexOverwrite: PreAnnotationListRow = {
    id: 7,
    data: JSON.stringify({
      meta: { source_type: 'rex_omni_generated', apply_to_canvas_overwrite: true },
      annotations: {},
    }),
  };

  it('prefers uploaded row when AI row was added later', () => {
    expect(selectCanvasPreAnnotationRow([uploaded, aiOnly])).toBe(uploaded);
  });

  it('uses generated row when no upload exists', () => {
    expect(selectCanvasPreAnnotationRow([aiOnly])).toBe(aiOnly);
  });

  it('uses explicit YOLO overwrite even when an uploaded row exists and YOLO produced zero boxes', () => {
    expect(selectCanvasPreAnnotationRow([uploaded, explicitYoloOverwrite])).toBe(explicitYoloOverwrite);
  });

  it('uses the latest Rex result even when Rex produced zero boxes', () => {
    expect(selectCanvasPreAnnotationRow([experimentDatasetRect, explicitRexOverwrite])).toBe(explicitRexOverwrite);
  });

  it('keeps generated global tags when Rex replaces the geometry canvas', () => {
    const rexWithBox: PreAnnotationListRow = {
      id: 8,
      data: JSON.stringify({
        meta: {
          source_type: 'rex_omni_generated',
          apply_to_canvas_overwrite: true,
          preserve_global_annotations: true,
        },
        annotations: { rectTool: { toolName: 'rectTool', result: [{ id: 'rex-box' }] } },
      }),
    };

    const selected = selectCanvasPreAnnotationRow([tagtoolOnly, rexWithBox]);
    const data = JSON.parse(selected?.data ?? '{}');
    expect(data.annotations.rectTool.result).toEqual([{ id: 'rex-box' }]);
    expect(data.annotations.tagTool.result).toEqual([{ id: 'tag-1', type: 'tag', value: { scene: ['illegal'] } }]);
  });

  it('prefers geometry row over newer tag-only generated row', () => {
    expect(selectCanvasPreAnnotationRow([experimentDatasetRect, aiOnly])).toBe(experimentDatasetRect);
  });

  it('merges global tag results into a newer VLX geometry row', () => {
    const selected = selectCanvasPreAnnotationRow([tagtoolOnly, vlxRect]);
    expect(selected).not.toBe(vlxRect);
    const data = JSON.parse(selected?.data ?? '{}');
    expect(data.annotations.rectTool.result).toHaveLength(1);
    expect(data.annotations.tagTool.result).toEqual([{ id: 'tag-1', type: 'tag', value: { scene: ['illegal'] } }]);
  });

  it('detects uploaded by file attachment', () => {
    expect(isUploadedPreAnnotationRow(uploaded)).toBe(true);
    expect(isUploadedPreAnnotationRow(aiOnly)).toBe(false);
  });
});
