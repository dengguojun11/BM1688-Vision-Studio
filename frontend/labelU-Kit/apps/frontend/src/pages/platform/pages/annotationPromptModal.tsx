import {
  CheckCircleOutlined,
  CloseOutlined,
  CloudOutlined,
  FileTextOutlined,
  LockOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SaveOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Input, Modal, Space, Spin, Tag, Typography, message } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';

import request from '@/api/request';

export type PromptContractLabel = {
  label_id?: number;
  label_key?: string;
  label_value?: string;
  annotation_kind?: string;
  color?: string;
  attributes?: Array<Record<string, any>>;
};

export type PromptContract = {
  id: number;
  revision: number;
  annotation_kind?: string;
  label_scope_mode?: string;
  label_set_signature?: string;
  class_order_signature?: string;
  status?: string;
  labels?: PromptContractLabel[];
};

export type AnnotationPrompt = {
  id?: number;
  dataset_id?: number;
  contract_id?: number;
  contract_revision?: number;
  operation?: string;
  provider_key?: string;
  model_name?: string | null;
  profile_key?: string;
  profile_version?: number | string | null;
  generated_prompt?: string | null;
  edited_prompt?: string | null;
  effective_prompt?: string | null;
  prompt_sha256?: string | null;
  status?: string;
  warnings?: string[];
  created_at?: string;
  updated_at?: string;
  published_at?: string | null;
};

type AnnotationPromptModalProps = {
  dataset: { id?: number | string; name?: string } | null;
  contract: PromptContract | null;
  open: boolean;
  onClose: () => void;
};

const DEFAULT_PROFILE_KEY = 'annotation_bbox_v1';

function supportsBboxPrompt(contract: PromptContract | null) {
  return String(contract?.annotation_kind || 'bbox').trim().toLowerCase() === 'bbox';
}

const EditorGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(245px, 0.78fr) minmax(420px, 1.42fr);
  gap: 16px;
  align-items: start;

  @media (max-width: 900px) {
    grid-template-columns: 1fr;
  }
`;

const WorkPanel = styled.section`
  min-width: 0;
  border: 1px solid #e5eaf1;
  border-radius: 6px;
  background: #fff;
  padding: 16px;

  h3 {
    margin: 0 0 6px;
    color: #172033;
    font-size: 15px;
    font-weight: 650;
  }

  .panel-hint {
    display: block;
    margin-bottom: 14px;
    color: #64748b;
    font-size: 12px;
    line-height: 1.5;
  }
`;

const PromptEditor = styled(Input.TextArea)`
  min-height: 430px !important;
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.65;
`;

const LabelList = styled.div`
  display: grid;
  gap: 7px;
  max-height: 270px;
  overflow: auto;
  margin-bottom: 16px;
`;

const LabelRow = styled.div`
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  padding: 8px 9px;
  border: 1px solid #edf0f5;
  border-radius: 4px;
  background: #f8fafc;

  .label-index {
    width: 20px;
    color: #94a3b8;
    font-size: 11px;
    text-align: right;
  }

  .label-name {
    min-width: 0;
    overflow: hidden;
    color: #334155;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  code {
    color: #0f4c81;
    font-size: 11px;
  }
