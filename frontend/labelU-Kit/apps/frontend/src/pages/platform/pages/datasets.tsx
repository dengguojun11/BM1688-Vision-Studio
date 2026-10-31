import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloseOutlined,
  LockOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SettingOutlined,
  TagsOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Steps,
  Tag,
  Typography,
  message,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import styled from 'styled-components';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import ResourcePage from '../ResourcePage';
import { listData, listTotal, type Row } from '../shared';
import { AnnotationPromptModal, type PromptContract } from './annotationPromptModal';

type DatasetVersion = Row & {
  id: number;
  dataset_id: number;
  version_no: number;
  status: string;
  manifest_rel_path: string;
  manifest_sha256: string;
  sample_count: number;
  quality_status?: string;
  review_status?: string;
  review_comment?: string | null;
  reviewed_by?: number | null;
  reviewed_at?: string | null;
  dataset_label_contract_id?: number | null;
  dataset_contract_revision?: number | null;
  labels_snapshot_json?: string | null;
};
type SourceTask = Row & {
  id: number;
  name?: string;
  status?: string;
  media_type?: string;
  training_ready?: boolean;
  label_set_signature?: string;
  class_order_signature?: string;
  dataset_id?: number;
  dataset_label_contract_id?: number;
  task_label_scope_mode?: 'FULL' | 'SUBSET';
};
type AnnotationLabel = {
  id: number;
  label_key?: string;
  label_value?: string;
  annotation_kind?: string;
  status?: string;
  color?: string;
};

const ANNOTATION_LABEL_PAGE_SIZE_OPTIONS = [20, 50, 100];

function normalizeAnnotationLabel(value: any): AnnotationLabel | null {
  const id = Number(value?.id ?? value?.label_id);
  if (!Number.isFinite(id) || id <= 0) return null;
  return {
    id,
    label_key: value?.label_key,
    label_value: value?.label_value,
    annotation_kind: value?.annotation_kind,
    status: value?.status,
    color: value?.color,
  };
}

export function fetchAnnotationLabels(page = 0, size = ANNOTATION_LABEL_PAGE_SIZE_OPTIONS[0], search?: string) {
  const keyword = String(search || '').trim();
  return request.get('/v1/annotation-taxonomy/labels', {
    params: {
      include_disabled: false,
      page,
      size,
      ...(keyword ? { q: keyword } : {}),
    },
  });
}
export type DatasetLabelContract = PromptContract & {
  id: number;
  dataset_id: number;
  revision: number;
  annotation_kind: string;
  label_ids: number[];
  labels: Array<{
    label_id?: number;
    label_key?: string;
    label_value?: string;
    annotation_kind?: string;
    color?: string;
  }>;
  label_set_signature?: string;
  class_order_signature?: string;
  status: string;
  created_at?: string;
  frozen_at?: string | null;
};

export function isBboxOnlyContract(contract: Pick<DatasetLabelContract, 'labels'> | null | undefined) {
  return Boolean(
    contract &&
      (contract.labels || []).every((label) => {
        const kind = String(label.annotation_kind || '').trim().toLowerCase();
        // Legacy snapshots omitted the per-label kind and are known bbox
        // contracts; an explicit polygon makes the contract non-bbox.
        return !kind || kind === 'bbox';
      }),
  );
}
type ValidationResult = { version_id: number; valid: boolean; checks: Record<string, boolean>; warnings?: string[] };

function labelDisplayName(label: { label_key?: string; label_value?: string; label_id?: number }) {
  const chineseName = String(label.label_key || '').trim();
  const englishName = String(label.label_value || '').trim();
  if (chineseName && englishName) return `${chineseName}（${englishName}）`;
  return chineseName || englishName || `标签 #${label.label_id ?? ''}`;
}

function annotationKindLabel(value: unknown) {
  const kind = String(value || '').trim().toLowerCase();
  if (kind === 'polygon') return '多边形';
  if (kind === 'bbox') return '矩形框';
  return kind || '未设置';
}

export function fetchDatasets(page = 0, size = 20, search?: string) {
  const keyword = String(search || '').trim();
  return request.get('/v1/datasets', {
    params: { page, size, ...(keyword ? { q: keyword } : {}) },
  });
}

export function fetchSourceTasks(datasetId?: number, page = 0, size = 20, search?: string) {
  const keyword = String(search || '').trim();
  return request.get('/v1/tasks', {
    params: {
      page,
      size,
      status: 'FINISHED',
      ...(datasetId ? { dataset_id: datasetId } : {}),
      ...(keyword ? { q: keyword } : {}),
    },
  });
}

const SOURCE_TASK_PAGE_SIZE_OPTIONS = [20, 50, 100];

function sourceTaskOptionLabel(task: SourceTask) {
  return `${task.name || `任务 #${task.id}`} · ${task.media_type || '未知媒体'} · 已完成`;
}

type SourceTaskSelectorProps = {
  datasetId?: number;
  enabled?: boolean;
  id?: string;
  value?: Array<number | string>;
  onChange?: (value: number[]) => void;
};

/**
 * Server-backed source-task picker used by dataset-version creation.
 *
 * The selected-task cache is intentionally kept outside the query result so
 * changing the search term or page never drops an already selected task.
 */
