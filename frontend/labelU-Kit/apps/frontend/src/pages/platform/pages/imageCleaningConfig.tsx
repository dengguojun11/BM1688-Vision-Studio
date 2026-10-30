import { ReloadOutlined, SaveOutlined, SettingOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Form, InputNumber, Radio, Space, Tag, Typography, message } from 'antd';
import { useEffect, useState } from 'react';

import {
  getImageCleaningPolicy,
  updateImageCleaningPolicy,
  type ImageCleaningPolicy,
  type ImageCleaningPolicyPreset,
  type ImageCleaningPolicyUpdate,
  type ImageCleaningPreset,
} from '@/api/services/imageCleaning';

import { TablePanel, responseStatus } from '../shared';

export const IMAGE_CLEANING_PRESETS = ['simple', 'standard', 'strict'] as const;

type ImageCleaningFormValues = {
  active_preset: ImageCleaningPreset;
  simple_threshold: number;
  standard_threshold: number;
  strict_threshold: number;
};

const PRESET_COPY: Record<
  ImageCleaningPreset,
  { label: string; strength: string; description: string; color: 'green' | 'blue' | 'orange' }
> = {
  simple: {
    label: '简单清洗',
    strength: '低强度',
    description: '低强度、较宽松地过滤重复样本，尽量保留更多图片。',
    color: 'green',
  },
  standard: {
    label: '标准清洗',
    strength: '中强度',
    description: '在去除近重复图片和保留有效样本之间取得平衡。',
    color: 'blue',
  },
  strict: {
    label: '严格清洗',
    strength: '高强度',
    description: '更积极过滤近重复图片，适合需要更紧凑样本集的场景。',
    color: 'orange',
  },
};

export function formatSimilarityPercent(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-';
  const percent = Number((value * 100).toFixed(2));
  return `${percent}%`;
}

function isRecord(value: unknown): value is Record<string, any> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Runtime requests return the business envelope directly, while page tests
 * commonly mock the outer Axios `{ data: ... }` shape. Accept both forms.
 */
export function normalizeImageCleaningPolicy(payload: unknown): ImageCleaningPolicy | null {
  let candidate: any = payload;
  for (let depth = 0; depth < 3; depth += 1) {
    if (!isRecord(candidate) || !isRecord(candidate.data) || 'active_preset' in candidate) break;
    candidate = candidate.data;
  }
  if (isRecord(candidate) && isRecord(candidate.policy) && !('active_preset' in candidate)) {
    candidate = candidate.policy;
  }
  if (!isRecord(candidate) || !isRecord(candidate.presets)) return null;

  const activePreset = IMAGE_CLEANING_PRESETS.includes(candidate.active_preset) ? candidate.active_preset : null;
  if (!activePreset) return null;

  const presets = {} as Record<ImageCleaningPreset, ImageCleaningPolicyPreset>;
  for (const preset of IMAGE_CLEANING_PRESETS) {
    const threshold = Number(candidate.presets[preset]?.threshold);
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) return null;
    presets[preset] = { threshold };
  }

  const revision = Number(candidate.revision);
  if (!Number.isFinite(revision)) return null;
  const algorithmVersion = String(candidate.algorithm_version || '').trim();
  const configBackendLabel = String(candidate.config_backend_label || '').trim();
  if (!algorithmVersion || !configBackendLabel) return null;

  return {
    active_preset: activePreset,
    presets,
    revision,
    algorithm_version: algorithmVersion,
    config_backend_label: configBackendLabel,
  };
}

function policyErrorText(error: unknown, operation: 'load' | 'save' = 'load'): string {
  const status = responseStatus(error);
  if (status === 403) return '只有管理人员可以维护图片清洗配置';
  if (status === 409) return '图片清洗配置已被其他管理人员更新，请刷新后重试';
  return operation === 'save' ? '图片清洗配置保存失败，请检查阈值后重试' : '图片清洗配置加载失败，请稍后重试';
}

