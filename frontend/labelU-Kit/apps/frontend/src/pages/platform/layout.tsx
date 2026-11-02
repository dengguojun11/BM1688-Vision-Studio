import {
  ArrowLeftOutlined,
  DownOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PoweroffOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { Button, Dropdown, Layout, Space, Tag, Tooltip, Typography } from 'antd';
import type React from 'react';
import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import styled from 'styled-components';

import useTaskSuperuser from '@/hooks/useTaskSuperuser';
import { isInAppIframe } from '@/utils/inAppIframe';
import { PRODUCT_LOGO_SRC } from '@/utils/product';
import { getActiveModule, getActiveNavigation, MODULE_COPY, PLATFORM_NAVIGATION, PRODUCT_NAME } from './modules';
import { isEvolutionScreenPath, isWorkspaceRootPath, isWorkspaceWorkbenchPath } from './routing';
import { UserAvatar } from '@/components/UserAvatar';

const Shell = styled(Layout)`
  width: 100%;
  min-width: 0;
  min-height: 100vh;
  overflow-x: clip;
  background: #f5f7fb;
`;

const Side = styled(Layout.Sider)`
  position: sticky !important;
  top: 0;
  align-self: flex-start;
  height: 100vh;
  max-height: 100vh;
  overflow-y: auto;
  flex: 0 0 auto;
  background:
    radial-gradient(circle at 18% 0%, rgba(59, 130, 246, 0.18), transparent 34%),
    linear-gradient(180deg, #0b1424 0%, #0d1a2e 58%, #0b1627 100%) !important;
  border-right: 1px solid rgba(148, 163, 184, 0.12);
  box-shadow: 8px 0 28px rgba(2, 8, 23, 0.14);
  transition: width 180ms ease;

  .ant-layout-sider-children {
    display: flex;
    flex-direction: column;
  }

  @media (max-width: 900px) {
    &:not(.ant-layout-sider-collapsed) {
      position: fixed !important;
      z-index: 40;
      left: 0;
      top: 0;
      box-shadow: 16px 0 36px rgba(2, 8, 23, 0.34);
    }
  }
`;

const MobileNavBackdrop = styled.button.attrs({ type: 'button' })`
  display: none;

  @media (max-width: 900px) {
    display: block;
    position: fixed;
    z-index: 30;
    inset: 0 0 0 256px;
    padding: 0;
    border: 0;
    background: var(--workspace-scrim);
    cursor: default;
  }
`;

const Brand = styled(Link)`
  display: flex;
  align-items: center;
  min-height: 68px;
  padding: 0 18px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.1);
  color: #f8fbff;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0.01em;
  white-space: nowrap;
  text-decoration: none;

  .ant-layout-sider-collapsed & {
    justify-content: center;
    padding: 0;
  }
`;

const BrandMark = styled.img.attrs({ src: PRODUCT_LOGO_SRC, alt: '7G 标志' })`
  display: block;
  flex-shrink: 0;
  width: 32px;
  height: 26px;
  margin-right: 10px;
  object-fit: contain;

  .ant-layout-sider-collapsed & {
    margin-right: 0;
  }
`;

const Nav = styled.nav`
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  padding: 14px 12px 18px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: rgba(148, 163, 184, 0.35) transparent;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-thumb {
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.3);
  }
`;

const NavHeading = styled.div`
  padding: 2px 10px 8px;
  color: rgba(148, 163, 184, 0.82);
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.14em;
  text-transform: uppercase;
`;

const NavIcon = styled.span<{ $active?: boolean }>`
  display: inline-flex;
  flex: 0 0 26px;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 7px;
  color: ${({ $active }) => ($active ? '#bfdbfe' : 'rgba(203, 213, 225, 0.72)')};
  background: ${({ $active }) => ($active ? 'rgba(96, 165, 250, 0.18)' : 'rgba(255, 255, 255, 0.04)')};
  font-size: 15px;
  transition: background 140ms ease, color 140ms ease;
`;

const NavItem = styled(Link)<{ $active: boolean }>`
  display: flex;
  align-items: center;
  min-height: 42px;
  padding: 0 9px;
  overflow: hidden;
  border-radius: 9px;
  color: ${({ $active }) => ($active ? '#ffffff' : 'rgba(226, 232, 240, 0.72)')};
  background: ${({ $active }) =>
    $active
      ? 'linear-gradient(90deg, rgba(59, 130, 246, 0.26), rgba(59, 130, 246, 0.06))'
      : 'transparent'};
  box-shadow: ${({ $active }) => ($active ? 'inset 3px 0 0 #60a5fa' : 'none')};
  font-size: 13px;
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  text-decoration: none;
  transition: background 140ms ease, color 140ms ease, box-shadow 140ms ease;

  &:hover {
    color: #fff;
    background: ${({ $active }) =>
      $active
        ? 'linear-gradient(90deg, rgba(59, 130, 246, 0.3), rgba(59, 130, 246, 0.08))'
        : 'rgba(255, 255, 255, 0.06)'};
  }

  &:focus-visible {
    outline: 2px solid #93c5fd;
    outline-offset: 2px;
  }

  .nav-label {
    margin-left: 9px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ant-layout-sider-collapsed & {
    justify-content: center;
    padding: 0;
    box-shadow: none;
  }
`;

const NavGroup = styled.div`
  display: flex;
  flex-direction: column;
  gap: 2px;
`;

const NavParentRow = styled.div`
  display: flex;
  align-items: center;
  gap: 2px;

  > a {
    flex: 1;
    min-width: 0;
  }
`;

const NavToggle = styled.button.attrs({ type: 'button' })`
  display: inline-flex;
  flex: 0 0 26px;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 38px;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: rgba(148, 163, 184, 0.9);
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease;

  .anticon {
    font-size: 10px;
  }

  &:hover {
    color: #fff;
    background: rgba(255, 255, 255, 0.07);
  }

  &:focus-visible {
    outline: 2px solid #93c5fd;
    outline-offset: 2px;
  }
`;

const NavChildren = styled.div`
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin: 2px 0 6px 21px;
  padding-left: 13px;
  border-left: 1px solid rgba(148, 163, 184, 0.18);
  animation: treeReveal 160ms ease both;

  @keyframes treeReveal {
    from {
      opacity: 0;
      transform: translateY(-3px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
`;

const NavChild = styled(Link)<{ $active: boolean }>`
  position: relative;
  display: flex;
  align-items: center;
  min-height: 31px;
  padding: 0 10px 0 9px;
  overflow: hidden;
  border-radius: 6px;
  color: ${({ $active }) => ($active ? '#ffffff' : 'rgba(148, 163, 184, 0.82)')};
  background: ${({ $active }) => ($active ? 'rgba(59, 130, 246, 0.2)' : 'transparent')};
  box-shadow: ${({ $active }) => ($active ? 'inset 2px 0 0 #60a5fa' : 'none')};
  font-size: 12px;
  font-weight: ${({ $active }) => ($active ? 500 : 400)};
  text-decoration: none;
  white-space: nowrap;
  text-overflow: ellipsis;
  transition: background 140ms ease, color 140ms ease;

  &::before {
    content: '';
    position: absolute;
    left: -14px;
    top: 50%;
    width: 11px;
    height: 1px;
    background: rgba(148, 163, 184, 0.22);
  }

  &:hover {
    color: #fff;
    background: ${({ $active }) => ($active ? 'rgba(59, 130, 246, 0.26)' : 'rgba(255, 255, 255, 0.055)')};
  }

  &:focus-visible {
    outline: 2px solid #93c5fd;
    outline-offset: 1px;
  }
`;

const Header = styled(Layout.Header)<{ $compact?: boolean }>`
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 20;
  min-width: 0;
  min-height: ${({ $compact }) => ($compact ? '48px' : '64px')};
  height: ${({ $compact }) => ($compact ? '48px' : '64px')};
  padding: ${({ $compact }) => ($compact ? '0 20px' : '0 28px')};
  border-bottom: 1px solid #e6eaf0;
  background: #fff;
  line-height: normal;

  @media (max-width: 600px) {
    padding: 0 12px;
  }
`;

const PageContext = styled.div`
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-self: stretch;
  min-width: 0;
  flex: 1;
  color: #64748b;
  font-size: 12px;
  line-height: 1.25;

  .page-description {
    display: block;
    margin-top: 2px;
    overflow: hidden;
    line-height: 1.25;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .page-title {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  @media (max-width: 600px) {
    .page-description {
      display: none;
    }

    .page-title {
      font-size: 14px;
    }
  }
`;

const Main = styled(Layout.Content)<{ $compact?: boolean }>`
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow-x: clip;
  padding: ${({ $compact }) => ($compact ? '10px 28px 28px' : '28px')};

  @media (max-width: 900px) {
    padding: ${({ $compact }) => ($compact ? '8px 20px 20px' : '20px')};
  }

  @media (max-width: 600px) {
    padding: ${({ $compact }) => ($compact ? '6px 12px 16px' : '16px 12px')};
  }
`;

const Footer = styled.div`
  margin-top: auto;
  padding: 14px 20px 20px;
  color: rgba(255, 255, 255, 0.45);
  font-size: 11px;
  white-space: nowrap;
`;

export default function PlatformLayout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  // Accordion navigation: only one first-level branch is expanded at a time.
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const params = new URLSearchParams(location.search);
  // `embedded=1` is also used by direct compatibility links. Only suppress the
  // platform shell when the legacy content is actually hosted in an iframe.
  const isDefaultDataScreen = isWorkspaceRootPath(location.pathname);
  const isLegacyFullscreen = isEvolutionScreenPath(location.pathname) && params.get('fullscreen') === '1';
  const hideChrome = params.has('noSave') || isDefaultDataScreen || isLegacyFullscreen || isInAppIframe();
  const active = getActiveModule(location.pathname);
  const activeNavigation = getActiveNavigation(location.pathname, location.search);
  // The task list is the working surface for annotation; its own title block is
  // intentionally removed, so collapse the shell header and top padding too.
  const isTaskListPage = location.pathname === '/tasks' || location.pathname === '/tasks/';
  const isTaskDetailPage = /^\/tasks\/[^/]+\/?$/.test(location.pathname);
  // The tree always opens exactly one branch: the current working surface.
  const isGroupExpanded = (key: string) => expandedGroup === key;
  const isGroupActive = (key: string) => activeNavigation.parentKey === key;
  const toggleGroup = (key: string) => {
    setExpandedGroup((current) => (current === key ? null : key));
  };
  const userQuery = useTaskSuperuser();
  const visibleGroups = PLATFORM_NAVIGATION.filter(
    (group) => userQuery.isTaskSuperuser || group.key !== 'admin',
  ).map((group) => ({
    ...group,
    children: group.children.filter(
      (child) => !child.managerOnly || userQuery.isTaskSuperuser,
    ),
  }));
  const isEvolutionScreen = isEvolutionScreenPath(location.pathname);
  const pageCopy = isDefaultDataScreen
    ? { title: '数据大屏', description: '查看模型谱系、训练进度、质量门禁与自动化决策证据。' }
    : isWorkspaceWorkbenchPath(location.pathname)
    ? { title: '运行工作台', description: '集中查看待办、作业状态与平台告警，并进入具体处理页面。' }
    : isEvolutionScreen
    ? { title: '模型进化大屏', description: '查看模型谱系、质量门禁与自动化决策证据。' }
    : MODULE_COPY[active];

  useEffect(() => {
    setExpandedGroup(activeNavigation.parentKey);
  }, [activeNavigation.parentKey]);

  useEffect(() => {
    const visualPreview = params.get('preview') === '1' && isEvolutionScreen;
    if (!visualPreview && !window.IS_ONLINE && !localStorage.getItem('token')) {
      navigate(`/login?redirect=${encodeURIComponent(location.pathname + location.search)}`, { replace: true });
    }
  }, [location.pathname, location.search, navigate]);

  if (hideChrome) return <>{children}</>;

  const currentUser = userQuery.data;
  const accountLabel = currentUser?.username || (userQuery.isLoading ? '正在读取账号' : '当前账号');
  const roleLabel = userQuery.isTaskSuperuser ? '管理人员' : '标注人员';
  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    localStorage.removeItem('userid');
    navigate('/login', { replace: true });
  };

  return (
    <Shell className="platform-shell">
      <Side
        width={256}
        collapsedWidth={72}
        collapsed={collapsed}
        trigger={null}
        breakpoint="md"
        onBreakpoint={(broken) => setCollapsed(broken)}
      >
        <Brand to="/workspace" aria-label={PRODUCT_NAME}>
          <BrandMark />
          {!collapsed && <span>{PRODUCT_NAME}</span>}
        </Brand>
        <Nav aria-label="主导航">
          {!collapsed && <NavHeading>功能导航</NavHeading>}
          {visibleGroups.map((group) => {
            const Icon = group.icon;
            const parentActive = isGroupActive(group.key);
            const expanded = isGroupExpanded(group.key);
            return (
              <NavGroup key={group.key}>
                <NavParentRow>
                  <Tooltip title={collapsed ? group.label : undefined} placement="right">
                    <NavItem
                      to={group.to}
                      $active={parentActive}
                      aria-current={parentActive ? 'page' : undefined}
                      aria-label={collapsed ? group.label : undefined}
                    >
                      <NavIcon $active={parentActive}>
                        <Icon aria-hidden="true" />
                      </NavIcon>
                      {!collapsed && <span className="nav-label">{group.label}</span>}
                    </NavItem>
                  </Tooltip>
                  {!collapsed && group.children.length > 0 && (
                    <NavToggle
                      aria-label={`${expanded ? '收起' : '展开'}${group.label}菜单`}
                      aria-expanded={expanded}
                      onClick={() => toggleGroup(group.key)}
                    >
                      {expanded ? <DownOutlined /> : <RightOutlined />}
                    </NavToggle>
                  )}
                </NavParentRow>
                {!collapsed && expanded && group.children.length > 0 && (
                  <NavChildren aria-label={`${group.label}子菜单`}>
                    {group.children.map((child) => {
                      const childActive = activeNavigation.childKey === child.key;
                      return (
                        <NavChild
                          key={child.key}
                          to={child.to}
                          $active={childActive}
                          aria-current={childActive ? 'page' : undefined}
                        >
                          {child.label}
                        </NavChild>
                      );
                    })}
                  </NavChildren>
                )}
              </NavGroup>
            );
          })}
        </Nav>
        {!collapsed && <Footer>{PRODUCT_NAME}</Footer>}
      </Side>
      {!collapsed && <MobileNavBackdrop aria-label="关闭导航" onClick={() => setCollapsed(true)} />}
      <Layout>
        <Header $compact={isTaskListPage}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 }}>
            {isTaskDetailPage && (
              <Button
                type="text"
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate('/tasks')}
                style={{ flex: '0 0 auto', paddingInline: 6 }}
                aria-label="返回任务列表"
              >
                返回任务列表
              </Button>
            )}
            {!isTaskListPage && (
              <PageContext>
                <Typography.Text strong className="page-title">{pageCopy.title}</Typography.Text>
                <span className="page-description">{pageCopy.description}</span>
              </PageContext>
            )}
          </div>
          <Space size="middle">
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  {
                    key: 'account',
                    disabled: true,
                    label: (
                      <div style={{ minWidth: 190 }}>
                        <div style={{ color: '#172033', fontWeight: 600 }}>{accountLabel}</div>
                        <div style={{ marginTop: 3, color: '#64748b', fontSize: 12 }}>{roleLabel}</div>
                      </div>
                    ),
                  },
                  { type: 'divider' },
                  {
                    key: 'logout',
                    icon: <PoweroffOutlined />,
                    label: '退出登录',
                    onClick: handleLogout,
                  },
                ],
              }}
            >
              <Button
                type="text"
                aria-label={`当前账号：${accountLabel}`}
                style={{ height: 40, padding: '0 6px', display: 'inline-flex', alignItems: 'center' }}
              >
                <UserAvatar
                  user={currentUser}
                  shortName={false}
                  showTooltip={false}
                  style={{ backgroundColor: 'var(--color-primary-bg)', color: 'var(--color-primary)' }}
                />
                <Tag color={userQuery.isTaskSuperuser ? 'blue' : 'default'} style={{ marginLeft: 8, marginRight: 0 }}>
                  {roleLabel}
                </Tag>
              </Button>
            </Dropdown>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              aria-label={collapsed ? '展开导航' : '收起导航'}
              onClick={() => setCollapsed((value) => !value)}
            />
          </Space>
        </Header>
        <Main $compact={isTaskListPage}>{children}</Main>
      </Layout>
    </Shell>
  );
}
