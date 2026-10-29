import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';
import AdminPage, { nextAvailableLabelColor } from './admin';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
const projectState = vi.hoisted(() => ({
  projectId: 1 as number | undefined,
  projectsQuery: { isLoading: false, data: [{ id: 1, name: '本地项目' }], refetch: vi.fn().mockResolvedValue({}) },
}));
vi.mock('../project', () => ({ usePlatformProject: () => projectState }));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedPatch = vi.mocked(request.patch);
const mockedPut = vi.mocked(request.put);
const mockedDelete = vi.mocked(request.delete);

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
  mockedPut.mockReset();
  mockedDelete.mockReset();
  window.localStorage.removeItem('token');
  projectState.projectId = 1;
  projectState.projectsQuery.data = [{ id: 1, name: '本地项目' }];
  projectState.projectsQuery.refetch.mockClear();
});

it('keeps governance to the label library and dataset management', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/annotation-taxonomy/labels') {
      return {
        data: {
          data: [{ id: 1, label_key: '人员', label_value: 'person', annotation_kind: 'bbox', status: 'active' }],
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('人员')).toBeTruthy());
  expect(mockedGet).toHaveBeenCalledWith('/v1/annotation-taxonomy/labels', {
    params: { page: 0, size: 10, include_disabled: true },
  });
  expect(mockedGet.mock.calls.some(([url]) => url === '/v1/annotation-taxonomy/dataset-categories')).toBe(false);
  expect(mockedGet.mock.calls.some(([url]) => url === '/v1/users' || url === '/v1/organizations')).toBe(false);
  expect(screen.queryByText('单一标签源')).toBeNull();
  expect(
    screen.queryByText(
      '任务和训练直接使用标签库中的标签。标注预设仅作为可选快捷组合，全局维度属于样本覆盖信息；两者暂不作为日常维护入口，后端兼容接口继续保留。',
    ),
  ).toBeNull();
  expect(screen.getAllByText(/标签库/).length).toBeGreaterThan(0);
  expect(screen.getByText('数据集管理')).toBeTruthy();
  expect(screen.queryByText(/数据集分类/)).toBeNull();
});

it('searches the label library by Chinese or English value through the server query', async () => {
  mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/v1/annotation-taxonomy/labels') {
      const keyword = config?.params?.q;
      return {
        data: {
          data: keyword
            ? [{ id: 2, label_key: '车辆', label_value: 'vehicle', annotation_kind: 'bbox', status: 'active' }]
            : [{ id: 1, label_key: '人员', label_value: 'person', annotation_kind: 'bbox', status: 'active' }],
          meta_data: { total: 1 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('人员')).toBeTruthy());
  const search = screen.getByRole('textbox', { name: '搜索标签库' });
  fireEvent.change(search, { target: { value: 'vehicle' } });
  fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });

  await waitFor(() => {
    expect(mockedGet).toHaveBeenCalledWith('/v1/annotation-taxonomy/labels', {
      params: { page: 0, size: 10, include_disabled: true, q: 'vehicle' },
    });
  });
  expect(await screen.findByText('车辆')).toBeTruthy();
  expect(screen.queryByText('人员')).toBeNull();

  fireEvent.change(search, { target: { value: '' } });
  await waitFor(() => {
    expect(mockedGet).toHaveBeenCalledWith('/v1/annotation-taxonomy/labels', {
      params: { page: 0, size: 10, include_disabled: true },
    });
  });
});

