import type { ComponentType } from 'react';

type RoutePageModule = { default: ComponentType };
type RoutePageLoader = () => Promise<RoutePageModule>;

export const platformPageLoaders = {
  workspace: () => import('./pages/workspace'),
  datasets: () => import('./pages/datasets'),
  ingestion: () => import('./pages/ingestion'),
  annotationReview: () => import('./pages/annotationReview'),
  algorithms: () => import('./pages/algorithms'),
  rules: () => import('./pages/rules'),
  providers: () => import('./pages/providers'),
  training: () => import('./pages/training'),
  models: () => import('./pages/models'),
  evolution: () => import('./pages/evolution'),
  edgeTaskCenter: () => import('./pages/edgeTaskCenter'),
  admin: () => import('./pages/admin'),
  systemLogs: () => import('./pages/systemLogs'),
  personnelStatistics: () => import('./pages/personnelStatistics'),
  activityStatistics: () => import('./pages/activityStatistics'),
  dataAnalysis: () => import('./pages/dataAnalysis'),
} as Record<string, RoutePageLoader>;
