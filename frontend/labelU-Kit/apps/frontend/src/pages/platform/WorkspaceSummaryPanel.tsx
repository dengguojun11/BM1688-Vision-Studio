import { ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Empty, Spin } from 'antd';
import styled from 'styled-components';

import { workspaceSummaryState } from './routing';

export type WorkspaceSummary = {
  task_count: number;
  pending_annotation_task_count: number;
  model_count: number;
  published_model_count: number;
  download_disabled_model_count: number;
  active_training_count: number;
  failed_training_count: number;
  pending_feedback_count: number;
};

const StatusStrip = styled.div`
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
  min-width: 0;

  @media (max-width: 680px) {
    grid-template-columns: 1fr;
  }
`;

const StatusItem = styled.div`
  min-height: 72px;
  padding: 15px 16px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;

  .status-label {
    display: block;
    margin-bottom: 7px;
    color: #64748b;
    font-size: 12px;
  }

  .status-value {
    color: #1e293b;
    font-size: 14px;
    font-weight: 600;
  }
`;

const WorkPanel = styled.section`
  min-width: 0;
  padding: 20px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;

  button:focus-visible,
  a:focus-visible {
    outline: 2px solid var(--color-primary);
    outline-offset: 2px;
  }

  button:active {
    transform: translateY(1px);
  }

  button:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
`;

export function WorkspaceSummaryPanel({
  state,
  summary,
  refreshing = false,
  onRetry,
}: {
  state: ReturnType<typeof workspaceSummaryState>;
  summary?: WorkspaceSummary;
  refreshing?: boolean;
  onRetry: () => void;
}) {
  if (state === 'ready' && summary) {
    return (
      <StatusStrip role="region" aria-label="工作台摘要">
        <StatusItem><span className="status-label">任务 / 待标注</span><span className="status-value">{summary.task_count} / {summary.pending_annotation_task_count}</span></StatusItem>
        <StatusItem><span className="status-label">训练中 / 失败</span><span className="status-value">{summary.active_training_count} / {summary.failed_training_count}</span></StatusItem>
        <StatusItem><span className="status-label">训练模型 / 已发布</span><span className="status-value">{summary.model_count} / {summary.published_model_count}</span></StatusItem>
        <StatusItem><span className="status-label">已禁止下载模型</span><span className="status-value">{summary.download_disabled_model_count}</span></StatusItem>
        <StatusItem><span className="status-label">待处理回流</span><span className="status-value">{summary.pending_feedback_count}</span></StatusItem>
        <StatusItem><span className="status-label">数据状态</span><span className="status-value">{refreshing ? '刷新中' : '已同步'}</span></StatusItem>
      </StatusStrip>
    );
  }

  return (
    <WorkPanel aria-live="polite">
      {state === 'loading' ? (
        <div role="status" aria-label="正在加载工作台摘要" style={{ minHeight: 80, display: 'grid', placeItems: 'center' }}>
          <Spin spinning tip="正在加载工作台摘要"><span style={{ display: 'inline-block', minHeight: 24 }} /></Spin>
        </div>
      ) : state === 'empty' ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无工作台数据">
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>重试</Button>
        </Empty>
      ) : (
        <Alert
          type={state === 'offline' ? 'warning' : 'error'}
          showIcon
          message={state === 'forbidden' ? '无权限查看工作台摘要' : state === 'missing' ? '工作台摘要接口不存在' : state === 'conflict' ? '工作台摘要版本冲突' : state === 'offline' ? '当前网络已断开' : '工作台摘要加载失败'}
          description={state === 'forbidden' ? '请切换到有权限的账号或联系管理员。' : state === 'conflict' ? '摘要版本已变化，请刷新后重试。' : state === 'offline' ? '恢复网络后重试，页面不会提交任何操作。' : '未取得真实摘要，不会把平台状态显示为正常。'}
          action={<Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>重试</Button>}
        />
      )}
    </WorkPanel>
  );
}
