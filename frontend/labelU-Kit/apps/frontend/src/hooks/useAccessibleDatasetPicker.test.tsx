import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getAccessibleDataset,
  getAccessibleDatasetsPage,
  type DatasetOption,
} from '@/api/services/task';

import useAccessibleDatasetPicker from './useAccessibleDatasetPicker';

vi.mock('@/api/services/task', () => ({
  getAccessibleDataset: vi.fn(),
  getAccessibleDatasetsPage: vi.fn(),
}));

const mockedGetPage = vi.mocked(getAccessibleDatasetsPage);
const mockedGetOne = vi.mocked(getAccessibleDataset);

const makeDataset = (id: number, name = `数据集-${id}`): DatasetOption => ({
  id,
  name,
  dataset_label_contract_id: id * 10,
  dataset_label_contract_revision: 1,
});

function Harness({ value }: { value?: number }) {
  const picker = useAccessibleDatasetPicker({ value, queryKey: 'picker-test', pageSize: 20 });
  return (
    <div>
      <input
        aria-label="搜索数据集"
        value={picker.searchValue}
        onChange={(event) => picker.onSearch(event.target.value)}
      />
      <button type="button" onClick={() => picker.onPageChange(2)}>第三页</button>
      <button type="button" onClick={() => picker.onChange(1)}>选择 1</button>
      <div data-testid="options">{picker.options.map((item) => item.name).join('|')}</div>
      <div data-testid="selected">{picker.selectedDataset?.name || ''}</div>
      <div data-testid="total">{picker.total}</div>
    </div>
  );
}

function renderHarness(value?: number) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness value={value} />
    </QueryClientProvider>,
  );
}

describe('useAccessibleDatasetPicker', () => {
  beforeEach(() => {
    mockedGetPage.mockResolvedValue({
      data: [makeDataset(1)],
      meta_data: { total: 41, page: 0, size: 20 },
    });
    mockedGetOne.mockImplementation(async (id) => makeDataset(id));
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('sends trimmed server search and keeps the total for pagination', async () => {
    renderHarness();
    await waitFor(() => expect(screen.getByTestId('total').textContent).toBe('41'));

    fireEvent.change(screen.getByLabelText('搜索数据集'), { target: { value: '  道路  ' } });
    await waitFor(() => expect(mockedGetPage).toHaveBeenLastCalledWith({ page: 0, size: 20, q: '道路' }));

    fireEvent.click(screen.getByRole('button', { name: '第三页' }));
    await waitFor(() => expect(mockedGetPage).toHaveBeenLastCalledWith({ page: 2, size: 20, q: '道路' }));
  });

  it('loads and preserves a selected dataset that is outside the current page', async () => {
    renderHarness(99);
    await waitFor(() => expect(mockedGetOne).toHaveBeenCalledWith(99));
    await waitFor(() => expect(screen.getByTestId('selected').textContent).toBe('数据集-99'));

    fireEvent.click(screen.getByRole('button', { name: '选择 1' }));
    await waitFor(() => expect(screen.getByTestId('options').textContent).toContain('数据集-99'));
  });
});

