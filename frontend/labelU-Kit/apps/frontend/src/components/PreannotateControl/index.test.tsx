import { createElement, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getAiProcess, getPreannotationCapabilities } from '@/api/services/samples';
import { usePreannotateJob } from '@/hooks/usePreannotateJob';
import PreannotateControl from './index';

vi.mock('@/api/services/samples', () => ({
  getPreannotationCapabilities: vi.fn(),
  getAiProcess: vi.fn(),
}));
vi.mock('@/hooks/usePreannotateJob', () => ({
  usePreannotateJob: vi.fn(),
  AI_DETECTION_DEFAULT_CONF: 0.15,
  getDefaultIncludeDoneSamples: () => true,
  aiDetectionImageSize: (model: { imgsz?: number }) => Math.max(2560, model.imgsz || 0),
}));
vi.mock('antd', () => {
  const Text = ({ children }: { children?: ReactNode }) => createElement('span', null, children);
  const Button = ({ children, disabled, onClick }: any) => createElement('button', { disabled, onClick }, children);
  const Modal = ({ children, open, onOk, okButtonProps }: any) => open
    ? createElement('section', null, children, createElement('button', { ...okButtonProps, loading: undefined, onClick: onOk }, '开始执行'))
    : null;
  const Alert = ({ message, description }: any) => createElement('div', { role: 'alert' }, message, description);
  const Popover = ({ children, content, open }: any) => createElement('div', null, children, open ? content : null);
  const parts = {
    Button,
    Modal,
    Alert,
    Popover,
    Checkbox: Text,
    InputNumber: Text,
    Progress: Text,
    Select: Text,
    Descriptions: Text,
    Empty: Text,
    Spin: Text,
    Table: Text,
    Typography: { Text, Title: Text },
  };
  return { ...parts, default: parts };
});

describe('AI detection service summary', () => {
  let root: Root;
  let container: HTMLDivElement;
  let state: ReturnType<typeof usePreannotateJob>;

  beforeEach(() => {
    vi.resetAllMocks();
    (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    state = {
      modelOptions: [], selectedModel: undefined, selectedRecommendedConf: null,
      selectedModelKey: undefined, setSelectedModelKey: vi.fn(), models: [],
      loadingModels: false, modelsResolved: true, isRunning: false, progress: null,
      loadModels: vi.fn().mockResolvedValue(undefined), resumeActive: vi.fn(), start: vi.fn(),
    } as any;
    vi.mocked(usePreannotateJob).mockImplementation(() => state);
    vi.mocked(getPreannotationCapabilities).mockResolvedValue({ data: { items: [{
      key: 'ai_bbox_detection', label: 'AI目标检测', visible: true, available: true,
      endpoint: 'https://ark.cn-beijing.volces.com/api/coding/v3',
      models: [{ model: 'doubao-seed-2-1-pro-260915' }],
    }] } } as any);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    delete (globalThis as any).IS_REACT_ACT_ENVIRONMENT;
  });

  async function open() {
    await act(async () => root.render(createElement(PreannotateControl, { taskId: 1, sampleId: 10, type: 'ai_detection', mode: 'sample' })));
    const action = container.querySelector('button');
    expect(action?.textContent).toBe('AI目标检测(当前图片)');
    await act(async () => action?.click());
  }

  it('starts current-image AI detection immediately without a second confirmation click', async () => {
    state.start = vi.fn();
    await open();
    expect(state.start).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain('开始执行');
  });

  it('does not block current-image AI detection on a YOLO model list', async () => {
    state.loadingModels = true;
    state.modelsResolved = false;
    state.start = vi.fn();
    await open();
    expect(state.start).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain('正在选择兼容的目标检测模型');
  });

  it('keeps the completion bubble open and offers the process detail', async () => {
    const onCompleted = vi.fn();
    vi.mocked(getAiProcess).mockResolvedValue({ data: { sample_id: 10, process: { box_count: 13 } } } as never);
    state.start = vi.fn(async (options: any) => {
      await options.onCompleted?.({
        id: 0,
        task_id: 1,
        status: 'COMPLETED',
        sample_count: 1,
        processed_count: 1,
        success_count: 1,
        failed_count: 0,
      });
    });

    await act(async () =>
      root.render(
        createElement(PreannotateControl, {
          taskId: 1,
          sampleId: 10,
          type: 'ai_detection',
          mode: 'sample',
          onCompleted,
        }),
      ),
    );
    const action = container.querySelector('button');
    await act(async () => {
      action?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(getAiProcess).toHaveBeenCalledWith(1, 10);
    expect(container.textContent).toContain('AI目标检测完成，生成 13 个框');
    const detailButton = Array.from(container.querySelectorAll('button')).find(
      (item) => item.textContent === '点击详细查看',
    );
    expect(detailButton).toBeTruthy();
  });
});