function toFormValues(policy: ImageCleaningPolicy): ImageCleaningFormValues {
  return {
    active_preset: policy.active_preset,
    simple_threshold: policy.presets.simple.threshold,
    standard_threshold: policy.presets.standard.threshold,
    strict_threshold: policy.presets.strict.threshold,
  };
}

export function isValidImageCleaningThresholdOrder(
  values: Pick<ImageCleaningFormValues, 'simple_threshold' | 'standard_threshold' | 'strict_threshold'>,
): boolean {
  const simple = Number(values.simple_threshold);
  const standard = Number(values.standard_threshold);
  const strict = Number(values.strict_threshold);
  return (
    Number.isFinite(simple) &&
    Number.isFinite(standard) &&
    Number.isFinite(strict) &&
    simple >= standard &&
    standard >= strict
  );
}

function toUpdate(values: ImageCleaningFormValues, expectedRevision: number): ImageCleaningPolicyUpdate {
  return {
    active_preset: values.active_preset,
    presets: {
      simple: { threshold: Number(values.simple_threshold) },
      standard: { threshold: Number(values.standard_threshold) },
      strict: { threshold: Number(values.strict_threshold) },
    },
    expected_revision: expectedRevision,
  };
}

function presetThresholdField(preset: ImageCleaningPreset): keyof ImageCleaningFormValues {
  return `${preset}_threshold` as keyof ImageCleaningFormValues;
}

function PresetRow({ preset, threshold }: { preset: ImageCleaningPreset; threshold: number | undefined }) {
  const copy = PRESET_COPY[preset];
  const field = presetThresholdField(preset);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(180px, 1fr) minmax(150px, 220px) minmax(120px, 1fr)',
        gap: 16,
        alignItems: 'center',
        padding: '14px 0',
        borderBottom: '1px solid #edf0f5',
      }}
    >
      <Radio value={preset} aria-label={copy.label}>
        <Space direction="vertical" size={2}>
          <Space size={8} wrap>
            <Typography.Text strong>{copy.label}</Typography.Text>
            <Tag color={copy.color}>{copy.strength}</Tag>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {copy.description}
          </Typography.Text>
        </Space>
      </Radio>
      <Form.Item
        name={field}
        label="相似度阈值"
        style={{ marginBottom: 0 }}
        rules={[
          { required: true, type: 'number', message: '请输入阈值' },
          { type: 'number', min: 0, max: 1, message: '阈值必须在 0 到 1 之间' },
        ]}
      >
        <InputNumber
          aria-label={`${copy.label}相似度阈值`}
          min={0}
          max={1}
          step={0.01}
          precision={2}
          style={{ width: '100%' }}
        />
      </Form.Item>
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{formatSimilarityPercent(threshold)}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          相似度百分比
        </Typography.Text>
      </Space>
    </div>
  );
}

