import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DataSourceResponse } from '@/api/types';

import S3ImportModal from './S3ImportModal';

import { getDataSources, listS3Objects } from '@/api/services/datasource';

vi.mock('@/api/services/datasource', () => ({
  getDataSources: vi.fn(),
  listS3Objects: vi.fn(),
}));

vi.mock('@/api/mutations/datasource', () => ({
  useImportS3SamplesMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@labelu/i18n', () => {
  const translation = { t: (key: string) => key };
  return { useTranslation: () => translation, i18n: translation };
});

vi.mock('@labelu/components-react', () => {
  const Layout = ({ children, ...props }: { children?: unknown; [key: string]: unknown }) => (
    <div {...props}>{children}</div>
  );
  Layout.Item = Layout;
  Layout.Content = Layout;
  return { FlexLayout: Layout };
});

vi.mock('@labelu/formatter', () => ({
  default: { format: (_name: string, value: string) => value },
}));

const mockedGetDataSources = vi.mocked(getDataSources);
const mockedListS3Objects = vi.mocked(listS3Objects);

const makeDataSource = (id: number, name = `source-${id}`): DataSourceResponse => ({
  id,
  name,
  type: 'S3',
  bucket: `bucket-${id}`,
});

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <S3ImportModal open onClose={vi.fn()} taskId={1} mediaType="IMAGE" onImportSuccess={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('S3ImportModal data source picker', () => {
  beforeEach(() => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as typeof window.matchMedia;
    mockedGetDataSources.mockImplementation(async ({ page = 1, size = 20, q }) => ({
      data: [makeDataSource(page, q ? `road-${page}` : `source-${page}`)],
      meta_data: { total: 41, page: page - 1, size },
    }));
    mockedListS3Objects.mockResolvedValue({
      data: { objects: [], next_page_token: null, truncated: false },
    } as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('requests a trimmed server search and a later data-source page', async () => {
    renderModal();

    await waitFor(() => expect(mockedGetDataSources).toHaveBeenLastCalledWith({ page: 1, size: 20, q: undefined }));

    const combobox = screen.getByRole('combobox', { name: 'selectDataSource' });
    fireEvent.mouseDown(combobox);
    fireEvent.change(combobox, { target: { value: '  road  ' } });

    await waitFor(() => expect(mockedGetDataSources).toHaveBeenLastCalledWith({ page: 1, size: 20, q: 'road' }));

    const secondPage = await screen.findByTitle('2');
    fireEvent.click(secondPage);
    await waitFor(() => expect(mockedGetDataSources).toHaveBeenLastCalledWith({ page: 2, size: 20, q: 'road' }));
  });

  it('keeps the selected data source visible after moving to another page', async () => {
    renderModal();

    const combobox = await screen.findByRole('combobox', { name: 'selectDataSource' });
    fireEvent.mouseDown(combobox);
    const firstOption = await screen.findByText('source-1 (bucket-1)');
    fireEvent.click(firstOption);

    await waitFor(() => expect(screen.getAllByText('source-1 (bucket-1)').length).toBeGreaterThan(0));
    fireEvent.mouseDown(combobox);
    const secondPage = await screen.findByTitle('2');
    fireEvent.click(secondPage);

    await waitFor(() => expect(screen.getAllByText('source-1 (bucket-1)').length).toBeGreaterThan(0));
  });
});
