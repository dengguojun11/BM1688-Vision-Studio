import {
  ApiOutlined,
  AppstoreOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  CloudUploadOutlined,
  ExpandOutlined,
  ExperimentOutlined,
  FullscreenExitOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Button, Drawer, Spin } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { getEvolutionLineage, getEvolutionOverview, getTrainingMetrics, type EvolutionNode, type EvolutionOverview, type TrainingMetrics } from '@/api/services/evolution';
import { responseStatus } from '../shared';
import { isWorkspaceRootPath } from '../routing';
import MetricChart from './MetricChart';
import EvolutionDataFlow from './EvolutionDataFlow';
import { previewLineage, previewMetrics, previewOverview } from './previewData';
import { automationCopy, formatMetric, formatTime, GATE_LABELS, isPassedStatus, mergeMetricHistory, metricHistoryAvailable, nodeProgress, pollingInterval, statusLabel, statusTone } from './cockpitUtils';
import {
  AutomationBadge,
  BottomGrid,
  Cockpit,
  Detail,
  EmptyState,
  ErrorState,
  EventItem,
  EventList,
  EvolutionFlow,
  GateList,
  GateRow,
  GovernanceNote,
  Hero,
  InsightBlock,
  JobItem,
  JobList,
  LineageItem,
  LineageList,
  MainGrid,
  Metric,
  Metrics,
  ModelJourney,
  Panel,
  PanelTitle,
  ScreenFooter,
  Stage,
  StageTrack,
  StatusPill,
  VersionConnector,
  VersionNode,
  VersionTrack,
} from './EvolutionCockpit.styles';

const EMPTY_OVERVIEW: EvolutionOverview = {
  generated_at: '',
  summary: { lineage_count: 0, active_round_count: 0, active_training_count: 0, model_count: 0, published_model_count: 0, pending_intervention_count: 0, autonomous_decision_count: 0 },
  automation: { training_enabled: false, plan_writes_enabled: false, auto_iteration_enabled: false, status: 'DISABLED', mode: 'MANUAL' },
  stages: [], lineages: [], active_jobs: [], decisions: [], quality_gates: [], events: [],
};

function MetricValue({ value, unit }: { value: number; unit: string }) {
  return <div className="metric-value">{value.toLocaleString('zh-CN')}<span className="metric-unit">{unit}</span></div>;
}

function NodeDetail({ node, championJobId }: { node: EvolutionNode; championJobId?: number | null }) {
  return (
    <Detail>
      <div className="detail-hero">
        <StatusPill $tone={statusTone(node.status)}>{statusLabel(node.status)}</StatusPill>
        <h3>{node.model?.display_name || node.name}</h3>
        <p>第 {node.round_no || '—'} 轮进化 · 训练作业 {node.job_id ? `#${node.job_id}` : '尚未创建'}</p>
        <div className="detail-score">{formatMetric(node.metrics.map50_95)} <small>mAP@50:95</small></div>
        {championJobId === node.job_id && node.job_id != null && <StatusPill $tone="success">当前 Champion</StatusPill>}
      </div>
      <dl>
        <dt>当前阶段</dt><dd>{node.stage} · {statusLabel(node.stage_status)}</dd>
        <dt>模型版本</dt><dd>{node.model ? `${node.model.model_key} · v${node.model.version_no}` : '尚未登记模型版本'}</dd>
        <dt>数据集版本</dt><dd>{node.dataset_version_id ? `#${node.dataset_version_id}` : '尚未关联'}</dd>
        <dt>执行节点</dt><dd>{node.train_node_id || node.execution_mode || '待调度'}</dd>
        <dt>父代训练</dt><dd>{node.parent_job_id ? `训练作业 #${node.parent_job_id}` : '初始基线'}</dd>
        <dt>训练进度</dt><dd>{node.progress?.current_epoch ?? 0} / {node.progress?.target_epochs ?? '—'} Epoch · {nodeProgress(node).toFixed(0)}%</dd>
        <dt>mAP@50</dt><dd>{formatMetric(node.metrics.map50)}</dd>
        <dt>Precision / Recall</dt><dd>{formatMetric(node.metrics.precision)} / {formatMetric(node.metrics.recall)}</dd>
        <dt>最近更新</dt><dd>{formatTime(node.updated_at || node.created_at)}</dd>
        {node.failure_reason && <><dt>失败原因</dt><dd>{node.failure_reason}</dd></>}
      </dl>
      <h4>五层质量门禁</h4>
      <GateList>
        {Object.entries(GATE_LABELS).map(([gate, label]) => {
          const value = node.quality_gate?.[gate] || 'PENDING';
          return <GateRow key={gate} $tone={statusTone(value)}><span className="gate-icon">{isPassedStatus(value) ? <CheckOutlined /> : <SafetyCertificateOutlined />}</span><span>{label}</span><StatusPill $tone={statusTone(value)}>{statusLabel(value)}</StatusPill></GateRow>;
        })}
      </GateList>
      <h4>AI 进化建议</h4>
      {node.decision ? <InsightBlock><div className="ai-label"><RobotOutlined /> {node.decision.decision_type}</div><div className="ai-summary">{node.decision.summary}</div><div className="ai-meta">{node.decision.provider_key || '策略引擎'} · {formatTime(node.decision.created_at)}</div></InsightBlock> : <EmptyState>本轮尚未生成 AI 决策</EmptyState>}
      {node.decision?.suggested_params && <><h4>建议参数（仅展示，未自动执行）</h4><pre>{JSON.stringify(node.decision.suggested_params, null, 2)}</pre></>}
    </Detail>
  );
}