function SourceTaskSelector({ datasetId, enabled = true, id, value = [], onChange }: SourceTaskSelectorProps) {
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(SOURCE_TASK_PAGE_SIZE_OPTIONS[0]);
  const [selectedTaskCache, setSelectedTaskCache] = useState<Record<number, SourceTask>>({});
  const selectedIds = Array.from(
    new Set(
      (Array.isArray(value) ? value : [])
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item) && item > 0),
    ),
  );
  const sourceTasksQuery = useQuery({
    queryKey: ['dataset-version-source-tasks', datasetId, page, pageSize, search],
    queryFn: async () => {
      const response = await fetchSourceTasks(datasetId, page, pageSize, search);
      const rows = listData(response) as SourceTask[];
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: enabled && Boolean(datasetId),
    staleTime: 30_000,
    retry: false,
  });
  const currentRows = sourceTasksQuery.data?.rows || [];
  const total = sourceTasksQuery.data?.total ?? 0;

  useEffect(() => {
    if (!currentRows.length) return;
    setSelectedTaskCache((previous) => {
      const next = { ...previous };
      currentRows.forEach((task) => {
        if (task.id) next[Number(task.id)] = task;
      });
      return next;
    });
  }, [currentRows]);

  const selectedOptions = selectedIds.map((id) => selectedTaskCache[id] || ({ id } as SourceTask));
  const optionMap = new Map<number, SourceTask>();
  [...selectedOptions, ...currentRows].forEach((task) => {
    if (task?.id) optionMap.set(Number(task.id), task);
  });
  const options = Array.from(optionMap.values()).map((task) => ({
    value: Number(task.id),
    label: sourceTaskOptionLabel(task),
  }));

  const applySearch = (nextSearch: string) => {
    const normalized = nextSearch.trim();
    setSearchDraft(nextSearch);
    setSearch(normalized);
    setPage(0);
  };

  const handleChange = (nextValue: Array<number | string>) => {
    const nextIds = Array.from(
      new Set(
        nextValue
          .map((item) => Number(item))
          .filter((item) => Number.isFinite(item) && item > 0),
      ),
    );
    setSelectedTaskCache((previous) => {
      const next = { ...previous };
      optionMap.forEach((task, id) => {
        if (nextIds.includes(id)) next[id] = task;
      });
      return next;
    });
    onChange?.(nextIds);
  };

  return (
    <div>
      <Space direction="vertical" size={8} style={{ display: 'flex' }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <ServerSearchInput
            value={searchDraft}
            onChange={(nextValue) => {
              setSearchDraft(nextValue);
              if (!nextValue.trim()) applySearch('');
            }}
            onSearch={applySearch}
            placeholder="按任务名称、任务 ID、来源或媒体类型搜索"
            ariaLabel="搜索来源标注任务"
            loading={sourceTasksQuery.isFetching}
            style={{ width: 360, maxWidth: '100%' }}
          />
          <Typography.Text type="secondary">
            已选 {selectedIds.length} 个 · {search ? `搜索结果 ${total} 个` : `共 ${total} 个`}
          </Typography.Text>
        </Space>
        <Select
          id={id}
          mode="multiple"
          allowClear
          optionFilterProp="label"
          value={selectedIds}
          loading={sourceTasksQuery.isLoading}
          disabled={sourceTasksQuery.isError}
          placeholder={sourceTasksQuery.isError ? '来源任务加载失败，请重试' : '选择已完成标注的任务'}
          options={options}
          onChange={handleChange}
          filterOption={(input, option) =>
            String(option?.label || '').toLowerCase().includes(input.trim().toLowerCase())
          }
          notFoundContent={sourceTasksQuery.isLoading ? <Spin size="small" /> : '当前页暂无匹配任务'}
        />
        {sourceTasksQuery.error ? (
          <Alert
            type="error"
            showIcon
            message="来源任务加载失败"
            description="请重试；当前已选任务不会被清除。"
            action={
              <Button size="small" onClick={() => void sourceTasksQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : !sourceTasksQuery.isLoading && total === 0 ? (
          <Alert
            type="warning"
            showIcon
            message={search ? '没有匹配的已完成标注任务' : '暂无可用于生成版本的已完成标注任务'}
            description="请先在“任务与标注”中完成样本标注，并确认当前账号是任务创建者或协作者。"
            action={
              <Button size="small" type="link" href="/tasks">
                去任务与标注
              </Button>
            }
          />
        ) : (
          <Pagination
            size="small"
            current={page + 1}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            pageSizeOptions={SOURCE_TASK_PAGE_SIZE_OPTIONS.map(String)}
            hideOnSinglePage
            onChange={(nextPage, nextPageSize) => {
              const resolvedPageSize = nextPageSize || pageSize;
              setPageSize(resolvedPageSize);
              setPage(resolvedPageSize === pageSize ? Math.max(0, nextPage - 1) : 0);
            }}
          />
        )}
      </Space>
    </div>
  );
}

const STATUS_COLORS: Record<string, string> = {
  INGESTING: 'blue',
  VALIDATING: 'processing',
  READY_TO_REVIEW: 'gold',
  FROZEN: 'green',
  RETIRED: 'default',
};
const STATUS_LABELS: Record<string, string> = {
  INGESTING: '接入中',
  VALIDATING: '校验中',
  READY_TO_REVIEW: '待评审',
  FROZEN: '已冻结',
  RETIRED: '已退役',
};
const NEXT_STATUS: Record<string, string | undefined> = { INGESTING: 'VALIDATING', VALIDATING: 'READY_TO_REVIEW' };
const QUALITY_STATUS_LABELS: Record<string, string> = {
  PASS: '质量通过',
  PASSED: '质量通过',
  PENDING: '待校验',
  FAILED: '质量异常',
  BLOCKED: '已阻断',
};
const REVIEW_STATUS_LABELS: Record<string, string> = {
  PENDING: '待评审',
  APPROVED: '评审通过',
  REJECTED: '评审驳回',
};

const GovernanceModalContent = styled.div`
  max-height: calc(100vh - 190px);
  overflow-y: auto;
  padding: 20px;
  color: #172033;

  .governance-summary {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 18px;
    padding-bottom: 16px;
    border-bottom: 1px solid #e8edf4;
  }

  .governance-summary-copy {
    min-width: 0;
  }

  .governance-summary-title {
    display: block;
    margin-bottom: 5px;
    color: #172033;
    font-size: 15px;
    font-weight: 650;
  }

  .governance-summary-copy p {
    max-width: 620px;
    margin: 0;
    color: #64748b;
    font-size: 13px;
    line-height: 1.6;
  }

  .governance-summary-actions {
    flex-shrink: 0;
  }

  .governance-workspace {
    display: grid;
    grid-template-columns: minmax(240px, 0.78fr) minmax(0, 1.65fr);
    gap: 16px;
    align-items: stretch;
  }

  .governance-panel {
    min-width: 0;
    border: 1px solid #e3e8f0;
    border-radius: 8px;
    background: #fff;
  }

  .governance-list-panel {
    display: flex;
    min-height: 480px;
    flex-direction: column;
  }

  .governance-panel-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 14px 16px;
    border-bottom: 1px solid #edf1f5;
  }

  .governance-panel-heading strong {
    color: #172033;
    font-size: 14px;
  }

  .governance-list {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 8px;
    max-height: 560px;
    overflow-y: auto;
    padding: 10px;
  }

  .version-option {
    width: 100%;
    padding: 12px;
    border: 1px solid transparent;
    border-radius: 7px;
    color: #172033;
    background: transparent;
    text-align: left;
    cursor: pointer;
    transition: border-color 120ms ease, background 120ms ease, box-shadow 120ms ease;
  }

  .version-option:hover {
    border-color: #c7d8f5;
    background: #f7faff;
  }

  .version-option[data-selected='true'] {
    border-color: #91caff;
    background: #eaf4ff;
    box-shadow: inset 3px 0 0 #1677ff;
  }

  .version-option-title,
  .version-option-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .version-option-title {
    margin-bottom: 8px;
    font-weight: 650;
  }

  .version-option-meta {
    margin-bottom: 7px;
    color: #64748b;
    font-size: 12px;
  }

  .version-option-path {
    display: block;
    overflow: hidden;
    color: #8a96a8;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .governance-detail-panel {
    padding: 18px;
  }

  .governance-detail-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    margin-bottom: 18px;
  }

  .governance-detail-header h3 {
    margin: 0 0 4px;
    color: #172033;
    font-size: 18px;
    line-height: 1.35;
  }

  .governance-detail-header p {
    margin: 0;
    color: #64748b;
    font-size: 12px;
  }

  .governance-steps {
    margin-bottom: 18px;
    padding: 14px 12px 4px;
    border: 1px solid #edf1f5;
    border-radius: 7px;
    background: #f8fafc;
  }

  .governance-metrics {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 18px;
  }

  .governance-metric {
    min-width: 0;
    padding: 12px;
    border: 1px solid #edf1f5;
    border-radius: 7px;
    background: #fff;
  }

  .governance-metric-label,
  .governance-metric-note {
    display: block;
    color: #64748b;
    font-size: 12px;
  }

  .governance-metric-value {
    display: block;
    margin: 5px 0 3px;
    overflow: hidden;
    color: #172033;
    font-size: 20px;
    font-weight: 700;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .governance-metric-value.is-code {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 14px;
  }

  .governance-section {
    margin-top: 18px;
  }

  .governance-section-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 9px;
    color: #172033;
    font-size: 13px;
    font-weight: 650;
  }

  .governance-evidence {
    overflow: hidden;
    border: 1px solid #edf1f5;
    border-radius: 7px;
  }

  .governance-evidence .ant-descriptions {
    margin: 0;
  }

  .governance-evidence .ant-descriptions-item-label {
    width: 112px;
    color: #64748b;
    background: #fafbfd;
  }

  .governance-evidence .ant-descriptions-item-content {
    min-width: 0;
    color: #172033;
    word-break: break-word;
  }

  .governance-code {
    display: inline-block;
    max-width: 100%;
    overflow: hidden;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
    text-overflow: ellipsis;
    vertical-align: bottom;
  }

  .governance-validation {
    padding: 12px;
    border: 1px solid #b7eb8f;
    border-radius: 7px;
    background: #f6ffed;
  }

  .governance-validation.is-invalid {
    border-color: #ffccc7;
    background: #fff2f0;
  }

  .governance-validation-check {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    margin-top: 7px;
    color: #3f4d61;
    font-size: 12px;
    line-height: 1.5;
  }

  .governance-validation-check svg {
    flex: 0 0 auto;
    margin-top: 2px;
  }

  .governance-action-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px solid #e8edf4;
  }

  .governance-action-hint {
    min-width: 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.5;
  }

  .tag-binding-modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 62px;
    padding: 0 22px;
    color: #fff;
    background: #1677ff;
  }

  .tag-binding-modal-header strong {
    font-size: 18px;
    font-weight: 650;
    letter-spacing: 0.01em;
  }

  .tag-binding-modal-close.ant-btn {
    color: #fff;
  }

  .tag-binding-modal-close.ant-btn:hover,
  .tag-binding-modal-close.ant-btn:focus {
    color: #fff;
    background: rgba(255, 255, 255, 0.14);
  }

  .tag-binding-intro {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
    padding: 0 2px;
    color: #64748b;
    font-size: 13px;
    line-height: 1.55;
  }

  .tag-binding-intro strong {
    color: #172033;
    font-weight: 650;
  }

  .tag-binding-status {
    flex: 0 0 auto;
    color: #64748b;
    white-space: nowrap;
  }

  .tag-binding-transfer {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 74px minmax(0, 1fr);
    gap: 16px;
    align-items: stretch;
    height: clamp(260px, calc(100vh - 390px), 420px);
    min-height: 0;
  }

  .tag-binding-pane {
    display: flex;
    min-width: 0;
    height: 100%;
    min-height: 0;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    background: #fff;
  }

  .tag-binding-pane-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 54px;
    padding: 0 16px;
    color: #172033;
    background: #f0f2f6;
    border-bottom: 1px solid #dfe5ee;
  }

  .tag-binding-pane-header strong {
    font-size: 15px;
    font-weight: 650;
  }

  .tag-binding-pane-count {
    color: #3f4d61;
    font-size: 13px;
  }

  .tag-binding-search {
    margin: 14px 14px 6px;
  }

  .tag-binding-table-head,
  .tag-binding-row {
    display: grid;
    grid-template-columns: 34px minmax(0, 1.05fr) minmax(0, .9fr) minmax(76px, .65fr);
    align-items: center;
    gap: 8px;
    padding: 0 14px;
  }

  .tag-binding-table-head {
    min-height: 42px;
    color: #64748b;
    font-size: 12px;
    border-bottom: 1px solid #e5eaf1;
  }

  .tag-binding-row {
    min-height: 58px;
    color: #172033;
    border-bottom: 1px solid #e5eaf1;
    cursor: pointer;
    transition: background 120ms ease;
  }

  .tag-binding-row:hover {
    background: #f7faff;
  }

  .tag-binding-row.is-selected {
    background: #eaf4ff;
  }

  .tag-binding-row .ant-checkbox-wrapper {
    margin: 0;
  }

  .tag-binding-cell {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tag-binding-cell-primary {
    font-size: 14px;
    font-weight: 550;
  }

  .tag-binding-cell-secondary {
    color: #64748b;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
  }

  .tag-binding-empty {
    display: grid;
    min-height: 180px;
    padding: 24px;
    color: #94a3b8;
    font-size: 13px;
    place-items: center;
    text-align: center;
  }

  .tag-binding-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }

  .tag-binding-actions {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 18px;
  }

  .tag-binding-action.ant-btn {
    width: 52px;
    height: 52px;
    color: #8a96a8;
    border-color: #cbd5e1;
    border-radius: 50%;
    background: #fff;
  }

  .tag-binding-action.ant-btn:not(:disabled):hover {
    color: #1677ff;
    border-color: #91caff;
    background: #f7faff;
  }

  .tag-binding-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin: 18px -20px -20px;
    padding: 16px 20px;
    border-top: 1px solid #e5eaf1;
    background: #fff;
  }

  .tag-binding-footer-hint {
    min-width: 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.5;
  }

  @media (max-width: 860px) {
    .governance-workspace {
      grid-template-columns: 1fr;
    }

    .governance-list-panel {
      min-height: 0;
    }

    .governance-list {
      max-height: 250px;
    }
  }

  @media (max-width: 620px) {
    padding: 14px;

    .governance-summary,
    .governance-action-bar {
      align-items: stretch;
      flex-direction: column;
    }

    .governance-summary-actions,
    .governance-summary-actions .ant-space,
    .governance-action-bar .ant-space {
      width: 100%;
    }

    .governance-summary-actions .ant-btn,
    .governance-action-bar .ant-btn {
      flex: 1;
    }

    .governance-metrics {
      grid-template-columns: 1fr;
    }

    .governance-detail-panel {
      padding: 14px;
    }

    .tag-binding-transfer {
      grid-template-columns: 1fr;
    }

    .tag-binding-actions {
      flex-direction: row;
    }

    .tag-binding-action.ant-btn {
      width: 44px;
      height: 44px;
    }

    .tag-binding-footer {
      align-items: stretch;
      flex-direction: column;
    }

    .tag-binding-footer .ant-space {
      justify-content: flex-end;
    }
  }
`;

export function datasetVersionStatus(value?: string) {
  const status = String(value || 'UNKNOWN').toUpperCase();
  return { status, label: STATUS_LABELS[status] || status, color: STATUS_COLORS[status] || 'default' };
}

function datasetQualityStatus(value?: string) {
  const status = String(value || '').toUpperCase();
  return QUALITY_STATUS_LABELS[status] || (status ? status : '待确认');
}

export function parseDatasetMetadata(value: string | undefined) {
  if (!value?.trim()) return undefined;
  const parsed = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('元数据必须是 JSON 对象');
  return parsed;
}

function VersionStatus({ value, reviewStatus }: { value?: string; reviewStatus?: string }) {
  const status = datasetVersionStatus(value);
  if (status.status === 'READY_TO_REVIEW' && String(reviewStatus || '').toUpperCase() === 'APPROVED') {
    return <Tag color="green">评审通过·待冻结</Tag>;
  }
  if (status.status === 'VALIDATING' && String(reviewStatus || '').toUpperCase() === 'REJECTED') {
    return <Tag color="red">评审驳回</Tag>;
  }
  return <Tag color={status.color}>{status.label}</Tag>;
}

function CreateDatasetAction() {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm<{
    name: string;
    description?: string;
    label_ids?: number[];
  }>();
  const selectedLabelIds = Form.useWatch('label_ids', form) || [];
  const queryClient = useQueryClient();
  const submit = async (values: {
    name: string;
    description?: string;
    label_ids?: number[];
  }) => {
    setSubmitting(true);
    try {
      await request.post('/v1/datasets', {
        name: values.name.trim(),
        ...(values.description?.trim() ? { description: values.description.trim() } : {}),
        label_ids: (values.label_ids || []).map((value) => Number(value)),
      });
      message.success('数据集已创建');
      setOpen(false);
      form.resetFields();
      await queryClient.invalidateQueries({ queryKey: ['datasets'] });
    } catch (error: any) {
      message.error(error?.response?.status === 403 ? '无权创建数据集' : '创建数据集失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
        新建数据集
      </Button>
      <Modal
        title={null}
        open={open}
        centered
        destroyOnClose
        width={980}
        closable={false}
        footer={null}
        aria-label="新建数据集"
        styles={{ content: { padding: 0 }, body: { padding: 0 } }}
        onCancel={() => {
          if (!submitting) setOpen(false);
        }}
      >
        <div className="tag-binding-modal-header">
          <strong>新建数据集</strong>
          <Button
            type="text"
            className="tag-binding-modal-close"
            icon={<CloseOutlined />}
            onClick={() => {
              if (!submitting) setOpen(false);
            }}
            aria-label="关闭"
          />
        </div>
        <GovernanceModalContent>
          <Form form={form} layout="vertical" onFinish={(values) => void submit(values)}>
            <Form.Item
              name="name"
              label="数据集名称"
              rules={[{ required: true, whitespace: true, message: '请输入数据集名称' }]}
            >
              <Input maxLength={128} />
            </Form.Item>
            <Form.Item name="description" label="描述">
              <Input.TextArea rows={3} maxLength={2000} />
            </Form.Item>
            <Form.Item
              name="label_ids"
              label="数据集标签（按顺序）"
              rules={[{ required: true, type: 'array', min: 1, message: '至少选择一个模型标签' }]}
            >
              <TagBindingTransfer enabled={open} />
            </Form.Item>
            <div className="tag-binding-footer">
              <span className="tag-binding-footer-hint">
                已选择 {selectedLabelIds.length} 个标签；训练时将继承此处的类别顺序。
              </span>
              <Space>
                <Button onClick={() => setOpen(false)} disabled={submitting}>
                  取消
                </Button>
                <Button type="primary" loading={submitting} onClick={() => void form.submit()}>
                  创建
                </Button>
              </Space>
            </div>
          </Form>
        </GovernanceModalContent>
      </Modal>
    </>
  );
}

function TagBindingTransfer({
  value,
  onChange,
  enabled = true,
  initialLabels = [],
}: {
  value?: number[];
  onChange?: (value: number[]) => void;
  enabled?: boolean;
  initialLabels?: Array<
    | AnnotationLabel
    | {
        label_id?: number;
        label_key?: string;
        label_value?: string;
        annotation_kind?: string;
        color?: string;
      }
  >;
}) {
  const selectedIds = Array.from(
    new Set(
      (value || [])
        .map(Number)
        .filter((id) => Number.isFinite(id) && id > 0),
    ),
  );
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(ANNOTATION_LABEL_PAGE_SIZE_OPTIONS[0]);
  const [labelCache, setLabelCache] = useState<Record<number, AnnotationLabel>>({});
  const [leftChecked, setLeftChecked] = useState<number[]>([]);
  const [rightChecked, setRightChecked] = useState<number[]>([]);
  const initialRows = useMemo(
    () =>
      initialLabels
        .map((item) => normalizeAnnotationLabel(item))
        .filter((item): item is AnnotationLabel => Boolean(item)),
    [initialLabels],
  );
  const initialLabelMap = useMemo(
    () => new Map(initialRows.map((label) => [Number(label.id), label] as const)),
    [initialRows],
  );

  const labelsQuery = useQuery({
    queryKey: ['annotation-labels', 'active', page, pageSize, search],
    queryFn: async () => {
      const response = await fetchAnnotationLabels(page, pageSize, search);
      const rows = listData(response)
        .map((item) => normalizeAnnotationLabel(item))
        .filter((item): item is AnnotationLabel => Boolean(item));
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled,
    staleTime: 60_000,
    retry: false,
  });

  const currentRows = labelsQuery.data?.rows || [];
  const total = labelsQuery.data?.total ?? 0;

  useEffect(() => {
    if (!currentRows.length) return;
    setLabelCache((previous) => {
      const next = { ...previous };
      currentRows.forEach((label) => {
        next[label.id] = label;
      });
      return next;
    });
  }, [currentRows]);

  const selectedSet = new Set(selectedIds);
  const labelMap = new Map<number, AnnotationLabel>([
    ...Object.values(labelCache).map((label) => [Number(label.id), label] as const),
    ...currentRows.map((label) => [Number(label.id), label] as const),
  ]);
  const availableLabels = currentRows.filter((label) => !selectedSet.has(Number(label.id)));
  const boundLabels = selectedIds
    .map((id) => initialLabelMap.get(id) || labelMap.get(id))
    .map((label, index) => label || ({ id: selectedIds[index] } as AnnotationLabel));

  const availableIdsKey = availableLabels.map((label) => Number(label.id)).join(',');
  const selectedIdsKey = selectedIds.join(',');

  useEffect(() => {
    setLeftChecked((ids) => {
      const next = ids.filter((id) => availableLabels.some((label) => Number(label.id) === id));
      return next.length === ids.length ? ids : next;
    });
    setRightChecked((ids) => {
      const next = ids.filter((id) => selectedSet.has(id));
      return next.length === ids.length ? ids : next;
    });
  }, [availableIdsKey, selectedIdsKey]);

  const update = (next: number[]) => onChange?.(Array.from(new Set(next)));
  const toggle = (id: number, side: 'left' | 'right') => {
    const setter = side === 'left' ? setLeftChecked : setRightChecked;
    setter((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };
  const toggleAllAvailable = () => {
    const visibleIds = availableLabels.map((label) => Number(label.id));
    const allChecked = visibleIds.length > 0 && visibleIds.every((id) => leftChecked.includes(id));
    setLeftChecked(allChecked ? [] : visibleIds);
  };
  const moveToBound = () => {
    if (!leftChecked.length) return;
    update([...selectedIds, ...leftChecked]);
    setLeftChecked([]);
  };
  const moveToAvailable = () => {
    if (!rightChecked.length) return;
    update(selectedIds.filter((id) => !rightChecked.includes(id)));
    setRightChecked([]);
  };

  const applySearch = (nextSearch: string) => {
    const normalized = nextSearch.trim();
    setSearchDraft(nextSearch);
    setSearch(normalized);
    setPage(0);
  };

  const handlePageChange = (nextPage: number, nextPageSize?: number) => {
    const resolvedPageSize = nextPageSize || pageSize;
    setPageSize(resolvedPageSize);
    setPage(resolvedPageSize === pageSize ? Math.max(0, nextPage - 1) : 0);
  };

  const renderRow = (label: AnnotationLabel, side: 'left' | 'right', index?: number) => {
    const id = Number(label.id);
    const checked = side === 'left' ? leftChecked.includes(id) : rightChecked.includes(id);
    return (
      <div
        className={`tag-binding-row${checked ? ' is-selected' : ''}`}
        key={`${side}-${id}`}
        role="button"
        tabIndex={0}
        onClick={() => toggle(id, side)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggle(id, side);
          }
        }}
      >
        <Checkbox
          checked={checked}
          onClick={(event) => event.stopPropagation()}
          onChange={() => toggle(id, side)}
          aria-label={`${side === 'left' ? '选择未绑定标签' : '选择已绑定标签'} ${labelDisplayName(label)}`}
        />
        <span className="tag-binding-cell tag-binding-cell-primary">
          {side === 'right' && index !== undefined ? `${index + 1}. ` : ''}
          {label.label_key || label.label_value || `标签 #${id}`}
        </span>
        <span className="tag-binding-cell tag-binding-cell-secondary">
          {label.label_value || label.label_key || `label_${id}`}
        </span>
        <span className="tag-binding-cell tag-binding-cell-secondary">
          {annotationKindLabel(label.annotation_kind)}
        </span>
      </div>
    );
  };

  return (
    <div className="tag-binding-transfer" aria-label="标签绑定选择器">
      <section className="tag-binding-pane" aria-label="未绑定标签">
        <div className="tag-binding-pane-header">
          <strong>未绑定标签</strong>
          <span className="tag-binding-pane-count">
            已勾选：{leftChecked.length} · {search ? `匹配 ${total} 个` : `共 ${total} 个`}
          </span>
        </div>
        <ServerSearchInput
          value={searchDraft}
          onChange={(nextValue) => {
            setSearchDraft(nextValue);
            if (!nextValue.trim()) applySearch('');
          }}
          onSearch={applySearch}
          placeholder="按中文名称、标签编码或 ID 搜索"
          ariaLabel="搜索标签"
          loading={labelsQuery.isFetching}
          style={{ width: '100%', marginBottom: 8 }}
        />
        {labelsQuery.error && (
          <Alert
            type="error"
            showIcon
            message="标签库加载失败"
            description="请重试；当前已选标签不会被清除。"
            action={
              <Button size="small" onClick={() => void labelsQuery.refetch()}>
                重试
              </Button>
            }
            style={{ marginBottom: 8 }}
          />
        )}
        <div className="tag-binding-table-head">
          <Checkbox
            checked={
              availableLabels.length > 0 &&
              availableLabels.every((label) => leftChecked.includes(Number(label.id)))
            }
            indeterminate={
              leftChecked.length > 0 &&
              leftChecked.length < availableLabels.length
            }
            onChange={toggleAllAvailable}
            aria-label="全选未绑定标签"
          />
          <span>中文名称</span>
          <span>标签编码</span>
          <span>几何类型</span>
        </div>
        <div className="tag-binding-list">
          {labelsQuery.isLoading ? (
            <div className="tag-binding-empty" role="status" aria-live="polite">
              <Spin size="small" />
            </div>
          ) : availableLabels.length ? (
            availableLabels.map((label) => renderRow(label, 'left'))
          ) : (
            <div className="tag-binding-empty">
              {total ? '没有符合条件的未绑定标签' : '标签库暂无可用标签'}
            </div>
          )}
        </div>
        {!labelsQuery.isLoading && total > 0 && (
          <Pagination
            size="small"
            current={page + 1}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            pageSizeOptions={ANNOTATION_LABEL_PAGE_SIZE_OPTIONS.map(String)}
            hideOnSinglePage
            onChange={handlePageChange}
            style={{ marginTop: 10, textAlign: 'right' }}
          />
        )}
      </section>

      <div className="tag-binding-actions" aria-label="移动标签">
        <Button
          className="tag-binding-action"
          shape="circle"
          icon={<ArrowRightOutlined />}
          disabled={!leftChecked.length}
          onClick={moveToBound}
          aria-label="绑定选中标签"
        />
        <Button
          className="tag-binding-action"
          shape="circle"
          icon={<ArrowLeftOutlined />}
          disabled={!rightChecked.length}
          onClick={moveToAvailable}
          aria-label="移除选中标签"
        />
      </div>

      <section className="tag-binding-pane" aria-label="已绑定标签">
        <div className="tag-binding-pane-header">
          <strong>已绑定标签</strong>
          <span className="tag-binding-pane-count">已选：{rightChecked.length}</span>
        </div>
        <div className="tag-binding-table-head">
          <Checkbox
            checked={boundLabels.length > 0 && boundLabels.every((label) => rightChecked.includes(Number(label.id)))}
            indeterminate={rightChecked.length > 0 && rightChecked.length < boundLabels.length}
            onChange={() => {
              const ids = boundLabels.map((label) => Number(label.id));
              setRightChecked(ids.length > 0 && ids.every((id) => rightChecked.includes(id)) ? [] : ids);
            }}
            aria-label="全选已绑定标签"
          />
          <span>中文名称</span>
          <span>标签编码</span>
          <span>几何类型</span>
        </div>
        <div className="tag-binding-list">
          {boundLabels.length ? (
            boundLabels.map((label, index) => renderRow(label, 'right', index))
          ) : (
            <div className="tag-binding-empty">请从左侧选择要绑定的标签</div>
          )}
        </div>
      </section>
    </div>
  );
}

export function DatasetLabelContractModal({
  dataset,
  open,
  onClose,
  onSaved,
  onOpenPrompt,
}: {
  dataset: Row | null;
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
  onOpenPrompt?: (contract: DatasetLabelContract) => void;
}) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{ label_ids: number[] }>();
  const [submitting, setSubmitting] = useState(false);
  const selectedLabelIds = Form.useWatch('label_ids', form) || [];
  const contractQuery = useQuery({
    queryKey: ['dataset-label-contract', dataset?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/datasets/${dataset?.id}/label-contracts/current`);
      const payload = response.data;
      return (
        payload && Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload
      ) as DatasetLabelContract | null;
    },
    enabled: open && Boolean(dataset?.id),
    staleTime: 10_000,
    retry: false,
  });
  const contract = contractQuery.data;
  const bboxPromptSupported = isBboxOnlyContract(contract);

  useEffect(() => {
    if (!open) {
      form.resetFields();
      return;
    }
    if (!contractQuery.isLoading) {
      form.setFieldsValue({ label_ids: contract?.label_ids || [] });
    }
  }, [contract, contractQuery.isLoading, form, open]);

  const submit = async (values: { label_ids?: number[] }) => {
    if (!dataset?.id) return;
    const labelIds = (values.label_ids || []).map(Number).filter((value) => value > 0);
    if (!labelIds.length) {
      form.setFields([{ name: 'label_ids', errors: ['至少选择一个模型标签'] }]);
      return;
    }
    setSubmitting(true);
    try {
      await request.post(`/v1/datasets/${dataset.id}/label-contracts`, {
        label_ids: labelIds,
      });
      message.success(contract ? '数据集标签契约已更新为新版本' : '数据集标签契约已绑定');
      await queryClient.invalidateQueries({
        queryKey: ['dataset-label-contract', dataset.id],
      });
      await queryClient.invalidateQueries({ queryKey: ['datasets'] });
      onSaved?.();
      onClose();
    } catch (error: any) {
      message.error(
        error?.response?.data?.msg ||
          error?.response?.data?.message ||
          error?.response?.data?.detail ||
          '数据集标签契约保存失败，请检查标签是否有效',
      );
    } finally {
      setSubmitting(false);
    }
  };

  const contractStatus = String(contract?.status || '').toUpperCase();
  const contractStatusLabel =
    contractStatus === 'FROZEN'
      ? '已冻结'
      : contractStatus === 'SUPERSEDED'
      ? '已被新版本替代'
      : contractStatus === 'ACTIVE'
      ? '生效中'
      : contractStatus
      ? '草稿'
      : '尚未绑定';

  return (
    <Modal
      title={null}
      open={open}
      centered
      destroyOnClose
      width={980}
      closable={false}
      footer={null}
      aria-label={`数据集标签契约 · ${dataset?.name || `#${dataset?.id || ''}`}`}
      styles={{ content: { padding: 0 }, body: { padding: 0 } }}
      onCancel={() => {
        if (!submitting) onClose();
      }}
    >
      <div className="tag-binding-modal-header">
        <strong>绑定标签</strong>
        <Button
          type="text"
          className="tag-binding-modal-close"
          icon={<CloseOutlined />}
          onClick={() => {
            if (!submitting) onClose();
          }}
          aria-label="关闭"
        />
      </div>
      <GovernanceModalContent>
        <div className="tag-binding-intro">
          <div>
            <strong>{dataset?.name || `数据集 #${dataset?.id || ''}`}</strong>
            <span>　标签绑定在数据集；选择顺序就是训练类别顺序。</span>
          </div>
          <div className="tag-binding-status">
            当前契约：{contract ? `v${contract.revision} · ${contractStatusLabel}` : '尚未绑定'}
          </div>
        </div>
        {contractStatus === 'FROZEN' && (
          <Alert
            type="warning"
            showIcon
            message="当前契约已冻结"
            description="保存不会覆盖历史版本，而是创建新的契约修订；已经生成的训练版本仍保留原标签快照。"
            style={{ marginBottom: 16 }}
          />
        )}
        {contractQuery.isLoading ? (
          <div role="status" aria-live="polite" style={{ minHeight: 440, display: 'grid', placeItems: 'center' }}>
            <Spin />
          </div>
        ) : contractQuery.error ? (
          <Alert
            type="error"
            showIcon
            message="当前标签契约加载失败"
            description="请刷新后重试；不会影响已有数据集和训练版本。"
            action={
              <Button size="small" onClick={() => void contractQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : (
          <Form form={form} layout="vertical" onFinish={(values) => void submit(values)}>
            <Form.Item
              name="label_ids"
              label="数据集标签（按顺序）"
              rules={[{ required: true, type: 'array', min: 1, message: '至少选择一个模型标签' }]}
            >
              <TagBindingTransfer enabled={open} initialLabels={contract?.labels || []} />
            </Form.Item>
            <div className="tag-binding-footer">
              <span className="tag-binding-footer-hint">
                已选择 {selectedLabelIds.length} 个标签；训练时将继承此处的类别顺序。
              </span>
              <Space>
                {contract && onOpenPrompt && (
                  <Button
                    aria-label="AI提示词"
                    icon={<RobotOutlined />}
                    onClick={() => onOpenPrompt(contract)}
                    disabled={submitting || !bboxPromptSupported}
                    title={bboxPromptSupported ? '生成并发布当前标签契约的目标检测提示词' : '当前仅支持 bbox 标签契约'}
                  >
                    AI提示词
                  </Button>
                )}
                <Button onClick={onClose} disabled={submitting}>
                  取消
                </Button>
                <Button type="primary" loading={submitting} onClick={() => void form.submit()}>
                  确定
                </Button>
              </Space>
            </div>
          </Form>
        )}
      </GovernanceModalContent>
    </Modal>
  );
}

export function CreateVersionModal({
  dataset,
  open,
  onClose,
  onCreated,
}: {
  dataset: Row;
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<Record<string, any>>();
  const [submitting, setSubmitting] = useState(false);
  const submit = async (values: Record<string, any>) => {
    const train_ratio = Number(values.train_ratio);
    const val_ratio = Number(values.val_ratio);
    const test_ratio = Number(values.test_ratio);
    if (Math.abs(train_ratio + val_ratio + test_ratio - 1) > 0.000001) {
      message.error('训练、验证、测试比例之和必须为 1');
      return;
    }
    setSubmitting(true);
    try {
      await request.post(`/v1/datasets/${dataset.id}/versions/from-tasks`, {
        source_task_ids: (values.source_task_ids || []).map((value: number) => Number(value)),
        train_ratio,
        val_ratio,
        test_ratio,
      });
      message.success('训练数据版本已生成，等待校验');
      form.resetFields();
      onClose();
      onCreated();
    } catch (error: any) {
      if (error instanceof SyntaxError || error?.message === '元数据必须是 JSON 对象')
        form.setFields([{ name: 'metadata_json', errors: ['请输入合法的 JSON 对象'] }]);
      else
        message.error(
          error?.response?.status === 409
            ? '相同来源和样本已经生成过数据版本'
            : error?.response?.data?.msg ||
                error?.response?.data?.message ||
                error?.response?.data?.detail ||
                '生成训练数据版本失败，请检查来源任务和标注契约',
        );
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <Modal
      title={`为「${dataset.name || `数据集 #${dataset.id}`}」生成训练版本`}
      open={open}
      centered
      destroyOnClose
      width={620}
      confirmLoading={submitting}
      okText="生成训练版本"
      cancelText="取消"
      onCancel={() => {
        if (!submitting) onClose();
      }}
      onOk={() => void form.submit()}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ train_ratio: 0.8, val_ratio: 0.1, test_ratio: 0.1 }}
        onFinish={(values) => void submit(values)}
      >
        <Form.Item
          name="source_task_ids"
          label="来源标注任务"
          rules={[{ required: true, type: 'array', min: 1, message: '请选择至少一个已完成标注的任务' }]}
        >
            <SourceTaskSelector datasetId={Number(dataset?.id)} enabled={open} />
        </Form.Item>
        <Alert
          type="info"
          showIcon
          message="来源任务支持服务端搜索和分页"
          description="可按任务名称、任务 ID、来源或媒体类型筛选；跨页选择会保留。系统将自动生成清单、摘要、样本数和版本元数据，生成后还需完成校验、评审和冻结。"
          style={{ marginBottom: 16 }}
        />
        <Typography.Text strong>数据划分</Typography.Text>
        <Space style={{ display: 'flex', marginTop: 8 }} size={12} align="start">
          <Form.Item
            name="train_ratio"
            label="训练比例"
            style={{ flex: 1 }}
            rules={[{ required: true, type: 'number', min: 0.01, max: 0.98, message: '请输入 0.01 - 0.98' }]}
          >
            <InputNumber min={0.01} max={0.98} step={0.05} precision={2} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="val_ratio"
            label="验证比例"
            style={{ flex: 1 }}
            rules={[{ required: true, type: 'number', min: 0.01, max: 0.98, message: '请输入 0.01 - 0.98' }]}
          >
            <InputNumber min={0.01} max={0.98} step={0.05} precision={2} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="test_ratio"
            label="测试比例"
            style={{ flex: 1 }}
            rules={[{ required: true, type: 'number', min: 0.01, max: 0.98, message: '请输入 0.01 - 0.98' }]}
          >
            <InputNumber min={0.01} max={0.98} step={0.05} precision={2} style={{ width: '100%' }} />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  );
}

export function VersionGovernanceModal({
  dataset,
  open,
  onClose,
}: {
  dataset: Row | null;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<number>();
  const [versionSearchDraft, setVersionSearchDraft] = useState('');
  const [versionSearch, setVersionSearch] = useState('');
  const [versionPage, setVersionPage] = useState(0);
  const [versionPageSize, setVersionPageSize] = useState(20);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [reviewDecision, setReviewDecision] = useState<'APPROVE' | 'REJECT' | null>(null);
  const [reviewComment, setReviewComment] = useState('');
  const versionsQuery = useQuery({
    queryKey: ['dataset-versions', dataset?.id, versionPage, versionPageSize, versionSearch],
    queryFn: async () => {
      const response = await request.get(`/v1/datasets/${dataset?.id}/versions`, {
        params: {
          page: versionPage,
          size: versionPageSize,
          ...(versionSearch ? { q: versionSearch } : {}),
        },
      });
      const rows = listData(response) as DatasetVersion[];
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: open && Boolean(dataset?.id),
    staleTime: 10_000,
    retry: false,
  });
  const versions = versionsQuery.data?.rows || [];
  const versionTotal = versionsQuery.data?.total ?? 0;
  const selected = versions.find((version) => Number(version.id) === Number(selectedId)) || versions[0];
  useEffect(() => {
    if (selected && Number(selected.id) !== Number(selectedId)) setSelectedId(Number(selected.id));
    if (!open) {
      setSelectedId(undefined);
      setVersionSearchDraft('');
      setVersionSearch('');
      setVersionPage(0);
      setValidation(null);
      setReviewDecision(null);
      setReviewComment('');
    }
  }, [open, selected, selectedId]);
  const applyVersionSearch = (value: string) => {
    setVersionSearchDraft(value);
    setVersionSearch(value.trim());
    setVersionPage(0);
    setSelectedId(undefined);
  };
  const refresh = () => {
    setValidation(null);
    void queryClient.invalidateQueries({ queryKey: ['dataset-versions', dataset?.id] });
  };
  const statusMutation = useMutation({
    mutationFn: async (status: string) => request.patch(`/v1/dataset-versions/${selected?.id}`, { status }),
    onSuccess: () => {
      message.success('版本状态已推进');
      refresh();
    },
    onError: (error: any) =>
      message.error(error?.response?.status === 409 ? '版本状态已变化，请刷新后重试' : '版本状态推进失败'),
  });
  const validateMutation = useMutation({
    mutationFn: async () => {
      const response = await request.get(`/v1/dataset-versions/${selected?.id}/validate`);
      return (response.data?.data ?? response.data) as ValidationResult;
    },
    onSuccess: (result: ValidationResult) => {
      setValidation(result);
      message[result.valid ? 'success' : 'warning'](result.valid ? '版本校验通过' : '版本校验未通过');
    },
    onError: () => message.error('版本校验失败，请稍后重试'),
  });
  const freezeMutation = useMutation({
    mutationFn: async () => request.post(`/v1/dataset-versions/${selected?.id}/freeze`),
    onSuccess: () => {
      message.success('数据集版本已冻结');
      refresh();
    },
    onError: (error: any) =>
      message.error(
        error?.response?.status === 422
          ? '版本校验未通过，不能冻结'
          : error?.response?.status === 409
          ? '仅待评审版本可以冻结'
          : '冻结失败，请稍后重试',
      ),
  });
  const reviewMutation = useMutation({
    mutationFn: async () => {
      const response = await request.post(`/v1/dataset-versions/${selected?.id}/review`, {
        decision: reviewDecision,
        comment: reviewComment.trim() || undefined,
      });
      return (response.data?.data ?? response.data) as DatasetVersion;
    },
    onSuccess: (_result) => {
      message.success(reviewDecision === 'APPROVE' ? '版本评审已通过' : '版本已驳回并退回校验');
      setReviewDecision(null);
      setReviewComment('');
      refresh();
    },
    onError: (error: any) => {
      message.error(
        error?.response?.status === 422
          ? '技术校验未通过，不能通过评审'
          : error?.response?.status === 409
          ? '版本状态已变化，请刷新后重试'
          : error?.response?.data?.msg || '版本评审失败',
      );
    },
  });
  const selectedStatus = String(selected?.status || '').toUpperCase();
  const selectedReviewStatus = String(selected?.review_status || 'PENDING').toUpperCase();
  const canFreeze =
    selectedStatus === 'READY_TO_REVIEW' &&
    selectedReviewStatus === 'APPROVED' &&
    validation?.version_id === selected?.id &&
    validation.valid;
  const canReview = selectedStatus === 'READY_TO_REVIEW' && selectedReviewStatus !== 'APPROVED';
  const canAdvanceStatus =
    selectedStatus === 'INGESTING' ||
    (selectedStatus === 'VALIDATING' && validation?.version_id === selected?.id && validation.valid);
  const lifecycleStep = { INGESTING: 0, VALIDATING: 1, READY_TO_REVIEW: 2, FROZEN: 3, RETIRED: 3 }[selectedStatus] ?? 0;
  const statusActionLabel =
    NEXT_STATUS[selectedStatus] === 'VALIDATING'
      ? '进入校验'
      : NEXT_STATUS[selectedStatus] === 'READY_TO_REVIEW'
      ? '提交评审'
      : undefined;
  const actionHint =
    selectedStatus === 'INGESTING'
      ? '版本已生成，先推进到校验阶段。'
      : selectedStatus === 'VALIDATING'
      ? validation?.version_id === selected?.id && validation.valid
        ? '校验已通过，可以提交评审。'
        : '请先执行版本校验，校验通过后才能提交评审。'
      : selectedStatus === 'READY_TO_REVIEW'
      ? selectedReviewStatus === 'APPROVED'
        ? canFreeze
          ? '版本已通过技术校验和人工评审，可以冻结并供训练使用。'
          : '评审已通过；请先执行并通过版本校验后再冻结。'
        : '版本已提交评审，请由数据集责任人或管理人员确认清单、样本范围和标签契约。'
      : selectedStatus === 'FROZEN'
      ? '版本已冻结，可作为训练输入，内容不可再修改。'
      : '当前版本已退役，不再参与后续训练。';
  return (
    <>
      <Modal
        title={`数据集版本治理${dataset ? ` · ${dataset.name || `#${dataset.id}`}` : ''}`}
        open={open}
        onCancel={onClose}
        width="min(1120px, calc(100vw - 24px))"
        destroyOnClose
        footer={null}
        centered
        styles={{ body: { padding: 0 } }}
      >
        <GovernanceModalContent>
          <div className="governance-summary">
            <div className="governance-summary-copy">
              <span className="governance-summary-title">可追溯版本工作区</span>
              <p>版本需经过接入、校验、评审后才能冻结。冻结版本可供训练使用，清单和标签契约均不可再修改。</p>
            </div>
            <div className="governance-summary-actions">
              <Space wrap>
                <Button icon={<ReloadOutlined />} loading={versionsQuery.isFetching} onClick={refresh}>
                  刷新
                </Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                  生成训练版本
                </Button>
              </Space>
            </div>
          </div>
          {versionsQuery.isLoading ? (
            <div role="status" aria-live="polite" style={{ minHeight: 360, display: 'grid', placeItems: 'center' }}>
              <Spin />
            </div>
          ) : versionsQuery.error ? (
            <Alert
              type="error"
              showIcon
              message="版本列表加载失败"
              description="请检查数据集访问权限或稍后重试。"
              action={
                <Button size="small" icon={<ReloadOutlined />} onClick={() => void versionsQuery.refetch()}>
                  重试
                </Button>
              }
            />
          ) : versions.length === 0 ? (
            <div className="governance-panel" style={{ padding: 36 }}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前数据集暂无版本">
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                  生成第一个训练版本
                </Button>
              </Empty>
            </div>
          ) : (
            <div className="governance-workspace">
              <section className="governance-panel governance-list-panel" aria-label="版本列表">
                <div className="governance-panel-heading">
                  <strong>版本列表</strong>
                  <Tag>{versionTotal} 个版本</Tag>
                </div>
                <Space direction="vertical" size={8} style={{ display: 'flex', marginBottom: 12 }}>
                  <ServerSearchInput
                    value={versionSearchDraft}
                    onChange={(value) => {
                      setVersionSearchDraft(value);
                      if (!value.trim()) applyVersionSearch('');
                    }}
                    onSearch={applyVersionSearch}
                    placeholder="按版本号、状态、清单或摘要搜索"
                    ariaLabel="搜索数据集版本"
                    loading={versionsQuery.isFetching}
                  />
                  <Pagination
                    size="small"
                    current={versionPage + 1}
                    pageSize={versionPageSize}
                    total={versionTotal}
                    showSizeChanger
                    pageSizeOptions={['20', '50', '100']}
                    hideOnSinglePage
                    onChange={(nextPage, nextPageSize) => {
                      const resolvedSize = nextPageSize || versionPageSize;
                      setVersionPageSize(resolvedSize);
                      setVersionPage(resolvedSize === versionPageSize ? Math.max(0, nextPage - 1) : 0);
                      setSelectedId(undefined);
                      setValidation(null);
                    }}
                  />
                </Space>
                <div className="governance-list">
                  {versions.map((version) => (
                    <button
                      key={version.id}
                      type="button"
                      className="version-option"
                      data-selected={Number(version.id) === Number(selected?.id)}
                      aria-pressed={Number(version.id) === Number(selected?.id)}
                      onClick={() => {
                        setSelectedId(Number(version.id));
                        setValidation(null);
                      }}
                    >
                      <div className="version-option-title">
                        <span>v{version.version_no}</span>
                        <VersionStatus value={version.status} reviewStatus={version.review_status} />
                      </div>
                      <div className="version-option-meta">
                        <span>{version.sample_count ?? 0} 个样本</span>
                        <span>{datasetQualityStatus(version.quality_status)}</span>
                      </div>
                      <span className="version-option-path">
                        #{version.id} · {version.manifest_rel_path || '暂无清单路径'}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              {selected && (
                <section className="governance-panel governance-detail-panel" aria-label="版本详情">
                  <div className="governance-detail-header">
                    <div>
                      <h3>v{selected.version_no} 版本控制</h3>
                      <p>
                        版本 ID #{selected.id} · 创建于{' '}
                        {selected.created_at ? new Date(selected.created_at).toLocaleString() : '暂无记录'}
                      </p>
                    </div>
                    <VersionStatus value={selected.status} reviewStatus={selected.review_status} />
                  </div>

                  <div className="governance-steps">
                    <Steps
                      size="small"
                      current={lifecycleStep}
                      responsive
                      items={[{ title: '接入' }, { title: '校验' }, { title: '评审' }, { title: '冻结' }]}
                    />
                  </div>

                  <div className="governance-metrics">
                    <div className="governance-metric">
                      <span className="governance-metric-label">样本总数</span>
                      <span className="governance-metric-value">{selected.sample_count ?? 0}</span>
                      <span className="governance-metric-note">纳入当前版本的样本</span>
                    </div>
                    <div className="governance-metric">
                      <span className="governance-metric-label">质量状态</span>
                      <span className="governance-metric-value">{datasetQualityStatus(selected.quality_status)}</span>
                      <span className="governance-metric-note">以后端质量门禁结果为准</span>
                    </div>
                    <div className="governance-metric">
                      <span className="governance-metric-label">清单摘要</span>
                      <span className="governance-metric-value is-code">
                        {selected.manifest_sha256?.slice(0, 12) || '-'}
                      </span>
                      <span className="governance-metric-note">SHA256 前 12 位</span>
                    </div>
                  </div>

                  <div className="governance-section">
                    <div className="governance-section-title">版本证据</div>
                    <div className="governance-evidence">
                      <Descriptions size="small" column={1}>
                        <Descriptions.Item label="清单路径">
                          <Typography.Text className="governance-code" title={selected.manifest_rel_path}>
                            {selected.manifest_rel_path || '-'}
                          </Typography.Text>
                        </Descriptions.Item>
                        <Descriptions.Item label="SHA256">
                          <Typography.Text
                            className="governance-code"
                            copyable
                            ellipsis={{ tooltip: selected.manifest_sha256 }}
                          >
                            {selected.manifest_sha256 || '-'}
                          </Typography.Text>
                        </Descriptions.Item>
                        <Descriptions.Item label="标签契约摘要">
                          <Typography.Text className="governance-code" title={selected.label_set_signature}>
                            {selected.label_set_signature || '-'}
                          </Typography.Text>
                        </Descriptions.Item>
                        <Descriptions.Item label="类别顺序摘要">
                          <Typography.Text className="governance-code" title={selected.class_order_signature}>
                            {selected.class_order_signature || '-'}
                          </Typography.Text>
                        </Descriptions.Item>
                      </Descriptions>
                    </div>
                  </div>

                  {selected.review_status && selected.review_status !== 'PENDING' && (
                    <div className="governance-section">
                      <div className="governance-section-title">
                        <span>人工评审结果</span>
                        <Tag color={selected.review_status === 'APPROVED' ? 'success' : 'error'}>
                          {REVIEW_STATUS_LABELS[selected.review_status] || selected.review_status}
                        </Tag>
                      </div>
                      <Descriptions size="small" column={1}>
                        <Descriptions.Item label="评审人">用户 #{selected.reviewed_by ?? '-'}</Descriptions.Item>
                        <Descriptions.Item label="评审时间">
                          {selected.reviewed_at ? new Date(selected.reviewed_at).toLocaleString() : '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="评审意见">{selected.review_comment || '-'}</Descriptions.Item>
                      </Descriptions>
                    </div>
                  )}

                  {validation && validation.version_id === selected.id && (
                    <div className={`governance-section governance-validation${validation.valid ? '' : ' is-invalid'}`}>
                      <div className="governance-section-title">
                        <span>{validation.valid ? '校验通过，可冻结' : '校验未通过，请修复清单后重试'}</span>
                        <Tag color={validation.valid ? 'success' : 'error'}>{validation.valid ? '通过' : '失败'}</Tag>
                      </div>
                      {Object.entries(validation.checks).map(([key, value]) => (
                        <div className="governance-validation-check" key={key}>
                          {value ? (
                            <CheckCircleOutlined style={{ color: '#389e0d' }} />
                          ) : (
                            <CloseCircleOutlined style={{ color: '#cf1322' }} />
                          )}
                          <span>
                            {key} · {value ? '通过' : '未通过'}
                          </span>
                        </div>
                      ))}
                      {validation.warnings?.map((warning) => (
                        <div className="governance-validation-check" key={warning}>
                          <span>提示：{warning}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="governance-action-bar">
                    <span className="governance-action-hint">{actionHint}</span>
                    <Space wrap>
                      {statusActionLabel && (
                        <Button
                          type={selectedStatus === 'INGESTING' ? 'primary' : 'default'}
                          icon={<SettingOutlined />}
                          loading={statusMutation.isPending}
                          disabled={!canAdvanceStatus}
                          onClick={() => statusMutation.mutate(NEXT_STATUS[selectedStatus] as string)}
                        >
                          {statusActionLabel}
                        </Button>
                      )}
                      <Button
                        icon={<CheckCircleOutlined />}
                        loading={validateMutation.isPending}
                        onClick={() => validateMutation.mutate()}
                      >
                        执行校验
                      </Button>
                      {canReview && (
                        <>
                          <Button
                            type="primary"
                            loading={reviewMutation.isPending && reviewDecision === 'APPROVE'}
                            onClick={() => {
                              setReviewComment('');
                              setReviewDecision('APPROVE');
                            }}
                          >
                            通过评审
                          </Button>
                          <Button
                            danger
                            loading={reviewMutation.isPending && reviewDecision === 'REJECT'}
                            onClick={() => {
                              setReviewComment('');
                              setReviewDecision('REJECT');
                            }}
                          >
                            驳回评审
                          </Button>
                        </>
                      )}
                      {selectedStatus === 'READY_TO_REVIEW' && (
                        <Popconfirm
                          title="冻结后版本不可修改，确认冻结？"
                          okText="确认冻结"
                          cancelText="取消"
                          onConfirm={() => {
                            if (!canFreeze) {
                              message.warning('请先执行并通过版本校验');
                              return;
                            }
                            freezeMutation.mutate();
                          }}
                        >
                          <Button
                            type="primary"
                            icon={<LockOutlined />}
                            loading={freezeMutation.isPending}
                            disabled={!canFreeze}
                          >
                            冻结版本
                          </Button>
                        </Popconfirm>
                      )}
                    </Space>
                  </div>
                </section>
              )}
            </div>
          )}
        </GovernanceModalContent>
      </Modal>
      <Modal
        title={reviewDecision === 'APPROVE' ? '通过数据集版本评审' : '驳回数据集版本评审'}
        open={Boolean(reviewDecision)}
        centered
        confirmLoading={reviewMutation.isPending}
        okText={reviewDecision === 'APPROVE' ? '确认通过' : '确认驳回'}
        cancelText="取消"
        onCancel={() => {
          if (!reviewMutation.isPending) {
            setReviewDecision(null);
            setReviewComment('');
          }
        }}
        onOk={() => {
          if (reviewDecision === 'REJECT' && !reviewComment.trim()) {
            message.warning('驳回评审必须填写原因');
            return;
          }
          reviewMutation.mutate();
        }}
      >
        <Alert
          type={reviewDecision === 'APPROVE' ? 'info' : 'warning'}
          showIcon
          message={reviewDecision === 'APPROVE' ? '确认清单、样本数和标签契约后再通过' : '驳回后版本会退回校验阶段'}
          style={{ marginBottom: 12 }}
        />
        <Input.TextArea
          rows={4}
          maxLength={2000}
          showCount
          value={reviewComment}
          onChange={(event) => setReviewComment(event.target.value)}
          placeholder={reviewDecision === 'REJECT' ? '请输入驳回原因（必填）' : '请输入评审意见（可选）'}
        />
      </Modal>
      {dataset && (
        <CreateVersionModal
          dataset={dataset}
          open={createOpen}
          onClose={() => setCreateOpen(false)}
          onCreated={refresh}
        />
      )}
    </>
  );
}

export default function DatasetsPage({ embedded = false }: { embedded?: boolean }) {
  const [selectedDataset, setSelectedDataset] = useState<Row | null>(null);
  const [labelContractDataset, setLabelContractDataset] = useState<Row | null>(null);
  const [promptDataset, setPromptDataset] = useState<Row | null>(null);
  const [promptContract, setPromptContract] = useState<DatasetLabelContract | null>(null);
  const [datasetSearch, setDatasetSearch] = useState('');
  return (
    <>
      <ResourcePage
        module="datasets"
        queryKey="datasets"
        queryFn={async (_scopeId, page = 0, size = 20, search) => await fetchDatasets(page, size, search)}
        needsProject={false}
        pagination
        pageSize={20}
        search={datasetSearch}
        onSearchChange={setDatasetSearch}
        searchPlaceholder="按中文名称、英文描述或标签搜索"
        searchAriaLabel="搜索数据集"
        beforeTable={<CreateDatasetAction />}
        detailPath={(row) => (row.id ? `/v1/datasets/${row.id}` : null)}
        actionColumnWidth={240}
        detailColumnWidth={88}
        hideModuleHeader={embedded}
        hideModuleStatus={embedded}
        titleOverride={embedded ? '数据集管理' : undefined}
        nextOverride={embedded ? '维护数据集、标签契约和版本治理；普通任务仍可从任务与标注直接训练。' : undefined}
        rowActions={(row) => (
          <Space size={4} wrap>
            <Button
              type="link"
              size="small"
              icon={<TagsOutlined />}
              title="配置该数据集的标签契约"
              aria-label="标签契约"
              data-testid={`dataset-label-contract-${row.id}`}
              onClick={() => setLabelContractDataset(row)}
            >
              标签契约
            </Button>
            <Button
              type="link"
              size="small"
              icon={<SettingOutlined />}
              title="管理该数据集的版本治理"
              aria-label="版本治理"
              onClick={() => setSelectedDataset(row)}
            >
              版本治理
            </Button>
          </Space>
        )}
        columns={[
          { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
          {
            title: '绑定标签',
            dataIndex: 'dataset_labels',
            key: 'dataset_labels',
            render: (value, row) => {
              const labels = Array.isArray(value) ? value : [];
              if (!labels.length) return <Tag color="orange">未绑定</Tag>;
              const names = labels.map((label: any) => labelDisplayName(label)).filter(Boolean);
              const contractStatus = String(row.dataset_label_contract_status || '').toUpperCase();
              const statusColor =
                contractStatus === 'FROZEN' ? 'green' : contractStatus === 'SUPERSEDED' ? 'orange' : 'blue';
              return (
                <Space size={[4, 4]} wrap>
                  <Typography.Text ellipsis={{ tooltip: names.join('、') }} style={{ maxWidth: 260 }}>
                    {names.join('、')}
                  </Typography.Text>
                  <Tag color={statusColor}>v{row.dataset_label_contract_revision ?? 1}</Tag>
                </Space>
              );
            },
          },
          { title: '状态', dataIndex: 'status', key: 'status', render: (value) => <Tag>{value}</Tag> },
          { title: '版本数', dataIndex: 'version_count', key: 'version_count', render: (value) => value ?? 0 },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            key: 'updated_at',
            render: (value) => (value ? new Date(value).toLocaleString() : '-'),
          },
        ]}
      />
      <VersionGovernanceModal
        dataset={selectedDataset}
        open={Boolean(selectedDataset)}
        onClose={() => setSelectedDataset(null)}
      />
      <DatasetLabelContractModal
        dataset={labelContractDataset}
        open={Boolean(labelContractDataset)}
        onClose={() => setLabelContractDataset(null)}
        onOpenPrompt={(contract) => {
          if (!labelContractDataset) return;
          setPromptDataset(labelContractDataset);
          setPromptContract(contract);
          setLabelContractDataset(null);
        }}
      />
      <AnnotationPromptModal
        dataset={promptDataset}
        contract={promptContract}
        open={Boolean(promptDataset && promptContract)}
        onClose={() => {
          setPromptDataset(null);
          setPromptContract(null);
        }}
      />
    </>
  );
}
