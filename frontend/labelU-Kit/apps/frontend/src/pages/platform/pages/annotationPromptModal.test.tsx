import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import { AnnotationPromptModal } from './annotationPromptModal';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

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

const contract = {
  id: 41,
  revision: 2,
  annotation_kind: 'bbox',
  label_scope_mode: 'FULL',
  labels: [{ label_id: 18, label_key: '人员', label_value: 'person' }],
  label_set_signature: 'labels-signature',
  class_order_signature: 'order-signature',
};

const latestContract = {
  ...contract,
  revision: 2,
};

function currentContractResponse(value = latestContract) {
  return { data: { data: value } } as never;
}

function renderModal() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AnnotationPromptModal dataset={{ id: 7, name: '道路数据' }} contract={contract} open onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
});

describe('AnnotationPromptModal', () => {
  it('loads cross-revision history and opens an INVALIDATED prompt detail', async () => {
    const currentPrompt = {
      id: 101,
      contract_revision: 2,
      operation: 'bbox_detection',
      status: 'DRAFT',
      effective_prompt: '当前 person 提示词',
    };
    const oldPrompt = {
      id: 90,
      contract_revision: 1,
      operation: 'bbox_detection',
      status: 'INVALIDATED',
      effective_prompt: '旧版 person 提示词',
      prompt_sha256: 'old-sha',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.endsWith('/label-contracts/current')) return currentContractResponse();
      if (path.endsWith('/ai-prompts/history')) return { data: { data: [oldPrompt, currentPrompt] } } as never;
      return { data: { data: [currentPrompt] } } as never;
    });

    renderModal();

    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/datasets/7/ai-prompts/history'),
    );
    expect(await screen.findByText('已失效（INVALIDATED）')).toBeTruthy();
    expect(screen.getByText('提示词 #90 · 契约 v1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看提示词版本 90' }));

    expect(await screen.findByDisplayValue('旧版 person 提示词')).toBeTruthy();
    expect(screen.getAllByText('已失效（INVALIDATED）').length).toBeGreaterThanOrEqual(2);
  });

  it('sends a selected image to the trial endpoint and renders the model result', async () => {
    const draft = {
      id: 101,
      contract_revision: 2,
      operation: 'bbox_detection',
      status: 'DRAFT',
      effective_prompt: '按 {{image_width}}x{{image_height}} 只输出 person 框。',
      prompt_sha256: 'draft-sha',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.endsWith('/label-contracts/current')) return currentContractResponse();
      return { data: { data: [draft] } } as never;
    });
    mockedPost.mockImplementation(async (path: string) => {
      if (path.endsWith('/trial')) {
        return {
          data: {
            data: {
              status: 'COMPLETED',
              prompt_id: 101,
              image_width: 640,
              image_height: 480,
      model_image_width: 320,
      model_image_height: 240,
              latency_ms: 42,
              effective_prompt: '按 {{image_width}}x{{image_height}} 只输出 person 框。',
              rendered_prompt: '按 320x240 只输出 person 框。',
              model_response: { rectTool: { result: [{ attribute: 'person' }] } },
              normalized_result: {
                annotations: {
                  rectTool: {
                    toolName: 'rectTool',
                    result: [
                      { id: 'box-1', label: 'person', x: 64, y: 48, width: 128, height: 160, attributes: { score: '0.950' } },
                      { id: 'box-2', label: 'person', x: 320, y: 120, width: 96, height: 144, attributes: { score: '0.870' } },
                    ],
                  },
                },
              },
            },
          },
        } as never;
      }
      return { data: { data: draft } } as never;
    });

    renderModal();
    await waitFor(() => expect((screen.getByLabelText('AI提示词编辑器') as HTMLTextAreaElement).value).toBe(draft.effective_prompt));

    const image = new File(['fake-image'], 'sample.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByLabelText('试运行图片'), { target: { files: [image] } });
    fireEvent.click(screen.getByRole('button', { name: '试运行一张图片' }));

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith(
      '/v1/datasets/7/label-contracts/2/ai-prompts/trial',
      expect.any(FormData),
      { timeout: 120_000 },
    ));
    const trialCall = mockedPost.mock.calls.find(([path]) => String(path).endsWith('/trial'));
    const formData = trialCall?.[1] as FormData;
    expect(formData.get('image')).toBe(image);
    expect(formData.get('prompt_id')).toBe('101');
    expect(formData.get('prompt_override')).toBe(draft.effective_prompt);
    expect(await screen.findByText('试运行完成')).toBeTruthy();
    expect(await screen.findByText(/640×480/)).toBeTruthy();
    expect(screen.getByLabelText('最终发送给模型的提示词').textContent).toContain('按 320x240 只输出 person 框。');
    expect(await screen.findByLabelText('试运行图片标注预览')).toBeTruthy();
    expect(screen.getByLabelText('试运行标注框数量').textContent).toContain('共 2 个拉框');
    expect(screen.getAllByTestId('trial-bounding-box').length).toBe(2);
    expect(screen.getAllByTestId('trial-bounding-box-label').map((node) => node.textContent)).toEqual([
      'person · 0.950',
      'person · 0.870',
    ]);

    fireEvent.change(screen.getByLabelText('AI提示词编辑器'), {
      target: { value: '按 {{image_width}}x{{image_height}} 只输出 vehicle 框。' },
    });
    expect(screen.getByLabelText('最终发送给模型的提示词').textContent).toContain('按 320x240 只输出 vehicle 框。');
  });

  it('shows zero boxes when the model returns no contract targets', async () => {
    const draft = {
      id: 102,
      contract_revision: 2,
      operation: 'bbox_detection',
      status: 'DRAFT',
      effective_prompt: '只输出 person 框。',
      prompt_sha256: 'draft-zero-sha',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.endsWith('/label-contracts/current')) return currentContractResponse();
      return { data: { data: [draft] } } as never;
    });
    mockedPost.mockImplementation(async (path: string) => {
      if (path.endsWith('/trial')) {
        return {
          data: {
            data: {
              status: 'COMPLETED',
              prompt_id: 102,
              image_width: 100,
              image_height: 80,
              rendered_prompt: '只输出 person 框。',
              normalized_result: { annotations: { rectTool: { result: [] } } },
            },
          },
        } as never;
      }
      return { data: { data: draft } } as never;
    });

    renderModal();
    await waitFor(() => expect((screen.getByLabelText('AI提示词编辑器') as HTMLTextAreaElement).value).toBe(draft.effective_prompt));
    const image = new File(['fake-image'], 'empty.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByLabelText('试运行图片'), { target: { files: [image] } });
    fireEvent.click(screen.getByRole('button', { name: '试运行一张图片' }));

    const count = await screen.findByLabelText('试运行标注框数量');
    expect(count.textContent).toBe('共 0 个拉框');
    expect(await screen.findByText('模型未检测到契约内目标，当前图片标注了 0 个拉框。')).toBeTruthy();
    expect(screen.queryAllByTestId('trial-bounding-box').length).toBe(0);
  });

  it('switches from a stale parent contract to the latest revision before loading prompts', async () => {
    const current = {
      ...contract,
      id: 42,
      revision: 3,
      labels: [{ label_id: 18, label_key: '行人', label_value: 'person' }],
      label_set_signature: 'latest-labels-signature',
      class_order_signature: 'latest-order-signature',
    };
    const draft = {
      id: 103,
      contract_revision: 3,
      operation: 'bbox_detection',
      status: 'DRAFT',
      effective_prompt: '只输出 person 框。',
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path.endsWith('/label-contracts/current')) return currentContractResponse(current);
      if (path.endsWith('/ai-prompts/history')) return { data: { data: [] } } as never;
      expect(path).toBe('/v1/datasets/7/label-contracts/3/ai-prompts');
      return { data: { data: [draft] } } as never;
    });

    renderModal();

    expect(await screen.findByText('道路数据 · 契约 v3')).toBeTruthy();
    expect(await screen.findByText('行人')).toBeTruthy();
    expect(await screen.findByText('person')).toBeTruthy();
    expect(await screen.findByDisplayValue('只输出 person 框。')).toBeTruthy();
  });
});
