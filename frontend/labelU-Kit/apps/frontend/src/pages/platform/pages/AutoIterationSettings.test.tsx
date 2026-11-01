import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import request from '@/api/request';

import AutoIterationSettings, {
  buildAutoIterationSaveBody,
  diffAutoIterationValues,
  formatAutoIterationValue,
  normalizeAutoIterationSettingsPayload,
} from './AutoIterationSettings';

vi.mock('@/api/request', () => ({
  default: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
}));

const mockedGet = vi.mocked(request.get);
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
  mockedPatch.mockReset();
});

const BASE_VALUES = {
  enabled: false,
  metric_basis: 'val_recall',
  pass_threshold: 0.6,
  warn_threshold: 0.5,
  max_rounds: 4,
  no_improvement_rounds: 2,
  min_improvement_delta: 0.02,
  allow_conditional_pass: true,
  require_quality_for_compile: true,
  min_class_recall_enabled: false,
  min_class_recall: 0.3,
  precision_floor_enabled: false,
  precision_floor: 0.7,
  max_regression: 0.05,
  eval_conf: 0.25,
};

function settingsPayload(overrides: Record<string, unknown> = {}) {
  return {
    values: { ...BASE_VALUES },
    defaults: { ...BASE_VALUES },
    sources: { enabled: 'db', pass_threshold: 'default' },
    locked_keys: [] as string[],
    can_edit: true,
    max_iterations_after_first: 3,
    metric_basis_options: [
      { value: 'val_recall', label: '验证集召回率' },
      { value: 'overall_recall', label: '整体加权召回率' },
      { value: 'map50_95', label: 'mAP50-95（历史口径）' },
    ],
    threshold_hint: '达到该阈值即判定通过：停止迭代并允许进入编译',
    max_rounds_hint: '该值包含首轮：4 = 首轮 + 3 次自动迭代',
    ...overrides,
  };
}

/** 页面测试里 request 被 mock 成 axios 响应外壳，组件需兼容这一层包装。 */
function axiosEnvelope(payload: unknown) {
  return { data: { data: payload } } as never;
}

function renderPanel() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AutoIterationSettings />
    </QueryClientProvider>,
  );
}

describe('AutoIterationSettings helpers', () => {
  it('normalizes the runtime envelope and the axios-wrapped test envelope', () => {
    const payload = settingsPayload();
    expect(normalizeAutoIterationSettingsPayload(payload)?.values.pass_threshold).toBe(0.6);
    expect(normalizeAutoIterationSettingsPayload({ data: payload })?.values.max_rounds).toBe(4);
    expect(normalizeAutoIterationSettingsPayload({ data: { data: payload } })?.can_edit).toBe(true);
  });

  it('diffs only changed keys and keeps the pre-edit value for the conflict check', () => {
    const changes = diffAutoIterationValues(
      { ...BASE_VALUES, pass_threshold: 0.7, max_rounds: 6 },
      BASE_VALUES,
    );
    expect(changes).toEqual({ pass_threshold: 0.7, max_rounds: 6 });
    expect(buildAutoIterationSaveBody(changes, BASE_VALUES, ' 业务调整 ')).toEqual({
      pass_threshold: 0.7,
      max_rounds: 6,
      reason: '业务调整',
      expected_values: { pass_threshold: 0.6, max_rounds: 4 },
    });
    expect(formatAutoIterationValue('pass_threshold', 0.7, [])).toBe('70%');
    expect(formatAutoIterationValue('enabled', true, [])).toBe('开启');
  });
});

describe('AutoIterationSettings panel', () => {
  it('renders the effective values returned by the backend', async () => {
    mockedGet.mockResolvedValue(axiosEnvelope(settingsPayload()));

    renderPanel();

    expect(await screen.findByText('自动迭代设置')).toBeTruthy();
    const passInput = (await screen.findByLabelText('达标阈值')) as HTMLInputElement;
    expect(Number(passInput.value)).toBe(0.6);
    expect((screen.getByLabelText('最大迭代次数') as HTMLInputElement).value).toBe('4');
    expect(screen.getAllByText('60%').length).toBeGreaterThan(0);
    expect(screen.getByText(/含首轮：4 = 首轮 \+ 3 次自动迭代/)).toBeTruthy();
    expect(screen.getByRole('switch', { name: '自动迭代开关' }).getAttribute('aria-checked')).toBe('false');
    expect(mockedGet).toHaveBeenCalledWith('/v1/auto-iteration-settings');
  });

  it('disables every control and explains the read-only mode when the account cannot edit', async () => {
    mockedGet.mockResolvedValue(axiosEnvelope(settingsPayload({ can_edit: false })));

    renderPanel();

    expect(await screen.findByText('仅管理员可修改')).toBeTruthy();
    expect(((await screen.findByLabelText('达标阈值')) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('最大迭代次数') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole('switch', { name: '自动迭代开关' }).hasAttribute('disabled')).toBe(true);
    expect((screen.getByRole('button', { name: /保存设置/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /恢复默认/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps environment-locked keys read-only while the rest of the form stays editable', async () => {
    mockedGet.mockResolvedValue(
      axiosEnvelope(settingsPayload({ can_edit: true, locked_keys: ['max_rounds'] })),
    );

    renderPanel();

    expect(await screen.findByText('部分配置已被环境变量锁定')).toBeTruthy();
    expect((screen.getByLabelText('最大迭代次数') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('达标阈值') as HTMLInputElement).disabled).toBe(false);
  });

  it('submits only the changed values plus the required reason through PATCH', async () => {
    mockedGet.mockResolvedValue(axiosEnvelope(settingsPayload()));
    mockedPatch.mockResolvedValue(
      axiosEnvelope(settingsPayload({ values: { ...BASE_VALUES, pass_threshold: 0.7 } })),
    );

    renderPanel();

    const passInput = (await screen.findByLabelText('达标阈值')) as HTMLInputElement;
    fireEvent.change(passInput, { target: { value: '0.7' } });

    fireEvent.click(screen.getByRole('button', { name: /保存设置/ }));

    const reasonInput = await screen.findByLabelText('修改原因');
    fireEvent.change(reasonInput, { target: { value: '业务确认达标线调整为 70%' } });
    fireEvent.click(screen.getByRole('button', { name: /确认保存/ }));

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith(
        '/v1/auto-iteration-settings',
        expect.objectContaining({
          pass_threshold: 0.7,
          reason: '业务确认达标线调整为 70%',
          expected_values: { pass_threshold: 0.6 },
        }),
      ),
    );
    expect(await screen.findByText('已保存，下一轮判定生效')).toBeTruthy();
  });

  it('renders the configuration change history from the server', async () => {
    mockedGet.mockImplementation(((url: string) => {
      if (String(url).includes('/history')) {
        return Promise.resolve({
          data: {
            data: [
              {
                id: 11,
                item_key: 'pass_threshold',
                before: 0.6,
                after: 0.7,
                change_type: 'update',
                change_reason: '业务确认达标线调整为 70%',
                changed_by: 1,
                changed_at: '2026-10-01T10:00:00Z',
              },
            ],
            meta_data: { total: 1, page: 0, size: 20 },
          },
        });
      }
      return Promise.resolve(axiosEnvelope(settingsPayload()));
    }) as never);

    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /查看变更历史/ }));

    expect(await screen.findByText('业务确认达标线调整为 70%')).toBeTruthy();
    expect(screen.getByText('60% → 70%')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith('/v1/auto-iteration-settings/history', {
      params: { page: 0, size: 20 },
    });
  });
});