it('keeps the label library total and requests the next server page', async () => {
  mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/v1/annotation-taxonomy/labels') {
      const page = config?.params?.page;
      return {
        data: {
          data: [
            {
              id: page === 1 ? 11 : 1,
              label_key: page === 1 ? '第二页标签' : '第一页标签',
              label_value: page === 1 ? 'second_page' : 'first_page',
              annotation_kind: 'bbox',
              status: 'active',
            },
          ],
          meta_data: { total: 11, page, size: 10 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('第一页标签')).toBeTruthy());
  expect(screen.getByText('共 11 个标签')).toBeTruthy();
  const nextPage = screen.getByTitle('Next Page');
  expect(nextPage.getAttribute('aria-disabled')).toBe('false');
  fireEvent.click(nextPage);

  await waitFor(() => expect(screen.getByText('第二页标签')).toBeTruthy());
  expect(mockedGet).toHaveBeenLastCalledWith('/v1/annotation-taxonomy/labels', {
    params: { page: 1, size: 10, include_disabled: true },
  });
});

it('keeps global tool tags separate from the label library', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/annotation-taxonomy/labels') {
      return { data: { data: [] } } as never;
    }
    if (url === '/v1/annotation-taxonomy/global-tag-dimensions') {
      return {
        data: {
          data: [
            {
              id: 11,
              field_key: 'sceneType',
              name_cn: '场景类型',
              sort_order: 0,
              options: [{ id: 1, name_cn: '城市主干道', value_en: 'urbanArterial', sort_order: 0 }],
            },
          ],
          meta_data: { total: 1 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance&tab=global-tools']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  expect(screen.getByRole('tab', { name: '全局工具标签' })).toBeTruthy();
  await waitFor(() => expect(screen.getByText('场景类型')).toBeTruthy());
  expect(mockedGet).toHaveBeenCalledWith('/v1/annotation-taxonomy/global-tag-dimensions', {
    params: { page: 0, size: 10 },
  });
  expect(mockedGet.mock.calls.some(([url]) => url === '/v1/annotation-taxonomy/labels')).toBe(false);
  expect(screen.getByText('sceneType')).toBeTruthy();
  expect(screen.getByRole('button', { name: /新建全局工具标签/ })).toBeTruthy();
});

it('derives a new global tool tag sort order from the server total, not the current page length', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/annotation-taxonomy/global-tag-dimensions') {
      return {
        data: {
          data: [{ id: 11, field_key: 'lastType', name_cn: '末页维度', sort_order: 10, options: [] }],
          meta_data: { total: 11, page: 0, size: 10 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance&tab=global-tools']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('末页维度')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /新建全局工具标签/ }));
  await waitFor(() => {
    const input = screen.getByRole('spinbutton', { name: '显示顺序' }) as HTMLInputElement;
    expect(input.value).toBe('11');
  });
});

it('searches global tool dimensions by Chinese, English, and option values', async () => {
  mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/v1/annotation-taxonomy/global-tag-dimensions') {
      const keyword = config?.params?.q;
      return {
        data: {
          data: keyword
            ? [
                {
                  id: 12,
                  field_key: 'weatherType',
                  name_cn: '天气类型',
                  sort_order: 0,
                  options: [{ id: 2, name_cn: '晴天', value_en: 'sunny', sort_order: 0 }],
                },
              ]
            : [],
          meta_data: { total: keyword ? 1 : 0 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance&tab=global-tools']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  const search = await screen.findByRole('textbox', { name: '搜索全局工具标签' });
  fireEvent.change(search, { target: { value: 'sunny' } });
  fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
  await waitFor(() => {
    expect(mockedGet).toHaveBeenCalledWith('/v1/annotation-taxonomy/global-tag-dimensions', {
      params: { page: 0, size: 10, q: 'sunny' },
    });
  });
  expect(await screen.findByText('天气类型')).toBeTruthy();
  expect(screen.getByText('weatherType')).toBeTruthy();
});

it('provides dataset management inside annotation and data governance', async () => {
  window.localStorage.setItem('token', 'test-token');
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/datasets') {
      return {
        data: {
          data: [{ id: 7, name: '占道经营', dataset_category_name: '占道经营', status: 'ACTIVE', version_count: 1 }],
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance&tab=datasets']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  expect(screen.getByRole('tab', { name: '数据集管理' })).toBeTruthy();
  await waitFor(() => expect(screen.getByTestId('admin-dataset-management')).toBeTruthy());
  await waitFor(() => expect(screen.getAllByText('占道经营').length).toBeGreaterThanOrEqual(1));
  expect(screen.getByRole('button', { name: '标签契约' })).toBeTruthy();
  expect(screen.getByRole('button', { name: '版本治理' })).toBeTruthy();
  expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
    params: { page: 0, size: 20 },
  });
});

it('searches datasets by a server-side Chinese or English keyword', async () => {
  window.localStorage.setItem('token', 'test-token');
  mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/v1/datasets') {
      const keyword = config?.params?.q;
      return {
        data: {
          data: keyword
            ? [{ id: 8, name: '道路数据集', description: 'Road traffic', status: 'ACTIVE', version_count: 1 }]
            : [{ id: 7, name: '占道经营', description: 'Street vendor', status: 'ACTIVE', version_count: 1 }],
          meta_data: { total: 1 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance&tab=datasets']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  const search = await screen.findByRole('textbox', { name: '搜索数据集' });
  fireEvent.change(search, { target: { value: 'Road' } });
  fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
  await waitFor(() => {
    expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
      params: { page: 0, size: 20, q: 'Road' },
    });
  });
  expect(await screen.findByText('道路数据集')).toBeTruthy();
  expect(screen.queryByText('占道经营')).toBeNull();
});

it('does not offer category maintenance or category selection for new datasets', async () => {
  window.localStorage.setItem('token', 'test-token');
  mockedGet.mockImplementation(async () => ({ data: { data: [] } } as never));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance&tab=datasets']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('新建数据集')).toBeTruthy());
  fireEvent.click(screen.getByText('新建数据集'));
  expect(screen.queryByLabelText('数据集分类')).toBeNull();
  expect(screen.queryByText('新建数据集分类')).toBeNull();
});

it('labels the English system value and delegates new color allocation to the API', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/annotation-taxonomy/labels') {
      return {
        data: {
          data: [
            {
              id: 1,
              label_key: '人员',
              label_value: 'person',
              annotation_kind: 'bbox',
              color: '#cf2727',
              status: 'active',
            },
          ],
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  mockedPost.mockResolvedValue({
    data: {
      data: { id: 2, label_key: '车辆', label_value: 'vehicle', color: '#2f8f6b', status: 'active' },
    },
  } as never);

  expect(nextAvailableLabelColor([{ id: 1, color: '#cf2727' }])).not.toBe('#cf2727');

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('人员')).toBeTruthy());
  expect(screen.getByText('矩形框（目标检测）')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '新建标签' }));

  expect(screen.getAllByText('系统值（英文）').length).toBeGreaterThanOrEqual(2);
  const previewColor = screen.getByLabelText('颜色（自动分配）') as HTMLInputElement;
  expect(previewColor.disabled).toBe(true);
  expect(previewColor.value).not.toBe('#cf2727');
  expect((screen.getByLabelText('当前标注类型') as HTMLInputElement).value).toBe('矩形框（目标检测）');

  fireEvent.change(screen.getByLabelText('中文名称'), { target: { value: '车辆' } });
  fireEvent.change(screen.getByLabelText('系统值（英文）'), { target: { value: 'vehicle' } });
  fireEvent.click(screen.getByRole('button', { name: /创\s*建/ }));

  await waitFor(() =>
    expect(mockedPost).toHaveBeenCalledWith(
      '/v1/annotation-taxonomy/labels',
      expect.objectContaining({ label_key: '车辆', label_value: 'vehicle', annotation_kind: 'bbox' }),
    ),
  );
  expect(mockedPost.mock.calls[0][1]).not.toHaveProperty('color');
});

it('allows editing the English system value and explains historical snapshot behavior', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/annotation-taxonomy/labels') {
      return {
        data: {
          data: [
            {
              id: 9,
              label_key: '安全帽',
              label_value: 'helmet',
              annotation_kind: 'bbox',
              color: '#1677ff',
              status: 'active',
              version: 4,
            },
          ],
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  mockedPatch.mockResolvedValue({
    data: { data: { id: 9, label_key: '安全帽', label_value: 'safety_helmet', version: 5 } },
  } as never);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/admin?view=governance']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('安全帽')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: '编辑' }));
  const valueInput = screen.getByLabelText('系统值（英文）') as HTMLInputElement;
  expect(valueInput.disabled).toBe(false);
  expect(valueInput.value).toBe('helmet');
  expect(screen.getByText('修改英文系统值后不会重写历史模型')).toBeTruthy();
  fireEvent.change(valueInput, { target: { value: 'safety_helmet' } });
  fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));

  await waitFor(() =>
    expect(mockedPatch).toHaveBeenCalledWith(
      '/v1/annotation-taxonomy/labels/9',
      expect.objectContaining({ version: 4, label_value: 'safety_helmet' }),
    ),
  );
});

it('shows only the two platform roles and no project management actions', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/users') {
      return {
        data: {
          data: [
            { id: 1, username: 'manager@example.com', is_superuser: true, status: 'ACTIVE' },
            { id: 2, username: 'annotator@example.com', is_superuser: false, status: 'ACTIVE' },
          ],
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText('manager@example.com')).toBeTruthy());
  expect(screen.getByText('annotator@example.com')).toBeTruthy();
  expect(screen.getByText('管理人员')).toBeTruthy();
  expect(screen.getByText('标注人员')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '创建项目' })).toBeNull();
  expect(screen.queryByRole('button', { name: /添加成员/ })).toBeNull();
  expect(mockedGet.mock.calls.some(([url]) => url === '/v1/projects')).toBe(false);
  expect(mockedGet.mock.calls.some(([url]) => url === '/v1/organizations')).toBe(false);
  expect(mockedPost).not.toHaveBeenCalled();
});

it('searches system users on the server and paginates the returned total', async () => {
  mockedGet.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/v1/users') {
      const params = config?.params || {};
      const page = Number(params.page || 0);
      const query = String(params.q || '');
      return {
        data: {
          data: [
            {
              id: page === 1 ? 21 : query ? 11 : 1,
              username: page === 1 ? '第二页账号' : query ? '筛选账号' : '第一页账号',
              is_superuser: page === 1,
              status: 'ACTIVE',
            },
          ],
          meta_data: { total: query ? 21 : 1, page, size: 20 },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('第一页账号')).toBeTruthy());
  const search = screen.getByRole('textbox', { name: '搜索系统账号' });
  fireEvent.change(search, { target: { value: 'annotator' } });
  fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });

  await waitFor(() => {
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/users', {
      params: { page: 0, size: 20, q: 'annotator' },
    });
  });
  expect(await screen.findByText('筛选账号')).toBeTruthy();
  expect(screen.getByText('已同步 21 条')).toBeTruthy();

  fireEvent.click(screen.getByTitle('Next Page'));
  await waitFor(() => {
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/users', {
      params: { page: 1, size: 20, q: 'annotator' },
    });
  });
  expect(await screen.findByText('第二页账号')).toBeTruthy();
});

