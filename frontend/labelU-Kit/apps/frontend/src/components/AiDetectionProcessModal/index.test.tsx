import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getAiProcess, type AiProcessResponse } from '@/api/services/samples';
import AiDetectionProcessModal from './index';

vi.mock('@/api/services/samples', () => ({ getAiProcess: vi.fn() }));

const mockedGetAiProcess = vi.mocked(getAiProcess);

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

function renderModal() {
  return render(<AiDetectionProcessModal open taskId={1} sampleId={71} onClose={() => undefined} />);
}

const processRecord: AiProcessResponse = {
  sample_id: 71,
  pre_annotation_id: 797,
  created_at: '2026-10-30 15:49:56',
  process: {
    provider: 'local_http',
    model: 'doubao-seed-2-1-pro-260915',
    endpoint_host: 'ark.cn-beijing.volces.com',
    request_protocol: 'responses',
    image_width: 3840,
    image_height: 2160,
    model_image_width: 2560,
    model_image_height: 1440,
    coordinate_space: 'normalized_0_1000',
    coordinate_scale: [2.56, 1.44],
    latency_ms: 22134,
    instruction: '请检测图片中的所有目标，并输出 person 坐标。',
    raw_response_text: '{"boxes":[{"label":"person"}]}',
    box_count: 1,
    dropped_items: [{ index: 1, tool: 'rectTool', reason: 'geometry y must be a number' }],
    repaired_items: [{ index: 2, tool: 'rectTool', reason: "recovered y from malformed key '128'" }],
    contract_prompt: {
      prompt_binding_id: 2,
      prompt_sha256: '6bec1234567890abcdef1234567890abcdef',
      contract_id: 1,
      contract_revision: 1,
    },
    boxes: [{ label: 'person', x: 0, y: 453, width: 64.5, height: 475.5, score: '0.95' }],
  },
};

beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(() => {
  cleanup();
  mockedGetAiProcess.mockReset();
  delete (globalThis as any).IS_REACT_ACT_ENVIRONMENT;
});

describe('AiDetectionProcessModal', () => {
  it('renders the sent prompt, raw response, coordinate space and parsed boxes', async () => {
    mockedGetAiProcess.mockResolvedValue({ data: processRecord });

    renderModal();

    expect(await screen.findByText('AI目标检测过程')).toBeTruthy();
    expect(await screen.findByText('请检测图片中的所有目标，并输出 person 坐标。')).toBeTruthy();
    expect(await screen.findByText('{"boxes":[{"label":"person"}]}')).toBeTruthy();
    expect(await screen.findByText('0-1000 归一化（已自动换算）')).toBeTruthy();
    // 解析后的框以表格呈现，包含标签、坐标与分数。
    expect(await screen.findByText('person')).toBeTruthy();
    expect(screen.getByText('64.5')).toBeTruthy();
    expect(screen.getByText('0.95')).toBeTruthy();
    // 丢弃 / 修复项展示原因。
    expect(screen.getByText(/geometry y must be a number/)).toBeTruthy();
    expect(screen.getByText(/recovered y from malformed key/)).toBeTruthy();
    expect(mockedGetAiProcess).toHaveBeenCalledWith(1, 71);
  });

  it('shows pixel coordinate space wording when the model returns original pixels', async () => {
    mockedGetAiProcess.mockResolvedValue({
      data: {
        ...processRecord,
        process: {
          ...processRecord.process!,
          coordinate_space: 'pixels',
          boxes: [{ label: 'person', x: 10, y: 20, width: 30, height: 40, score: 0.8 }],
        },
      },
    });

    renderModal();

    expect(await screen.findByText('原图像素')).toBeTruthy();
  });

  it('explains that legacy runs have no recorded process when process is null', async () => {
    mockedGetAiProcess.mockResolvedValue({
      data: {
        sample_id: 71,
        pre_annotation_id: null,
        created_at: '2026-10-30 15:49:56',
        process: null,
      },
    });

    renderModal();

    expect(await screen.findByText('本次运行未记录详细过程（旧版本数据或未执行成功）')).toBeTruthy();
  });
});
