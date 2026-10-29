import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import request from '@/api/request';
import { PredictionReviewQueue } from './annotationReview';

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

window.matchMedia = ((query: string) => ({
  matches: false, media: query, onchange: null, addListener: () => undefined,
  removeListener: () => undefined, addEventListener: () => undefined,
  removeEventListener: () => undefined, dispatchEvent: () => false,
})) as typeof window.matchMedia;

afterEach(() => {
  cleanup();
  mockedGet.mockReset();
  mockedPost.mockReset();
});

function renderPage(prediction: Record<string, unknown>) {
  mockedGet.mockImplementation(async (path: string) => {
    if (path === '/v1/prediction-sets') return { data: { data: [prediction] } } as never;
    return { data: { data: [] } } as never;
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<MemoryRouter><QueryClientProvider client={queryClient}><PredictionReviewQueue /></QueryClientProvider></MemoryRouter>);
}

describe('prediction review queue', () => {
  it('loads predictions in the current account scope and enters review', async () => {
    mockedPost.mockResolvedValue({ data: { data: { id: 31, review_status: 'IN_REVIEW' } } } as never);
    renderPage({ id: 31, provider_job_id: 12, sample_id: 99, item_count: 2, review_status: 'PENDING' });
    await waitFor(() => expect(screen.getByText('进入审核')).toBeTruthy());
    expect(mockedGet).toHaveBeenCalledWith('/v1/prediction-sets', expect.objectContaining({
      params: expect.objectContaining({ page: 0, size: 20 }),
    }));
    expect(mockedGet).toHaveBeenCalledWith('/v1/prediction-sets', expect.objectContaining({ params: expect.not.objectContaining({ project_id: expect.anything() }) }));
    fireEvent.click(screen.getByRole('button', { name: '进入审核' }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/prediction-sets/31/review', { target_status: 'IN_REVIEW', reason: undefined }, { headers: { 'Idempotency-Key': 'prediction-review-ui:31:IN_REVIEW' } }));
  });

  it('passes the applied search keyword to the prediction-set query and clears it immediately', async () => {
    renderPage({ id: 34, provider_job_id: 15, sample_id: 102, item_count: 1, review_status: 'PENDING' });
    const search = await screen.findByRole('textbox', { name: '搜索 AI 预测审核' });
    fireEvent.change(search, { target: { value: 'camera' } });
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/prediction-sets', expect.objectContaining({
      params: expect.objectContaining({ page: 0, size: 20, q: 'camera' }),
    })));

    fireEvent.change(search, { target: { value: '' } });
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/v1/prediction-sets', expect.objectContaining({
      params: expect.objectContaining({ page: 0, size: 20 }),
    })));
    expect(mockedGet.mock.calls.some(([, config]) => Boolean((config as { params?: { q?: string } } | undefined)?.params?.q === ''))).toBe(false);
  });

  it('accepts and rejects in-review predictions with distinct transitions', async () => {
    mockedPost.mockResolvedValue({ data: { data: { review_status: 'ACCEPTED' } } } as never);
    renderPage({ id: 32, provider_job_id: 13, sample_id: 100, item_count: 1, review_status: 'IN_REVIEW' });
    await waitFor(() => expect(screen.getByText('通过')).toBeTruthy());
    fireEvent.click(screen.getByText('通过'));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/prediction-sets/32/review', { target_status: 'ACCEPTED', reason: undefined }, { headers: { 'Idempotency-Key': 'prediction-review-ui:32:ACCEPTED' } }));
  });

  it('projects accepted predictions only when a sample is bound', async () => {
    mockedPost.mockResolvedValue({ data: { data: { projected: true } } } as never);
    renderPage({ id: 33, provider_job_id: 14, sample_id: 101, item_count: 3, review_status: 'ACCEPTED' });
    await waitFor(() => expect(screen.getByText('投影到画布')).toBeTruthy());
    fireEvent.click(screen.getByText('投影到画布'));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/prediction-sets/33/project-legacy', {}, { headers: { 'Idempotency-Key': 'prediction-projection-ui:33' } }));
  });
});
