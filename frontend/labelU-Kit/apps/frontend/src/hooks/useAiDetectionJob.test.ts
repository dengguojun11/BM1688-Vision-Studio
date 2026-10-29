import { createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { message } from 'antd';

import * as api from '@/api/services/samples';
import type { PreannotateJobResponse } from '@/api/types';
import { usePreannotateJob } from './usePreannotateJob';

vi.mock('antd', () => {
  const message = { success: vi.fn(), warning: vi.fn(), error: vi.fn() };
  return { message, default: { message } };
});
vi.mock('@/api/services/samples', () => ({
  autoLabelSample: vi.fn(),
  createAutoLabelJob: vi.fn(),
  createTagtoolPreannotateJob: vi.fn(),
  createProviderPreannotateJob: vi.fn(),
  createYoloPreannotateJob: vi.fn(),
  getActiveAutoLabelJob: vi.fn(),
  getAutoLabelJobStatus: vi.fn(),
  getActiveProviderPreannotateJob: vi.fn(),
  getActiveTagtoolPreannotateJob: vi.fn(),
  getActiveYoloPreannotateJob: vi.fn(),
  getTagtoolPreannotateJobStatus: vi.fn(),
  getTagtoolPreannotateModels: vi.fn(),
  getYoloPreannotateJobStatus: vi.fn(),
  getYoloPreannotateModels: vi.fn(),
  getProviderPreannotateJobStatus: vi.fn(),
}));

const completed: PreannotateJobResponse = {
  id: 71,
  task_id: 1,
  status: 'COMPLETED',
  sample_count: 1,
  processed_count: 1,
  success_count: 1,
  annotated_count: 1,
  empty_count: 0,
  failed_count: 0,
};

describe('AI detection backend selection', () => {
  let root: Root;
  let container: HTMLDivElement;
  let hook: ReturnType<typeof usePreannotateJob>;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.mocked(api.createYoloPreannotateJob).mockResolvedValue({ data: { ...completed, status: 'PENDING' } });
    vi.mocked(api.getYoloPreannotateJobStatus).mockResolvedValue({ data: completed });
    vi.mocked(api.createAutoLabelJob).mockResolvedValue({ data: { ...completed, status: 'PENDING' } });
    vi.mocked(api.getAutoLabelJobStatus).mockResolvedValue({ data: completed });
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllTimers();
    vi.useRealTimers();
    delete (globalThis as any).IS_REACT_ACT_ENVIRONMENT;
  });

  async function mount(mode: 'task' | 'sample') {
    function Harness() {
      hook = usePreannotateJob({ type: 'ai_detection', mode });
      return null;
    }
    await act(async () => root.render(createElement(Harness)));
  }

  async function loadModels(items: any[]) {
    vi.mocked(api.getYoloPreannotateModels).mockResolvedValue({ data: { items } });
    await act(async () => hook.loadModels(1));
  }

  async function runJob(sampleId?: number, onCompleted = vi.fn()) {
    await act(async () => {
      const pending = hook.start({ taskId: 1, sampleId, onCompleted });
      await vi.advanceTimersByTimeAsync(1200);
      await pending;
    });
    return onCompleted;
  }

  it('blocks execution before the compatible model list has resolved', async () => {
    await mount('sample');
    await act(async () => hook.start({ taskId: 1, sampleId: 10 }));
    expect(api.autoLabelSample).not.toHaveBeenCalled();
    expect(api.createYoloPreannotateJob).not.toHaveBeenCalled();
    expect(api.createAutoLabelJob).not.toHaveBeenCalled();
  });

  it('selects the first server-ranked detector for a single image and polls the YOLO job', async () => {
    await mount('sample');
    await loadModels([
      {
        kind: 'imported', id: 4, title: 'full-coverage', imgsz: 640, recommended_conf: 0.25,
        matched_labels: ['person', 'vehicle'], missing_task_labels: [],
      },
      {
        kind: 'trained', id: 7, title: 'partial-coverage', recommended_conf: 0.95,
        matched_labels: ['person'], missing_task_labels: ['vehicle'],
      },
    ]);
    expect(hook.selectedModel?.id).toBe(4);
    const onCompleted = await runJob(10);
    expect(api.createYoloPreannotateJob).toHaveBeenCalledWith(1, expect.objectContaining({
      model_kind: 'imported', model_id: 4, sample_ids: [10], sample_id: 10,
      imgsz: 2560, conf: 0.15, agnostic_nms: true, apply_to_canvas_overwrite: true,
    }), 10);
    expect(api.getYoloPreannotateJobStatus).toHaveBeenCalledWith(1, 71);
    expect(api.getAutoLabelJobStatus).not.toHaveBeenCalled();
    expect(api.autoLabelSample).not.toHaveBeenCalled();
    expect(onCompleted).toHaveBeenCalledWith(completed);
  });

  it('uses the same dedicated detector for a whole-task run and preserves larger input sizes', async () => {
    await mount('task');
    await loadModels([{
      kind: 'trained', id: 8, title: 'detector', imgsz: 2560,
      matched_labels: ['person'], missing_task_labels: [],
    }]);
    await runJob();
    expect(api.createYoloPreannotateJob).toHaveBeenCalledWith(1, expect.objectContaining({
      model_kind: 'trained', model_id: 8, include_done_samples: true,
      imgsz: 2560, agnostic_nms: true, apply_to_canvas_overwrite: false,
    }), undefined);
    expect(api.createAutoLabelJob).not.toHaveBeenCalled();
  });

  it('prefers a partial compatible detector and leaves missing classes for explicit review', async () => {
    await mount('sample');
    await loadModels([{
      kind: 'imported', id: 4, title: 'partial-detector', imgsz: 1280,
      matched_labels: ['person'], missing_task_labels: ['person_motorbike'],
    }]);
    expect(hook.selectedModel?.id).toBe(4);
    await runJob(10);

    expect(api.createYoloPreannotateJob).toHaveBeenCalledWith(1, expect.objectContaining({
      model_kind: 'imported', model_id: 4, imgsz: 2560, conf: 0.15,
      agnostic_nms: true, apply_to_canvas_overwrite: true,
    }), 10);
    expect(api.autoLabelSample).not.toHaveBeenCalled();
  });

  it('uses the configured vision model only after a successful empty detector list and displays its warning', async () => {
    await mount('sample');
    await loadModels([]);
    vi.mocked(api.autoLabelSample).mockResolvedValue({ data: {
      status: 'COMPLETED', task_id: 1, sample_id: 10, provider: 'volcengine', media_type: 'IMAGE',
      warning_message: '未生成有效目标框，请人工复核',
    } } as any);
    await runJob(10);
    expect(api.autoLabelSample).toHaveBeenCalledWith(1, 10, {
      filter_by_labels: true, include_geometry_tools: true, overwrite_existing_annotations: false,
    });
    expect(message.warning).toHaveBeenCalledWith('未生成有效目标框，请人工复核');
    expect(message.success).not.toHaveBeenCalled();
    expect(api.createYoloPreannotateJob).not.toHaveBeenCalled();
  });

  it('keeps the whole-task vision fallback and polls its own job endpoint', async () => {
    await mount('task');
    await loadModels([]);
    await runJob();
    expect(api.createAutoLabelJob).toHaveBeenCalledWith(1, expect.objectContaining({
      mode: 'BOX_AND_TAGS', include_geometry_tools: true, include_done_samples: true,
      overwrite_existing_annotations: false, skip_complete_global_tags: false,
    }));
    expect(api.getAutoLabelJobStatus).toHaveBeenCalledWith(1, 71);
    expect(api.getYoloPreannotateJobStatus).not.toHaveBeenCalled();
  });

  it('does not treat a detector-list failure as permission to run paid vision inference', async () => {
    await mount('sample');
    vi.mocked(api.getYoloPreannotateModels).mockRejectedValue(new Error('list unavailable'));
    await act(async () => hook.loadModels(1));
    expect(hook.modelsResolved).toBe(false);
    await runJob(10);
    expect(api.autoLabelSample).not.toHaveBeenCalled();
    expect(api.createYoloPreannotateJob).not.toHaveBeenCalled();
    expect(message.error).toHaveBeenCalledWith('模型列表加载失败');
  });

  it('reports a non-completed vision response instead of reporting success', async () => {
    await mount('sample');
    await loadModels([]);
    vi.mocked(api.autoLabelSample).mockResolvedValue({ data: {
      status: 'FAILED', warning_message: '响应中没有有效检测结果',
    } } as any);
    const onCompleted = await runJob(10);
    expect(message.error).toHaveBeenCalledWith('响应中没有有效检测结果');
    expect(message.success).not.toHaveBeenCalled();
    expect(onCompleted).not.toHaveBeenCalled();
  });
});
