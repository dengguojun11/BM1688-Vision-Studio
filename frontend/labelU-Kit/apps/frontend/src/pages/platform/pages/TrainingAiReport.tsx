import { RobotOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Modal, Space, Spin, Tabs, Tag, message } from 'antd';
import { useMemo, useState } from 'react';

import request from '@/api/request';
import type { Row } from '../shared';
import AiReportMarkdown from './AiReportMarkdown';

type ReportKind = 'full_analysis' | 'next_iteration';

type AiReport = {
  id: number;
  provider_key: string;
  report_kind: ReportKind;
  operation_id?: string | null;
  title?: string | null;
  body?: string | null;
  suggested_params?: string | null;
  error_message?: string | null;
  created_at?: string | null;
};

type AiReportData = {
  job: Row;
  reports: AiReport[];
};

const KIND_LABELS: Record<ReportKind, string> = {
  full_analysis: '训练过程分析',
  next_iteration: '下一轮参数建议',
};

const PROVIDER_LABELS: Record<string, string> = {
  volcengine: '火山方舟',
  qwen_plus: '通义千问 Plus',
  qwen_235b: '通义千问 235B',
};

function statusField(kind: ReportKind) {
  return kind === 'full_analysis' ? 'ai_eval_full_status' : 'ai_eval_next_status';
}

function statusTag(status?: string | null) {
  const normalized = String(status || '').toUpperCase();
  if (normalized === 'COMPLETED') return <Tag color="success">分析完成</Tag>;
  if (normalized === 'RUNNING') return <Tag color="processing">正在分析</Tag>;
  if (normalized === 'FAILED') return <Tag color="error">分析失败</Tag>;
  return <Tag>等待分析</Tag>;
}

export function recommendedTrainParams(value?: string | null): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value);
    const params = parsed?.recommended_train_params ?? parsed?.train ?? parsed?.suggested_params ?? parsed;
    return params && typeof params === 'object' && !Array.isArray(params) ? params : null;
  } catch {
    return null;
  }
}

export function latestReportsByProvider(reports: AiReport[], kind: ReportKind): AiReport[] {
  const latest = new Map<string, AiReport>();
  [...reports]
    .filter((report) => report.report_kind === kind)
    .sort((left, right) => right.id - left.id)
    .forEach((report) => {
      if (!latest.has(report.provider_key)) latest.set(report.provider_key, report);
    });
  return Array.from(latest.values());
}

function ReportCard({ report, kind }: { report: AiReport; kind: ReportKind }) {
  const params = kind === 'next_iteration' ? recommendedTrainParams(report.suggested_params) : null;
  const createdAt = report.created_at?.replace('T', ' ').replace(/\.\d+$/, '');
  return (
    <section
      aria-label={`${PROVIDER_LABELS[report.provider_key] || report.provider_key}报告`}
      style={{ border: '1px solid #dfe6ef', borderRadius: 10, marginBottom: 16, background: '#fff', overflow: 'hidden' }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
          flexWrap: 'wrap',
          padding: '12px 20px',
          borderBottom: '1px solid #edf1f5',
          background: '#fafcff',
        }}
      >
        <strong style={{ display: 'flex', alignItems: 'center', gap: 7, color: '#172033', fontSize: 15 }}>
          <RobotOutlined style={{ color: '#1677ff' }} />
          {PROVIDER_LABELS[report.provider_key] || report.provider_key}
        </strong>
        <span style={{ color: '#7b8798', fontSize: 12 }}>
          报告 #{report.id}
          {createdAt ? ` · ${createdAt}` : ''}
        </span>
      </div>
      <div style={{ padding: '20px 22px' }}>
        {report.error_message ? (
          <Alert type="error" showIcon message="本次分析未生成报告" description={report.error_message} />
        ) : (
          <>
            {params && Object.keys(params).length > 0 && (
              <div style={{ marginBottom: 22 }}>
                <div style={{ marginBottom: 10, color: '#172033', fontWeight: 650, fontSize: 15 }}>建议训练参数</div>
                <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 3 }}>
                  {Object.entries(params).map(([key, value]) => (
                    <Descriptions.Item key={key} label={key}>
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </div>
            )}
            <AiReportMarkdown source={report.body || '模型未返回报告正文。'} />
          </>
        )}
      </div>
    </section>
  );
}