it('adds edge device management to system management and reuses the edge device API', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/edge-devices') {
      return {
        data: {
          active_device_id: 'edge-1',
          devices: [
            {
              id: 'edge-1',
              name: '本地 BM1688 设备',
              is_active: true,
              enabled: true,
              host: '10.10.11.87',
              port: 22,
              user: 'linaro',
              validation_channel: 'ssh',
              password_configured: true,
              password_runtime_available: true,
              last_probe_status: 'ok',
              environment_status: 'ready',
              environment_report: { environment_ready: true, architecture_ok: true, bm1688_device_ok: true },
              available_for_dispatch: true,
              scheduling_status: 'idle',
            },
          ],
        },
      } as never;
    }
    if (url === '/v1/users') return { data: { data: [] } } as never;
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  const home = render(
    <MemoryRouter initialEntries={['/admin']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole('button', { name: /边端设备管理/ })).toBeTruthy();
  home.unmount();

  render(
    <MemoryRouter initialEntries={['/admin?view=edge-devices']}>
      <QueryClientProvider client={queryClient}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('本地 BM1688 设备')).toBeTruthy());
  expect(
    mockedGet.mock.calls.some(
      ([url, config]) =>
        url === '/v1/edge-devices' &&
        config?.params?.page === 0 &&
        config?.params?.size === 10,
    ),
  ).toBe(true);
  expect(mockedGet.mock.calls.some(([url]) => url === '/v1/users')).toBe(true);
  expect(screen.getByRole('button', { name: /新增设备/ })).toBeTruthy();
  expect(screen.getByText('当前设备可执行边端检查')).toBeTruthy();
  const deviceSearch = screen.getByRole('textbox', { name: '搜索边端设备' });
  fireEvent.change(deviceSearch, { target: { value: '10.10.11.87' } });
  fireEvent.keyDown(deviceSearch, { key: 'Enter', code: 'Enter' });
  await waitFor(() => {
    expect(
      mockedGet.mock.calls.some(
        ([url, config]) => url === '/v1/edge-devices' && config?.params?.q === '10.10.11.87',
      ),
    ).toBe(true);
  });
  fireEvent.click(screen.getByRole('button', { name: /eye 查看/ }));
  expect(await screen.findByText('连接与凭据')).toBeTruthy();
  expect(screen.getByText('BM1688 环境检查')).toBeTruthy();
});

