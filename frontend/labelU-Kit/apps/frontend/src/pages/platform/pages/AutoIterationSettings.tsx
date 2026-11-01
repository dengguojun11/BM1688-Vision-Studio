import {
  HistoryOutlined,
  ReloadOutlined,
  SaveOutlined,
  SettingOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Collapse,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import request from '@/api/request';

import { TablePanel, listData, listTotal, responseStatus } from '../shared';

/**
 * 自动迭代策略的可视化配置面板。
 *
 * 数据来自 `GET /v1/auto-iteration-settings`：页面展示判定口径、达标/预警阈值、
 * 停止条件与高级守护项，保存时只提交发生变化的值并附带修改原因，写入系统配置
 * 治理存储后会立即失效运行时缓存，因此「下一轮判定」马上使用新口径。
 */

const SETTINGS_PATH = '/v1/auto-iteration-settings';
const HISTORY_PAGE_SIZE = 20;

export type AutoIterationValues = {
  enabled: boolean;
  metric_basis: string;
  pass_threshold: number;
  warn_threshold: number;
  max_rounds: number;
  no_improvement_rounds: number;
  min_improvement_delta: number;
  allow_conditional_pass: boolean;
  require_quality_for_compile: boolean;
  min_class_recall_enabled: boolean;
  min_class_recall: number;
  precision_floor_enabled: boolean;
  precision_floor: number;
  max_regression: number;
  eval_conf: number;
};

export type AutoIterationMetricBasisOption = { value: string; label: string };

export type AutoIterationSettingsPayload = {
  values: AutoIterationValues;
  defaults: AutoIterationValues;
  sources: Record<string, string>;
  locked_keys: string[];
  can_edit: boolean;
  max_iterations_after_first: number;
  metric_basis_options: AutoIterationMetricBasisOption[];
  threshold_hint: string;
  max_rounds_hint: string;
};

export type AutoIterationHistoryRow = {
  id: number | string;
  item_key: string;
  before?: unknown;
  after?: unknown;
  change_type?: string | null;
  change_reason?: string | null;
  changed_by?: number | string | null;
  changed_at?: string | null;
};

/** 后端 `auto_iteration` 组默认值的前端兜底；服务端返回缺字段时保持一致。 */
const FALLBACK_VALUES: AutoIterationValues = {
  enabled: false,
  metric_basis: 'val_recall',
  pass_threshold: 0.6,
  warn_threshold: 0.5,
  max_rounds: 4,
  no_improvement_rounds: 2,
  min_improvement_delta: 0.02,
  allow_conditional_pass: true,
  require_quality_for_compile: true,
  min_class_recall_enabled: false,
  min_class_recall: 0.3,
  precision_floor_enabled: false,
  precision_floor: 0.7,
  max_regression: 0.05,
  eval_conf: 0.25,
};

const DEFAULT_METRIC_BASIS_OPTIONS: AutoIterationMetricBasisOption[] = [
  { value: 'val_recall', label: '验证集召回率' },
  { value: 'overall_recall', label: '整体加权召回率' },
  { value: 'map50_95', label: 'mAP50-95（历史口径）' },
];

const BOOLEAN_KEYS = [
  'enabled',
  'allow_conditional_pass',
  'require_quality_for_compile',
  'min_class_recall_enabled',
  'precision_floor_enabled',
] as const;

const INTEGER_KEYS = ['max_rounds', 'no_improvement_rounds'] as const;

const PERCENT_KEYS = [
  'pass_threshold',
  'warn_threshold',
  'min_improvement_delta',
  'min_class_recall',
  'precision_floor',
  'max_regression',
  'eval_conf',
] as const;

const ALL_KEYS = Object.keys(FALLBACK_VALUES) as (keyof AutoIterationValues)[];

const BOOLEAN_KEY_SET = new Set<string>(BOOLEAN_KEYS);
const INTEGER_KEY_SET = new Set<string>(INTEGER_KEYS);
const PERCENT_KEY_SET = new Set<string>(PERCENT_KEYS);

export const AUTO_ITERATION_FIELD_LABELS: Record<string, string> = {
  enabled: '自动迭代开关',
  metric_basis: '判定口径',
  pass_threshold: '达标阈值',
  warn_threshold: '预警阈值',
  max_rounds: '最大迭代次数',
  no_improvement_rounds: '无提升停止轮数',
  min_improvement_delta: '最小提升幅度',
  allow_conditional_pass: '条件达标算通过',
  require_quality_for_compile: '达标才编译',
  min_class_recall_enabled: '单类最低召回开关',
  min_class_recall: '单类最低召回',
  precision_floor_enabled: 'precision 下限开关',
  precision_floor: 'precision 下限',
  max_regression: '相对上一轮回退上限',
  eval_conf: '评估置信度工作点',
};

const SOURCE_LABELS: Record<string, string> = {
  db: '页面配置',
  env: '环境变量',
  default: '系统默认',
  runtime: '运行时覆盖',
};

function isRecord(value: unknown): value is Record<string, any> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function formatAutoIterationPercent(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const percent = Number((value * 100).toFixed(2));
  return `${percent}%`;
}

function coerceValues(raw: unknown): AutoIterationValues | null {
  if (!isRecord(raw)) return null;
  const resolved: Record<string, any> = { ...FALLBACK_VALUES };
  for (const key of ALL_KEYS) {
    const candidate = raw[key];
    if (candidate === undefined || candidate === null) continue;
    if (BOOLEAN_KEY_SET.has(key)) {
      if (typeof candidate === 'boolean') resolved[key] = candidate;
      else if (typeof candidate === 'number') resolved[key] = candidate !== 0;
      else if (typeof candidate === 'string') resolved[key] = ['1', 'true', 'yes', 'on'].includes(candidate.trim().toLowerCase());
    } else if (INTEGER_KEY_SET.has(key)) {
      const parsed = Number(candidate);
      if (Number.isFinite(parsed)) resolved[key] = Math.trunc(parsed);
    } else if (key === 'metric_basis') {
      if (typeof candidate === 'string' && candidate.trim()) resolved[key] = candidate.trim();
    } else {
      const parsed = Number(candidate);
      if (Number.isFinite(parsed)) resolved[key] = parsed;
    }
  }
  return resolved as AutoIterationValues;
}

/**
 * 兼容两种响应形态：运行时拦截器已剥掉 axios 外层（`{ data: payload }`），
 * 而页面测试/旧调用方仍可能包一层（`{ data: { data: payload } }`）。
 */
export function normalizeAutoIterationSettingsPayload(payload: unknown): AutoIterationSettingsPayload | null {
  let candidate: any = payload;
  for (let depth = 0; depth < 4; depth += 1) {
    if (!isRecord(candidate)) return null;
    if (isRecord(candidate.values)) break;
    candidate = candidate.data;
  }
  if (!isRecord(candidate) || !isRecord(candidate.values)) return null;
  const values = coerceValues(candidate.values);
  if (!values) return null;
  const defaults = coerceValues(candidate.defaults) ?? { ...FALLBACK_VALUES };

  const rawOptions = Array.isArray(candidate.metric_basis_options) ? candidate.metric_basis_options : [];
  const metricBasisOptions = rawOptions
    .filter((item: unknown) => isRecord(item) && typeof item.value === 'string' && String(item.value).trim())
    .map((item: Record<string, any>) => ({
      value: String(item.value),
      label: String(item.label || item.value),
    }));

  const maxIterations = Number(candidate.max_iterations_after_first);
  return {
    values,
    defaults,
    sources: isRecord(candidate.sources) ? { ...candidate.sources } : {},
    locked_keys: Array.isArray(candidate.locked_keys)
      ? candidate.locked_keys.filter((key: unknown): key is string => typeof key === 'string')
      : [],
    can_edit: candidate.can_edit === true,
    max_iterations_after_first: Number.isFinite(maxIterations)
      ? Math.max(0, maxIterations)
      : Math.max(0, values.max_rounds - 1),
    metric_basis_options: metricBasisOptions.length > 0 ? metricBasisOptions : DEFAULT_METRIC_BASIS_OPTIONS,
    threshold_hint: typeof candidate.threshold_hint === 'string' && candidate.threshold_hint
      ? candidate.threshold_hint
      : '达到该阈值即判定通过：停止迭代并允许进入编译',
    max_rounds_hint: typeof candidate.max_rounds_hint === 'string' && candidate.max_rounds_hint
      ? candidate.max_rounds_hint
      : '该值包含首轮：4 = 首轮 + 3 次自动迭代',
  };
}

/** 只挑出与已保存快照不同的配置项，页面不会覆盖别人未提交的字段。 */
export function diffAutoIterationValues(
  current: Partial<AutoIterationValues>,
  baseline: Partial<AutoIterationValues>,
): Partial<AutoIterationValues> {
  const changes: Record<string, unknown> = {};
  for (const key of ALL_KEYS) {
    const next = current[key];
    const previous = baseline[key];
    if (next === undefined || next === null) continue;
    if (typeof next === 'number' && typeof previous === 'number') {
      if (Math.abs(next - previous) > 1e-9) changes[key] = next;
    } else if (next !== previous) {
      changes[key] = next;
    }
  }
  return changes as Partial<AutoIterationValues>;
}

/** PATCH 请求体：修改项 + 必填原因 + 修改前看到的值（服务端做并发 409 校验）。 */
export function buildAutoIterationSaveBody(
  changes: Partial<AutoIterationValues>,
  baseline: Partial<AutoIterationValues> | null,
  reason: string,
): Record<string, unknown> {
  const body: Record<string, unknown> = { ...changes, reason: reason.trim() };
  const expected: Record<string, unknown> = {};
  for (const key of Object.keys(changes)) {
    const previous = baseline ? baseline[key as keyof AutoIterationValues] : undefined;
    if (previous !== undefined && previous !== null) expected[key] = previous;
  }
  if (Object.keys(expected).length > 0) body.expected_values = expected;
  return body;
}

export function formatAutoIterationValue(
  key: string,
  value: unknown,
  options: AutoIterationMetricBasisOption[],
): string {
  if (value === null || value === undefined || value === '') return '—';
  if (BOOLEAN_KEY_SET.has(key)) {
    if (typeof value === 'boolean') return value ? '开启' : '关闭';
    return String(value) === 'true' || String(value) === '1' ? '开启' : '关闭';
  }
  if (PERCENT_KEY_SET.has(key)) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? formatAutoIterationPercent(parsed) : String(value);
  }
  if (key === 'metric_basis') {
    return options.find((option) => option.value === value)?.label ?? String(value);
  }
  return String(value);
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

function loadErrorText(error: unknown): string {
  const status = responseStatus(error);
  if (status === 403) return '当前账号无权查看自动迭代设置';
  return '自动迭代设置加载失败，请稍后重试';
}

function saveErrorText(error: unknown): string {
  const status = responseStatus(error);
  const serverMessage = (error as { response?: { data?: { msg?: unknown } } } | null)?.response?.data?.msg;
  const serverText = typeof serverMessage === 'string' ? serverMessage.trim() : '';
  if (status === 403) return '仅管理员可修改自动迭代设置';
  if (status === 409) return serverText || '配置已被其他管理员修改，请刷新后重试';
  if (serverText) return serverText;
  return '自动迭代设置保存失败，请稍后重试';
}

type PercentFieldProps = {
  value?: number | null;
  onChange?: (value: number | null) => void;
  min: number;
  max: number;
  ariaLabel: string;
  disabled?: boolean;
  id?: string;
};

/** 阈值型字段：数字输入 + 滑杆同源，右侧持续显示百分比。 */
function PercentField({ value, onChange, min, max, ariaLabel, disabled, id }: PercentFieldProps) {
  const numeric = typeof value === 'number' && Number.isFinite(value) ? value : undefined;
  const handleNumberChange = (next: string | number | null) => {
    if (typeof next === 'number' && Number.isFinite(next)) {
      onChange?.(next);
      return;
    }
    onChange?.(null);
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
      <InputNumber
        id={id}
        aria-label={ariaLabel}
        min={min}
        max={max}
        step={0.01}
        precision={2}
        value={numeric}
        disabled={disabled}
        onChange={handleNumberChange}
        style={{ width: 110, flex: '0 0 auto' }}
      />
      <Slider
        min={min}
        max={max}
        step={0.01}
        value={numeric ?? min}
        disabled={disabled}
        onChange={(next) => onChange?.(typeof next === 'number' ? next : null)}
        tooltip={{ formatter: (next) => formatAutoIterationPercent(typeof next === 'number' ? next : undefined) }}
        style={{ flex: 1, minWidth: 120, margin: 0 }}
      />
      <Typography.Text type="secondary" style={{ flex: '0 0 auto', fontSize: 12, minWidth: 48, textAlign: 'right' }}>
        {formatAutoIterationPercent(numeric)}
      </Typography.Text>
    </div>
  );
}

export default function AutoIterationSettings() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<AutoIterationValues>();
  const [reasonForm] = Form.useForm<{ reason: string }>();
  const [savedValues, setSavedValues] = useState<AutoIterationValues | null>(null);
  const [pendingChanges, setPendingChanges] = useState<Partial<AutoIterationValues> | null>(null);
  const [reasonOpen, setReasonOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyPage, setHistoryPage] = useState(0);

  const settingsQuery = useQuery<AutoIterationSettingsPayload>({
    queryKey: ['auto-iteration-settings'],
    queryFn: async () => {
      const payload = await request.get(SETTINGS_PATH);
      const normalized = normalizeAutoIterationSettingsPayload(payload);
      if (!normalized) throw new Error('自动迭代设置响应格式无效');
      return normalized;
    },
    staleTime: 10_000,
  });
  const settings = settingsQuery.data;
  const canEdit = Boolean(settings?.can_edit);
  const lockedKeys = useMemo(() => new Set(settings?.locked_keys ?? []), [settings]);
  const metricOptions = settings?.metric_basis_options?.length
    ? settings.metric_basis_options
    : DEFAULT_METRIC_BASIS_OPTIONS;

  const maxRounds = Form.useWatch('max_rounds', form) ?? savedValues?.max_rounds ?? settings?.values.max_rounds;
  const minClassRecallEnabled = Form.useWatch('min_class_recall_enabled', form) ?? false;
  const precisionFloorEnabled = Form.useWatch('precision_floor_enabled', form) ?? false;

  useEffect(() => {
    if (!settings) return;
    form.setFieldsValue(settings.values);
    setSavedValues(settings.values);
  }, [form, settings]);

  const historyQuery = useQuery<{ rows: AutoIterationHistoryRow[]; total: number }>({
    queryKey: ['auto-iteration-settings-history', historyPage],
    queryFn: async () => {
      const payload = await request.get(`${SETTINGS_PATH}/history`, {
        params: { page: historyPage, size: HISTORY_PAGE_SIZE },
      });
      const rows = listData(payload) as AutoIterationHistoryRow[];
      return { rows, total: listTotal(payload, rows.length) };
    },
    enabled: historyOpen,
    staleTime: 5_000,
  });

  const saveMutation = useMutation({
    mutationFn: async ({ changes, reason }: { changes: Partial<AutoIterationValues>; reason: string }) => {
      const body = buildAutoIterationSaveBody(changes, savedValues, reason);
      const payload = await request.patch(SETTINGS_PATH, body);
      const normalized = normalizeAutoIterationSettingsPayload(payload);
      if (!normalized) throw new Error('自动迭代设置响应格式无效');
      return normalized;
    },
    onSuccess: (next) => {
      queryClient.setQueryData(['auto-iteration-settings'], next);
      form.setFieldsValue(next.values);
      setSavedValues(next.values);
      setReasonOpen(false);
      setPendingChanges(null);
      setSaveError(null);
      reasonForm.resetFields();
      message.success('已保存，下一轮判定生效');
      void queryClient.invalidateQueries({ queryKey: ['auto-iteration-settings-history'] });
    },
    onError: (error) => {
      const text = saveErrorText(error);
      setSaveError(text);
      message.error(text);
    },
  });

  const isLocked = (key: string) => lockedKeys.has(key);
  const fieldDisabled = (key: string) => !canEdit || isLocked(key);
  const lockedExtra = (key: string, extra?: ReactNode): ReactNode => (
    isLocked(key) ? '该项已被环境变量锁定，页面只读；如需修改请调整服务端环境变量' : extra
  );

  const handleOpenSave = async () => {
    if (!settings || !canEdit) return;
    setSaveError(null);
    try {
      await form.validateFields();
    } catch {
      return;
    }
    const current = form.getFieldsValue(true) as Partial<AutoIterationValues>;
    const changes = diffAutoIterationValues(current, savedValues ?? settings.values);
    if (Object.keys(changes).length === 0) {
      message.info('当前设置与已保存值一致，无需保存');
      return;
    }
    setPendingChanges(changes);
    setReasonOpen(true);
  };

  const handleRestoreDefaults = () => {
    if (!settings) return;
    form.setFieldsValue(settings.defaults);
    message.info('已填入系统默认值，点击「保存设置」后生效');
  };

  const handleHistoryRefresh = () => {
    void historyQuery.refetch();
  };

  const historyColumns: ColumnsType<AutoIterationHistoryRow> = [
    {
      title: '时间',
      dataIndex: 'changed_at',
      key: 'changed_at',
      width: 180,
      render: (value: string | null | undefined) => formatDateTime(value),
    },
    {
      title: '配置项',
      dataIndex: 'item_key',
      key: 'item_key',
      width: 170,
      render: (value: string) => AUTO_ITERATION_FIELD_LABELS[value] || value,
    },
    {
      title: '旧值 → 新值',
      key: 'change',
      render: (_, row) =>
        `${formatAutoIterationValue(row.item_key, row.before, metricOptions)} → ${formatAutoIterationValue(
          row.item_key,
          row.after,
          metricOptions,
        )}`,
    },
    {
      title: '原因',
      dataIndex: 'change_reason',
      key: 'change_reason',
      render: (value: string | null | undefined) => value || '—',
    },
  ];

  const changedEntries = pendingChanges
    ? Object.entries(pendingChanges).map(([key, value]) => ({
        key,
        label: AUTO_ITERATION_FIELD_LABELS[key] || key,
        before: formatAutoIterationValue(key, savedValues?.[key as keyof AutoIterationValues], metricOptions),
        after: formatAutoIterationValue(key, value, metricOptions),
      }))
    : [];

  const lockedLabels = (settings?.locked_keys ?? []).map((key) => AUTO_ITERATION_FIELD_LABELS[key] || key);
  const enabledSource = settings?.sources?.enabled;

  return (
    <TablePanel>
      <Space
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}
        wrap
      >
        <div style={{ minWidth: 260 }}>
          <h2 style={{ marginBottom: 4 }}>
            <SettingOutlined style={{ marginRight: 8 }} />
            自动迭代设置
          </h2>
          <Typography.Text type="secondary">
            配置训练未达标时自动迭代的判定口径、阈值与停止条件；保存后下一轮判定立即生效。
          </Typography.Text>
        </div>
        <Space wrap>
          <Button icon={<HistoryOutlined />} onClick={() => { setHistoryPage(0); setHistoryOpen(true); }}>
            查看变更历史
          </Button>
          <Button icon={<ReloadOutlined />} loading={settingsQuery.isFetching} onClick={() => void settingsQuery.refetch()}>
            刷新配置
          </Button>
          <Button icon={<UndoOutlined />} disabled={!canEdit || !settings} onClick={handleRestoreDefaults}>
            恢复默认
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saveMutation.isPending}
            disabled={!canEdit || !settings || settingsQuery.isError}
            onClick={() => void handleOpenSave()}
          >
            保存设置
          </Button>
        </Space>
      </Space>

      {settingsQuery.isLoading ? (
        <div role="status" aria-live="polite" style={{ padding: '24px 0' }}>
          <Typography.Text type="secondary">正在加载自动迭代设置</Typography.Text>
        </div>
      ) : settingsQuery.error ? (
        <Alert
          type="error"
          showIcon
          message="自动迭代设置加载失败"
          description={loadErrorText(settingsQuery.error)}
          action={<Button size="small" onClick={() => void settingsQuery.refetch()}>重试</Button>}
        />
      ) : settings ? (
        <>
          {!canEdit ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 14 }}
              message="仅管理员可修改"
              description="当前账号没有修改权限，下方配置以只读方式展示；如需调整请联系管理员。"
            />
          ) : null}
          {lockedLabels.length > 0 ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 14 }}
              message="部分配置已被环境变量锁定"
              description={`以下配置由服务端环境变量强制指定，页面只读：${lockedLabels.join('、')}`}
            />
          ) : null}
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={`达标判定：${settings.threshold_hint}`}
            description={`当前自动迭代${settings.values.enabled ? '已开启' : '未开启'}${
              enabledSource ? `（来源：${SOURCE_LABELS[enabledSource] || enabledSource}）` : ''
            }；最大迭代次数含首轮，当前允许 ${settings.max_iterations_after_first} 次自动迭代。`}
          />
          {saveError ? (
            <Alert
              type="error"
              showIcon
              closable
              style={{ marginBottom: 16 }}
              message="保存失败"
              description={saveError}
              onClose={() => setSaveError(null)}
            />
          ) : null}

          <Form form={form} layout="vertical" disabled={!canEdit}>
            <Typography.Text strong style={{ display: 'block', marginBottom: 10 }}>
              基础判定口径
            </Typography.Text>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
              <Form.Item
                name="enabled"
                label="自动迭代开关"
                valuePropName="checked"
                extra={lockedExtra('enabled', '关闭后训练未达标不再自动开启下一轮训练')}
              >
                <Switch aria-label="自动迭代开关" disabled={fieldDisabled('enabled')} />
              </Form.Item>
              <Form.Item
                name="metric_basis"
                label="判定口径"
                extra={lockedExtra('metric_basis', '用哪个召回/精度口径判定本轮是否达标')}
                rules={[{ required: true, message: '请选择判定口径' }]}
              >
                <Select
                  aria-label="判定口径"
                  options={metricOptions}
                  disabled={fieldDisabled('metric_basis')}
                  placeholder="请选择判定口径"
                />
              </Form.Item>
              <Form.Item
                name="pass_threshold"
                label="达标阈值"
                extra={lockedExtra('pass_threshold', '达到该阈值即判定通过并停止迭代')}
                rules={[
                  { required: true, message: '请输入达标阈值' },
                  { type: 'number', min: 0.3, max: 0.99, message: '达标阈值必须在 0.3 ~ 0.99 之间' },
                ]}
              >
                <PercentField ariaLabel="达标阈值" min={0.3} max={0.99} disabled={fieldDisabled('pass_threshold')} />
              </Form.Item>
              <Form.Item
                name="warn_threshold"
                label="预警阈值"
                extra={lockedExtra('warn_threshold', '低于该阈值触发预警提示，不得高于达标阈值')}
                rules={[
                  { required: true, message: '请输入预警阈值' },
                  {
                    validator: async (_rule, value) => {
                      const pass = form.getFieldValue('pass_threshold');
                      if (typeof value === 'number' && typeof pass === 'number' && value > pass) {
                        throw new Error('预警阈值不能大于达标阈值');
                      }
                    },
                  },
                ]}
              >
                <PercentField ariaLabel="预警阈值" min={0} max={0.99} disabled={fieldDisabled('warn_threshold')} />
              </Form.Item>
              <Form.Item
                name="max_rounds"
                label="最大迭代次数"
                extra={lockedExtra(
                  'max_rounds',
                  `${settings.max_rounds_hint}；当前 = 首轮 + ${Math.max(0, Number(maxRounds || 1) - 1)} 次自动迭代`,
                )}
                rules={[
                  { required: true, message: '请输入最大迭代次数' },
                  { type: 'number', min: 1, max: 20, message: '最大迭代次数必须在 1 ~ 20 之间' },
                ]}
              >
                <InputNumber
                  aria-label="最大迭代次数"
                  min={1}
                  max={20}
                  step={1}
                  precision={0}
                  disabled={fieldDisabled('max_rounds')}
                  style={{ width: '100%' }}
                />
              </Form.Item>
              <Form.Item
                name="no_improvement_rounds"
                label="无提升停止轮数"
                extra={lockedExtra('no_improvement_rounds', '连续 N 轮无提升即停止自动迭代；0 表示不因此停止')}
                rules={[
                  { required: true, message: '请输入无提升停止轮数' },
                  { type: 'number', min: 0, max: 10, message: '无提升停止轮数必须在 0 ~ 10 之间' },
                ]}
              >
                <InputNumber
                  aria-label="无提升停止轮数"
                  min={0}
                  max={10}
                  step={1}
                  precision={0}
                  disabled={fieldDisabled('no_improvement_rounds')}
                  style={{ width: '100%' }}
                />
              </Form.Item>
              <Form.Item
                name="min_improvement_delta"
                label="最小提升幅度"
                extra={lockedExtra('min_improvement_delta', '每轮相对上一轮的提升小于该幅度时视为「无提升」')}
                rules={[
                  { required: true, message: '请输入最小提升幅度' },
                  { type: 'number', min: 0, max: 0.5, message: '最小提升幅度必须在 0 ~ 0.5 之间' },
                ]}
              >
                <PercentField
                  ariaLabel="最小提升幅度"
                  min={0}
                  max={0.5}
                  disabled={fieldDisabled('min_improvement_delta')}
                />
              </Form.Item>
              <Form.Item
                name="allow_conditional_pass"
                label="条件达标算通过"
                valuePropName="checked"
                extra={lockedExtra('allow_conditional_pass', '允许在带条件下判定通过并进入下一阶段')}
              >
                <Switch aria-label="条件达标算通过" disabled={fieldDisabled('allow_conditional_pass')} />
              </Form.Item>
              <Form.Item
                name="require_quality_for_compile"
                label="达标才编译"
                valuePropName="checked"
                extra={lockedExtra('require_quality_for_compile', '未达到质量门禁时不自动进入 BM1688 编译')}
              >
                <Switch aria-label="达标才编译" disabled={fieldDisabled('require_quality_for_compile')} />
              </Form.Item>
            </div>

            <Divider style={{ margin: '18px 0 10px' }} />
            <Collapse
              size="small"
              items={[
                {
                  key: 'advanced',
                  label: '高级 / 守护项',
                  children: (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
                      <Form.Item
                        name="min_class_recall_enabled"
                        label="单类最低召回开关"
                        valuePropName="checked"
                        extra={lockedExtra('min_class_recall_enabled', '开启后对每个类别单独设置最低召回下限')}
                      >
                        <Switch
                          aria-label="单类最低召回开关"
                          disabled={fieldDisabled('min_class_recall_enabled')}
                        />
                      </Form.Item>
                      <Form.Item
                        name="min_class_recall"
                        label="单类最低召回"
                        extra={lockedExtra('min_class_recall', '任一类别召回低于该值则本轮判定不达标')}
                        rules={[
                          { required: true, message: '请输入单类最低召回' },
                          {
                            validator: async (_rule, value) => {
                              const pass = form.getFieldValue('pass_threshold');
                              if (typeof value === 'number' && typeof pass === 'number' && value > pass) {
                                throw new Error('单类最低召回不能大于达标阈值');
                              }
                            },
                          },
                        ]}
                      >
                        <PercentField
                          ariaLabel="单类最低召回"
                          min={0}
                          max={0.99}
                          disabled={fieldDisabled('min_class_recall') || !minClassRecallEnabled}
                        />
                      </Form.Item>
                      <Form.Item
                        name="precision_floor_enabled"
                        label="precision 下限开关"
                        valuePropName="checked"
                        extra={lockedExtra('precision_floor_enabled', '开启后通过 precision 下限守住误检率')}
                      >
                        <Switch
                          aria-label="precision 下限开关"
                          disabled={fieldDisabled('precision_floor_enabled')}
                        />
                      </Form.Item>
                      <Form.Item
                        name="precision_floor"
                        label="precision 下限"
                        extra={lockedExtra('precision_floor', 'precision 低于该值则本轮判定不达标')}
                        rules={[
                          { required: true, message: '请输入 precision 下限' },
                          { type: 'number', min: 0, max: 0.99, message: 'precision 下限必须在 0 ~ 0.99 之间' },
                        ]}
                      >
                        <PercentField
                          ariaLabel="precision 下限"
                          min={0}
                          max={0.99}
                          disabled={fieldDisabled('precision_floor') || !precisionFloorEnabled}
                        />
                      </Form.Item>
                      <Form.Item
                        name="max_regression"
                        label="相对上一轮回退上限"
                        extra={lockedExtra('max_regression', '任一指标相对上一轮下降超过该幅度视为回退')}
                        rules={[
                          { required: true, message: '请输入回退上限' },
                          { type: 'number', min: 0, max: 0.5, message: '回退上限必须在 0 ~ 0.5 之间' },
                        ]}
                      >
                        <PercentField
                          ariaLabel="相对上一轮回退上限"
                          min={0}
                          max={0.5}
                          disabled={fieldDisabled('max_regression')}
                        />
                      </Form.Item>
                      <Form.Item
                        name="eval_conf"
                        label="评估置信度工作点"
                        extra={lockedExtra('eval_conf', '评估判定使用的置信度工作点，影响召回与 precision 口径')}
                        rules={[
                          { required: true, message: '请输入评估置信度' },
                          { type: 'number', min: 0.01, max: 0.99, message: '评估置信度必须在 0.01 ~ 0.99 之间' },
                        ]}
                      >
                        <PercentField
                          ariaLabel="评估置信度工作点"
                          min={0.01}
                          max={0.99}
                          disabled={fieldDisabled('eval_conf')}
                        />
                      </Form.Item>
                    </div>
                  ),
                },
              ]}
            />
          </Form>
        </>
      ) : null}

      <Modal
        title="保存自动迭代设置"
        open={reasonOpen}
        centered
        okText="确认保存"
        cancelText="取消"
        confirmLoading={saveMutation.isPending}
        onCancel={() => {
          setReasonOpen(false);
          setPendingChanges(null);
        }}
        onOk={() => reasonForm.submit()}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          保存后下一轮判定立即使用新口径，请填写本次调整原因（会写入配置变更历史）。
        </Typography.Paragraph>
        {changedEntries.length > 0 ? (
          <div style={{ marginBottom: 14, padding: '10px 12px', background: '#f6f8fb', borderRadius: 6 }}>
            <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>
              本次修改
            </Typography.Text>
            {changedEntries.map((entry) => (
              <div key={entry.key} style={{ fontSize: 13, color: '#334155', lineHeight: 1.7 }}>
                {entry.label}：{entry.before} → {entry.after}
              </div>
            ))}
          </div>
        ) : null}
        <Form
          form={reasonForm}
          layout="vertical"
          onFinish={({ reason }) => {
            if (!pendingChanges) return;
            saveMutation.mutate({ changes: pendingChanges, reason });
          }}
        >
          <Form.Item
            name="reason"
            label="修改原因"
            rules={[
              { required: true, whitespace: true, message: '请填写修改原因' },
              { max: 2000, message: '修改原因不能超过 2000 个字符' },
            ]}
          >
            <Input.TextArea aria-label="修改原因" rows={3} maxLength={2000} showCount placeholder="例如：业务确认达标线调整为 70%" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="配置变更历史"
        open={historyOpen}
        centered
        width={900}
        footer={null}
        onCancel={() => setHistoryOpen(false)}
        destroyOnClose
      >
        <Space style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 10 }}>
          <Button size="small" icon={<ReloadOutlined />} onClick={handleHistoryRefresh} loading={historyQuery.isFetching}>
            刷新历史
          </Button>
        </Space>
        {historyQuery.isLoading ? (
          <div role="status" aria-live="polite" style={{ padding: '16px 0' }}>
            <Typography.Text type="secondary">正在加载变更历史</Typography.Text>
          </div>
        ) : historyQuery.error ? (
          <Alert
            type="error"
            showIcon
            message="变更历史加载失败"
            description="请稍后重试"
            action={<Button size="small" onClick={handleHistoryRefresh}>重试</Button>}
          />
        ) : (
          <Table<AutoIterationHistoryRow>
            size="small"
            rowKey={(row) => String(row.id ?? `${row.item_key}-${row.changed_at ?? ''}`)}
            dataSource={historyQuery.data?.rows ?? []}
            columns={historyColumns}
            scroll={{ x: 'max-content' }}
            locale={{ emptyText: '暂无变更记录' }}
            pagination={
              (historyQuery.data?.total ?? 0) > HISTORY_PAGE_SIZE
                ? {
                    current: historyPage + 1,
                    pageSize: HISTORY_PAGE_SIZE,
                    total: historyQuery.data?.total ?? 0,
                    size: 'small',
                    showSizeChanger: false,
                    onChange: (page) => setHistoryPage(page - 1),
                  }
                : false
            }
          />
        )}
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
          配置项来源：<Tag style={{ marginInlineEnd: 4 }}>页面配置</Tag> 表示由本页面保存；被环境变量锁定的项不会出现在可写变更中。
        </Typography.Text>
      </Modal>
    </TablePanel>
  );
}