export default function ImageCleaningConfigPanel() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ImageCleaningFormValues>();
  const [saveError, setSaveError] = useState<string | null>(null);
  const policyQuery = useQuery<ImageCleaningPolicy>({
    queryKey: ['image-cleaning-policy'],
    queryFn: async () => {
      const normalized = normalizeImageCleaningPolicy(await getImageCleaningPolicy());
      if (!normalized) throw new Error('图片清洗配置响应格式无效');
      return normalized;
    },
    staleTime: 5_000,
  });
  const policy = policyQuery.data;
  const activePreset = Form.useWatch('active_preset', form) || policy?.active_preset || 'standard';
  const thresholds = {
    simple: Form.useWatch('simple_threshold', form),
    standard: Form.useWatch('standard_threshold', form),
    strict: Form.useWatch('strict_threshold', form),
  } as Record<ImageCleaningPreset, number | undefined>;

  useEffect(() => {
    if (policy) form.setFieldsValue(toFormValues(policy));
  }, [form, policy]);

  const saveMutation = useMutation({
    mutationFn: async (values: ImageCleaningFormValues) => {
      setSaveError(null);
      if (!policy) throw new Error('图片清洗配置尚未加载');
      return updateImageCleaningPolicy(toUpdate(values, policy.revision));
    },
    onSuccess: (response) => {
      const normalized = normalizeImageCleaningPolicy(response);
      if (normalized) {
        queryClient.setQueryData(['image-cleaning-policy'], normalized);
      } else {
        void queryClient.invalidateQueries({ queryKey: ['image-cleaning-policy'] });
      }
      message.success('图片清洗配置已保存');
    },
    onError: (error) => {
      const text = policyErrorText(error, 'save');
      setSaveError(text);
      message.error(text);
    },
  });

  const submit = (values: ImageCleaningFormValues) => {
    if (!isValidImageCleaningThresholdOrder(values)) {
      form.setFields([
        {
          name: 'simple_threshold',
          errors: ['阈值顺序必须满足：简单清洗 ≥ 标准清洗 ≥ 严格清洗'],
        },
      ]);
      message.error('阈值顺序必须满足：简单清洗 ≥ 标准清洗 ≥ 严格清洗');
      return;
    }
    saveMutation.mutate(values);
  };

  return (
    <TablePanel>
      <Space
        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}
        wrap
      >
        <div style={{ minWidth: 220 }}>
          <h2 style={{ marginBottom: 4 }}>
            <SettingOutlined style={{ marginRight: 8 }} />
            图片清洗配置
          </h2>
          <Typography.Text type="secondary">
            管理图片近重复清洗的三档相似度阈值；当前规则会用于后续清洗任务。
          </Typography.Text>
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={policyQuery.isFetching} onClick={() => void policyQuery.refetch()}>
            刷新配置
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saveMutation.isPending}
            disabled={!policy || policyQuery.isError}
            onClick={() => void form.submit()}
          >
            保存配置
          </Button>
        </Space>
      </Space>

      {policyQuery.isLoading ? (
        <div role="status" aria-live="polite" style={{ padding: '24px 0' }}>
          <Typography.Text type="secondary">正在加载图片清洗配置</Typography.Text>
        </div>
      ) : policyQuery.error ? (
        <Alert
          type="error"
          showIcon
          message="图片清洗配置加载失败"
          description={policyErrorText(policyQuery.error)}
          action={
            <Button size="small" onClick={() => void policyQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : policy ? (
        <>
          <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 4 }} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="当前规则">
              <Tag color={PRESET_COPY[policy.active_preset].color}>{PRESET_COPY[policy.active_preset].label}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="当前相似度">
              {formatSimilarityPercent(policy.presets[policy.active_preset].threshold)}
            </Descriptions.Item>
            <Descriptions.Item label="算法版本">{policy.algorithm_version}</Descriptions.Item>
            <Descriptions.Item label="配置来源">
              <Space direction="vertical" size={0}>
                <span>{policy.config_backend_label}</span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  修订号 {policy.revision}
                </Typography.Text>
              </Space>
            </Descriptions.Item>
          </Descriptions>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message={`当前规则：${PRESET_COPY[activePreset].label}（${formatSimilarityPercent(
              thresholds[activePreset] ?? policy.presets[activePreset].threshold,
            )}）`}
            description="选择档位后，保存配置即可让后续图片清洗任务使用新的规则。三档阈值均会一起保存。"
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
          <Form form={form} layout="vertical" onFinish={submit}>
            <Form.Item
              name="active_preset"
              label="当前清洗规则"
              extra="选择当前使用的清洗强度；每档阈值可以独立调整。"
              rules={[{ required: true, message: '请选择当前清洗规则' }]}
            >
              <Radio.Group style={{ display: 'block' }}>
                {IMAGE_CLEANING_PRESETS.map((preset) => (
                  <PresetRow key={preset} preset={preset} threshold={thresholds[preset]} />
                ))}
              </Radio.Group>
            </Form.Item>
          </Form>
        </>
      ) : null}
    </TablePanel>
  );
}