it('provides AICM publish configuration in system management', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/yolo-aicm-publish/environments') {
      return {
        data: {
          active_environment_id: 'local-aicm',
          environments: [
            {
              id: 'local-aicm',
              name: '本地 AICM',
              base_url: 'http://10.10.11.20:8102',
              username: 'publisher',
              password_configured: true,
              password_runtime_available: true,
              password_masked: '******',
              ready_to_publish: true,
              readiness_reason: '连接地址、账号和密码均已就绪',
              is_active: true,
            },
          ],
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  render(
    <MemoryRouter initialEntries={['/admin?view=publish-config']}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getAllByText('本地 AICM').length).toBeGreaterThanOrEqual(1));
  expect(screen.getByText('发布配置')).toBeTruthy();
  expect(screen.getByText('可发布环境')).toBeTruthy();
  expect(screen.getAllByText('连接地址、账号和密码均已就绪').length).toBeGreaterThanOrEqual(1);
  expect(screen.getByRole('button', { name: /新增环境/ })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /新增环境/ }));
  expect(await screen.findByText(/填写 AICM 主机、端口、账号和密码/)).toBeTruthy();
  expect(screen.getByLabelText('IP 地址或主机名')).toBeTruthy();
  expect(screen.getByLabelText('账号')).toBeTruthy();
  expect(screen.getByLabelText('密码')).toBeTruthy();
});

it('provides model access configuration in system management', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/ai-model-configs') {
      return {
        data: {
          data: {
            configs: [
              {
                id: 'vlx-prod',
                name: '生产 VLX-Seek',
                provider_key: 'vlx_seek',
                capability: 'bbox_detection',
                endpoint: 'http://10.10.11.20:8099/api/infer',
                model: 'vlx-seek',
                enabled: true,
                is_default: true,
                last_check_status: 'PASSED',
              },
            ],
          },
        },
      } as never;
    }
    return { data: { data: [] } } as never;
  });
  render(
    <MemoryRouter initialEntries={['/admin?view=model-access-config']}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <AdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('生产 VLX-Seek')).toBeTruthy());
  expect(screen.getByText('模型接入配置')).toBeTruthy();
  expect(screen.getByText('目标检测预标注')).toBeTruthy();
  expect(screen.getByText('当前默认')).toBeTruthy();
  expect(screen.getByText('连通正常')).toBeTruthy();
  expect(screen.getByRole('button', { name: /新增模型配置/ })).toBeTruthy();
  expect(mockedGet).toHaveBeenCalledWith('/v1/ai-model-configs', {
    params: { page: 0, size: 10 },
  });
});
