import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import AlgorithmsPage, { AlgorithmDefinitionForm, parseAlgorithmJson } from './algorithms';

const resourcePageProps = vi.hoisted(() => ({ current: null as any }));

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
vi.mock('../project', () => ({
  usePlatformProject: () => ({ projectId: 1, projectsQuery: { isLoading: false, data: [] } }),
}));
vi.mock('../ResourcePage', () => ({
  default: (props: any) => {
    resourcePageProps.current = props;
    return (
      <div data-testid="resource">
        {props.beforeTable}
        {props.rowActions?.({
          id: 1,
          algorithm_key: 'demo',
          name: 'Demo',
          training_adapter: 'demo_adapter',
          task_type: 'object_detection',
        })}
      </div>
    );
  },
}));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedPatch = vi.mocked(request.patch);
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
afterEach(() => {
  cleanup();
  resourcePageProps.current = null;
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedPatch.mockReset();
});

it('passes the applied algorithm search to the server-backed resource query', async () => {
  mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AlgorithmsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(resourcePageProps.current?.queryFn).toBeTypeOf('function'));
  await resourcePageProps.current.queryFn(1, 2, 20, 'vehicle');
  expect(mockedGet).toHaveBeenCalledWith('/v1/algorithms', {
    params: { status: 'ACTIVE', page: 2, size: 20, q: 'vehicle' },
  });
});

it('reads direct capability responses and renders execution details', async () => {
  mockedGet.mockResolvedValue({
    data: {
      algorithm_key: 'demo',
      adapter_key: 'demo_adapter',
      execution_mode: 'worker',
      task_type: 'object_detection',
      supported_chips: ['BM1688'],
      stages: ['train'],
      implemented: ['export'],
      available: true,
      protocol_version: 'v1',
      worker: 'local',
    },
  } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AlgorithmsPage />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: /查看能力/ }));
  await waitFor(() => expect(screen.getByText('demo_adapter')).toBeTruthy());
  expect(screen.getByText('BM1688')).toBeTruthy();
  expect(screen.getByText('train')).toBeTruthy();
  expect(mockedGet).toHaveBeenCalledWith('/v1/algorithms/demo/capabilities');
});

it('validates algorithm contract JSON as an object', () => {
  expect(parseAlgorithmJson('{"media":["image"]}', '输入契约')).toEqual({ media: ['image'] });
  expect(parseAlgorithmJson('   ')).toBeUndefined();
  expect(() => parseAlgorithmJson('[]', '质量门禁')).toThrow('质量门禁必须是 JSON 对象');
});

it('registers a new algorithm with normalized contract fields', async () => {
  mockedGet.mockResolvedValue({
    data: { data: [{ id: 4, group_key: 'person_vehicle_detection', name: '人车组' }] },
  } as never);
  mockedPost.mockResolvedValue({ data: { data: { id: 9 } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AlgorithmsPage />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/algorithm-groups', expect.anything()));
  fireEvent.click(screen.getByLabelText('注册算法'));
  expect(screen.getByText('运行与能力')).toBeTruthy();
  expect(screen.getByText('契约与质量')).toBeTruthy();
  expect(screen.getByText('治理与发布')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('算法标识'), { target: { value: '  custom_demo ' } });
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: '自定义模型' } });
  fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'object_detection' } });
  fireEvent.change(screen.getByLabelText('训练适配器'), { target: { value: 'custom_container' } });
  fireEvent.change(screen.getByLabelText('评估适配器'), { target: { value: 'custom_container' } });
  fireEvent.change(screen.getByLabelText('支持芯片（逗号分隔）'), { target: { value: 'cpu, bm1688' } });
  fireEvent.click(document.querySelector('.ant-modal-footer .ant-btn-primary') as HTMLButtonElement);
  await waitFor(() =>
    expect(mockedPost).toHaveBeenCalledWith(
      '/v1/algorithms',
      expect.objectContaining({
        algorithm_key: 'custom_demo',
        algorithm_group_id: 4,
        supported_chips: ['cpu', 'bm1688'],
        training_adapter: 'custom_container',
      }),
    ),
  );
});

it('updates an existing algorithm without changing its key', async () => {
  mockedPatch.mockResolvedValue({ data: { data: { id: 9 } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <AlgorithmDefinitionForm
        algorithm={{
          id: 9,
          algorithm_key: 'demo',
          name: '旧名称',
          task_type: 'custom',
          training_adapter: 'old',
          evaluation_adapter: 'old',
          supported_chips: ['cpu'],
          algorithm_kind: 'model',
          status: 'ACTIVE',
        }}
        groups={[]}
        onClose={() => undefined}
        onSaved={() => undefined}
      />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText('名称'), { target: { value: '新名称' } });
  fireEvent.change(screen.getByLabelText('训练适配器'), { target: { value: 'custom_container' } });
  fireEvent.change(screen.getByLabelText('评估适配器'), { target: { value: 'custom_container' } });
  fireEvent.change(screen.getByLabelText('支持芯片（逗号分隔）'), { target: { value: 'cpu, bm1688' } });
  fireEvent.click(screen.getByRole('button', { name: '保存契约' }));
  await waitFor(() =>
    expect(mockedPatch).toHaveBeenCalledWith(
      '/v1/algorithms/9',
      expect.objectContaining({
        name: '新名称',
        supported_chips: ['cpu', 'bm1688'],
        training_adapter: 'custom_container',
      }),
    ),
  );
});
