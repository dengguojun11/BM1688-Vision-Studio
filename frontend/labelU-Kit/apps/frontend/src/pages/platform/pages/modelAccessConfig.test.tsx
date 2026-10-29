import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import request from '@/api/request';
import ModelAccessConfigPanel, { validateEndpoint } from './modelAccessConfig';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedPatch = vi.mocked(request.patch);

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

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPatch.mockReset();
});

function renderPanel() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ModelAccessConfigPanel />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

it('lists configured model services and explains native annotation paths', async () => {
  mockedGet.mockResolvedValue({
    data: {
      data: [
        {
          id: 'vlx-prod',
          name: '生产 VLX-Seek',
          provider_key: 'vlx_seek',
          capability: 'bbox_detection',
          endpoint: 'http://10.10.11.20:8099/api/infer',
          model: 'vlx-seek',
          enabled: true,
          last_check_status: 'passed',
          last_check_message: '服务响应正常',
        },
      ],
    },
  } as never);
  renderPanel();

  expect(await screen.findByText('生产 VLX-Seek')).toBeTruthy();
  expect(screen.getByText('目标检测预标注')).toBeTruthy();
  expect(screen.getByText('连通正常')).toBeTruthy();
  expect(screen.getByText(/YOLO 检测框和上传 JSON\/JSONL 预标注文件不依赖大模型/)).toBeTruthy();
  expect(mockedGet).toHaveBeenCalledWith('/v1/ai-model-configs', {
    params: { page: 0, size: 10 },
  });
});

it('searches on the server and accepts the runtime business envelope', async () => {
  mockedGet.mockImplementation(((_url: string, config?: { params?: Record<string, unknown> }) => {
    const query = String(config?.params?.q || '');
    const row = {
      id: query ? 'searched' : 'initial',
      name: query ? 'VLX 搜索配置' : '初始配置',
      provider_key: 'vlx_seek',
      capability: 'bbox_detection',
      endpoint: 'http://model.example/api/infer',
      enabled: true,
      last_check_status: 'PASSED',
    };
    return Promise.resolve({
      meta_data: { total: 1, page: config?.params?.page, size: 1 },
      data: { configs: [row] },
    });
  }) as never);
  renderPanel();

  const input = await screen.findByPlaceholderText('按配置名称、模型、地址或能力搜索');
  fireEvent.change(input, { target: { value: 'VLX' } });
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

  await waitFor(() => {
    expect(mockedGet.mock.calls.some(([, options]) => String((options as any)?.params?.q || '') === 'VLX')).toBe(true);
  });
  expect(await screen.findByText('VLX 搜索配置')).toBeTruthy();
});

it('creates a model access config from the system management dialog', async () => {
  mockedGet.mockResolvedValue({ data: { data: [] } } as never);
  mockedPost.mockResolvedValue({ data: { data: { id: 'tag-prod' } } } as never);
  renderPanel();

  fireEvent.click(await screen.findByText('新增模型配置'));
  fireEvent.change(screen.getByLabelText('配置名称'), { target: { value: '生产全局标签' } });
  fireEvent.change(screen.getByLabelText('服务地址'), { target: { value: 'https://10.10.11.20:8099/api/infer' } });
  fireEvent.click(screen.getByText('保存配置'));

  await waitFor(() =>
    expect(mockedPost).toHaveBeenCalledWith(
      '/v1/ai-model-configs',
      expect.objectContaining({
        name: '生产全局标签',
        provider_key: 'vision_llm',
        capability: 'vision_reasoning',
        endpoint: 'https://10.10.11.20:8099/api/infer',
        enabled: true,
      }),
    ),
  );
});

it('runs a connectivity check from the row action', async () => {
  mockedGet.mockResolvedValue({ data: { data: [{ id: 1, name: 'VLX', provider_key: 'vlx_seek', capability: 'bbox_detection', endpoint: 'http://localhost:8099', enabled: true }] } } as never);
  mockedPost.mockResolvedValue({ data: { data: { message: '服务响应正常' } } } as never);
  renderPanel();

  fireEvent.click(await screen.findByRole('button', { name: '连通性检查' }));
  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/ai-model-configs/1/test'));
});

it('requires the Coding Plan route for the Volcengine provider', () => {
  expect(validateEndpoint('https://ark.cn-beijing.volces.com/api/v3', 'volcengine')).toBeTruthy();
  expect(validateEndpoint('https://ark.cn-beijing.volces.com/api/coding', 'volcengine')).toBeTruthy();
  expect(validateEndpoint('https://ark.cn-beijing.volces.com/api/coding/v3', 'volcengine')).toBeUndefined();
  expect(validateEndpoint('https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions', 'volcengine')).toBeUndefined();
  expect(validateEndpoint('https://example.com/v1', 'vision_llm')).toBeUndefined();
});
