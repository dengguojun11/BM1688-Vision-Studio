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
  const parts = {
    Button,
    Modal,
    Alert,
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

  it('shows the configured Ark detector without loading a YOLO model', async () => {
    state.selectedModel = {
      kind: 'imported', id: 4, title: '道路专用检测模型', imgsz: 640,
      matched_labels: ['car', 'person'], missing_task_labels: ['person_motorbike', 'person_tricycle'],
    };
    await open();
    expect(container.textContent).toContain('火山方舟');
    expect(container.textContent).toContain('doubao-seed-2-1-pro-260915');
    expect(container.textContent).toContain('置信度阈值 0.15');
    expect(container.textContent).not.toContain('道路专用检测模型');
  });

  it('looks up the ai_bbox_detection capability to show the actual Ark model when no detector is compatible', async () => {
    await open();
    expect(container.textContent).toContain('火山方舟');
    expect(container.textContent).toContain('doubao-seed-2-1-pro-260915');
    expect(container.textContent).toContain('ark.cn-beijing.volces.com');
  });

  it('does not block execution on a YOLO model list', async () => {
    state.loadingModels = true;
    state.modelsResolved = false;
    await open();
    const run = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === '开始执行');
    expect(run?.disabled).toBe(false);
    expect(container.textContent).not.toContain('正在选择兼容的目标检测模型');
  });

  it('keeps the current-image AI dialog open after completion and offers the process detail', async () => {
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
    await act(async () => action?.click());

    const run = Array.from(container.querySelectorAll('button')).find((item) => item.textContent === '开始执行');
    await act(async () => {
      run?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(getAiProcess).toHaveBeenCalledWith(1, 10);
    // 主弹窗保持打开，并展示完成提示与「点击详细查看」入口。
    expect(container.textContent).toContain('AI目标检测完成，生成 13 个框');
    const detailButton = Array.from(container.querySelectorAll('button')).find(
      (item) => item.textContent === '点击详细查看',
    );
    expect(detailButton).toBeTruthy();
  });
});
