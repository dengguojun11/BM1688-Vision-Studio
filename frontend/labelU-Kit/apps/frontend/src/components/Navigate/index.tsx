import Icon, { DatabaseOutlined, PoweroffOutlined } from '@ant-design/icons';
import { FlexLayout } from '@labelu/components-react';
import { Button, Divider, Dropdown, Popover, Tag } from 'antd';
import { Link, useLocation, useMatch, useMatches, useNavigate, useRevalidator } from 'react-router-dom';
import { i18n } from '@labelu/i18n';
import type { CSSProperties } from 'react';
import { useEffect, useState } from 'react';

import { ReactComponent as LocalDeploy } from '@/assets/svg/local-deploy.svg';
import { ReactComponent as ToolboxSvg } from '@/assets/svg/toolbox.svg';
import { goAuth } from '@/utils/sso';
import useMe from '@/hooks/useMe';
import useTaskSuperuser from '@/hooks/useTaskSuperuser';

import AppPanel from '../AppPanel';
import Breadcrumb from '../Breadcrumb';
import type { Match } from '../Breadcrumb';
import { LabeluLogo, NavigationWrapper } from './style';
import TaskTip from './TaskTip';
import { UserAvatar } from '../UserAvatar';

function readWindowReleaseVersions() {
  return {
    frontend: (window.__frontend?.version || '').trim(),
    backend: (window.__backend?.version || '').trim(),
  };
}

type ReleaseVersions = { frontend: string; backend: string };

type TasksView = 'list' | 'training' | 'stats' | 'training-analysis';

const tasksViewSwitcherStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.375rem',
  flex: '0 0 auto',
};

const tasksRouteTrailStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
};

const crumbDividerStyle: CSSProperties = {
  margin: '0 0.5rem',
  color: 'rgba(0, 0, 0, 0.45)',
};

const getTasksViewButtonStyle = (active: boolean): CSSProperties => ({
  height: '2rem',
  border: active ? '1px solid #bfdbfe' : '1px solid transparent',
  borderRadius: 8,
  padding: '0 0.875rem',
  font: 'inherit',
  fontSize: '0.875rem',
  fontWeight: active ? 600 : 500,
  color: active ? '#2f6fed' : '#475569',
  background: active ? '#eef5ff' : 'transparent',
  cursor: 'pointer',
  transition: 'background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease',
});

const getTasksRouteLinkStyle = (active: boolean): CSSProperties => ({
  border: 0,
  padding: 0,
  color: active ? '#333' : '#999',
  background: 'transparent',
  cursor: active ? 'default' : 'pointer',
  font: 'inherit',
});

function resolveTasksView(pathname: string, isAdmin: boolean): TasksView {
  if (pathname.startsWith('/training-analysis')) return 'training-analysis';
  if (pathname.startsWith('/training')) return 'training';
  if (pathname.startsWith('/stats') && isAdmin) return 'stats';
  return 'list';
}

const TasksBreadcrumbTools = () => {
  const taskSuperuserQuery = useTaskSuperuser();
  const isAdmin = taskSuperuserQuery.isTaskSuperuser;
  const location = useLocation();
  const matches = useMatches() as Match[];
  const navigate = useNavigate();
  const revalidator = useRevalidator();
  const view = resolveTasksView(location.pathname, isAdmin);
  const isTasksPath = location.pathname.startsWith('/tasks');

  // 非管理员访问 /stats 时重定向到 /tasks
  useEffect(() => {
    if (location.pathname.startsWith('/stats') && !isAdmin && taskSuperuserQuery.isSuccess) {
      navigate('/tasks', { replace: true });
    }
  }, [location.pathname, isAdmin, taskSuperuserQuery.isSuccess, navigate]);

  const tasksCrumb = isTasksPath
    ? matches
        .filter(
          (match) =>
            match.pathname !== '/tasks' &&
            match.pathname.startsWith('/tasks') &&
            match.handle?.crumb?.(match.data),
        )
        .map((match) => ({
          ...match,
          crumb: match.handle.crumb!(match.data),
        }))
    : [];

  const switchView = (next: TasksView) => {
    if (next === 'training') {
      navigate('/training');
    } else if (next === 'stats') {
      navigate('/stats');
    } else if (next === 'training-analysis') {
      navigate('/training-analysis');
    } else {
      navigate('/tasks');
    }
  };

  const handleLinkClick = (pathname: string) => () => {
    navigate(pathname);
    setTimeout(revalidator.revalidate);
  };

  return (
    <>
      <div style={tasksViewSwitcherStyle} role="tablist" aria-label="任务页视图">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'list'}
          style={getTasksViewButtonStyle(view === 'list')}
          onClick={() => switchView('list')}
        >
          任务列表
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'training'}
          style={getTasksViewButtonStyle(view === 'training')}
          onClick={() => switchView('training')}
        >
          模型训练
        </button>
        {isAdmin && (
          <button
            type="button"
            role="tab"
            aria-selected={view === 'stats'}
            style={getTasksViewButtonStyle(view === 'stats')}
            onClick={() => switchView('stats')}
          >
            标注统计
          </button>
        )}
        <button
          type="button"
          role="tab"
          aria-selected={view === 'training-analysis'}
          style={getTasksViewButtonStyle(view === 'training-analysis')}
          onClick={() => switchView('training-analysis')}
        >
          数据集分析
        </button>
      </div>
      {tasksCrumb.length > 0 && (
        <div style={tasksRouteTrailStyle}>
          <span style={crumbDividerStyle}>/</span>
          {tasksCrumb.map((item, index) => {
            const isCurrent = index === tasksCrumb.length - 1;

            if (!isCurrent) {
              return (
                <div style={tasksRouteTrailStyle} key={index}>
                  <button type="button" style={getTasksRouteLinkStyle(false)} onClick={handleLinkClick(item.pathname)}>
                    {item.crumb}
                  </button>
                  <span style={crumbDividerStyle}>/</span>
                </div>
              );
            }

            return (
              <button type="button" style={getTasksRouteLinkStyle(true)} key={index}>
                {item.crumb}
              </button>
            );
          })}
        </div>
      )}
    </>
  );
};

