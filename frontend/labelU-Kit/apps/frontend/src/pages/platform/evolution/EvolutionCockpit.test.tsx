import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getEvolutionLineage, getEvolutionOverview, getTrainingMetrics, type EvolutionLineage, type EvolutionOverview } from '@/api/services/evolution';
import EvolutionCockpit from './EvolutionCockpit';

vi.mock('@/api/services/evolution', () => ({
  getEvolutionOverview: vi.fn(),
  getEvolutionLineage: vi.fn(),
  getTrainingMetrics: vi.fn(),
}));

const mockedOverview = vi.mocked(getEvolutionOverview);
const mockedLineage = vi.mocked(getEvolutionLineage);
const mockedMetrics = vi.mocked(getTrainingMetrics);

const decision = {
  id: 'decision-1', report_id: 1, lineage_id: 'job-chain:1', job_id: 2,
  decision_type: 'PARAMETER_OPTIMIZATION', actor_type: 'AI' as const,
  provider_key: 'quality-policy', status: 'COMPLETED', summary: '召回率仍有提升空间，建议提高训练轮次并保持当前数据契约。',
  suggested_params: { epochs: 120 }, created_at: '2026-10-24T10:00:00',
};

const lineage: EvolutionLineage = {
  id: 'job-chain:1', source_type: 'JOB_CHAIN', name: '占道经营检测模型', series_type: 'YOLO 检测',
  status: 'RUNNING', round_count: 2, model_count: 1, current_job_id: 2, champion_job_id: 1,
  latest_metrics: { map50_95: 0.68, map50: 0.89 }, best_metrics: { map50_95: 0.68 },
  metric_delta: { metric: 'map50_95', before: 0.62, after: 0.68, delta: 0.06 },
  latest_decision: decision, updated_at: '2026-10-24T10:00:00',
  nodes: [
    { id: 'job:1', lineage_id: 'job-chain:1', round_no: 1, job_id: 1, name: '基线模型', status: 'COMPLETED', stage: 'PUBLISH', stage_status: 'PASSED', metrics: { map50_95: 0.62 }, quality_gate: { training: 'PASSED' }, model: { id: 1, model_key: 'yolo', display_name: '占道经营 V1', version_no: 1, status: 'PUBLISHED' } },
    { id: 'job:2', lineage_id: 'job-chain:1', round_no: 2, job_id: 2, parent_job_id: 1, name: '自主进化第 2 代', status: 'PROCESSING', stage: 'TRAINING', stage_status: 'RUNNING', progress: { current_epoch: 40, target_epochs: 100, percent: 40 }, metrics: { map50_95: 0.68 }, quality_gate: { training: 'PENDING' }, decision },
  ],
  decisions: [decision],
};

const overview: EvolutionOverview = {
  generated_at: '2026-10-24T10:00:00',
  summary: { lineage_count: 1, active_round_count: 1, active_training_count: 1, model_count: 1, published_model_count: 1, pending_intervention_count: 0, autonomous_decision_count: 1 },
  automation: { training_enabled: true, plan_writes_enabled: true, auto_iteration_enabled: true, status: 'ENABLED', mode: 'PLAN' },
  stages: [
    { key: 'FEEDBACK', label: '难例回流', count: 1, active_count: 0, failed_count: 0, waiting_count: 0, status: 'PASSED' },
    { key: 'TRAINING', label: '模型训练', count: 1, active_count: 1, failed_count: 0, waiting_count: 0, status: 'RUNNING' },
    { key: 'AI_DECISION', label: 'AI 决策', count: 1, active_count: 0, failed_count: 0, waiting_count: 0, status: 'PASSED' },
  ],
  lineages: [{ ...lineage, nodes: undefined, decisions: undefined }],
  active_jobs: [lineage.nodes![1]], decisions: [decision],
  quality_gates: [{ gate: 'training', pass_count: 1, fail_count: 0, pending_count: 1, status: 'RUNNING' }],
  events: [{ id: 'event-1', event_type: 'AI_DECISION', lineage_id: lineage.id, job_id: 2, title: 'AI 完成第 2 代评估', summary: decision.summary, status: 'COMPLETED', actor_type: 'AI', occurred_at: decision.created_at }],
};

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({ matches: false, media: query, onchange: null, addListener: () => undefined, removeListener: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPage(entry = '/') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[entry]}><EvolutionCockpit /></MemoryRouter></QueryClientProvider>);
}

describe('EvolutionCockpit', () => {
  it('renders the real lineage, AI decision and quality evidence', async () => {
    mockedOverview.mockResolvedValue(overview);
    mockedLineage.mockResolvedValue(lineage);
    mockedMetrics.mockResolvedValue({ job_id: 2, available: true, has_more: false, history: [{ epoch: 1, map50_95: 0.4 }, { epoch: 2, map50_95: 0.68 }] });
    renderPage();

    expect(await screen.findByText('占道经营检测模型')).toBeTruthy();
    expect(await screen.findByText('自主进化第 2 代')).toBeTruthy();
    expect(screen.getAllByText(/召回率仍有提升空间/).length).toBeGreaterThan(0);
    expect(screen.getByRole('img', { name: '模型逐轮精度曲线' })).toBeTruthy();
    expect(screen.getByText('自主进化策略运行中')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '查看 占道经营 V1 的进化详情' }));
    expect(await screen.findByText('模型进化证据')).toBeTruthy();
    expect(screen.getByText('数据集版本')).toBeTruthy();
    expect(screen.getByText('五层质量门禁')).toBeTruthy();
  });

  it('shows an honest empty and disabled state without mock activity', async () => {
    mockedOverview.mockResolvedValue({ ...overview, summary: { ...overview.summary, lineage_count: 0, active_training_count: 0 }, automation: { training_enabled: false, plan_writes_enabled: false, auto_iteration_enabled: false, status: 'DISABLED', mode: 'MANUAL' }, lineages: [], active_jobs: [], decisions: [], events: [] });
    renderPage();

    expect(await screen.findByText('自动进化未开启')).toBeTruthy();
    expect(screen.getByText(/从任务标注与首次训练开始/)).toBeTruthy();
    expect(screen.getByText(/当前没有活跃训练作业/)).toBeTruthy();
    expect(mockedLineage).not.toHaveBeenCalled();
    expect(mockedMetrics).not.toHaveBeenCalled();
  });

  it('renders explicit preview fixtures without calling production APIs', async () => {
    renderPage('/workspace/evolution-screen?fullscreen=1&preview=1');

    expect(await screen.findByText('界面预览 · 演示数据')).toBeTruthy();
    expect(await screen.findByText('占道经营 · 自主进化第 2 代')).toBeTruthy();
    expect(screen.getByText('界面预览 · 所有指标均为演示数据')).toBeTruthy();
    expect(mockedOverview).not.toHaveBeenCalled();
    expect(mockedLineage).not.toHaveBeenCalled();
    expect(mockedMetrics).not.toHaveBeenCalled();
  });

  it('offers retry when the overview is unavailable', async () => {
    mockedOverview.mockRejectedValue(new Error('offline'));
    renderPage();
    expect(await screen.findByText('进化数据暂时无法连接')).toBeTruthy();
    expect(screen.getByRole('button', { name: /重新连接/ })).toBeTruthy();
  });
});
