import type { RouteObject } from 'react-router';
import { Outlet, useLocation, useNavigate } from 'react-router';
import { lazy, useEffect } from 'react';
import { i18n } from '@labelu/i18n';

const Register = lazy(() => import('@/pages/register'));
const Tasks = lazy(() => import('@/pages/tasks'));
const TaskEdit = lazy(() => import('@/pages/tasks.[id].edit'));
const TaskAnnotation = lazy(() => import('@/pages/tasks.[id].samples.[id]'));
const Samples = lazy(() => import('@/pages/tasks.[id]'));
const TaskSamplesFinished = lazy(() => import('@/pages/tasks.[id].samples.finished'));
const Page404 = lazy(() => import('@/pages/404'));
const DataSources = lazy(() => import('@/pages/datasources'));
const ModelTrainingPanel = lazy(() => import('@/pages/tasks/components/ModelTraining'));
const AnnotationStatsPanel = lazy(() => import('@/pages/tasks/components/AnnotationStats'));
const TrainingAnalysisPage = lazy(() => import('@/pages/training-analysis'));
const TaskLabelImagesPage = lazy(() => import('@/pages/task-label-images'));

import type { TaskLoaderResult } from './loaders/task.loader';
import { taskLabelImagesLoader, taskLoader, tasksLoader } from './loaders/task.loader';
import { shouldRevalidateTaskRoute } from './loaders/taskRouteRevalidation';
import { rootLoader } from './loaders/root.loader';
import { sampleLoader } from './loaders/sample.loader';
import RequireAuth from './components/RequireSSO';
import { registerLoader } from './loaders/register.loader';
import { loginLoader } from './loaders/login.loader';
import LoginPage from './pages/login';
import PlatformRoute from './pages/platform/PlatformRoute';
import RequireManager from './pages/platform/RequireManager';
import { platformPageLoaders } from './pages/platform/routeEntries';

const WorkspacePage = lazy(platformPageLoaders.workspace);
const IngestionPage = lazy(platformPageLoaders.ingestion);
const AlgorithmsPage = lazy(platformPageLoaders.algorithms);
const ProvidersPage = lazy(platformPageLoaders.providers);
const ModelsPage = lazy(platformPageLoaders.models);
const EvolutionPage = lazy(platformPageLoaders.evolution);
const EdgeTaskCenterPage = lazy(platformPageLoaders.edgeTaskCenter);
const TrainingPage = lazy(platformPageLoaders.training);
const AdminPage = lazy(platformPageLoaders.admin);

/**
 * Keep the legacy iframe wrapper for embedded and panel-specific links while
 * making the regular /training entry point part of the 7GAI platform shell.
 */
function TrainingRoute() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const preserveLegacy = params.get('embedded') === '1' || params.has('panel');

  if (preserveLegacy) {
    return <ModelTrainingPanel />;
  }

  return <TrainingPage />;
}

function DatasetManagementRedirect() {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    params.set('view', 'governance');
    params.set('tab', 'datasets');
    navigate(`/admin?${params.toString()}`, { replace: true });
  }, [location.search, navigate]);

  return (
    <div role="status" aria-live="polite" style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}>
      正在打开数据集管理…
    </div>
  );
}

function RetiredPlatformRoute({ target, label }: { target: string; label: string }) {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    params.delete('project_id');
    const query = params.toString();
    navigate(`${target}${query ? `?${query}` : ''}`, { replace: true });
  }, [location.search, navigate, target]);

  return (
    <div role="status" aria-live="polite" style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}>
      {label}
    </div>
  );
}

function Root() {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    // 如果是根路径，跳转到任务管理（以任务管理为首页）
    if (location.pathname === '/' || location.pathname === '') {
      navigate('/workspace');
    }
  }, [location.pathname, navigate]);

  const content = <Outlet />;

  if (!window.IS_ONLINE) {
    return <PlatformRoute>{content}</PlatformRoute>;
  }

  return (
    <RequireAuth>
      <PlatformRoute>{content}</PlatformRoute>
    </RequireAuth>
  );
}