export default function TrainingAiReportAction({ row, compact = false }: { row: Row; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [activeKind, setActiveKind] = useState<ReportKind>('full_analysis');
  const taskId = Number(row.task_id);
  const jobId = Number(row.id);
  const completed = String(row.status || '').toUpperCase() === 'COMPLETED';
  const available = completed && taskId > 0 && jobId > 0;
  const query = useQuery<AiReportData>({
    queryKey: ['training-ai-reports', taskId, jobId],
    enabled: open && available,
    queryFn: async () => {
      const [jobResponse, reportResponse] = await Promise.all([
        request.get(`/v1/tasks/${taskId}/yolo-train-jobs/${jobId}`),
        request.get(`/v1/tasks/${taskId}/yolo-train-jobs/${jobId}/evaluate-reports`),
      ]);
      return {
        job: jobResponse.data?.data ?? jobResponse.data,
        reports: reportResponse.data?.data ?? reportResponse.data ?? [],
      };
    },
    refetchInterval: (current) => {
      const job = current.state.data?.job;
      return ['ai_eval_full_status', 'ai_eval_next_status'].some(
        (key) => String(job?.[key] || '').toUpperCase() === 'RUNNING',
      )
        ? 5000
        : false;
    },
  });
  const analyze = useMutation({
    mutationFn: async (kind: ReportKind) =>
      request.post(`/v1/tasks/${taskId}/yolo-train-jobs/${jobId}/evaluate`, { report_kind: kind }),
    onSuccess: () => {
      message.success('AI 分析已提交，完成后会自动刷新');
      void query.refetch();
    },
    onError: (error: any) => {
      const detail = error?.response?.data;
      message.error(String(detail?.message || detail?.msg || 'AI 分析提交失败，请稍后重试'));
    },
  });
  const reports = useMemo(
    () => latestReportsByProvider(query.data?.reports || [], activeKind),
    [query.data?.reports, activeKind],
  );
  const currentStatus = query.data?.job?.[statusField(activeKind)] ?? row[statusField(activeKind)];

  return (
    <>
      <Button
        type={compact ? 'link' : 'default'}
        size={compact ? 'small' : 'middle'}
        icon={<RobotOutlined />}
        aria-label={compact ? 'AI报告' : 'AI报告分析'}
        onClick={() => setOpen(true)}
        disabled={!available}
        title={available ? '查看训练分析和下一轮参数建议' : '训练完成后可进行 AI 报告分析'}
      >
        {compact ? 'AI报告' : 'AI报告分析'}
      </Button>
      <Modal
        title={`AI 报告分析 · 训练作业 #${jobId}`}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={1120}
        style={{ maxWidth: 'calc(100vw - 24px)' }}
        bodyStyle={{
          maxHeight: 'calc(100vh - 210px)',
          overflowY: 'auto',
          padding: '18px 20px 24px',
          background: '#f5f7fa',
        }}
        destroyOnClose={false}
      >
        <Alert
          type="info"
          showIcon
          message="训练完成后自动生成训练过程分析和下一轮参数建议"
          description="AI 结论用于辅助复核数据和调整参数，不替代指标验证，也不会自动发起下一轮训练。"
          style={{ marginBottom: 16 }}
        />
        <Tabs
          activeKey={activeKind}
          onChange={(key) => setActiveKind(key as ReportKind)}
          items={(Object.keys(KIND_LABELS) as ReportKind[]).map((kind) => ({
            key: kind,
            label: (
              <Space size={6}>
                {KIND_LABELS[kind]}
                {statusTag(query.data?.job?.[statusField(kind)] ?? row[statusField(kind)])}
              </Space>
            ),
          }))}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
          <Button onClick={() => void query.refetch()} loading={query.isFetching}>
            刷新结果
          </Button>
          <Button
            type="primary"
            onClick={() => analyze.mutate(activeKind)}
            loading={analyze.isPending}
            disabled={String(currentStatus || '').toUpperCase() === 'RUNNING'}
          >
            {reports.length ? '重新分析' : '立即分析'}
          </Button>
        </div>
        {query.isLoading ? (
          <div role="status" style={{ textAlign: 'center', padding: '36px 0' }}>
            <Spin tip="正在读取 AI 报告" />
          </div>
        ) : query.isError ? (
          <Alert
            type="error"
            showIcon
            message="AI 报告加载失败"
            description="请检查网络连接后重试。"
            action={
              <Button size="small" onClick={() => void query.refetch()}>
                重试
              </Button>
            }
          />
        ) : reports.length ? (
          reports.map((report) => (
            <ReportCard key={`${report.provider_key}-${report.id}`} report={report} kind={activeKind} />
          ))
        ) : String(currentStatus || '').toUpperCase() === 'RUNNING' ? (
          <Alert
            type="info"
            showIcon
            message="火山模型正在分析训练产物"
            description="报告生成后会自动显示，无需重复提交。"
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无该类 AI 报告，可点击“立即分析”生成" />
        )}
      </Modal>
    </>
  );
}
