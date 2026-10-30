import { MenuFoldOutlined, MenuUnfoldOutlined, PoweroffOutlined } from '@ant-design/icons';
import { Button, Dropdown, Layout, Space, Tag, Tooltip, Typography } from 'antd';
import type React from 'react';
import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import styled from 'styled-components';

import useTaskSuperuser from '@/hooks/useTaskSuperuser';
import { isInAppIframe } from '@/utils/inAppIframe';
import { PRODUCT_LOGO_SRC } from '@/utils/product';
import { getActiveModule, MODULE_COPY, PLATFORM_MODULES, PRODUCT_NAME } from './modules';
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
  background: #101a2b !important;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
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
      box-shadow: 10px 0 24px rgba(15, 23, 42, 0.18);
    }
  }
`;

const MobileNavBackdrop = styled.button.attrs({ type: 'button' })`
  display: none;

  @media (max-width: 900px) {
    display: block;
    position: fixed;
    z-index: 30;
    inset: 0 0 0 224px;
    padding: 0;
    border: 0;
    background: var(--workspace-scrim);
    cursor: default;
  }
`;

const Brand = styled(Link)`
  display: flex;
  align-items: center;
  min-height: 64px;
  padding: 0 20px;
  color: #fff;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0;
  white-space: nowrap;
  text-decoration: none;

  .ant-layout-sider-collapsed & {
    padding: 0 15px;
  }
`;

const BrandMark = styled.img.attrs({ src: PRODUCT_LOGO_SRC, alt: '7G 标志' })`
  display: block;
  flex-shrink: 0;
  width: 34px;
  height: 28px;
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
  gap: 4px;
  padding: 12px 10px;
`;

const NavItem = styled(Link)<{ $active: boolean }>`
  display: flex;
  align-items: center;
  min-height: 40px;
  padding: 0 11px;
  overflow: hidden;
  border-radius: 6px;
  color: ${({ $active }) => ($active ? '#fff' : 'rgba(255, 255, 255, 0.68)')};
  background: ${({ $active }) => ($active ? 'rgba(65, 123, 255, 0.92)' : 'transparent')};
  font-size: 13px;
  text-decoration: none;
  transition: background 120ms ease, color 120ms ease;

  &:hover {
    color: #fff;
    background: ${({ $active }) => ($active ? 'rgba(65, 123, 255, 0.92)' : 'rgba(255, 255, 255, 0.08)')};
  }

  &:focus-visible {
    outline: 2px solid #93c5fd;
    outline-offset: 2px;
  }

  .nav-label {
    margin-left: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const Header = styled(Layout.Header)`
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 20;
  min-width: 0;
  min-height: 64px;
  height: 64px;
  padding: 0 28px;
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

const Main = styled(Layout.Content)`
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow-x: clip;
  padding: 28px;

  @media (max-width: 900px) {
    padding: 20px;
  }

  @media (max-width: 600px) {
    padding: 16px 12px;
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
  const params = new URLSearchParams(location.search);
  // `embedded=1` is also used by direct compatibility links. Only suppress the
  // platform shell when the legacy content is actually hosted in an iframe.
  const isDefaultDataScreen = isWorkspaceRootPath(location.pathname);
  const isLegacyFullscreen = isEvolutionScreenPath(location.pathname) && params.get('fullscreen') === '1';
  const hideChrome = params.has('noSave') || isDefaultDataScreen || isLegacyFullscreen || isInAppIframe();
  const active = getActiveModule(location.pathname);
  // Algorithms, rules and providers are advanced surfaces under the
  // training center. Keep their direct URLs working while
  // presenting one primary navigation entry to operators.
  const navigationActive = active === 'algorithms' || active === 'rules' || active === 'providers'
    ? 'training'
    : active === 'datasets' || active === 'ingestion' || active === 'annotation-review'
    ? 'admin'
    : active;
  const userQuery = useTaskSuperuser();
  const visibleModules = userQuery.isTaskSuperuser
    ? PLATFORM_MODULES
    : PLATFORM_MODULES.filter(({ key }) => key !== 'admin');
  const isEvolutionScreen = isEvolutionScreenPath(location.pathname);
  const pageCopy = isDefaultDataScreen
    ? { title: '数据大屏', description: '查看模型谱系、训练进度、质量门禁与自动化决策证据。' }
    : isWorkspaceWorkbenchPath(location.pathname)
    ? { title: '运行工作台', description: '集中查看待办、作业状态与平台告警，并进入具体处理页面。' }
    : isEvolutionScreen
    ? { title: '模型进化大屏', description: '查看模型谱系、质量门禁与自动化决策证据。' }
    : MODULE_COPY[active];

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
        width={224}
        collapsedWidth={64}
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
          {visibleModules.map(({ key, label, icon: Icon }) => (
            <Tooltip key={key} title={collapsed ? label : undefined} placement="right">
              <NavItem
                to={`/${key}`}
                $active={navigationActive === key}
                aria-current={navigationActive === key ? 'page' : undefined}
                aria-label={collapsed ? label : undefined}
              >
                <Icon aria-hidden="true" />
                {!collapsed && <span className="nav-label">{label}</span>}
              </NavItem>
            </Tooltip>
          ))}
        </Nav>
        {!collapsed && <Footer>{PRODUCT_NAME}</Footer>}
      </Side>
      {!collapsed && <MobileNavBackdrop aria-label="关闭导航" onClick={() => setCollapsed(true)} />}
      <Layout>
        <Header>
          <PageContext>
            <Typography.Text strong className="page-title">{pageCopy.title}</Typography.Text>
            <span className="page-description">{pageCopy.description}</span>
          </PageContext>
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
        <Main>{children}</Main>
      </Layout>
    </Shell>
  );
}
