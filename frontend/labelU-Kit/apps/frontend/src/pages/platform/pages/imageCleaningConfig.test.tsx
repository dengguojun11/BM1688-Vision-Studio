import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import request from '@/api/request';

import ImageCleaningConfigPanel, { isValidImageCleaningThresholdOrder } from './imageCleaningConfig';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), patch: vi.fn() } }));

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

const policy = {
  active_preset: 'standard',
  presets: {
    simple: { threshold: 0.96 },
    standard: { threshold: 0.9 },
    strict: { threshold: 0.82 },
  },
  revision: 7,
  algorithm_version: 'visual-v1',
  config_backend_label: '系统配置中心',
} as const;

function renderPanel() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ImageCleaningConfigPanel />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('ImageCleaningConfigPanel', () => {
  it('requires simple >= standard >= strict thresholds', () => {
    expect(
      isValidImageCleaningThresholdOrder({ simple_threshold: 0.98, standard_threshold: 0.95, strict_threshold: 0.9 }),
    ).toBe(true);
    expect(
      isValidImageCleaningThresholdOrder({ simple_threshold: 0.9, standard_threshold: 0.95, strict_threshold: 0.8 }),
    ).toBe(false);
  });

  it('loads the policy and shows the current rule, percentages, and descriptions', async () => {
    mockedGet.mockResolvedValue({ data: { data: policy } } as never);

    renderPanel();

    expect((await screen.findAllByText('标准清洗', {}, { timeout: 3000 })).length).toBeGreaterThan(0);
    expect(screen.getAllByText('标准清洗').length).toBeGreaterThan(0);
    expect(screen.getAllByText('90%').length).toBeGreaterThan(0);
    expect(screen.getByText('系统配置中心')).toBeTruthy();
    expect(screen.getByText('低强度、较宽松地过滤重复样本，尽量保留更多图片。')).toBeTruthy();
    expect(mockedGet).toHaveBeenCalledWith('/v1/image-cleaning-policy');
  });

  it('saves all three thresholds and the selected preset', async () => {
    mockedGet.mockResolvedValue({ data: policy } as never);
    mockedPatch.mockResolvedValue({ data: { data: { ...policy, active_preset: 'strict' } } } as never);

    renderPanel();

    await screen.findByRole('radio', { name: '标准清洗' });
    fireEvent.click(screen.getByLabelText('严格清洗'));
    fireEvent.change(screen.getByLabelText('简单清洗相似度阈值'), { target: { value: '0.94' } });
    fireEvent.change(screen.getByLabelText('标准清洗相似度阈值'), { target: { value: '0.91' } });
    fireEvent.change(screen.getByLabelText('严格清洗相似度阈值'), { target: { value: '0.84' } });
    fireEvent.click(screen.getByRole('button', { name: /保存配置/ }));

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith('/v1/image-cleaning-policy', {
        active_preset: 'strict',
        expected_revision: 7,
        presets: {
          simple: { threshold: 0.94 },
          standard: { threshold: 0.91 },
          strict: { threshold: 0.84 },
        },
      }),
    );
  });

  it('renders a retryable load error', async () => {
    mockedGet.mockRejectedValue({ response: { status: 403 } });

    renderPanel();

    expect(await screen.findByText('图片清洗配置加载失败')).toBeTruthy();
    expect(screen.getByText('只有管理人员可以维护图片清洗配置')).toBeTruthy();
    expect(screen.getByRole('button', { name: /重\s*试/ })).toBeTruthy();
  });

  it('keeps an inline error when saving fails', async () => {
    mockedGet.mockResolvedValue({ data: policy } as never);
    mockedPatch.mockRejectedValue({ response: { status: 409 } });

    renderPanel();

    await screen.findByRole('radio', { name: '标准清洗' });
    fireEvent.click(screen.getByRole('button', { name: /保存配置/ }));

    expect(await screen.findByText('图片清洗配置已被其他管理人员更新，请刷新后重试')).toBeTruthy();
    expect(screen.getByText('保存失败')).toBeTruthy();
  });
});
