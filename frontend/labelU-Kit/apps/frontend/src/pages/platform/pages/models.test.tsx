import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, waitFor, within } from '@testing-library/react';
import { Modal, message } from 'antd';
import { MemoryRouter, useLocation } from 'react-router-dom';
import request from '@/api/request';
import { describe, expect, it, vi } from 'vitest';

import ModelsPage, {
  ModelLineage,
  hasActiveCompileExports,
  latestModelFamilyVersion,
  modelBm1688ExportId,
  modelArtifactTypeLabel,
  modelVersionLabel,
  modelTrainingComparison,
  modelTrainingMetrics,
  modelPublishState,
  modelStatusLabel,
  parseOptionalJson,
  previousModelVersion,
  sameModelBranch,
  resolveExternalModelAlgorithm,
} from './models';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach } from 'vitest';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
let isTaskSuperuser = true;
vi.mock('@/hooks/useTaskSuperuser', () => ({ default: () => ({ isTaskSuperuser }) }));
const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);
const mockedDelete = vi.mocked(request.delete);

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mockedGet.mockReset();
  mockedPost.mockReset();
  mockedDelete.mockReset();
  vi.restoreAllMocks();
  isTaskSuperuser = true;
  window.history.pushState({}, '', '/models');
});

describe('published AICM model catalog', () => {
  const historicalModel = {
    catalog_id: 12,
    model_id: '536',
    aicm_model_set_id: 536,
    display_name: '非机动车检测历史模型',
    source_type: 'remote_legacy',
    class_names: ['nonvehicle_tricycle'],
    class_labels: [{ label_key: '三轮车', label_value: 'nonvehicle_tricycle' }],
    label_group_version: 'v2',
    label_group_size: 3,
    related_model_count: 8,
    published_at: '2026-08-03T12:00:00',
    availability_status: 'available',
    download_enabled: true,
  };

  function renderPublished(search = '') {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/models?view=published${search}`]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  function mockList(total = 45) {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/published-models') return {
        data: { models: [historicalModel] }, meta_data: { total },
      } as never;
      throw new Error(`unexpected GET ${path}`);
    });
  }

  it('paginates the full catalog, searches from page one and preserves linked filters', async () => {
    mockList();
    renderPublished('&export_id=7&training_job_id=9');
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    expect(screen.getByText('AICM历史模型')).toBeTruthy();
    expect(screen.getByText('v2 / 3个版本')).toBeTruthy();
    expect(screen.getByText('三轮车')).toBeTruthy();
    expect(screen.getAllByText('共 45 个模型').length).toBeGreaterThan(0);
    expect(screen.queryByText(/已同步/)).toBeNull();

    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: {
      page: 1, size: 20, q: undefined, source_type: undefined, export_id: 7, yolo_train_job_id: 9,
    } }));
    const search = screen.getByPlaceholderText('搜索模型编号或名称');
    fireEvent.change(search, { target: { value: '536' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: {
      page: 0, size: 20, q: '536', source_type: undefined, export_id: 7, yolo_train_job_id: 9,
    } }));
  });

  it('keeps total from the actual intercepted response when the server returns 20 of 356 models', async () => {
    mockedGet.mockImplementation(async (path: string, config) => {
      if (path !== '/v1/published-models') throw new Error(`unexpected GET ${path}`);
      const params = config?.params as { page: number; q?: string };
      const models = params.q === '536' ? [historicalModel] : Array.from({ length: 20 }, (_, index) => ({
        ...historicalModel,
        catalog_id: index + 100 + params.page * 20,
        model_id: String(index + 1000 + params.page * 20),
        aicm_model_set_id: index + 1000 + params.page * 20,
        display_name: `历史目录模型${index + 1 + params.page * 20}`,
      }));
      // request.successHandler 返回 HTTP 正文，meta_data 和 data 处于同一层。
      return { meta_data: { total: params.q ? 1 : 356, page: params.page, size: models.length }, data: { summary: {}, models } } as never;
    });
    renderPublished();
    expect(await screen.findByText('历史目录模型1')).toBeTruthy();
    expect(screen.getAllByText('共 356 个模型').length).toBeGreaterThan(0);
    expect(screen.getByText('当前页 20 条记录')).toBeTruthy();
    const nextPage = screen.getByTitle('Next Page');
    expect(nextPage.getAttribute('aria-disabled')).toBe('false');
    fireEvent.click(nextPage);
    expect(await screen.findByText('历史目录模型21')).toBeTruthy();
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: expect.objectContaining({ page: 1 }) });
    const search = screen.getByPlaceholderText('搜索模型编号或名称');
    fireEvent.change(search, { target: { value: '536' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: expect.objectContaining({ page: 0, q: '536' }) });
    fireEvent.click(screen.getByRole('button', { name: '详情' }));
    const details = within(await screen.findByRole('dialog'));
    expect(details.getByText('536')).toBeTruthy();
    expect(details.getByText('v2')).toBeTruthy();
  });

  it('keeps retained local packages downloadable without inventing a missing label state', async () => {
    mockedGet.mockResolvedValue({
      data: { models: [{ ...historicalModel, model_id: '603', aicm_model_set_id: 603, source_type: 'labelu_publish', availability_status: 'unavailable', local_package_available: true, label_group_version: null, label_group_size: 0 }] },
      meta_data: { total: 1 },
    } as never);
    renderPublished();
    expect(await screen.findByText('本地可下载')).toBeTruthy();
    expect(screen.getByText('未参与当前目录分组')).toBeTruthy();
    expect(screen.queryByText('未记录标签')).toBeNull();
    expect((screen.getByRole('button', { name: '下载模型' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '详情' }));
    expect(await screen.findByText(/AICM当前列表未列出该模型/)).toBeTruthy();
  });

  it('restores the active search text when returning from another model submenu', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/published-models') return {
        data: { models: [historicalModel] }, meta_data: { total: 1 },
      } as never;
      if (path === '/v1/models/families') return { data: { data: [], meta_data: { total: 0 } } } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    renderPublished();
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    const search = screen.getByPlaceholderText('搜索模型编号或名称');
    fireEvent.change(search, { target: { value: '536' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: expect.objectContaining({ q: '536' }) }));
    fireEvent.click(screen.getByRole('button', { name: '训练权重（PT）' }));
    expect(await screen.findByText('暂无 PT 模型')).toBeTruthy();
    expect(screen.queryByPlaceholderText('搜索模型编号或名称')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '已发布模型' }));
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    expect((screen.getByPlaceholderText('搜索模型编号或名称') as HTMLInputElement).value).toBe('536');
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: expect.objectContaining({ q: '536' }) }));
  });

  it('filters historical source from page one and shows a reusable detail dialog', async () => {
    mockList();
    renderPublished();
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: expect.objectContaining({ page: 1 }) }));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '模型来源' }));
    fireEvent.click(screen.getAllByText('AICM历史模型').slice(-1)[0]);
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/published-models', { params: expect.objectContaining({ page: 0, source_type: 'remote_legacy' }) }));
    fireEvent.click(screen.getByRole('button', { name: '详情' }));
    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(screen.getByText('标签有交集的其他模型')).toBeTruthy();
    expect(screen.getByText('nonvehicle_tricycle')).toBeTruthy();
    expect(screen.getByText(/不代表后一个版本效果更好/)).toBeTruthy();
  });

  it('synchronizes remotely only on the manager action and prevents repeated clicks while pending', async () => {
    mockList(1);
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);
    let resolveSync!: (value: any) => void;
    mockedPost.mockImplementation(() => new Promise((resolve) => { resolveSync = resolve; }) as never);
    renderPublished();
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    expect(mockedPost).not.toHaveBeenCalled();
    const syncButton = screen.getByRole('button', { name: '同步AICM模型' });
    fireEvent.click(syncButton);
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole('button', { name: /同步AICM模型/ }).classList.contains('ant-btn-loading')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: /同步AICM模型/ }));
    expect(mockedPost).toHaveBeenCalledTimes(1);
    expect(mockedPost).toHaveBeenCalledWith('/v1/published-models/sync', { include_remote_models: true });
    const previousReads = mockedGet.mock.calls.length;
    resolveSync({ data: { remote_scanned: 40, created: 39, updated: 1 } });
    await waitFor(() => expect(successNotice).toHaveBeenCalledWith('AICM同步完成：读取 40 个模型，新增 39 条，更新 1 条'));
    await waitFor(() => expect(mockedGet.mock.calls.length).toBeGreaterThan(previousReads));
  });

  it('keeps a failed sync visible without claiming success and leaves existing records available', async () => {
    mockList(1);
    const errorNotice = vi.spyOn(message, 'error').mockImplementation(() => ({}) as never);
    const successNotice = vi.spyOn(message, 'success').mockImplementation(() => ({}) as never);
    mockedPost.mockRejectedValue({ response: { status: 400, data: { msg: '发布配置中尚未设置AICM地址' } } });
    renderPublished();
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '同步AICM模型' }));
    await waitFor(() => expect(errorNotice).toHaveBeenCalledWith('发布配置中尚未设置AICM地址'));
    expect(screen.getByText('非机动车检测历史模型')).toBeTruthy();
    expect(successNotice).not.toHaveBeenCalled();
  });

  it('lets annotation users inspect the catalog without showing management synchronization', async () => {
    isTaskSuperuser = false;
    mockList(1);
    renderPublished();
    expect(await screen.findByText('非机动车检测历史模型')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '同步AICM模型' })).toBeNull();
    expect(screen.getByRole('button', { name: '详情' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '下载模型' })).toBeTruthy();
  });
});

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

const passedGates = {
  training: 'PASS',
  generalization: 'PASS',
  conversion: 'PASS',
  edge: 'PASS',
  release: 'PASS',
};

function familyList(versions: any[], overrides: Record<string, unknown> = {}) {
  const latest = versions[0];
  return {
    data: {
      data: [{
        family_key: 'task:1:algorithm:1',
        display_name: latest.display_name,
        task_id: 1,
        algorithm_id: 1,
        latest_version_id: latest.id,
        version_count: versions.length,
        published_version_id: null,
        updated_at: latest.updated_at || latest.created_at || null,
        versions,
        ...overrides,
      }],
      meta_data: { total: 1 },
    },
  } as never;
}

function compileExport(overrides: Record<string, unknown> = {}) {
  return {
    id: 302,
    task_id: 1,
    task_name: '安全帽检测',
    yolo_train_job_id: 88,
    model_version_id: 103,
    version_no: 3,
    dataset_version_id: 23,
    dataset_version_no: 4,
    algorithm_id: 7,
    algorithm_key: 'yolo11n-detect',
    status: 'COMPLETED',
    bmodel_filenames: ['model_fp32.bmodel', 'model_fp16.bmodel', 'model_int8.bmodel'],
    current_verdict_label: '编译完成，等待边端验证',
    created_at: '2026-10-24T08:00:00',
    updated_at: '2026-10-24T08:10:00',
    can_recompile_bmodel: true,
    ...overrides,
  };
}

function compileFamilyList(
  exports: any[],
  overrides: Record<string, unknown> = {},
  total = 1,
) {
  const latest = exports[0];
  return {
    data: {
      data: [{
        family_key: 'task:1:algorithm:7',
        display_name: '安全帽检测',
        task_name: '安全帽检测',
        task_id: 1,
        algorithm_id: 7,
        algorithm_key: 'yolo11n-detect',
        latest_model_version_id: latest?.model_version_id || null,
        latest_version_no: latest?.version_no || null,
        version_count: 2,
        compile_task_count: exports.length,
        latest_export_id: latest?.id || null,
        latest_status: latest?.status || null,
        updated_at: latest?.updated_at || null,
        exports,
        ...overrides,
      }],
      meta_data: { total },
    },
  } as never;
}

function renderModels(entry: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderModelsWithLocation(entry: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ModelsPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{location.pathname}{location.search}</div>;
}

describe('BM1688 compile family history', () => {
  it('requests family pagination and preserves repeated exports for one model version', async () => {
    const exports = [
      compileExport({ id: 302, status: 'PROCESSING', bmodel_filenames: [], updated_at: '2026-10-24T09:00:00' }),
      compileExport({ id: 301, status: 'COMPLETED' }),
      compileExport({
        id: 250,
        yolo_train_job_id: 77,
        model_version_id: 102,
        version_no: 2,
        dataset_version_id: 22,
        dataset_version_no: 3,
        bmodel_filenames: ['model_v2_int8.bmodel'],
      }),
    ];
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/families') return compileFamilyList(exports);
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels('/models?view=compile');

    expect(await screen.findByText('安全帽检测')).toBeTruthy();
    expect(screen.getByText('共 2 个版本')).toBeTruthy();
    expect(screen.getByText('3 个编译任务')).toBeTruthy();
    expect(screen.getAllByText('处理中').length).toBeGreaterThan(0);
    expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-bm1688-exports/families', {
      params: {
        page: 0,
        size: 20,
        export_id: undefined,
        yolo_train_job_id: undefined,
        task_name: undefined,
        export_status: undefined,
      },
    });

    fireEvent.click(document.querySelector('.ant-table-row-expand-icon') as HTMLButtonElement);
    expect(await screen.findByText('编译历史')).toBeTruthy();
    expect(screen.getByText('#302')).toBeTruthy();
    expect(screen.getByText('#301')).toBeTruthy();
    expect(screen.getByText('#250')).toBeTruthy();
    expect(screen.getAllByText('v3').length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText('数据快照 v4').length).toBe(2);
    expect(screen.getByText('1 个制品')).toBeTruthy();

    const panel = screen.getByText('模型仓库').closest('section') as HTMLElement;
    expect(panel.style.minWidth).toBe('0');
    expect(panel.style.width).toBe('100%');
    expect(panel.style.overflowX).toBe('hidden');
    const historyHeading = screen.getByText('编译历史');
    const historyViewport = historyHeading.parentElement?.parentElement as HTMLElement;
    expect(historyViewport.style.minWidth).toBe('0');
    expect(historyViewport.style.maxWidth).toBe('100%');
    expect(historyViewport.style.overflowX).toBe('auto');
  });

  it('keeps the family total from the runtime-unwrapped API envelope', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/families') {
        return {
          data: [
            {
              family_key: 'task:1:algorithm:7',
              display_name: '安全帽检测',
              task_name: '安全帽检测',
              task_id: 1,
              algorithm_id: 7,
              algorithm_key: 'yolo11n-detect',
              latest_model_version_id: 103,
              latest_version_no: 3,
              version_count: 2,
              compile_task_count: 1,
              latest_export_id: 301,
              latest_status: 'COMPLETED',
              updated_at: '2026-10-24T08:10:00',
              exports: [compileExport()],
            },
          ],
          meta_data: { total: 25, page: 0, size: 1 },
        } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels('/models?view=compile');

    expect(await screen.findByText('安全帽检测')).toBeTruthy();
    expect(screen.getAllByText('共 25 个模型系列')).toHaveLength(2);
    expect(screen.getByTitle('2')).toBeTruthy();
  });

  it('resets pagination for filters and tabs, supports reset, and refreshes on demand', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/families') return compileFamilyList([compileExport()], {}, 25);
      if (path === '/v1/models/families') return { data: { data: [], meta_data: { total: 0 } } } as never;
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels('/models?view=compile');
    expect(await screen.findByText('安全帽检测')).toBeTruthy();
    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/yolo-bm1688-exports/families', {
      params: expect.objectContaining({ page: 1 }),
    }));

    const search = screen.getByRole('textbox', { name: '搜索编译任务' });
    fireEvent.change(search, { target: { value: '安全帽' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/yolo-bm1688-exports/families', {
      params: expect.objectContaining({ page: 0, task_name: '安全帽' }),
    }));

    fireEvent.mouseDown(screen.getByRole('combobox', { name: '编译状态' }));
    fireEvent.click(screen.getAllByText('失败').slice(-1)[0]);
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/yolo-bm1688-exports/families', {
      params: expect.objectContaining({ page: 0, task_name: '安全帽', export_status: 'FAILED' }),
    }));

    const readsBeforeRefresh = mockedGet.mock.calls.length;
    const refreshButton = screen.getByRole('button', { name: /刷新列表/ }) as HTMLButtonElement;
    await waitFor(() => expect(refreshButton.disabled).toBe(false));
    fireEvent.click(refreshButton);
    await waitFor(() => expect(mockedGet.mock.calls.length).toBeGreaterThan(readsBeforeRefresh));

    fireEvent.click(screen.getByRole('button', { name: '重置筛选' }));
    await waitFor(() => expect((search as HTMLInputElement).value).toBe(''));
    expect(document.querySelector('.ant-select-selection-item')?.textContent).toBe('全部状态');
    expect(document.querySelector('.ant-pagination-item-active')?.textContent).toBe('1');

    fireEvent.click(screen.getByTitle('2'));
    await waitFor(() => expect(document.querySelector('.ant-pagination-item-active')?.textContent).toBe('2'));
    fireEvent.click(screen.getByRole('button', { name: '训练权重（PT）' }));
    expect(await screen.findByText('暂无 PT 模型')).toBeTruthy();
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/models/families', {
      params: { page: 0, size: 20 },
    });
  });

  it.each([
    ['export_id', 302],
    ['training_job_id', 88],
  ])('opens the complete compile family for a %s deep link', async (parameter, identifier) => {
    const exports = [compileExport({ id: 302 }), compileExport({ id: 301 })];
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/families') return compileFamilyList(exports);
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels(`/models?view=compile&${parameter}=${identifier}`);

    expect(await screen.findByText('编译历史')).toBeTruthy();
    expect(screen.getByText('#302')).toBeTruthy();
    expect(screen.getByText('#301')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-bm1688-exports/families', {
      params: expect.objectContaining({
        export_id: parameter === 'export_id' ? identifier : undefined,
        yolo_train_job_id: parameter === 'training_job_id' ? identifier : undefined,
      }),
    });
  });

  it('keeps legacy bmodel exports in compile history instead of opening an empty model family', async () => {
    const legacy = compileExport({
      id: 401,
      model_version_id: null,
      version_no: null,
      dataset_version_id: null,
      dataset_version_no: null,
      bmodel_filenames: ['legacy_fp32.bmodel'],
    });
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/families') {
        return compileFamilyList([legacy], {
          latest_model_version_id: null,
          latest_version_no: null,
        });
      }
      if (path === '/v1/models/families') throw new Error('legacy export must not open model families');
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels('/models?view=compile');
    expect(await screen.findByText('安全帽检测')).toBeTruthy();
    fireEvent.click(document.querySelector('.ant-table-row-expand-icon') as HTMLButtonElement);
    expect(await screen.findByText('编译历史')).toBeTruthy();
    expect(screen.getByRole('button', { name: '查看编译历史' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '查看编译历史' }));
    await waitFor(() =>
      expect(mockedGet).toHaveBeenLastCalledWith(
        '/v1/yolo-bm1688-exports/families',
        { params: expect.objectContaining({ export_id: 401 }) },
      ),
    );
    expect(mockedGet.mock.calls.some(([path]) => path === '/v1/models/families')).toBe(false);
  });

  it('still opens the bmodel family for canonical exports', async () => {
    const canonical = compileExport({
      id: 402,
      model_version_id: 104,
      bmodel_filenames: ['model_int8.bmodel'],
    });
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-bm1688-exports/families') return compileFamilyList([canonical]);
      if (path === '/v1/models/families') return { data: [], meta_data: { total: 0 } } as never;
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels('/models?view=compile');
    expect(await screen.findByText('安全帽检测')).toBeTruthy();
    fireEvent.click(document.querySelector('.ant-table-row-expand-icon') as HTMLButtonElement);
    expect(await screen.findByText('编译历史')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看边端模型' }));
    await waitFor(() =>
      expect(mockedGet).toHaveBeenLastCalledWith(
        '/v1/models/families',
        { params: expect.objectContaining({ artifact_type: 'bmodel', export_id: 402 }) },
      ),
    );
  });

  it('polls active compile families every five seconds and stops after terminal data', async () => {
    vi.useFakeTimers();
    let readCount = 0;
    mockedGet.mockImplementation(async (path: string) => {
      if (path !== '/v1/yolo-bm1688-exports/families') throw new Error(`unexpected GET ${path}`);
      readCount += 1;
      const status = readCount === 1 ? 'ONNX_READY' : 'COMPLETED';
      return compileFamilyList([compileExport({ status })], { latest_status: status });
    });

    renderModels('/models?view=compile');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(readCount).toBe(1);
    expect(await screen.findByText('ONNX 已就绪')).toBeTruthy();
    expect(hasActiveCompileExports((compileFamilyList([compileExport({ status: 'ONNX_READY' })]) as any).data)).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(readCount).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(readCount).toBe(2);
  });
});

describe('model repository publish guard', () => {
  it('blocks publishing while any quality gate is pending', () => {
    const result = modelPublishState({
      status: 'CANDIDATE',
      quality_gate: { ...passedGates, edge: 'PENDING' },
      artifacts: [{ artifact_type: 'bmodel', status: 'READY' } as any],
    });

    expect(result.allowed).toBe(false);
    expect(result.reason).toContain('设备适配检查');
  });

  it('blocks publishing unless all three BM1688 precisions are ready', () => {
    const result = modelPublishState({
      status: 'VALIDATED',
      quality_gate: passedGates,
      artifacts: [{ artifact_type: 'bmodel', chip_type: 'BM1688', precision: 'INT8', status: 'READY' } as any],
    });

    expect(result.allowed).toBe(false);
    expect(result.reason).toContain('FP32、FP16、INT8');
  });

  it('allows a validated model with passed gates and a ready edge artifact', () => {
    const result = modelPublishState({
      status: 'VALIDATED',
      quality_gate: passedGates,
      artifacts: [
        { artifact_type: 'bmodel', chip_type: 'BM1688', precision: 'FP32', status: 'READY' } as any,
        { artifact_type: 'bmodel', chip_type: 'BM1688', precision: 'FP16', status: 'READY' } as any,
        { artifact_type: 'bmodel', chip_type: 'BM1688', precision: 'INT8', status: 'READY' } as any,
      ],
    });

    expect(result.allowed).toBe(true);
  });
});

describe('model artifact/evidence payload parsing', () => {
  it('accepts object JSON and treats blank values as omitted', () => {
    expect(parseOptionalJson('{"map50":0.92}')).toEqual({ map50: 0.92 });
    expect(parseOptionalJson('   ')).toBeUndefined();
  });

  it('rejects malformed or non-object JSON', () => {
    expect(() => parseOptionalJson('{oops')).toThrow();
    expect(() => parseOptionalJson('[1, 2]')).toThrow('JSON 必须是对象');
  });
});

describe('BM1688 compile lineage', () => {
  it('prefers the canonical model export id when artifact metadata is absent', () => {
    expect(
      modelBm1688ExportId({
        bm1688_export_id: 19,
        artifacts: [],
      }),
    ).toBe(19);
    expect(
      modelBm1688ExportId({
        edge_validation_status: { export_id: 20 },
        artifacts: [],
      }),
    ).toBe(20);
  });

  it('reads one shared export id from ready BM1688 artifacts', () => {
    expect(
      modelBm1688ExportId({
        artifacts: ['FP32', 'FP16', 'INT8'].map((precision, index) => ({
          id: index + 1,
          artifact_type: 'bmodel',
          chip_type: 'BM1688',
          precision,
          relative_path: `${precision}.bmodel`,
          sha256: String(index).repeat(64),
          status: 'READY',
          metadata: { export_id: 7 },
        })),
      }),
    ).toBe(7);
  });

  it('rejects missing or conflicting export lineage', () => {
    expect(modelBm1688ExportId({ artifacts: [] })).toBeNull();
    expect(
      modelBm1688ExportId({
        artifacts: [
          {
            id: 1,
            artifact_type: 'bmodel',
            chip_type: 'BM1688',
            relative_path: 'fp16.bmodel',
            sha256: 'a'.repeat(64),
            status: 'READY',
            metadata: { export_id: 1 },
          },
          {
            id: 2,
            artifact_type: 'bmodel',
            chip_type: 'BM1688',
            relative_path: 'int8.bmodel',
            sha256: 'b'.repeat(64),
            status: 'READY',
            metadata: { export_id: 2 },
          },
        ],
      }),
    ).toBeNull();
  });
});

describe('model user-facing labels', () => {
  it('translates internal model statuses and artifact types to Chinese', () => {
    expect(modelStatusLabel('PROCESSING')).toBe('处理中');
    expect(modelStatusLabel('COMPLETED')).toBe('已完成');
    expect(modelStatusLabel('FAILED')).toBe('失败');
    expect(modelStatusLabel('PUBLISHED')).toBe('已发布');
    expect(modelStatusLabel('unseen_status')).toBe('待确认（UNSEEN_STATUS）');
    expect(modelArtifactTypeLabel('bmodel')).toBe('BM1688 模型（BModel）');
  });
});

describe('PT training metric presentation', () => {
  it('uses best validation recall and the shared recommended confidence rule', () => {
    expect(
      modelTrainingMetrics({
        metrics: {
          best_pt_val_metrics: { recall: 0.9266477517, map50_95: 0.8446324284 },
          final_metrics: { recall: 0.94246, map50_95: 0.84261 },
        },
        manifest: {},
      }),
    ).toEqual({ recall: 0.9266477517, confidence: 0.85, confidenceRecommended: true });
  });

  it('does not invent a confidence value when no training metric is available', () => {
    expect(modelTrainingMetrics({ metrics: null, manifest: null })).toEqual({
      recall: null,
      confidence: null,
      confidenceRecommended: false,
    });
  });

  it('compares against the previous family version when explicit lineage is missing', () => {
    const previous = {
      id: 11,
      version_no: 1,
      created_at: '2026-10-25T10:00:00Z',
      metrics: { best_pt_val_metrics: { map50_95: 0.4 } },
    } as any;
    const current = {
      id: 12,
      version_no: 2,
      created_at: '2026-10-26T10:00:00Z',
      parent_model_version_id: null,
      metrics: { best_pt_val_metrics: { map50_95: 0.45 } },
    } as any;
    expect(previousModelVersion(current, [current, previous])).toBe(previous);
    expect(modelTrainingComparison(current, [current, previous])).toMatchObject({
      previous,
      metric: 'mAP50-95',
      status: 'improved',
    });
  });

  it('distinguishes regression, first version, and missing metrics', () => {
    const previous = { id: 21, version_no: 1, created_at: '2026-10-25T10:00:00Z', metrics: { best_pt_val_metrics: { map50_95: 0.5 } } } as any;
    const regressed = { id: 22, version_no: 2, created_at: '2026-10-26T10:00:00Z', metrics: { best_pt_val_metrics: { map50_95: 0.45 } } } as any;
    const first = { id: 31, version_no: 1, created_at: '2026-10-26T10:00:00Z', metrics: { best_pt_val_metrics: { map50_95: 0.45 } } } as any;
    const missing = { id: 32, version_no: 2, created_at: '2026-10-27T10:00:00Z', metrics: { dataset: { train: 10 } } } as any;
    expect(modelTrainingComparison(regressed, [regressed, previous]).status).toBe('regressed');
    expect(modelTrainingComparison(first, [first]).status).toBe('first');
    expect(modelTrainingComparison(missing, [missing, previous]).status).toBe('insufficient');
  });

  it('falls back to mAP50 when mAP50-95 is unavailable in both versions', () => {
    const previous = { id: 41, version_no: 1, created_at: '2026-10-25T10:00:00Z', metrics: { best_pt_val_metrics: { map50: 0.7 } } } as any;
    const current = { id: 42, version_no: 2, created_at: '2026-10-26T10:00:00Z', metrics: { best_pt_val_metrics: { map50: 0.8 } } } as any;
    expect(modelTrainingComparison(current, [current, previous])).toMatchObject({ metric: 'mAP50', status: 'improved' });
  });

  it('does not compare an implicit parent across model branches', () => {
    const dayV1 = {
      id: 51,
      series_id: 7,
      variant_id: 101,
      branch_version_no: 1,
      version_no: 9,
      created_at: '2026-10-25T10:00:00Z',
      metrics: { best_pt_val_metrics: { map50_95: 0.4 } },
    } as any;
    const nightV1 = {
      id: 52,
      series_id: 7,
      variant_id: 202,
      branch_version_no: 1,
      version_no: 10,
      created_at: '2026-10-26T10:00:00Z',
      metrics: { best_pt_val_metrics: { map50_95: 0.9 } },
    } as any;
    const dayV2 = {
      id: 53,
      series_id: 7,
      variant_id: 101,
      branch_version_no: 2,
      version_no: 11,
      parent_model_version_id: null,
      created_at: '2026-10-27T10:00:00Z',
      metrics: { best_pt_val_metrics: { map50_95: 0.45 } },
    } as any;

    expect(sameModelBranch(dayV2, nightV1)).toBe(false);
    expect(previousModelVersion(dayV2, [dayV2, nightV1, dayV1])).toBe(dayV1);
    expect(modelTrainingComparison(dayV2, [dayV2, nightV1, dayV1])).toMatchObject({
      previous: dayV1,
      status: 'improved',
    });
  });

  it('uses the server-selected family latest row and renders its branch-local number', () => {
    const day = { id: 61, version_no: 12, branch_version_no: 1, variant_id: 301, variant_name: '白天' } as any;
    const night = { id: 62, version_no: 13, branch_version_no: 1, variant_id: 302, variant_name: '夜间' } as any;
    const family = { latest_version_id: 62, versions: [day, night] } as any;
    expect(latestModelFamilyVersion(family)).toBe(night);
    expect(modelVersionLabel(night)).toBe('v1');
    expect(modelVersionLabel(day)).toBe('v1');
  });
});

describe('model lineage presentation', () => {
  it('shows the resolved series, branch contract and actual parent references', () => {
    render(
      <ModelLineage
        model={{
          algorithm_id: 7,
          algorithm_key: 'yolo_detection',
          display_name: '白天下雨模型',
          dataset_id: 9,
          dataset_version_id: 12,
          series_id: 3,
          series_name: '道路检测系列',
          model_family: 'yolov11',
          variant_id: 8,
          variant_name: '白天下雨',
          branch_key: 'abcdef1234567890abcdef1234567890',
          selection_contract_sha256: 'abcdef1234567890abcdef1234567890',
          selection_contract: {
            mode: 'label_dimension',
            selected_label_ids: [11, 12],
            global_conditions: [{ dimension: '天气', values: ['雨天'] }],
          },
          inheritance_mode: 'same_series',
          training_job_id: 34,
          parent_model_version_id: 21,
          parent_training_job_id: 20,
          published_catalog_id: null,
        }}
      />,
    );

    expect(screen.getByText(/道路检测系列 · yolov11/)).toBeTruthy();
    expect(screen.getByText('白天下雨')).toBeTruthy();
    expect(screen.getByText('abcdef1234567890…')).toBeTruthy();
    expect(screen.getByText(/标签维度 · 全局条件 天气:雨天 · 已选 2 个标签/)).toBeTruthy();
    expect(screen.getByText('同系列迭代')).toBeTruthy();
    expect(screen.getByText('#21 · 父作业 #20')).toBeTruthy();
  });

  it('renders source references and an empty marker for missing optional links', () => {
    render(
      <ModelLineage
        model={{
          algorithm_id: 7,
          dataset_id: 9,
          dataset_version_id: 12,
          training_job_id: 34,
          parent_model_version_id: null,
          published_catalog_id: 56,
        }}
      />,
    );

    expect(screen.getByText('模型来源')).toBeTruthy();
    expect(screen.getByText('#7')).toBeTruthy();
    expect(screen.getByText('#9')).toBeTruthy();
    expect(screen.getByText('#12')).toBeTruthy();
    expect(screen.getByText('#34')).toBeTruthy();
    expect(screen.getByText('#56')).toBeTruthy();
    expect(screen.getByText('-')).toBeTruthy();
  });

  it('explains PT training source in business terms instead of internal ids', () => {
    render(
      <ModelLineage
        mode="pt"
        model={{
          display_name: '安全帽检测',
          algorithm_id: 7,
          algorithm_key: 'yolo_detection',
          dataset_id: 9,
          dataset_version_id: 12,
          training_job_id: 34,
          parent_model_version_id: null,
          published_catalog_id: null,
          manifest: {
            task_name: '安全帽第一批',
            dataset_manifest: {
              split_counts: { train: 80, val: 20, test: 10 },
              class_names: ['安全帽', '未戴安全帽'],
            },
          },
        }}
      />,
    );

    expect(screen.getByText('训练来源')).toBeTruthy();
    expect(screen.getByText('安全帽第一批')).toBeTruthy();
    expect(screen.getByText('训练 80 张，验证 20 张，测试 10 张')).toBeTruthy();
    expect(screen.getByText('2 类目标（标签详情请到任务与标注查看）')).toBeTruthy();
    expect(screen.queryByText('训练数据集编号')).toBeNull();
  });
});

describe('external model dataset compatibility', () => {
  it('derives the generic detection capability from the current bbox label contract', () => {
    const algorithm = resolveExternalModelAlgorithm(
      {
        id: 8,
        dataset_label_contract_id: 3,
        dataset_label_set_signature: 'labels-a',
        dataset_labels: [{ label_key: '车牌', annotation_kind: 'bbox' }],
      },
      [
        { id: 99, algorithm_key: 'safety_helmet_detection', name: '安全帽识别' },
        { id: 1, algorithm_key: 'yolo_detection', name: 'YOLO 通用目标检测' },
      ],
    );

    expect(algorithm).toMatchObject({ id: 1, algorithm_key: 'yolo_detection' });
  });

  it('prefers an algorithm explicitly bound to the dataset label signature', () => {
    const algorithm = resolveExternalModelAlgorithm(
      {
        id: 8,
        dataset_label_contract_id: 3,
        dataset_label_set_signature: 'labels-a',
        dataset_labels: [{ label_key: '安全帽', annotation_kind: 'bbox' }],
      },
      [
        { id: 1, algorithm_key: 'yolo_detection', name: 'YOLO 通用目标检测' },
        {
          id: 24,
          algorithm_key: 'safety_helmet_detection',
          name: '安全帽识别',
          label_set_signature: 'labels-a',
          training_adapter: 'yolo_detection',
        },
      ],
    );

    expect(algorithm).toMatchObject({ id: 24, algorithm_key: 'safety_helmet_detection' });
  });

  it('returns no capability when the selected dataset has no compatible registered adapter', () => {
    expect(
      resolveExternalModelAlgorithm(
        { id: 8, dataset_label_contract_id: 3, dataset_labels: [{ label_key: '轮廓', annotation_kind: 'polygon' }] },
        [{ id: 1, algorithm_key: 'yolo_detection', name: 'YOLO 通用目标检测' }],
      ),
    ).toBeNull();
  });
});

describe('model repository server search submission', () => {
  it.each([
    ['/models', 'PT'],
    ['/models?view=bmodel', 'BModel'],
  ])('%s keeps typing local and submits the %s search on Enter', async (entry, _label) => {
    const allModel = {
      id: 101,
      display_name: '全量模型系列',
      version_no: 1,
      status: 'CANDIDATE',
      artifacts: [],
      quality_gate: {},
    };
    const matchedModel = { ...allModel, id: 102, display_name: '匹配模型系列' };
    mockedGet.mockImplementation(async (path: string, config) => {
      if (path !== '/v1/models/families') throw new Error(`unexpected GET ${path}`);
      const params = (config?.params || {}) as { q?: string };
      return familyList([params.q ? matchedModel : allModel]) as never;
    });

    renderModels(entry);
    expect(await screen.findByText('全量模型系列')).toBeTruthy();
    const initialCalls = mockedGet.mock.calls.length;
    const search = screen.getByRole('textbox', { name: '搜索模型系列' });
    fireEvent.change(search, { target: { value: '匹配' } });
    expect(mockedGet.mock.calls.length).toBe(initialCalls);

    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(await screen.findByText('匹配模型系列')).toBeTruthy();
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/models/families', {
      params: expect.objectContaining({ q: '匹配' }),
    }));

    fireEvent.change(search, { target: { value: '' } });
    expect(await screen.findByText('全量模型系列')).toBeTruthy();
    expect((search as HTMLInputElement).value).toBe('');
  });

  it('keeps external-model typing local, submits from the search button, and clears to all rows', async () => {
    const allModel = {
      id: 7,
      display_name: '全量外部模型',
      original_filename: 'all.pt',
      file_size: 1024,
      can_delete: false,
      registered_model_count: 0,
    };
    const matchedModel = { ...allModel, id: 8, display_name: '匹配外部模型', original_filename: 'match.pt' };
    mockedGet.mockImplementation(async (path: string, config) => {
      if (path !== '/v1/yolo-imported-models') throw new Error(`unexpected GET ${path}`);
      const params = (config?.params || {}) as { q?: string };
      return {
        data: {
          data: [params.q ? matchedModel : allModel],
          meta_data: { total: 1 },
        },
      } as never;
    });

    renderModels('/models?view=external');
    expect(await screen.findByText('全量外部模型')).toBeTruthy();
    const initialCalls = mockedGet.mock.calls.length;
    const search = screen.getByRole('textbox', { name: '搜索外部导入模型' });
    fireEvent.change(search, { target: { value: '匹配' } });
    expect(mockedGet.mock.calls.length).toBe(initialCalls);

    fireEvent.click(screen.getByRole('button', { name: '搜索外部导入模型' }));
    expect(await screen.findByText('匹配外部模型')).toBeTruthy();
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/yolo-imported-models', {
      params: { page: 0, size: 20, q: '匹配' },
    }));

    fireEvent.change(search, { target: { value: '' } });
    expect(await screen.findByText('全量外部模型')).toBeTruthy();
    expect((search as HTMLInputElement).value).toBe('');
  });

  it('restores q from the URL and keeps the model view deep-link when updating search', async () => {
    const family = {
      family_key: 'task:1:algorithm:7',
      display_name: '夜间道路模型',
      task_name: '道路检测',
      task_id: 1,
      algorithm_id: 7,
      latest_model_version_id: 101,
      latest_version_no: 1,
      version_count: 1,
      compile_task_count: 0,
      exports: [],
    };
    mockedGet.mockImplementation(async (path: string, config) => {
      if (path !== '/v1/models/families') throw new Error(`unexpected GET ${path}`);
      const params = (config?.params || {}) as { q?: string };
      return familyList(params.q ? [family] : [family]) as never;
    });

    renderModelsWithLocation('/models?view=bmodel&export_id=77&q=夜间');
    const search = await screen.findByRole('textbox', { name: '搜索模型系列' });
    await waitFor(() => expect((search as HTMLInputElement).value).toBe('夜间'));
    expect(screen.getByTestId('location-probe').textContent).toBe('/models?view=bmodel&export_id=77&q=夜间');
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/models/families', {
      params: expect.objectContaining({ q: '夜间', artifact_type: 'bmodel', export_id: 77 }),
    }));

    fireEvent.change(search, { target: { value: '白天' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    await waitFor(() => expect(screen.getByTestId('location-probe').textContent).toBe('/models?view=bmodel&export_id=77&q=%E7%99%BD%E5%A4%A9'));
    await waitFor(() => expect(mockedGet).toHaveBeenLastCalledWith('/v1/models/families', {
      params: expect.objectContaining({ q: '白天', artifact_type: 'bmodel', export_id: 77 }),
    }));

    fireEvent.change(search, { target: { value: '' } });
    await waitFor(() => expect(screen.getByTestId('location-probe').textContent).toBe('/models?view=bmodel&export_id=77'));
  });
});

describe('external model registration selectors', () => {
  it('loads remote pages, searches each catalog, and preserves a selected row across pages', async () => {
    const paramsOf = (config: any) => (config?.params || {}) as Record<string, unknown>;
    const dataset = (id: number, name: string) => ({
      id,
      name,
      dataset_label_contract_id: 3,
      dataset_label_contract_revision: 1,
      dataset_label_contract_annotation_kind: 'bbox',
      dataset_label_set_signature: 'helmet-labels',
      dataset_labels: [{ label_key: '安全帽', annotation_kind: 'bbox' }],
    });
    const algorithm = (id: number, name: string, algorithmKey = 'yolo_detection') => ({
      id,
      name,
      algorithm_key: algorithmKey,
      status: 'ACTIVE',
      adapter_available: true,
      trainable: true,
    });
    mockedGet.mockImplementation(async (path: string, config) => {
      const params = paramsOf(config);
      const page = Number(params.page || 0);
      const q = String(params.q || '').trim();
      if (path === '/v1/yolo-imported-models') {
        const row = q
          ? { id: 8, display_name: '匹配外部模型', original_filename: 'match.pt' }
          : { id: 7, display_name: '全量外部模型', original_filename: 'all.pt' };
        return { data: { data: [row], meta_data: { total: q ? 1 : 41, page, size: 20 } } } as never;
      }
      if (path === '/v1/datasets') {
        const rows = q
          ? [dataset(9, '匹配数据集')]
          : [dataset(page === 1 ? 21 : 1, page === 1 ? '第二页数据集' : '第一页数据集')];
        return { data: { data: rows, meta_data: { total: q ? 1 : 41, page, size: 20 } } } as never;
      }
      if (path === '/v1/algorithms') {
        const rows = q
          ? [algorithm(12, '匹配算法', 'classification')]
          : [algorithm(11, 'YOLO 通用目标检测')];
        return { data: { data: rows, meta_data: { total: q ? 1 : 41, page, size: 20 } } } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });

    renderModels('/models?view=external');
    expect(await screen.findByText('全量外部模型')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /登记外部模型/ }));
    expect(await screen.findByRole('dialog')).toBeTruthy();

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-imported-models', {
        params: expect.objectContaining({ page: 0, size: 20 }),
      });
      expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
        params: expect.objectContaining({ page: 0, size: 20 }),
      });
      expect(mockedGet).toHaveBeenCalledWith('/v1/algorithms', {
        params: expect.objectContaining({ status: 'ACTIVE', page: 0, size: 20 }),
      });
    });

    const datasetSelect = screen.getByRole('combobox', { name: '搜索关联数据集' });
    fireEvent.mouseDown(datasetSelect);
    expect(await screen.findByText(/第一页数据集/)).toBeTruthy();
    fireEvent.click(screen.getByText(/第一页数据集/));
    await waitFor(() => expect(screen.getByText(/当前自动匹配：YOLO 通用目标检测/)).toBeTruthy());

    fireEvent.mouseDown(datasetSelect);
    const datasetPageTwo = await waitFor(() => {
      const item = document.querySelector('.ant-select-dropdown .ant-pagination-item-2') as HTMLElement | null;
      expect(item).toBeTruthy();
      return item as HTMLElement;
    });
    fireEvent.click(datasetPageTwo);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
      params: expect.objectContaining({ page: 1, size: 20 }),
    }));
    expect(screen.getAllByText(/第一页数据集/).length).toBeGreaterThan(0);

    fireEvent.change(datasetSelect, { target: { value: '匹配' } });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/datasets', {
      params: expect.objectContaining({ page: 0, size: 20, q: '匹配' }),
    }));
    expect(screen.getAllByText(/第一页数据集/).length).toBeGreaterThan(0);

    const algorithmSelect = screen.getByRole('combobox', { name: '搜索算法能力' });
    if (algorithmSelect.getAttribute('aria-expanded') !== 'true') fireEvent.mouseDown(algorithmSelect);
    fireEvent.change(algorithmSelect, { target: { value: '分类' } });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/algorithms', {
      params: expect.objectContaining({ status: 'ACTIVE', page: 0, size: 20, q: '分类' }),
    }));
    if (algorithmSelect.getAttribute('aria-expanded') !== 'true') {
      fireEvent.mouseDown(algorithmSelect);
    }
    fireEvent.click(await screen.findByText(/匹配算法/));
    await waitFor(() => {
      const control = algorithmSelect.closest('.ant-select') as HTMLElement;
      expect(control.textContent).toContain('匹配算法');
    });

    const importedSelect = screen.getByRole('combobox', { name: '搜索外部权重' });
    fireEvent.mouseDown(importedSelect);
    fireEvent.change(importedSelect, { target: { value: '匹配' } });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-imported-models', {
      params: expect.objectContaining({ page: 0, size: 20, q: '匹配' }),
    }));
  });

  it('keeps an uploaded weight selected when the request returns an unwrapped row', async () => {
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-imported-models') {
        return { data: { data: [], meta_data: { total: 0 } } } as never;
      }
      if (path === '/v1/datasets' || path === '/v1/algorithms') {
        return { data: { data: [], meta_data: { total: 0 } } } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });
    mockedPost.mockResolvedValue({
      data: { id: 42, display_name: '刚上传权重', original_filename: 'new.pt' },
    } as never);

    renderModels('/models?view=external');
    fireEvent.click(screen.getByRole('button', { name: /登记外部模型/ }));
    expect(await screen.findByRole('dialog')).toBeTruthy();
    const uploadInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(uploadInput, {
      target: { files: [new File(['weights'], 'new.pt', { type: 'application/octet-stream' })] },
    });

    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith(
      '/v1/yolo-imported-models',
      expect.any(FormData),
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } }),
    ));
    expect(await screen.findByText(/刚上传权重/)).toBeTruthy();
  });
});

describe('model repository gate presentation', () => {
  it('opens the scoped edge task center from the BModel family row', async () => {
    const model = {
      id: 31,
      display_name: '安全帽检测模型',
      version_no: 3,
      task_id: 42,
      bm1688_export_id: 77,
      status: 'VALIDATED',
      artifacts: [
        { id: 1, artifact_type: 'bmodel', chip_type: 'BM1688', precision: 'INT8', status: 'READY' },
      ],
      quality_gate: passedGates,
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models/families') return familyList([model], { task_id: 7 });
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/models?view=bmodel']}>
          <ModelsPage />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText('安全帽检测模型')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '查看边端任务' }));
    expect(screen.getByTestId('location-probe').textContent).toBe('/edge-task-center?export_id=77&task_id=42');
  });

  it('lists all external imports and deletes only after confirmation', async () => {
    window.history.pushState({}, '', '/models?view=external');
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-imported-models') {
        return {
          data: {
            data: [
              {
                id: 7,
                display_name: '车辆检测外部模型',
                original_filename: 'vehicle.pt',
                file_size: 1048576,
                creator_email: 'annotator@example.com',
                can_delete: true,
                registered_model_count: 0,
              },
              {
                id: 8,
                display_name: '共享安全帽模型',
                original_filename: 'helmet.pt',
                creator_email: 'manager@example.com',
                can_delete: false,
                registered_model_count: 1,
              },
            ],
            meta_data: { total: 2 },
          },
        } as never;
      }
      throw new Error(`unexpected GET ${path}`);
    });
    mockedDelete.mockResolvedValue({ data: { deleted: true } } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText('车辆检测外部模型')).toBeTruthy());
    expect(screen.getByText('共享安全帽模型')).toBeTruthy();
    expect(screen.getAllByText('共 2 个外部模型').length).toBeGreaterThanOrEqual(1);
    expect(mockedGet).toHaveBeenCalledWith('/v1/yolo-imported-models', { params: { page: 0, size: 20 } });
    expect(screen.getAllByRole('button', { name: /删除/ })).toHaveLength(1);

    const confirmSpy = vi.spyOn(Modal, 'confirm').mockReturnValue({ destroy: vi.fn(), update: vi.fn() });
    fireEvent.click(screen.getByRole('button', { name: /删除/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0]).toMatchObject({
      title: '删除外部导入模型',
      okText: '确认删除',
      cancelText: '取消',
    });
    expect(mockedDelete).not.toHaveBeenCalled();

    await (confirmSpy.mock.calls[0][0].onOk as () => Promise<void>)();
    await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith('/v1/yolo-imported-models/7'));
  });

  it('groups iterations into one series and keeps historical version actions independent', async () => {
    const models = [
      {
        id: 102,
        display_name: '人体识别 · 作业 #2',
        version_no: 3,
        training_job_id: 2,
        training_model_weights: 'trained:1',
        parent_model_version_id: 101,
        dataset_version_id: 2,
        dataset_version_no: 2,
        created_at: '2026-10-24T21:40:00',
        metrics: { dataset: { train: 54, val: 16, test: 8 }, best_pt_val_metrics: { map50_95: 0.3384991477 } },
        status: 'CANDIDATE',
        artifacts: [{ id: 12, artifact_type: 'pt', relative_path: 'models/job-2.pt', sha256: 'b'.repeat(64), status: 'READY' }],
        quality_gate: {},
      },
      {
        id: 101,
        display_name: '人体识别 · 作业 #1',
        version_no: 2,
        training_job_id: 1,
        training_model_weights: 'yolo11n.pt',
        dataset_version_id: 1,
        dataset_version_no: 1,
        created_at: '2026-10-24T20:35:00',
        metrics: { dataset: { train: 37, val: 10, test: 5 }, best_pt_val_metrics: { map50_95: 0.263033664 } },
        status: 'PUBLISHED',
        published_catalog_id: 55,
        artifacts: [{ id: 11, artifact_type: 'pt', relative_path: 'models/job-1.pt', sha256: 'a'.repeat(64), status: 'READY' }],
        quality_gate: {},
      },
    ];
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models/families') {
        return familyList(models, { display_name: '人体识别', published_version_id: 101 });
      }
      if (path === '/v1/models/101') return { data: models[1] } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/models']}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('人体识别')).toBeTruthy();
    expect(screen.getByText('共 2 个版本')).toBeTruthy();
    expect(screen.getByText('已发布 v2')).toBeTruthy();
    expect(screen.queryByText('人体识别 · 作业 #1')).toBeNull();
    expect(screen.queryByText('人体识别 · 作业 #2')).toBeNull();

    fireEvent.click(document.querySelector('.ant-table-row-expand-icon') as HTMLButtonElement);
    const latestRow = (await screen.findByText('来源作业 #2')).closest('tr') as HTMLTableRowElement;
    const historicalRow = screen.getByText('来源作业 #1').closest('tr') as HTMLTableRowElement;
    expect(within(latestRow).getByText('基于 v2 迭代')).toBeTruthy();
    expect(within(latestRow).getByText('数据快照 v2')).toBeTruthy();
    expect(within(latestRow).getByText('78 张样本')).toBeTruthy();
    expect(within(latestRow).getByText('26.3% → 33.8%')).toBeTruthy();
    expect(within(latestRow).getByText('较 v2 +7.55 pts')).toBeTruthy();
    expect(within(historicalRow).getByText('基础权重训练')).toBeTruthy();
    expect(within(historicalRow).getByText('数据快照 v1')).toBeTruthy();
    expect(within(historicalRow).getByText('52 张样本')).toBeTruthy();
    expect(within(historicalRow).getByText('mAP50-95：26.3%')).toBeTruthy();
    expect(screen.queryByText('人体识别 · 作业 #1')).toBeNull();
    expect(screen.queryByText('人体识别 · 作业 #2')).toBeNull();
    fireEvent.click(within(historicalRow).getByText('详情'));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('人体识别 · 作业 #1 · v2')).toBeTruthy();
  });

  it('expands the complete family and opens the exact version from a model deep link', async () => {
    window.history.pushState({}, '', '/models?model_id=101');
    const models = [
      { id: 102, display_name: '人体识别 · 作业 #2', version_no: 3, training_job_id: 2, status: 'CANDIDATE', artifacts: [], quality_gate: {} },
      { id: 101, display_name: '人体识别 · 作业 #1', version_no: 2, training_job_id: 1, status: 'CANDIDATE', artifacts: [], quality_gate: {} },
    ];
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models/families') return familyList(models, { display_name: '人体识别' });
      if (path === '/v1/models/101') return { data: models[1] } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('来源作业 #2')).toBeTruthy();
    expect(screen.getByText('来源作业 #1')).toBeTruthy();
    expect(screen.queryByText('人体识别 · 作业 #2')).toBeNull();
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('人体识别 · 作业 #1 · v2')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith('/v1/models/families', {
      params: { page: 0, size: 20, model_id: 101 },
    });
  });

  it('presents the model generation flow as dataset training compile', async () => {
    window.history.pushState({}, '', '/models?view=quality-gates');
    const model = {
      id: 21,
      display_name: '占道经营模型',
      version_no: 1,
      status: 'CANDIDATE',
      dataset_version_id: 8,
      training_job_id: 66,
      quality_gate: { training: 'PENDING' },
      artifacts: [{ id: 1, artifact_type: 'pt', relative_path: 'best.pt', sha256: 'a'.repeat(64), status: 'READY' }],
      quality_evidence: [],
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models') return { data: { data: [model], meta_data: { total: 1 } } } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText('占道经营模型')).toBeTruthy());
    expect(mockedGet).toHaveBeenCalledWith('/v1/models', {
      params: { page: 0, size: 100, source: 'training' },
    });
    expect(
      mockedGet.mock.calls.some(
        (call) => 'project_id' in ((call[1] as { params?: Record<string, unknown> } | undefined)?.params || {}),
      ),
    ).toBe(false);
    expect(screen.getByText('模型生成流程')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /待处理/ })).toBeNull();
    expect(screen.getByText('当前步骤')).toBeTruthy();
    expect(screen.getByText('查看处理')).toBeTruthy();
    expect(screen.getByText('已完成 2 / 3')).toBeTruthy();
    expect(screen.getByText('编译')).toBeTruthy();
    expect(screen.queryByText('发布门禁')).toBeNull();
  });

  it('searches the quality-gate queue on the server and resets the queue search when cleared', async () => {
    window.history.pushState({}, '', '/models?view=quality-gates');
    const firstModel = {
      id: 21,
      display_name: '占道经营模型',
      version_no: 1,
      status: 'CANDIDATE',
      dataset_version_id: 8,
      training_job_id: 66,
      quality_gate: { training: 'PENDING' },
      artifacts: [{ id: 1, artifact_type: 'pt', relative_path: 'best.pt', sha256: 'a'.repeat(64), status: 'READY' }],
      quality_evidence: [],
    };
    const searchedModel = { ...firstModel, id: 22, display_name: '夜间道路模型', training_job_id: 67 };
    mockedGet.mockImplementation(async (path: string, config) => {
      if (path !== '/v1/models') throw new Error(`unexpected GET ${path}`);
      const params = config?.params as Record<string, unknown>;
      return {
        data: { data: params.q === 'night' ? [searchedModel] : [firstModel], meta_data: { total: 1 } },
      } as never;
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('占道经营模型')).toBeTruthy();
    const search = screen.getByRole('textbox', { name: '搜索质量门禁模型' });
    fireEvent.change(search, { target: { value: 'night' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
    expect(await screen.findByText('夜间道路模型')).toBeTruthy();
    expect(mockedGet).toHaveBeenLastCalledWith('/v1/models', {
      params: { page: 0, size: 100, source: 'training', q: 'night' },
    });

    const clear = search.parentElement?.querySelector('.ant-input-clear-icon');
    expect(clear).toBeTruthy();
    fireEvent.mouseDown(clear!);
    fireEvent.click(clear!);
    await waitFor(() => expect((search as HTMLInputElement).value).toBe(''));
    expect(await screen.findByText('占道经营模型')).toBeTruthy();
  });

  it('keeps PT details focused on training output', async () => {
    const model = {
      id: 11,
      display_name: '待发布模型',
      version_no: 1,
      status: 'VALIDATED',
      quality_gate: { ...passedGates },
      artifacts: [
        { id: 1, artifact_type: 'onnx', relative_path: 'model.onnx', sha256: 'a'.repeat(64), status: 'PROCESSING' },
      ],
      quality_evidence: [{ id: 1, gate_type: 'edge', revision: 1, status: 'PASS' }],
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models/families') return familyList([model]);
      if (path === '/v1/models/11') return { data: model } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText('待发布模型')).toBeTruthy());
    fireEvent.click(screen.getByText('最新详情').closest('button') as HTMLButtonElement);
    await waitFor(() => expect(screen.getByText('训练权重尚未生成，暂时不能下载 PT。')).toBeTruthy());
    expect(screen.queryByRole('button', { name: '发布模型' })).toBeNull();
    expect(mockedPost).not.toHaveBeenCalledWith('/v1/model-versions/11/publish', {});
    expect(screen.getAllByText('训练文件生成中').length).toBeGreaterThan(0);
    expect(screen.queryByText('边端模型质量')).toBeNull();
    expect(screen.queryByText('模型生成进度')).toBeNull();
  });

  it('lets an authorized labeling user publish a model with three BM1688 artifacts', async () => {
    window.history.pushState({}, '', '/models?view=bmodel');
    const model = {
      id: 31,
      display_name: '安全帽检测模型',
      version_no: 3,
      status: 'VALIDATED',
      dataset_version_id: 18,
      quality_gate: { ...passedGates },
      artifacts: [
        {
          id: 1,
          artifact_type: 'bmodel',
          chip_type: 'BM1688',
          precision: 'FP32',
          relative_path: 'model_fp32.bmodel',
          sha256: 'a'.repeat(64),
          status: 'READY',
          metadata: { export_id: 1 },
        },
        {
          id: 2,
          artifact_type: 'bmodel',
          chip_type: 'BM1688',
          precision: 'FP16',
          relative_path: 'model_fp16.bmodel',
          sha256: 'b'.repeat(64),
          status: 'READY',
          metadata: { export_id: 1 },
        },
        {
          id: 3,
          artifact_type: 'bmodel',
          chip_type: 'BM1688',
          precision: 'INT8',
          relative_path: 'model_int8.bmodel',
          sha256: 'c'.repeat(64),
          status: 'READY',
          metadata: { export_id: 1 },
        },
      ],
      quality_evidence: [],
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models/families') return familyList([model]);
      if (path === '/v1/models/31') return { data: model } as never;
      if (path === '/v1/yolo-aicm-publish/environments')
        return {
          data: {
            active_environment_id: 'local-aicm',
            environments: [{ id: 'local-aicm', name: '本地 AICM', ready_to_publish: true }],
          },
        } as never;
      if (path === '/v1/yolo-aicm-publish/publish-jobs/job-1')
        return { data: { job_id: 'job-1', status: 'COMPLETED', result: { ok: true, model_set_id: 534 } } } as never;
      throw new Error(`unexpected GET ${path}`);
    });
    mockedPost.mockImplementation(async (path: string) => {
      if (path === '/v1/yolo-aicm-publish/publish')
        return { data: { job_id: 'job-1', status: 'PROCESSING' } } as never;
      throw new Error(`unexpected POST ${path}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText('安全帽检测模型')).toBeTruthy());
    fireEvent.click(screen.getByText('最新详情').closest('button') as HTMLButtonElement);
    expect(await screen.findByRole('button', { name: /配置边端检查设备/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: '新建边端检查' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '查看检查结果' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: '查看边端任务' }).length).toBeGreaterThanOrEqual(1);
    const publishButton = await screen.findByRole('button', { name: '发布模型' });
    expect((publishButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(publishButton);
    expect(await screen.findByText('发布模型到 AICM')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确认发布' }));
    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/v1/yolo-aicm-publish/publish', {
        export_id: 1,
        environment_id: 'local-aicm',
        bmodel_precision: 'int8',
      }),
    );
  });

  it('offers PT download before the model is published', async () => {
    const model = {
      id: 42,
      display_name: '未发布训练权重',
      version_no: 2,
      status: 'CANDIDATE',
      artifacts: [
        {
          id: 1,
          artifact_type: 'pt',
          relative_path: 'models/best.pt',
          sha256: 'a'.repeat(64),
          status: 'READY',
        },
      ],
      quality_gate: {},
    };
    mockedGet.mockImplementation(async (path: string) => {
      if (path === '/v1/models/families') return familyList([model]);
      if (path === '/v1/model-versions/42/artifacts/pt/download') return new Blob(['pt']) as never;
      throw new Error(`unexpected GET ${path}`);
    });
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:test') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText('未发布训练权重')).toBeTruthy());
    expect(screen.queryByText('PT 下载')).toBeNull();
    fireEvent.click(document.querySelector('.ant-table-row-expand-icon') as HTMLButtonElement);
    const downloadButton = (await screen.findByText('下载 PT')).closest('button') as HTMLButtonElement;
    expect(downloadButton.disabled).toBe(false);
    fireEvent.click(downloadButton);
    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/v1/model-versions/42/artifacts/pt/download', { responseType: 'blob' }),
    );
  });

  it('wraps the model header action on narrow layouts', async () => {
    mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
          <ModelsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole('heading', { name: '模型仓库', level: 2 })).toBeTruthy());
    expect(screen.queryByRole('button', { name: /登记外部模型/ })).toBeNull();
    const heading = screen.getByRole('heading', { name: '模型仓库', level: 2 });
    const header = heading.closest('.ant-space') as HTMLElement;
    expect(header).toBeTruthy();
    expect(header.style.flexWrap).toBe('wrap');
    expect(header.style.width).toBe('100%');
  });
});
