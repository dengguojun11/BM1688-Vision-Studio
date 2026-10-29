import { lazy } from 'react';
import type React from 'react';

import AdminPage from './pages/admin';
import AlgorithmsPage from './pages/algorithms';
import AnnotationReviewPage from './pages/annotationReview';
import DatasetsPage from './pages/datasets';
import EvolutionPage from './pages/evolution';
import IngestionPage from './pages/ingestion';
import ModelsPage from './pages/models';
import ProvidersPage from './pages/providers';
import RulesPage from './pages/rules';
import TrainingPage from './pages/training';
import WorkspacePage from './pages/workspace';
const TasksPage = lazy(() => import('../tasks'));
import type { PlatformModule } from './modules';

export { default as PlatformLayout } from './layout';
export { PLATFORM_MODULES, getActiveModule, type PlatformModule } from './modules';
export {
  AdminPage,
  AlgorithmsPage,
  AnnotationReviewPage,
  DatasetsPage,
  EvolutionPage,
  IngestionPage,
  ModelsPage,
  ProvidersPage,
  RulesPage,
  TrainingPage,
  WorkspacePage,
};

const PAGE_BY_MODULE: Record<PlatformModule, React.ElementType> = {
  workspace: WorkspacePage,
  datasets: DatasetsPage,
  ingestion: IngestionPage,
  'annotation-review': AnnotationReviewPage,
  tasks: TasksPage,
  algorithms: AlgorithmsPage,
  rules: RulesPage,
  providers: ProvidersPage,
  training: TrainingPage,
  models: ModelsPage,
  evolution: EvolutionPage,
  admin: AdminPage,
};

export function PlatformPage({ module }: { module: PlatformModule }) {
  const Page = PAGE_BY_MODULE[module];
  return <Page />;
}

export default PlatformPage;
