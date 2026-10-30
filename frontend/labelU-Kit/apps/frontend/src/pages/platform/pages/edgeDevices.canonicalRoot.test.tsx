import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import request from '@/api/request';
import EdgeDevicesPanel from './edgeDevices';

vi.mock('@/api/request', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const mockedGet = vi.mocked(request.get);
const mockedPost = vi.mocked(request.post);

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
});

it('uses the server canonical edge root when creating a device', async () => {
  mockedGet.mockImplementation(async (url: string) => {
    if (url === '/v1/yolo-bm1688-exports/edge-ssh-settings') {
      return { data: { edge_root: '/data/7G_AI/edge' } } as never;
    }
    if (url === '/v1/edge-devices') {
      return {
        data: { active_device_id: '', devices: [], total: 0, page: 0, size: 10 },
      } as never;
    }
    throw new Error(`Unexpected GET ${url}`);
  });
  mockedPost.mockResolvedValue({
    data: {
      id: 'edge_new',
      name: '新增边端测试设备',
      validation_channel: 'ssh',
      agent_bootstrap_control_plane_enabled: false,
      agent_bootstrap_status: 'NOT_STARTED',
    },
  } as never);

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <EdgeDevicesPanel />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText('边端设备管理')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /新增设备/ }));

  const rootInput = (await screen.findByLabelText('边端工作目录')) as HTMLInputElement;
  expect(rootInput.value).toBe('/data/7G_AI/edge');
  fireEvent.change(screen.getByLabelText('设备地址'), { target: { value: '10.1.1.147' } });
  fireEvent.change(screen.getByLabelText('SSH 密码'), { target: { value: 'test-password' } });
  const addButtons = screen.getAllByRole('button', { name: /新\s*增/ });
  fireEvent.click(addButtons[addButtons.length - 1]);

  await waitFor(() => expect(mockedPost).toHaveBeenCalledWith('/v1/edge-devices', expect.any(Object)));
  const [, payload] = mockedPost.mock.calls[0];
  expect(payload).toMatchObject({ edge_root: '/data/7G_AI/edge', host: '10.1.1.147' });
  expect(JSON.stringify(payload)).not.toContain('/data/labelu_edge');
});