export default function EvolutionCockpit() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const defaultDataScreen = isWorkspaceRootPath(location.pathname);
  const fullscreen = params.get('fullscreen') === '1' || defaultDataScreen;
  const preview = params.get('preview') === '1';
  const [selectedLineageId, setSelectedLineageId] = useState<string>();
  const [selectedJobId, setSelectedJobId] = useState<number>();
  const [selectedNode, setSelectedNode] = useState<EvolutionNode | null>(null);
  const [metricState, setMetricState] = useState<{ jobId: number | null; rows: TrainingMetrics[] }>({ jobId: null, rows: [] });
  const [metricMode, setMetricMode] = useState<'live' | 'snapshot'>('live');

  const overviewQuery = useQuery({
    queryKey: ['evolution-cockpit-overview', preview],
    queryFn: preview ? async () => previewOverview : getEvolutionOverview,
    refetchInterval: preview ? false : (query) => pollingInterval(undefined, query.state.data?.summary.active_training_count || 0),
    refetchIntervalInBackground: false,
    retry: false,
  });
  const overview = overviewQuery.data || EMPTY_OVERVIEW;

  useEffect(() => {
    if (!overview.lineages.length) {
      setSelectedLineageId(undefined);
      return;
    }
    if (!selectedLineageId || !overview.lineages.some((item) => item.id === selectedLineageId)) {
      setSelectedLineageId(overview.lineages[0].id);
    }
  }, [overview.lineages, selectedLineageId]);

  useEffect(() => {
    if (!selectedJobId || !overview.active_jobs.some((item) => item.job_id === selectedJobId)) {
      setSelectedJobId(overview.active_jobs.find((item) => item.job_id != null)?.job_id || undefined);
    }
  }, [overview.active_jobs, selectedJobId]);

  const lineageQuery = useQuery({
    queryKey: ['evolution-cockpit-lineage', selectedLineageId, preview],
    queryFn: preview ? async () => previewLineage : () => getEvolutionLineage(selectedLineageId!),
    enabled: Boolean(selectedLineageId),
    refetchInterval: preview ? false : (query) => pollingInterval(query.state.data?.status, overview.summary.active_training_count),
    refetchIntervalInBackground: false,
  });
  const lineage = lineageQuery.data;
  const selectedLineageSummary = overview.lineages.find((item) => item.id === selectedLineageId);
  const nodes = useMemo(
    () => [...(lineage?.nodes || [])].sort((left, right) => {
      const roundDifference = (left.round_no || 0) - (right.round_no || 0);
      if (roundDifference !== 0) return roundDifference;
      return String(left.created_at || left.id).localeCompare(String(right.created_at || right.id));
    }),
    [lineage?.nodes],
  );
  const selectedJob = overview.active_jobs.find((item) => item.job_id === selectedJobId);
  const metricRows = metricState.jobId === (selectedJobId || null) ? metricState.rows : [];
  const latestMetricEpoch = metricRows.reduce<number | null>((latest, row) => {
    if (typeof row.epoch !== 'number' || !Number.isFinite(row.epoch)) return latest;
    return latest == null ? row.epoch : Math.max(latest, row.epoch);
  }, null);
  const metricsQuery = useQuery({
    queryKey: ['evolution-cockpit-metrics', selectedJobId, preview, latestMetricEpoch],
    queryFn: preview ? async () => previewMetrics : () => getTrainingMetrics(selectedJobId!, latestMetricEpoch),
    enabled: Boolean(selectedJobId) && metricMode === 'live',
    refetchInterval: !preview && selectedJob && ['PENDING', 'PROCESSING', 'RUNNING', 'QUEUED'].includes(selectedJob.status) ? 8_000 : false,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    setMetricState({ jobId: selectedJobId || null, rows: [] });
  }, [selectedJobId]);
  useEffect(() => {
    if (metricsQuery.data?.history && selectedJobId) {
      setMetricState((current) => ({
        jobId: selectedJobId,
        rows: mergeMetricHistory(current.jobId === selectedJobId ? current.rows : [], metricsQuery.data.history),
      }));
    }
  }, [metricsQuery.data, selectedJobId]);

  useEffect(() => {
    if (selectedNode && selectedNode.lineage_id !== selectedLineageId) {
      setSelectedNode(null);
    }
  }, [selectedLineageId, selectedNode]);

  useEffect(() => {
    if (!selectedNode) return;
    const refreshedNode = nodes.find((node) => node.id === selectedNode.id);
    if (!refreshedNode) {
      setSelectedNode(null);
    } else if (refreshedNode !== selectedNode) {
      setSelectedNode(refreshedNode);
    }
  }, [nodes, selectedNode]);

  const selectedNodeMetrics = selectedNode?.metrics;
  const snapshotRows = useMemo(() => selectedNodeMetrics && Object.keys(selectedNodeMetrics).length ? [{ ...selectedNodeMetrics, epoch: selectedNode?.round_no || 1 }] : [], [selectedNodeMetrics, selectedNode?.round_no]);
  const chartRows = metricMode === 'snapshot' ? snapshotRows : metricRows;
  const activeMetricNode = metricMode === 'snapshot' ? selectedNode : selectedJob;
  const metricAvailable = metricHistoryAvailable(metricMode, metricRows.length, metricsQuery.data?.available);
  const automation = preview
    ? { label: '界面预览 · 演示数据', description: '仅用于查看驾驶舱视觉与交互，不代表真实训练或自动化运行状态。' }
    : automationCopy(overview.automation);
  const latestDecision = lineage?.latest_decision || overview.decisions[0];

  const toggleFullscreen = () => {
    if (fullscreen) {
      navigate('/workspace/workbench');
      return;
    }
    const params = new URLSearchParams(location.search);
    params.set('fullscreen', '1');
    navigate({ pathname: '/workspace/evolution-screen', search: params.toString() ? `?${params}` : '' });
  };

  if (overviewQuery.isLoading) {
    return <Cockpit className={fullscreen ? 'is-fullscreen' : undefined}><ErrorState><Spin size="large" /><h2>正在连接模型进化中枢</h2><p>读取真实训练谱系、模型指标与自动化策略…</p></ErrorState></Cockpit>;
  }

  if (overviewQuery.isError) {
    const status = responseStatus(overviewQuery.error);
    return <Cockpit className={fullscreen ? 'is-fullscreen' : undefined}><ErrorState><ApiOutlined style={{ color: 'var(--red)', fontSize: 32 }} /><h2>{status === 403 ? '当前账号无权访问进化驾驶舱' : '进化数据暂时无法连接'}</h2><p>{status === 401 ? '登录状态已失效，请重新登录后查看。' : '请确认后端服务与网络连接正常。本页不会使用模拟数据替代真实运行状态。'}</p><Button icon={<ReloadOutlined />} onClick={() => void overviewQuery.refetch()}>重新连接</Button></ErrorState></Cockpit>;
  }

  return (
    <Cockpit className={fullscreen ? 'is-fullscreen' : undefined}>
      <Hero>
        <div className="hero-copy">
          <h1>模型自主进化中枢<span>Model evolution intelligence</span></h1>
          <p>让数据成为燃料，让训练持续发生。贯通难例回流、数据治理、模型训练、AI 评估与质量发布，见证每一代模型的真实进化。</p>
        </div>
        <div className="hero-actions">
          <AutomationBadge role="status" aria-label={automation.description} $tone={!preview && overview.automation.status === 'ENABLED' ? 'on' : 'off'} title={automation.description}><RobotOutlined />{automation.label}</AutomationBadge>
          {!preview && <span className="hero-freshness" role="status">更新于 {formatTime(overview.generated_at)}</span>}
          <Button title="刷新进化数据" aria-label="刷新进化数据" icon={<ReloadOutlined spin={overviewQuery.isFetching} />} onClick={() => { void overviewQuery.refetch(); void lineageQuery.refetch(); }} />
          <Button icon={<AppstoreOutlined />} onClick={() => navigate('/workspace/workbench')}>运行工作台</Button>
          {!defaultDataScreen && <Button icon={fullscreen ? <FullscreenExitOutlined /> : <ExpandOutlined />} onClick={toggleFullscreen}>{fullscreen ? '退出大屏' : '大屏模式'}</Button>}
        </div>
      </Hero>

      <Metrics aria-label="进化运行总览">
        <Metric><div className="metric-label">模型进化谱系<ExperimentOutlined /></div><MetricValue value={overview.summary.lineage_count} unit="条" /></Metric>
        <Metric $accent="var(--blue)"><div className="metric-label">运行中训练<ApiOutlined /></div><MetricValue value={overview.summary.active_training_count} unit="个" /></Metric>
        <Metric $accent="var(--violet)"><div className="metric-label">活跃进化谱系<ArrowRightOutlined /></div><MetricValue value={overview.summary.active_round_count} unit="条" /></Metric>
        <Metric $accent="var(--green)"><div className="metric-label">模型资产<SafetyCertificateOutlined /></div><MetricValue value={overview.summary.model_count} unit="个" /></Metric>
        <Metric $accent="var(--green)"><div className="metric-label">已发布模型<CloudUploadOutlined /></div><MetricValue value={overview.summary.published_model_count} unit="个" /></Metric>
        <Metric $accent={overview.summary.pending_intervention_count ? 'var(--amber)' : 'var(--cyan)'}><div className="metric-label">待人工介入<RobotOutlined /></div><MetricValue value={overview.summary.pending_intervention_count} unit="项" /></Metric>
      </Metrics>

      <MainGrid>
        <Panel>
          <PanelTitle><div><h2>模型进化谱系</h2><p>Model lineages</p></div><span className="panel-extra">{overview.lineages.length} 条</span></PanelTitle>
          {overview.lineages.length ? <LineageList>
            {overview.lineages.map((item) => <LineageItem key={item.id} type="button" title={item.name} $active={item.id === selectedLineageId} $tone={statusTone(item.status)} onClick={() => { setSelectedLineageId(item.id); setSelectedNode(null); setMetricMode('live'); }} aria-pressed={item.id === selectedLineageId}>
              <div className="lineage-top"><span className="lineage-name">{item.name}</span><StatusPill $tone={statusTone(item.status)}>{statusLabel(item.status)}</StatusPill></div>
              <div className="lineage-type">{item.series_type} · {item.source_type === 'PLAN' ? '自治计划' : '迭代链'}</div>
              <div className="lineage-meta"><span>{item.round_count} 轮 · {item.model_count} 模型</span><span className="lineage-score">mAP {formatMetric(item.best_metrics.map50_95 || item.latest_metrics.map50_95)}</span></div>
            </LineageItem>)}
          </LineageList> : <EmptyState>尚无模型进化谱系<br />从任务标注与首次训练开始，平台将在这里记录每一代模型。</EmptyState>}
        </Panel>

        <Panel className="flow-panel">
          <PanelTitle><div><h2>自主进化闭环</h2><p>Data to train to evaluate to evolve</p></div><span className="panel-extra">{lineage?.name || selectedLineageSummary?.name || '等待谱系'}</span></PanelTitle>
          <EvolutionFlow className={preview ? 'has-preview' : undefined}>
            <EvolutionDataFlow active={preview || overview.summary.active_training_count > 0} preview={preview} />
            <StageTrack aria-label="模型进化十阶段">
              {overview.stages.map((stage, index) => <Stage key={stage.key} $tone={statusTone(stage.status)} title={`${stage.label}：${statusLabel(stage.status)}`}>
                <div className="stage-node">{isPassedStatus(stage.status) ? <CheckOutlined /> : index + 1}</div><span className="stage-label">{stage.label}</span><span className="stage-count">{stage.active_count ? `${stage.active_count} 进行中` : `${stage.count} 记录`}</span>
              </Stage>)}
            </StageTrack>
          </EvolutionFlow>
          <ModelJourney>
            <div className="journey-caption"><span>代际演进路径 · 点击查看模型证据</span>{lineage?.metric_delta && <strong>mAP 提升 {lineage.metric_delta.delta >= 0 ? '+' : ''}{(lineage.metric_delta.delta * 100).toFixed(2)} pts</strong>}</div>
            {lineageQuery.isLoading ? <EmptyState><Spin /> 正在读取谱系…</EmptyState> : lineageQuery.isError ? <EmptyState>谱系详情加载失败 <Button size="small" onClick={() => void lineageQuery.refetch()}>重试</Button></EmptyState> : nodes.length ? <VersionTrack>
              {nodes.map((node, index) => <div key={node.id} style={{ display: 'contents' }}>
                {index > 0 && <VersionConnector aria-hidden="true" />}
                <VersionNode type="button" $tone={statusTone(node.status)} $selected={selectedNode?.id === node.id} onClick={() => { setSelectedNode(node); setMetricMode('snapshot'); }} aria-label={`查看 ${node.model?.display_name || node.name} 的进化详情`}>
                  {lineage?.champion_job_id === node.job_id && node.job_id != null && <span className="champion">CHAMPION</span>}
                  <div className="version-top"><span className="version-name">{node.model ? `V${node.model.version_no}` : `R${node.round_no || index + 1}`}</span><StatusPill $tone={statusTone(node.status)}>{statusLabel(node.status)}</StatusPill></div>
                  <div className="version-job">{node.name}</div>
                  <div className="version-map">{formatMetric(node.metrics.map50_95)} <span className="version-map-label">mAP@50:95</span></div>
                  <div className="version-stage">{node.stage} · {statusLabel(node.stage_status)}</div>
                  <div className="version-progress"><i style={{ width: `${nodeProgress(node)}%` }} /></div>
                </VersionNode>
              </div>)}
            </VersionTrack> : <EmptyState>{selectedLineageId ? '该谱系尚未产生训练轮次，正在等待数据或策略条件。' : '选择左侧谱系查看模型代际演进。'}</EmptyState>}
          </ModelJourney>
        </Panel>

        <Panel className="intelligence-panel">
          <PanelTitle><div><h2>AI 进化决策</h2><p>Intelligence and governance</p></div><RobotOutlined style={{ color: 'var(--violet)' }} /></PanelTitle>
          {latestDecision ? <InsightBlock><div className="ai-label"><RobotOutlined /> AI evolution analyst</div><div className="ai-summary">{latestDecision.summary}</div><div className="ai-meta">{latestDecision.decision_type} · {latestDecision.provider_key || '策略引擎'}<br />{formatTime(latestDecision.created_at)}</div></InsightBlock> : <EmptyState>尚未形成 AI 决策<br />完成模型评估后，这里将呈现参数建议与进化判断。</EmptyState>}
           <GateList aria-label="质量门禁汇总">
             {overview.quality_gates.map((gate) => <GateRow key={gate.gate} $tone={statusTone(gate.status)}><span className="gate-icon">{isPassedStatus(gate.status) ? <CheckOutlined /> : <SafetyCertificateOutlined />}</span><span>{GATE_LABELS[gate.gate] || gate.gate}</span><span className="gate-count"><span className="gate-count-pass">{gate.pass_count} 通过</span><span className="gate-count-pending">{gate.pending_count} 待验证</span>{gate.fail_count ? <span className="gate-count-fail">{gate.fail_count} 阻断</span> : null}</span></GateRow>)}
          </GateList>
           <GovernanceNote>{automation.description}<br />自主决策 {overview.summary.autonomous_decision_count} 次，发布始终受质量门禁约束。</GovernanceNote>
        </Panel>
      </MainGrid>

      <BottomGrid>
        <Panel>
          <PanelTitle><div><h2>实时训练矩阵</h2><p>Live training jobs</p></div><span className="panel-extra">{overview.active_jobs.length} 活跃</span></PanelTitle>
          {overview.active_jobs.length ? <JobList>{overview.active_jobs.map((job) => <JobItem key={job.id} type="button" $selected={selectedJobId === job.job_id} onClick={() => { if (job.job_id) { setSelectedJobId(job.job_id); setMetricMode('live'); } }}>
            <div className="job-top"><span className="job-name">{job.name}</span><StatusPill $tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusPill></div>
            <div className="job-meta"><span>#{job.job_id} · {job.train_node_id || job.execution_mode || '待调度'}</span><span>{job.progress?.current_epoch ?? 0}/{job.progress?.target_epochs ?? '—'} epoch</span></div>
            <div className="job-progress"><i style={{ width: `${nodeProgress(job)}%` }} /></div>
          </JobItem>)}</JobList> : <EmptyState>当前没有活跃训练作业<br />系统空闲时仍保留完整谱系与历史决策。</EmptyState>}
        </Panel>

        <Panel>
          <PanelTitle><div><h2>模型进化遥测</h2><p>{activeMetricNode ? `${activeMetricNode.name} · 真实逐轮指标` : 'Training telemetry'}</p></div>{selectedNode && <Button size="small" onClick={() => setMetricMode(metricMode === 'snapshot' ? 'live' : 'snapshot')}>{metricMode === 'snapshot' ? '实时曲线' : '节点快照'}</Button>}</PanelTitle>
           <MetricChart history={chartRows} available={metricAvailable} error={metricMode === 'live' ? metricsQuery.error : undefined} loading={metricMode === 'live' && metricsQuery.isFetching} onRetry={() => void metricsQuery.refetch()} />
        </Panel>

        <Panel className="events-panel">
          <PanelTitle><div><h2>进化事件流</h2><p>Autonomous activity</p></div><span className="panel-extra">最新动态</span></PanelTitle>
          {overview.events.length ? <EventList>{overview.events.map((event) => <EventItem key={event.id}><div className="event-title">{event.title}</div><div className="event-summary">{event.summary}</div><div className="event-time">{event.actor_type} · {formatTime(event.occurred_at)}</div></EventItem>)}</EventList> : <EmptyState>暂无进化事件<br />训练、评估和模型发布产生的事件将汇聚于此。</EmptyState>}
        </Panel>
      </BottomGrid>

      <ScreenFooter><span>7GAI · 数据智能与模型自我进化平台</span><span>{preview ? '界面预览 · 所有指标均为演示数据' : `只读实时投影 · 数据更新于 ${formatTime(overview.generated_at)}`}</span></ScreenFooter>

      <Drawer title="模型进化证据" width={440} open={Boolean(selectedNode)} onClose={() => setSelectedNode(null)} destroyOnClose>
        {selectedNode && <NodeDetail node={selectedNode} championJobId={lineage?.champion_job_id} />}
      </Drawer>
    </Cockpit>
  );
}
