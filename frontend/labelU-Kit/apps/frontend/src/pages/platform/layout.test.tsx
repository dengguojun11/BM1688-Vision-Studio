import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import PlatformLayout from './layout';

let isTaskSuperuser = true;
let currentUser: { username: string; is_task_superuser: boolean } | undefined = {
  username: 'admin@123.com',
  is_task_superuser: true,
};
vi.mock('@/hooks/useTaskSuperuser', () => ({
  default: () => ({
    isTaskSuperuser,
    data: currentUser,
    isLoading: false,
  }),
}));

vi.mock('@/utils/inAppIframe', () => ({ isInAppIframe: () => false }));
vi.mock('@/components/UserAvatar', () => ({
  UserAvatar: ({ user }: { user?: { username?: string } }) => <span>{user?.username || '当前账号'}</span>,
}));

beforeEach(() => {
  isTaskSuperuser = true;
  currentUser = { username: 'admin@123.com', is_task_superuser: true };
  localStorage.setItem('token', 'layout-test-token');
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe('PlatformLayout role navigation', () => {
  it('keeps dataset compatibility routes under the system management navigation', () => {
    render(
      <MemoryRouter initialEntries={['/datasets?project_id=7']}>
        <PlatformLayout>
          <div>页面内容</div>
        </PlatformLayout>
      </MemoryRouter>,
    );

    expect(screen.queryByRole('link', { name: '数据中心' })).toBeNull();
    expect(screen.getByRole('link', { name: '系统管理' }).getAttribute('aria-current')).toBe('page');
  });

  it('does not render a project selector and keeps the header context visible', () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/datasets?project_id=7']}>
        <PlatformLayout>
          <div>页面内容</div>
        </PlatformLayout>
      </MemoryRouter>,
    );

    const header = container.querySelector('header.ant-layout-header');

    expect(header).toBeTruthy();
    expect(getComputedStyle(header as HTMLElement).position).toBe('sticky');
    expect(getComputedStyle(header as HTMLElement).top).toBe('0px');
    expect(screen.queryByRole('combobox', { name: '当前项目' })).toBeNull();
    expect(screen.getByText('数据集管理')).toBeTruthy();
    expect(screen.queryByText(/可访问项目/)).toBeNull();
  });

  it('hides the platform shell only for an explicit evolution fullscreen request', () => {
    render(
      <MemoryRouter initialEntries={['/workspace/evolution-screen?fullscreen=1']}>
        <PlatformLayout>
          <div>模型进化大屏内容</div>
        </PlatformLayout>
      </MemoryRouter>,
    );

    expect(screen.getByText('模型进化大屏内容')).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: '主导航' })).toBeNull();
    expect(screen.queryByRole('link', { name: '工作台' })).toBeNull();
  });

  it('allows the explicitly isolated visual preview without an auth token', () => {
    localStorage.removeItem('token');
    render(
      <MemoryRouter initialEntries={['/workspace/evolution-screen?fullscreen=1&preview=1']}>
        <PlatformLayout>
          <div>免后端界面预览</div>
        </PlatformLayout>
      </MemoryRouter>,
    );

    expect(screen.getByText('免后端界面预览')).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: '主导航' })).toBeNull();
  });

  it('shows the read-only evolution entry but hides system management from annotation users', () => {
    isTaskSuperuser = false;
    currentUser = { username: 'annotator@example.com', is_task_superuser: false };
    render(
      <MemoryRouter initialEntries={['/tasks']}>
        <PlatformLayout>
          <div>页面内容</div>
        </PlatformLayout>
      </MemoryRouter>,
    );

    expect(screen.getByRole('link', { name: '工作台' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '任务与标注' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '训练中心' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '模型中心' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '进化中心' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '系统管理' })).toBeNull();
    expect(screen.getByText('annotator@example.com')).toBeTruthy();
    expect(screen.getByText('标注人员')).toBeTruthy();
    expect(screen.getByRole('button', { name: '当前账号：annotator@example.com' })).toBeTruthy();
  });
});
