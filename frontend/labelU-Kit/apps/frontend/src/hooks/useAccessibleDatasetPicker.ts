import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getAccessibleDataset, getAccessibleDatasetsPage, type DatasetOption } from '@/api/services/task';

export const ACCESSIBLE_DATASET_PAGE_SIZE = 20;

export interface UseAccessibleDatasetPickerOptions {
  value?: number | null;
  queryKey?: string;
  pageSize?: number;
  enabled?: boolean;
}

export interface AccessibleDatasetPickerState {
  options: DatasetOption[];
  selectedDataset: DatasetOption | null;
  total: number;
  page: number;
  pageSize: number;
  searchValue: string;
  loading: boolean;
  error: unknown;
  onSearch: (value: string) => void;
  onPageChange: (page: number) => void;
  onChange: (value: number | string | null | undefined) => DatasetOption | null;
  resolve: (datasetId: number | string | null | undefined) => DatasetOption | null;
}

const normalizeDatasetId = (value: number | string | null | undefined) => {
  const normalized = Number(value || 0);
  return Number.isInteger(normalized) && normalized > 0 ? normalized : undefined;
};

/**
 * Shared server-backed dataset picker state for forms and filter controls.
 *
 * The list query is intentionally paged. A separate detail query keeps the
 * controlled value renderable after the user searches or moves to another
 * page, which is important for long-running edit forms.
 */
export default function useAccessibleDatasetPicker({
  value,
  queryKey = 'default',
  pageSize = ACCESSIBLE_DATASET_PAGE_SIZE,
  enabled = true,
}: UseAccessibleDatasetPickerOptions = {}): AccessibleDatasetPickerState {
  const normalizedValue = normalizeDatasetId(value);
  const [page, setPage] = useState(0);
  const [searchValue, setSearchValue] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedOption, setSelectedOption] = useState<DatasetOption | null>(null);
  const previousValueRef = useRef<number | undefined>(normalizedValue);

  const listQuery = useQuery({
    queryKey: ['accessible-datasets', queryKey, page, pageSize, searchQuery],
    queryFn: () =>
      getAccessibleDatasetsPage({
        page,
        size: pageSize,
        q: searchQuery || undefined,
      }),
    enabled,
    staleTime: 60_000,
    retry: false,
    placeholderData: (previous) => previous,
  });

  const selectedQuery = useQuery({
    queryKey: ['accessible-dataset', queryKey, normalizedValue],
    queryFn: () => getAccessibleDataset(normalizedValue as number),
    enabled: enabled && normalizedValue !== undefined,
    staleTime: 60_000,
    retry: false,
  });

  // A pending option may be opened for confirmation before the form value is
  // committed. Only clear the local option cache when the controlled value
  // actually changes, so that pending option remains visible in the modal.
  useEffect(() => {
    if (previousValueRef.current === normalizedValue) return;
    previousValueRef.current = normalizedValue;
    if (normalizedValue === undefined || selectedOption?.id !== normalizedValue) {
      setSelectedOption(null);
    }
  }, [normalizedValue, selectedOption?.id]);

  const options = useMemo(() => {
    const byId = new Map<number, DatasetOption>();
    [...(listQuery.data?.data || []), selectedQuery.data, selectedOption].forEach((item) => {
      if (item?.id) byId.set(Number(item.id), item);
    });
    return Array.from(byId.values());
  }, [listQuery.data?.data, selectedOption, selectedQuery.data]);

  const resolve = useCallback(
    (datasetId: number | string | null | undefined) => {
      const normalizedId = normalizeDatasetId(datasetId);
      return normalizedId === undefined ? null : options.find((item) => Number(item.id) === normalizedId) || null;
    },
    [options],
  );

  const onSearch = useCallback((valueToSearch: string) => {
    setSearchValue(valueToSearch);
    setSearchQuery(valueToSearch.trim());
    setPage(0);
  }, []);

  const onPageChange = useCallback((nextPage: number) => {
    setPage(Math.max(0, Number(nextPage) || 0));
  }, []);

  const onChange = useCallback(
    (nextValue: number | string | null | undefined) => {
      const nextOption = resolve(nextValue);
      setSelectedOption(nextOption);
      onSearch('');
      return nextOption;
    },
    [onSearch, resolve],
  );

  const selectedDataset = resolve(normalizedValue);

  return {
    options,
    selectedDataset,
    total: Number(listQuery.data?.meta_data?.total ?? 0),
    page,
    pageSize,
    searchValue,
    loading: listQuery.isLoading || listQuery.isFetching || selectedQuery.isFetching,
    error: listQuery.error || selectedQuery.error,
    onSearch,
    onPageChange,
    onChange,
    resolve,
  };
}