const routes: RouteObject[] = [
  {
    path: '/',
    id: 'root',
    element: <Root />,
    loader: rootLoader,
    children: [
      {
        path: 'workspace',
        element: <WorkspacePage />,
        handle: { crumb: () => '工作台' },
      },
      {
        path: 'workspace/evolution-screen',
        element: <WorkspacePage />,
        handle: { crumb: () => '模型进化大屏' },
      },
      {
        path: 'datasets',
        element: <DatasetManagementRedirect />,
        handle: { crumb: () => '数据集管理' },
      },
      {
        path: 'ingestion',
        element: <IngestionPage />,
        handle: { crumb: () => '数据接入' },
      },
      {
        path: 'annotation-review',
        element: <RetiredPlatformRoute target="/evolution" label="历史审核页面已归入进化中心，正在打开…" />,
        handle: { crumb: () => '进化中心' },
      },
      {
        path: 'algorithms',
        element: <AlgorithmsPage />,
        handle: { crumb: () => '训练中心' },
      },
      {
        path: 'rules',
        element: <RetiredPlatformRoute target="/admin" label="历史规则页面已归入系统管理，正在打开…" />,
        handle: { crumb: () => '系统管理' },
      },
      {
        path: 'providers',
        element: <ProvidersPage />,
        handle: { crumb: () => '外部能力' },
      },
      {
        path: 'models',
        element: <ModelsPage />,
        handle: { crumb: () => '模型中心' },
      },
      {
        path: 'deployments',
        element: <RetiredPlatformRoute target="/models" label="设备发布能力不属于本平台，正在打开模型中心…" />,
        handle: { crumb: () => '模型中心' },
      },
      {
        path: 'evolution',
        element: <EvolutionPage />,
        handle: { crumb: () => '进化中心' },
      },
      {
        path: 'edge-task-center',
        element: (
          <RequireManager>
            <EdgeTaskCenterPage />
          </RequireManager>
        ),
        handle: { crumb: () => '统一 BM1688 边端验证任务中心' },
      },
      {
        path: 'edge-tasks',
        element: (
          <RequireManager>
            <EdgeTaskCenterPage />
          </RequireManager>
        ),
        handle: { crumb: () => '统一 BM1688 边端验证任务中心' },
      },
      {
        path: 'workflows',
        element: <RetiredPlatformRoute target="/admin" label="异常流程已归入系统管理，正在打开…" />,
        handle: { crumb: () => '系统管理' },
      },
      {
        path: 'admin',
        element: (
          <RequireManager>
            <AdminPage />
          </RequireManager>
        ),
        handle: { crumb: () => '系统管理' },
      },
      {
        path: 'datasources',
        element: <Outlet />,
        errorElement: <Page404 />,
        handle: {
          crumb: () => {
            return i18n.t('dataSources');
          },
        },
        children: [
          {
            index: true,
            element: <DataSources />,
          },
        ],
      },
      {
        path: 'tasks',
        element: <Outlet />,
        errorElement: <Page404 />,
        handle: {
          crumb: () => {
            return i18n.t('taskList');
          },
        },
        children: [
          {
            index: true,
            id: 'tasks',
            element: <Tasks />,
            loader: tasksLoader,
          },
          {
            path: 'labels/:labelId',
            element: <TaskLabelImagesPage />,
            loader: taskLabelImagesLoader,
          },
          {
            path: ':taskId',
            id: 'task',
            element: <Outlet />,
            loader: taskLoader,
            shouldRevalidate: shouldRevalidateTaskRoute,
            handle: {
              crumb: (data: TaskLoaderResult) => {
                return data?.task?.name;
              },
            },
            children: [
              {
                index: true,
                element: <Samples />,
              },
              {
                path: 'edit',
                element: <TaskEdit />,
                loader: async ({ params }) => {
                  return params.taskId !== '0' ? i18n.t('taskEdit') : i18n.t('createTask');
                },
                handle: {
                  crumb: (data: string) => {
                    return data;
                  },
                },
              },
              {
                path: 'samples',
                id: 'samples',
                element: <Outlet />,
                children: [
                  {
                    path: ':sampleId',
                    element: <TaskAnnotation />,
                    loader: sampleLoader,
                    id: 'annotation',
                    handle: {
                      crumb: () => {
                        return i18n.t('start');
                      },
                    },
                  },
                  {
                    path: 'finished',
                    element: <TaskSamplesFinished />,
                    loader: taskLoader,
                    handle: {
                      crumb: () => {
                        return i18n.t('finished');
                      },
                    },
                  },
                ],
              },
            ],
          },
        ],
      },
      {
        path: 'training',
        element: <TrainingRoute />,
        errorElement: <Page404 />,
        handle: { crumb: () => '训练中心' },
      },
      {
        path: 'stats',
        element: <Outlet />,
        errorElement: <Page404 />,
        children: [
          {
            index: true,
            element: <AnnotationStatsPanel />,
          },
        ],
      },
      {
        path: 'training-analysis',
        element: <Outlet />,
        errorElement: <Page404 />,
        handle: {
          crumb: () => '数据集分析',
        },
        children: [
          {
            index: true,
            element: <TrainingAnalysisPage />,
          },
        ],
      },
    ],
  },
  {
    path: 'login',
    loader: loginLoader,
    element: <LoginPage />,
  },
  {
    path: 'register',
    loader: registerLoader,
    element: <Register />,
    handle: {
      crumb: () => {
        return i18n.t('signUp');
      },
    },
  },
  {
    path: '*',
    element: <Page404 />,
  },
];

export default routes;