const Homepage = () => {
  const me = useMe();
  const navigate = useNavigate();
  const isSampleDetail = useMatch('/tasks/:taskId/samples/:sampleId');
  const isTasksRoute = useMatch('/tasks/*');
  const isTrainingRoute = useMatch('/training');
  const isTrainingAnalysisRoute = useMatch('/training-analysis');
  const isStatsRoute = useMatch('/stats');
  const isTaskRelatedRoute =
    isTasksRoute || isTrainingRoute || isTrainingAnalysisRoute || isStatsRoute;
  const t = i18n.t.bind(i18n);
  const [releaseVersions, setReleaseVersions] = useState<ReleaseVersions>(readWindowReleaseVersions);

  useEffect(() => {
    const syncFromWindow = () => setReleaseVersions(readWindowReleaseVersions());
    syncFromWindow();
    fetch('/api/v1/users/about', { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        const data = body?.data;
        if (!data) {
          syncFromWindow();
          return;
        }
        setReleaseVersions({
          frontend: (data.frontend_version || readWindowReleaseVersions().frontend || '—').trim(),
          backend: (data.version || readWindowReleaseVersions().backend || '—').trim(),
        });
      })
      .catch(() => syncFromWindow());
  }, []);

  const logout = async (e: any) => {
    e.stopPropagation();
    e.nativeEvent.stopPropagation();
    e.preventDefault();

    localStorage.setItem('token', '');

    if (window.IS_ONLINE) {
      await goAuth();
    } else {
      navigate('/login');
    }
  };

  return (
    <NavigationWrapper className="navigation" items="center" justify="space-between" padding="0 1.5rem">
      <FlexLayout.Item flex items="center" gap={window.IS_ONLINE ? '.5rem' : '3rem'}>
        <Link to="/">
          <FlexLayout.Item flex items="center">
            <LabeluLogo />

            {window.IS_ONLINE && (
              <Tag bordered={false} color="var(--color-fill-secondary)" style={{ color: 'var(--color-text)' }}>
                Beta
              </Tag>
            )}
          </FlexLayout.Item>
        </Link>
        {window.IS_ONLINE && <Divider type="vertical" />}
        {isTaskRelatedRoute ? <TasksBreadcrumbTools /> : <Breadcrumb hideHome={window.IS_ONLINE} />}
      </FlexLayout.Item>
      <FlexLayout.Item flex gap="1rem">
        <TaskTip visible={Boolean(isSampleDetail)} />
        {window.IS_ONLINE && (
          <Popover title={null} content={<AppPanel />}>
            <Button
              type="link"
              icon={<ToolboxSvg />}
              style={{ color: 'rgba(0, 0, 0, 0.85)', display: 'flex', alignItems: 'center' }}
            >
              {t('openSourceToolbox')}
            </Button>
          </Popover>
        )}
        {window.IS_ONLINE && (
          <a
            data-wiz="local-deploy-top-right"
            href={`https://opendatalab.github.io/labelU/${i18n.language.startsWith('en') ? 'en/' : ''}guide/install`}
          >
            <Button type="link" style={{ color: 'rgba(0, 0, 0, 0.85)' }} icon={<Icon component={LocalDeploy} />}>
              {t('localDeploy')}
            </Button>
          </a>
        )}
        <Link to="/datasources">
          <Button type="link" icon={<DatabaseOutlined />} style={{ color: 'rgba(0, 0, 0, 0.85)' }}>
            {t('dataSources')}
          </Button>
        </Link>
        <Dropdown
          trigger={['click']}
          menu={{
            items: [
              {
                key: 'version',
                disabled: true,
                label: (
                  <div
                    style={{
                      fontSize: 12,
                      lineHeight: 1.55,
                      color: 'rgba(0, 0, 0, 0.45)',
                      padding: '2px 0',
                      cursor: 'default',
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                    }}
                  >
                    <div>前端版本：v{releaseVersions.frontend || '—'}</div>
                    <div>后端版本：v{releaseVersions.backend || '—'}</div>
                  </div>
                ),
              },
              { type: 'divider' },
              {
                label: (
                  <FlexLayout.Item onClick={logout} flex gap=".5rem" padding=".25rem 0">
                    <PoweroffOutlined />
                    <span>{t('logout')}</span>
                  </FlexLayout.Item>
                ),
                key: 'logout',
                title: String(t('logout')),
              },
            ],
          }}
        >
          <UserAvatar
            user={me?.data}
            shortName={false}
            showTooltip={false}
            placement="bottomRight"
            style={{ backgroundColor: 'var(--color-primary)', color: '#fff' }}
          />
        </Dropdown>
      </FlexLayout.Item>
    </NavigationWrapper>
  );
};
export default Homepage;