`;

function unwrap(value: any): any {
  const body = value?.data ?? value;
  return body?.data ?? body;
}

function promptRows(value: any): AnnotationPrompt[] {
  const data = unwrap(value);
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  if (Array.isArray(data?.prompts)) return data.prompts;
  return data ? [data] : [];
}

function promptText(prompt?: AnnotationPrompt | null) {
  return String(prompt?.effective_prompt || prompt?.edited_prompt || prompt?.generated_prompt || '');
}

function statusLabel(status?: string) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'published' || normalized === 'active') return '已发布';
  if (normalized === 'draft') return '草稿';
  if (normalized === 'invalidated' || normalized === 'superseded') return '已失效';
  return status || '未生成';
}

function isPublished(status?: string) {
  return ['published', 'active'].includes(String(status || '').toLowerCase());
}

function errorMessage(error: any, fallback: string) {
  return (
    error?.response?.data?.msg ||
    error?.response?.data?.message ||
    error?.response?.data?.detail ||
    fallback
  );
}

export function AnnotationPromptModal({ dataset, contract, open, onClose }: AnnotationPromptModalProps) {
  const queryClient = useQueryClient();
  const [promptTextValue, setPromptTextValue] = useState('');
  const [selectedPrompt, setSelectedPrompt] = useState<AnnotationPrompt | null>(null);
  const [model, setModel] = useState('');
  const [profileKey, setProfileKey] = useState(DEFAULT_PROFILE_KEY);
  const initializedContextRef = useRef<string | null>(null);
  const promptContextKey = `${dataset?.id ?? ''}:${contract?.id ?? ''}:${contract?.revision ?? ''}`;

  const promptQuery = useQuery({
    queryKey: ['dataset-annotation-prompts', dataset?.id, contract?.id, contract?.revision],
    queryFn: async () =>
      request.get(`/v1/datasets/${dataset?.id}/label-contracts/${contract?.revision}/ai-prompts`),
    enabled: open && Boolean(dataset?.id) && Boolean(contract?.id) && Boolean(contract?.revision),
    staleTime: 5_000,
    retry: false,
  });

  const prompts = useMemo(() => promptRows(promptQuery.data), [promptQuery.data]);
  const bboxPrompts = useMemo(
    () => prompts.filter((item) => !item.operation || item.operation === 'bbox_detection'),
    [prompts],
  );

  useEffect(() => {
    if (!open) {
      initializedContextRef.current = null;
      setPromptTextValue('');
      setSelectedPrompt(null);
      setModel('');
      setProfileKey(DEFAULT_PROFILE_KEY);
      return;
    }
    if (promptQuery.isLoading || initializedContextRef.current === promptContextKey) return;
    // Keep the draft just saved selected when the invalidated query returns.
    // Otherwise a previously published row wins and the editor silently
    // discards the user's newly saved version from view.
    const hasLocalSelection = selectedPrompt !== null;
    const selectedId = selectedPrompt?.id;
    const selectedIsForeignOperation = Boolean(
      selectedPrompt?.operation && selectedPrompt.operation !== 'bbox_detection',
    );
    // A preview is intentionally not persisted yet and therefore has no id.
    // Keep it visible while the initial/refreshed list request settles instead
    // of replacing the user's preview with the empty-list fallback.
    if (
      hasLocalSelection &&
      !selectedIsForeignOperation &&
      (!selectedId || !bboxPrompts.some((item) => Number(item.id) === Number(selectedId)))
    ) {
      return;
    }
    const next =
      (selectedId ? bboxPrompts.find((item) => Number(item.id) === Number(selectedId)) : null) ||
      bboxPrompts.find((item) => String(item.status || '').toLowerCase() === 'draft') ||
      bboxPrompts.find((item) => isPublished(item.status) && !String(item.model_name || '').trim()) ||
      bboxPrompts.find((item) => isPublished(item.status)) ||
      bboxPrompts[0] ||
      null;
    setSelectedPrompt(next);
    setPromptTextValue(promptText(next));
    setModel(next?.model_name ? String(next.model_name) : '');
    setProfileKey(next?.profile_key ? String(next.profile_key) : DEFAULT_PROFILE_KEY);
    initializedContextRef.current = promptContextKey;
  }, [bboxPrompts, open, promptContextKey, promptQuery.isLoading, prompts]);

  const previewMutation = useMutation({
    mutationFn: async () =>
      request.post(`/v1/datasets/${dataset?.id}/label-contracts/${contract?.revision}/ai-prompts/preview`, {
        operation: 'bbox_detection',
        provider_key: 'volcengine',
        model_name: model.trim() || null,
        profile_key: profileKey.trim() || DEFAULT_PROFILE_KEY,
        profile_version: '1',
      }),
    onSuccess: (response) => {
      const generated = unwrap(response);
      const nextPrompt: AnnotationPrompt = generated?.prompt || generated;
      setSelectedPrompt(nextPrompt);
      setPromptTextValue(promptText(nextPrompt));
      message.success('已根据当前标签契约生成提示词预览');
    },
    onError: (error) => message.error(errorMessage(error, '提示词生成失败，请检查契约状态')),
  });

  const saveMutation = useMutation({
    mutationFn: async () =>
      request.post(`/v1/datasets/${dataset?.id}/label-contracts/${contract?.revision}/ai-prompts`, {
        operation: 'bbox_detection',
        provider_key: 'volcengine',
        model_name: model.trim() || null,
        profile_key: profileKey.trim() || DEFAULT_PROFILE_KEY,
        profile_version: '1',
        effective_prompt: promptTextValue,
        edited_prompt: promptTextValue,
        expected_label_set_signature: contract?.label_set_signature || null,
        expected_class_order_signature: contract?.class_order_signature || null,
        // A published snapshot is immutable. Saving after editing it creates
        // a new draft; only an existing draft may be updated in place.
        expected_prompt_id: selectedPrompt && !isPublished(selectedPrompt.status) ? selectedPrompt.id || null : null,
      }),
    onSuccess: async (response) => {
      const saved = unwrap(response);
      setSelectedPrompt(saved);
      setPromptTextValue(promptText(saved));
      await queryClient.invalidateQueries({ queryKey: ['dataset-annotation-prompts', dataset?.id, contract?.id, contract?.revision] });
      message.success('提示词草稿已保存');
    },
    onError: (error) => message.error(errorMessage(error, '提示词草稿保存失败')),
  });

  const publishMutation = useMutation({
    mutationFn: async (promptId: number) => request.post(`/v1/datasets/${dataset?.id}/label-contracts/${contract?.revision}/ai-prompts/${promptId}/publish`, {
      expected_contract_revision: contract?.revision,
      expected_prompt_sha256: selectedPrompt?.prompt_sha256 || null,
    }),
    onSuccess: async (response) => {
      const published = unwrap(response);
      setSelectedPrompt(published);
      setPromptTextValue(promptText(published));
      await queryClient.invalidateQueries({ queryKey: ['dataset-annotation-prompts', dataset?.id, contract?.id, contract?.revision] });
      message.success('提示词已发布，AI标注将使用该契约版本');
    },
    onError: (error) => message.error(errorMessage(error, '提示词发布失败，请刷新后重试')),
  });

  const labels = contract?.labels || [];
  const warnings = [
    ...(selectedPrompt?.warnings ?? []),
    ...(labels.some((label) => !String(label.label_key || '').trim()) ? ['部分标签缺少中文显示名称'] : []),
  ];
  const busy = previewMutation.isPending || saveMutation.isPending || publishMutation.isPending;
  const savedPromptText = promptText(selectedPrompt);
  const savedModel = String(selectedPrompt?.model_name || '').trim();
  const savedProfileKey = String(selectedPrompt?.profile_key || DEFAULT_PROFILE_KEY).trim();
  const currentModel = model.trim();
  const currentProfileKey = profileKey.trim() || DEFAULT_PROFILE_KEY;
  const promptDirty = Boolean(selectedPrompt?.id) && (
    promptTextValue !== savedPromptText ||
    currentModel !== savedModel ||
    currentProfileKey !== savedProfileKey
  );
  const canPublish = Boolean(selectedPrompt?.id) && Boolean(promptTextValue.trim()) && !promptDirty && !busy;
  const bboxPromptSupported = supportsBboxPrompt(contract);

  return (
    <Modal
      title={null}
      open={open}
      centered
      destroyOnClose
      width={1180}
      closable={false}
      footer={null}
      aria-label={`AI提示词 · ${dataset?.name || `数据集 #${dataset?.id || ''}`}`}
      styles={{ content: { padding: 0 }, body: { padding: 0 } }}
      onCancel={() => {
        if (!busy) onClose();
      }}
    >
      <div className="tag-binding-modal-header">
        <div>
          <strong>AI提示词</strong>
          <Typography.Text type="secondary" style={{ marginLeft: 10, fontSize: 12 }}>
            {dataset?.name || `数据集 #${dataset?.id || ''}`} · 契约 v{contract?.revision ?? '-'}
          </Typography.Text>
        </div>
        <Button type="text" className="tag-binding-modal-close" icon={<CloseOutlined />} onClick={() => !busy && onClose()} aria-label="关闭" />
      </div>
      <div style={{ padding: 20, background: '#f7f9fc' }}>
        {!contract ? (
          <Alert type="warning" showIcon message="当前数据集尚未建立标签契约" description="请先绑定标签契约，再生成 AI 提示词。" />
        ) : !bboxPromptSupported ? (
          <Alert
            type="info"
            showIcon
            message="当前契约暂不支持目标检测提示词"
            description="当前版本只为 bbox 标签契约编译目标框提示词；全局标签维度仍由任务配置驱动。"
          />
        ) : (
          <>
            <Descriptions size="small" column={{ xs: 1, sm: 2, md: 4 }} bordered style={{ marginBottom: 14, background: '#fff' }}>
              <Descriptions.Item label="契约">#{contract.id} · v{contract.revision}</Descriptions.Item>
              <Descriptions.Item label="标注类型">{contract.annotation_kind || 'bbox'}</Descriptions.Item>
              <Descriptions.Item label="范围">{contract.label_scope_mode || 'FULL'}</Descriptions.Item>
              <Descriptions.Item label="提示词状态">
                <Tag color={isPublished(selectedPrompt?.status) ? 'green' : selectedPrompt ? 'blue' : 'default'}>{statusLabel(selectedPrompt?.status)}</Tag>
              </Descriptions.Item>
            </Descriptions>
            {warnings.length > 0 && <Alert type="warning" showIcon message="提示词需要复核" description={<ul style={{ margin: 0, paddingLeft: 18 }}>{warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>} style={{ marginBottom: 14 }} />}
            <EditorGrid>
              <WorkPanel>
                <h3><LockOutlined style={{ marginRight: 6 }} />契约约束</h3>
                <span className="panel-hint">这些标签来自契约快照；编辑时必须保留契约类别，模型输出也会由服务端按契约过滤。</span>
                {promptQuery.isLoading ? <Spin /> : labels.length ? (
                  <LabelList>
                    {labels.map((label, index) => (
                      <LabelRow key={`${label.label_id || label.label_value || index}`}>
                        <span className="label-index">{index + 1}</span>
                        <span className="label-name">{label.label_key || '未命名标签'}</span>
                        <code>{label.label_value || '-'}</code>
                      </LabelRow>
                    ))}
                  </LabelList>
                ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="契约没有标签" />}
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label="标签集合摘要">{contract.label_set_signature ? `${contract.label_set_signature.slice(0, 12)}…` : '未提供'}</Descriptions.Item>
                  <Descriptions.Item label="类别顺序摘要">{contract.class_order_signature ? `${contract.class_order_signature.slice(0, 12)}…` : '未提供'}</Descriptions.Item>
                </Descriptions>
                <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
                  模型只允许输出以上稳定英文值；契约版本变化后，当前提示词会自动失效。
                </Typography.Text>
              </WorkPanel>
              <WorkPanel>
                <h3><FileTextOutlined style={{ marginRight: 6 }} />提示词编辑</h3>
                <span className="panel-hint">生成内容可保存为草稿并发布。契约标签、工具和输出格式由服务端校验。</span>
                <Space wrap style={{ marginBottom: 12 }}>
                  <Tag icon={<CloudOutlined />} color="blue">火山方舟</Tag>
                  <Tag>目标框检测</Tag>
                  <Input size="small" aria-label="模型名称" placeholder="模型名称（可选）" value={model} onChange={(event) => setModel(event.target.value)} style={{ width: 180 }} />
                  <Input size="small" aria-label="提示词模板" value={profileKey} onChange={(event) => setProfileKey(event.target.value)} style={{ width: 220 }} />
                </Space>
                <PromptEditor aria-label="AI提示词编辑器" value={promptTextValue} onChange={(event) => setPromptTextValue(event.target.value)} placeholder="点击“根据契约生成”创建提示词…" spellCheck={false} />
                <Space wrap style={{ marginTop: 12 }}>
                  <Button aria-label="根据契约生成" icon={<ReloadOutlined />} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()} disabled={!contract || busy}>根据契约生成</Button>
                  <Button aria-label="保存草稿" type="primary" icon={<SaveOutlined />} loading={saveMutation.isPending} onClick={() => saveMutation.mutate()} disabled={!contract || !promptTextValue.trim() || busy}>保存草稿</Button>
                  <Button aria-label="发布" title={promptDirty ? '请先保存当前修改，再发布' : undefined} icon={<SendOutlined />} loading={publishMutation.isPending} onClick={() => selectedPrompt?.id && publishMutation.mutate(Number(selectedPrompt.id))} disabled={!canPublish}>发布</Button>
                  {isPublished(selectedPrompt?.status) && <Tag color="green" icon={<CheckCircleOutlined />}>当前生效</Tag>}
                </Space>
                {selectedPrompt?.prompt_sha256 && <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 11 }}>摘要：{selectedPrompt.prompt_sha256}</Typography.Text>}
              </WorkPanel>
            </EditorGrid>
            <Alert type="info" showIcon icon={<PlayCircleOutlined />} message="发布后 AI 标注会按契约 ID、revision、操作类型和模型精确读取该提示词；不会使用其他契约的提示词。" style={{ marginTop: 14 }} />
          </>
        )}
      </div>
    </Modal>
  );
}
