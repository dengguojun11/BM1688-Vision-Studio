import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import request from '@/api/request';
import ProvidersPage, { ProviderEvidenceEditor, ProviderRuntimeActions } from './providers';

const resourcePageProps = vi.hoisted(() => ({ current: null as any }));

vi.mock('@/api/request', () => ({ default: { get: vi.fn(), patch: vi.fn(), post: vi.fn() } }));
vi.mock('../ResourcePage', () => ({
  default: (props: any) => {
    resourcePageProps.current = props;
    return <div data-testid="resource">{props.beforeTable}</div>;
  },
}));
const mockedGet = vi.mocked(request.get);
const mockedPatch = vi.mocked(request.patch);
const mockedPost = vi.mocked(request.post);
window.matchMedia = (() => ({ matches: false, media: '', onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
afterEach(() => { cleanup(); resourcePageProps.current = null; mockedGet.mockReset(); mockedPatch.mockReset(); mockedPost.mockReset(); });

it('passes the applied provider search to the server-backed resource query', async () => {
  mockedGet.mockResolvedValue({ data: { data: [], meta_data: { total: 0 } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ProvidersPage />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(resourcePageProps.current?.queryFn).toBeTypeOf('function'));
  await resourcePageProps.current.queryFn(undefined, 3, 20, 'bm1688');
  expect(mockedGet).toHaveBeenCalledWith('/v1/providers', {
    params: { status: 'ACTIVE', page: 3, size: 20, q: 'bm1688' },
  });
});

it('submits normalized provider evidence and runtime snapshot', async () => {
  mockedPatch.mockResolvedValue({ data: { data: { id: 1 } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ProviderEvidenceEditor provider={{ id: 1, provider_key: 'YOLO', provider_version: '11.0.1', display_name: 'YOLO', status: 'ACTIVE' }} open onClose={() => undefined} /></QueryClientProvider>);
  fireEvent.change(screen.getByLabelText('镜像 SHA256'), { target: { value: 'A'.repeat(64) } });
  fireEvent.change(screen.getByLabelText('工具链版本'), { target: { value: ' sophgo-3.2 ' } });
  fireEvent.click(screen.getByLabelText('已通过 HIL 验证'));
  fireEvent.change(screen.getByLabelText('HIL 证据 JSON'), { target: { value: '{"status":"PASSED","evidence_id":"hil-1"}' } });
  fireEvent.change(screen.getByLabelText('运行时证据 JSON'), { target: { value: '{"status":"READY"}' } });
  fireEvent.click(screen.getByRole('button', { name: '保存证据' }));
  await waitFor(() => expect(mockedPatch).toHaveBeenCalledWith('/v1/providers/yolo/evidence', expect.objectContaining({ provider_version: '11.0.1', image_digest: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', toolchain_version: 'sophgo-3.2', hil_verified: true, hil_evidence: { status: 'PASSED', evidence_id: 'hil-1' }, runtime_evidence: { status: 'READY' } })));
});

it('runs runtime probe and local HIL without presenting local evidence as production ready', async () => {
  mockedPost.mockResolvedValue({ data: { data: { runtime_ready: true, production_ready: false } } } as never);
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ProviderRuntimeActions provider={{ provider_key: 'LocateAnything', provider_version: '1.0.0' }} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: /运行探测/ }));
  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/providers/locateanything/runtime-check', null, { params: { provider_version: '1.0.0' } }));
  fireEvent.click(screen.getByRole('button', { name: /本地 HIL/ }));
  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/providers/locateanything/local-hil', expect.objectContaining({ provider_version: '1.0.0', sample_uri: 'file:///7gai-hil/sample.jpg' })));
});
