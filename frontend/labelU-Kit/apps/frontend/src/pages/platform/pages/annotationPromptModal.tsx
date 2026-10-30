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
  UploadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Input, Modal, Space, Spin, Tag, Typography, message } from 'antd';
import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
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
  rendered_prompt?: string | null;
  compiler_version?: string | null;
};

type PromptTrialResult = {
  status?: string;
  prompt?: string | null;
  rendered_prompt?: string | null;
  effective_prompt?: string | null;
  raw_response?: unknown;
  model_response?: unknown;
  normalized_result?: unknown;
  annotations?: unknown;
  result?: unknown;
  latency_ms?: number | null;
  image_width?: number | null;
  image_height?: number | null;
  model_image_width?: number | null;
  model_image_height?: number | null;
  prompt_binding?: Record<string, unknown>;
  error?: string | null;
};

type PromptTrialRequestContext = {
  prompt: string;
  model: string;
  profileKey: string;
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
  grid-template-columns: minmax(220px, 0.78fr) minmax(360px, 1.22fr) minmax(300px, 1fr);
  gap: 16px;
  align-items: start;

  @media (max-width: 1180px) {
    grid-template-columns: minmax(220px, 0.8fr) minmax(360px, 1.2fr);
  }

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

const PromptPreview = styled.pre`
  min-height: 430px;
  max-height: 560px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid #e5eaf1;
  border-radius: 4px;
  background: #f8fafc;
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
`;

const HistoryList = styled.div`
  display: grid;
  gap: 6px;
  max-height: 230px;
  overflow: auto;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #edf0f5;
`;

const HistoryRow = styled.div<{ $active?: boolean }>`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
  padding: 7px 8px;
  border: 1px solid ${({ $active }) => ($active ? '#91caff' : '#edf0f5')};
  border-radius: 4px;
  background: ${({ $active }) => ($active ? '#e6f4ff' : '#fff')};

  .history-main {
    min-width: 0;
  }

  .history-title {
    display: block;
    overflow: hidden;
    color: #334155;
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .history-meta {
    display: block;
    margin-top: 2px;
    color: #94a3b8;
    font-size: 11px;
  }
`;

const TrialResult = styled.pre`
  max-height: 260px;
  margin: 10px 0 0;
  padding: 10px;
  overflow: auto;
  border: 1px solid #dbeafe;
  border-radius: 4px;
  background: #eff6ff;
  color: #1e3a8a;
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
`;

const TrialVisualPanel = styled.div`
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #edf0f5;
`;

const TrialVisualHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
`;

const TrialImageStage = styled.div`
  max-height: 420px;
  overflow: auto;
  border: 1px solid #dbe3ee;
  border-radius: 4px;
  background: #0f172a;
  cursor: zoom-in;

  &:focus-visible {
    outline: 3px solid #1677ff;
    outline-offset: 2px;
  }
`;

const TrialImageCanvas = styled.div`
  position: relative;
  width: 100%;
  line-height: 0;
`;

const TrialImage = styled.img`
  display: block;
  width: 100%;
  height: auto;
`;

const TrialZoomViewport = styled.div`
  display: flex;
  width: 100%;
  height: min(78vh, 760px);
  align-items: center;
  justify-content: center;
  overflow: auto;
  padding: 12px;
  box-sizing: border-box;
  background: #0f172a;
`;

const TrialZoomCanvas = styled.div`
  position: relative;
  display: inline-block;
  width: fit-content;
  max-width: 100%;
  line-height: 0;
`;

const TrialZoomImage = styled.img`
  display: block;
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: min(720px, calc(78vh - 24px));
`;

const TrialBox = styled.div<{
  $left: string;
  $top: string;
  $width: string;
  $height: string;
  $color: string;
}>`
  position: absolute;
  left: ${({ $left }) => $left};
  top: ${({ $top }) => $top};
  width: ${({ $width }) => $width};
  height: ${({ $height }) => $height};
  box-sizing: border-box;
  border: 2px solid ${({ $color }) => $color};
  pointer-events: none;
`;

const TrialBoxLabel = styled.span<{ $color: string }>`
  position: absolute;
  top: 2px;
  left: 2px;
  max-width: 180px;
  overflow: hidden;
  padding: 2px 4px;
  color: #fff;
  background: ${({ $color }) => $color};
  font-size: 11px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const TrialVisualEmpty = styled.div`
  padding: 22px 12px;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
`;

function unwrap(value: any): any {
  const body = value?.data ?? value;
  return body?.data ?? body;
}

function contractPayload(value: any): PromptContract | null {
  const data = unwrap(value);
  if (!data || Array.isArray(data) || typeof data !== 'object') return null;
  const id = Number(data.id);
  const revision = Number(data.revision);
  if (!Number.isFinite(id) || id <= 0 || !Number.isFinite(revision) || revision <= 0) return null;
  return data as PromptContract;
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
  if (normalized === 'invalidated') return '已失效（INVALIDATED）';
  if (normalized === 'superseded') return '已替代（SUPERSEDED）';
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

function formatDate(value?: string | null) {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function renderPromptPreview(prompt: string, imageWidth = 1920, imageHeight = 1080) {
  return String(prompt || '')
    .replace(/\{\{image_width\}\}/g, String(imageWidth))
    .replace(/\{\{image_height\}\}/g, String(imageHeight));
}

function trialResultPayload(value: any): PromptTrialResult {
  const data = unwrap(value);
  if (data?.trial && typeof data.trial === 'object') return data.trial;
  return (data || {}) as PromptTrialResult;
}

function trialResultBody(result: PromptTrialResult) {
  return result.normalized_result ?? result.annotations ?? result.result ?? result.model_response ?? result.raw_response ?? result;
}

type TrialBoundingBox = {
  id?: string | number;
  x: number;
  y: number;
  width: number;
  height: number;
  label?: string;
  score?: string;
};

function asRecord(value: unknown): Record<string, any> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : null;
}

function finiteNumber(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function trialBoundingBoxes(result: PromptTrialResult | null): TrialBoundingBox[] {
  if (!result) return [];
  const normalized = asRecord(result.normalized_result);
  const annotations = asRecord(normalized?.annotations) || normalized;
  const rectBlock = asRecord(annotations?.rectTool);
  const rectItems = rectBlock ? rectBlock.result : null;
  let items: unknown[] = Array.isArray(rectItems) ? rectItems : [];

  // Keep the preview tolerant of the compact shape used by older trial
  // responses and of an un-normalized model response.
  if (!items.length) {
    const compactBlock = asRecord(result.annotations)?.rectTool || asRecord(result.result)?.rectTool;
    const compactItems = asRecord(compactBlock);
    const compactResult = compactItems ? compactItems.result : null;
    if (Array.isArray(compactResult)) items = compactResult;
  }
  if (!items.length) {
    const rawResults = asRecord(result.model_response)?.results;
    if (Array.isArray(rawResults)) {
      items = rawResults.filter((item) => asRecord(item)?.toolName === 'rectTool');
    }
  }

  return items.flatMap((item) => {
    const row = asRecord(item);
    const payload = asRecord(row?.result) || row;
    if (!payload) return [];
    const x = finiteNumber(payload.x);
    const y = finiteNumber(payload.y);
    const width = finiteNumber(payload.width);
    const height = finiteNumber(payload.height);
    if (x === null || y === null || width === null || height === null || width <= 0 || height <= 0) return [];
    const attributes = asRecord(row?.attributes);
    const score = finiteNumber(attributes?.score ?? row?.score);
    return [{
      id: row?.id,
      x,
      y,
      width,
      height,
      label: String(row?.label || payload.label || '').trim() || undefined,
      score: score === null ? undefined : score.toFixed(3),
    }];
  });
}

function percentWithin(value: number, total: number) {
  if (!Number.isFinite(total) || total <= 0) return '0%';
  return `${Math.max(0, Math.min(100, (value / total) * 100))}%`;
}

export function AnnotationPromptModal({ dataset, contract, open, onClose }: AnnotationPromptModalProps) {
  const queryClient = useQueryClient();
  const [promptTextValue, setPromptTextValue] = useState('');
  const [selectedPrompt, setSelectedPrompt] = useState<AnnotationPrompt | null>(null);
  const [model, setModel] = useState('');
  const [profileKey, setProfileKey] = useState(DEFAULT_PROFILE_KEY);
  const [historyPrompt, setHistoryPrompt] = useState<AnnotationPrompt | null>(null);
  const [trialImage, setTrialImage] = useState<File | null>(null);
  const [trialImageUrl, setTrialImageUrl] = useState<string | null>(null);
  const [trialNaturalSize, setTrialNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [trialResult, setTrialResult] = useState<PromptTrialResult | null>(null);
  const [trialRequestContext, setTrialRequestContext] = useState<PromptTrialRequestContext | null>(null);
  const [trialZoomOpen, setTrialZoomOpen] = useState(false);
  const trialImageInputRef = useRef<HTMLInputElement | null>(null);
  const initializedContextRef = useRef<string | null>(null);

  // The parent may have loaded the contract before a taxonomy administrator
  // changes a label. Refresh on every modal mount so the editor never starts
  // from an immutable, stale contract snapshot.
  const currentContractQuery = useQuery({
    queryKey: ['dataset-annotation-current-contract', dataset?.id],
    queryFn: async () => {
      const response = await request.get(`/v1/datasets/${dataset?.id}/label-contracts/current`);
      return contractPayload(response);
    },
    enabled: open && Boolean(dataset?.id),
    staleTime: 0,
    refetchOnMount: 'always',
    retry: false,
  });
  // A failed refresh must not silently resurrect the immutable contract passed
  // by the parent. That snapshot may contain the old label vocabulary.
  const activeContract = currentContractQuery.data || null;
  const refreshingContract = Boolean(open && currentContractQuery.isFetching && !currentContractQuery.isError);
  const promptContextKey = `${dataset?.id ?? ''}:${activeContract?.id ?? ''}:${activeContract?.revision ?? ''}`;

  const refreshLatestContract = async () => {
    const result = await currentContractQuery.refetch();
    if (result.error) {
      throw new Error('当前数据集标签契约读取失败，请刷新后重试');
    }
    const latest = result.data;
    if (!latest) throw new Error('当前数据集标签契约不可用，请刷新后重试');
    return latest;
  };

  useEffect(() => {
    if (!trialImage) {
      setTrialImageUrl(null);
      return undefined;
    }
    setTrialImageUrl(null);
    let cancelled = false;
    let objectUrl: string | null = null;
    if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
      objectUrl = URL.createObjectURL(trialImage);
      setTrialImageUrl(objectUrl);
      return () => {
        cancelled = true;
        if (objectUrl && typeof URL.revokeObjectURL === 'function') {
          URL.revokeObjectURL(objectUrl);
        }
      };
    }

    if (typeof FileReader === 'undefined') return undefined;
    const reader = new FileReader();
    reader.onload = () => {
      if (!cancelled && typeof reader.result === 'string') setTrialImageUrl(reader.result);
    };
    reader.onerror = () => {
      if (!cancelled) setTrialImageUrl(null);
    };
    reader.readAsDataURL(trialImage);
    return () => {
      cancelled = true;
      reader.abort();
    };
  }, [trialImage]);

  const promptQuery = useQuery({
    queryKey: ['dataset-annotation-prompts', dataset?.id, activeContract?.id, activeContract?.revision],
    queryFn: async () =>
      request.get(`/v1/datasets/${dataset?.id}/label-contracts/${activeContract?.revision}/ai-prompts`),
    enabled: open && !refreshingContract && Boolean(dataset?.id) && Boolean(activeContract?.id) && Boolean(activeContract?.revision),
    staleTime: 5_000,
    retry: false,
  });

  const historyQuery = useQuery({
    queryKey: ['dataset-annotation-prompt-history', dataset?.id],
    queryFn: async () => request.get(`/v1/datasets/${dataset?.id}/ai-prompts/history`),
    enabled: open && !refreshingContract && Boolean(dataset?.id) && Boolean(activeContract?.id),
    staleTime: 5_000,
    retry: false,
  });

  const prompts = useMemo(() => promptRows(promptQuery.data), [promptQuery.data]);
  const bboxPrompts = useMemo(
    () => prompts.filter((item) => !item.operation || item.operation === 'bbox_detection'),
    [prompts],
  );
  const historyPrompts = useMemo(() => promptRows(historyQuery.data), [historyQuery.data]);
  const historyBboxPrompts = useMemo(
    () => historyPrompts.filter((item) => !item.operation || item.operation === 'bbox_detection'),
    [historyPrompts],
  );
  // The history endpoint includes old contract revisions and INVALIDATED rows.
  // Keep the current revision list visible if an older backend does not expose
  // that endpoint yet, but make the degraded state explicit in the UI.
  const visibleHistoryPrompts = historyQuery.isError ? bboxPrompts : historyBboxPrompts;

  useEffect(() => {
    if (!open) {
      initializedContextRef.current = null;
      setPromptTextValue('');
      setSelectedPrompt(null);
      setModel('');
      setProfileKey(DEFAULT_PROFILE_KEY);
      setHistoryPrompt(null);
      setTrialImage(null);
      setTrialImageUrl(null);
      setTrialNaturalSize(null);
      setTrialResult(null);
      setTrialRequestContext(null);
      setTrialZoomOpen(false);
      if (trialImageInputRef.current) trialImageInputRef.current.value = '';
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
    mutationFn: async () => {
      const latest = await refreshLatestContract();
      return request.post(`/v1/datasets/${dataset?.id}/label-contracts/${latest.revision}/ai-prompts/preview`, {
        operation: 'bbox_detection',
        provider_key: 'volcengine',
        model_name: model.trim() || null,
        profile_key: profileKey.trim() || DEFAULT_PROFILE_KEY,
        profile_version: '1',
      });
    },
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
    mutationFn: async () => {
      const latest = await refreshLatestContract();
      const selectedMatchesLatest = Boolean(
        selectedPrompt?.id && Number(selectedPrompt.contract_revision) === Number(latest.revision),
      );
      return request.post(`/v1/datasets/${dataset?.id}/label-contracts/${latest.revision}/ai-prompts`, {
        operation: 'bbox_detection',
        provider_key: 'volcengine',
        model_name: model.trim() || null,
        profile_key: profileKey.trim() || DEFAULT_PROFILE_KEY,
        profile_version: '1',
        effective_prompt: promptTextValue,
        edited_prompt: promptTextValue,
        expected_label_set_signature: latest.label_set_signature || null,
        expected_class_order_signature: latest.class_order_signature || null,
        // A published snapshot is immutable. Saving after editing it creates
        // a new draft; only an existing draft may be updated in place.
        expected_prompt_id: selectedMatchesLatest && selectedPrompt && !isPublished(selectedPrompt.status)
          ? selectedPrompt.id || null
          : null,
      });
    },
    onSuccess: async (response) => {
      const saved = unwrap(response);
      setSelectedPrompt(saved);
      setPromptTextValue(promptText(saved));
      await queryClient.invalidateQueries({ queryKey: ['dataset-annotation-prompts', dataset?.id] });
      await queryClient.invalidateQueries({ queryKey: ['dataset-annotation-prompt-history', dataset?.id] });
      message.success('提示词草稿已保存');
    },
    onError: (error) => message.error(errorMessage(error, '提示词草稿保存失败')),
  });

  const publishMutation = useMutation({
    mutationFn: async (promptId: number) => {
      const latest = await refreshLatestContract();
      if (Number(selectedPrompt?.contract_revision) !== Number(latest.revision)) {
        throw new Error('标签契约已更新，请重新加载当前提示词后再发布');
      }
      return request.post(`/v1/datasets/${dataset?.id}/label-contracts/${latest.revision}/ai-prompts/${promptId}/publish`, {
        expected_contract_revision: latest.revision,
        expected_prompt_sha256: selectedPrompt?.prompt_sha256 || null,
      });
    },
    onSuccess: async (response) => {
      const published = unwrap(response);
      setSelectedPrompt(published);
      setPromptTextValue(promptText(published));
      await queryClient.invalidateQueries({ queryKey: ['dataset-annotation-prompts', dataset?.id] });
      await queryClient.invalidateQueries({ queryKey: ['dataset-annotation-prompt-history', dataset?.id] });
      message.success('提示词已发布，AI标注将使用该契约版本');
    },
    onError: (error) => message.error(errorMessage(error, '提示词发布失败，请刷新后重试')),
  });

  const trialMutation = useMutation({
    mutationFn: async (image: File) => {
      const latest = await refreshLatestContract();
      const formData = new FormData();
      formData.append('image', image);
      if (selectedPrompt?.id && Number(selectedPrompt.contract_revision) === Number(latest.revision)) {
        formData.append('prompt_id', String(selectedPrompt.id));
      }
      formData.append('prompt_override', promptTextValue);
      // Keep prompt_text as a compatibility alias for older trial handlers.
      formData.append('prompt_text', promptTextValue);
      formData.append('operation', 'bbox_detection');
      formData.append('provider_key', 'volcengine');
      formData.append('model_name', model.trim());
      formData.append('profile_key', profileKey.trim() || DEFAULT_PROFILE_KEY);
      formData.append('profile_version', '1');
      return request.post(
        `/v1/datasets/${dataset?.id}/label-contracts/${latest.revision}/ai-prompts/trial`,
        formData,
        { timeout: 120_000 },
      );
    },
    onSuccess: (response) => {
      setTrialResult(trialResultPayload(response));
      message.success('试运行完成，结果未写入正式标注');
    },
    onError: (error) => {
      setTrialResult({ status: 'FAILED', error: errorMessage(error, '试运行失败，请检查图片和模型配置') });
      message.error(errorMessage(error, '试运行失败，请检查图片和模型配置'));
    },
  });

  const labels = activeContract?.labels || [];
  const warnings = [
    ...(selectedPrompt?.warnings ?? []),
    ...(labels.some((label) => !String(label.label_key || '').trim()) ? ['部分标签缺少中文显示名称'] : []),
  ];
  const busy = previewMutation.isPending || saveMutation.isPending || publishMutation.isPending || trialMutation.isPending;
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
  const bboxPromptSupported = supportsBboxPrompt(activeContract);
  const trialBoxes = useMemo(() => trialBoundingBoxes(trialResult), [trialResult]);
  const trialImageWidth = trialResult?.image_width || trialNaturalSize?.width || 0;
  const trialImageHeight = trialResult?.image_height || trialNaturalSize?.height || 0;
  const trialPromptMatchesCurrentEditor = Boolean(
    trialResult?.status === 'COMPLETED' &&
    trialResult.rendered_prompt &&
    trialRequestContext?.prompt === promptTextValue &&
    trialRequestContext.model === currentModel &&
    trialRequestContext.profileKey === currentProfileKey,
  );
  const previewWidth = trialResult?.model_image_width || trialResult?.image_width || 1920;
  const previewHeight = trialResult?.model_image_height || trialResult?.image_height || 1080;
  const finalPromptPreview = trialPromptMatchesCurrentEditor && trialResult?.rendered_prompt
    ? trialResult.rendered_prompt
    : renderPromptPreview(promptTextValue, previewWidth, previewHeight);
  const handleTrialImageChange = (event: ChangeEvent<HTMLInputElement>) => {
    const image = event.target.files?.[0] || null;
    setTrialImage(image);
    setTrialNaturalSize(null);
    setTrialResult(null);
    setTrialRequestContext(null);
    setTrialZoomOpen(false);
  };
  const handleTrialRun = () => {
    if (!trialImage || !promptTextValue.trim() || busy) return;
    setTrialRequestContext({ prompt: promptTextValue, model: currentModel, profileKey: currentProfileKey });
    trialMutation.mutate(trialImage);
  };

  return (
    <>
      <Modal
      title={null}
      open={open}
      centered
      destroyOnClose
      width={1480}
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
            {dataset?.name || `数据集 #${dataset?.id || ''}`} · 契约 v{activeContract?.revision ?? '-'}
          </Typography.Text>
        </div>
        <Button type="text" className="tag-binding-modal-close" icon={<CloseOutlined />} onClick={() => !busy && onClose()} aria-label="关闭" />
      </div>
      <div style={{ padding: 20, background: '#f7f9fc' }}>
        {refreshingContract ? (
          <Alert type="info" showIcon message="正在读取最新标签契约" description="标签库可能刚刚更新，提示词操作会绑定到最新 revision。" />
        ) : !activeContract ? (
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
              <Descriptions.Item label="契约">#{activeContract.id} · v{activeContract.revision}</Descriptions.Item>
              <Descriptions.Item label="标注类型">{activeContract.annotation_kind || 'bbox'}</Descriptions.Item>
              <Descriptions.Item label="范围">{activeContract.label_scope_mode || 'FULL'}</Descriptions.Item>
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
                  <Descriptions.Item label="标签集合摘要">{activeContract.label_set_signature ? `${activeContract.label_set_signature.slice(0, 12)}…` : '未提供'}</Descriptions.Item>
                  <Descriptions.Item label="类别顺序摘要">{activeContract.class_order_signature ? `${activeContract.class_order_signature.slice(0, 12)}…` : '未提供'}</Descriptions.Item>
                </Descriptions>
                <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12, fontSize: 12 }}>
                  模型只允许输出以上稳定英文值；契约版本变化后，当前提示词会自动失效。
                </Typography.Text>
                <Typography.Text strong style={{ display: 'block', marginTop: 16, fontSize: 13 }}>
                  历史版本
                </Typography.Text>
                {historyQuery.isError && (
                  <Typography.Text type="warning" style={{ display: 'block', marginTop: 4, fontSize: 11 }}>
                    历史接口暂不可用，当前仅显示本契约版本。
                  </Typography.Text>
                )}
                <HistoryList aria-label="提示词历史版本列表">
                  {historyQuery.isLoading ? <Spin size="small" /> : visibleHistoryPrompts.length ? visibleHistoryPrompts.map((item, index) => (
                    <HistoryRow key={item.id ?? `history-${index}`} $active={Number(item.id) === Number(selectedPrompt?.id)}>
                      <div className="history-main">
                        <span className="history-title">提示词 #{item.id ?? '预览'} · 契约 v{item.contract_revision ?? '-'}</span>
                        <span className="history-meta"><Tag color={String(item.status || '').toUpperCase() === 'INVALIDATED' ? 'red' : undefined}>{statusLabel(item.status)}</Tag></span>
                        <span className="history-meta">{formatDate(item.updated_at || item.published_at || item.created_at)}</span>
                      </div>
                      <Button
                        type="link"
                        size="small"
                        aria-label={`查看提示词版本 ${item.id ?? '预览'}`}
                        onClick={() => setHistoryPrompt(item)}
                      >
                        查看
                      </Button>
                    </HistoryRow>
                  )) : <Typography.Text type="secondary">暂无历史版本</Typography.Text>}
                </HistoryList>
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
                  <Button aria-label="根据契约生成" icon={<ReloadOutlined />} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()} disabled={!activeContract || refreshingContract || busy}>根据契约生成</Button>
                  <Button aria-label="保存草稿" type="primary" icon={<SaveOutlined />} loading={saveMutation.isPending} onClick={() => saveMutation.mutate()} disabled={!activeContract || refreshingContract || !promptTextValue.trim() || busy}>保存草稿</Button>
                  <Button aria-label="发布" title={promptDirty ? '请先保存当前修改，再发布' : undefined} icon={<SendOutlined />} loading={publishMutation.isPending} onClick={() => selectedPrompt?.id && publishMutation.mutate(Number(selectedPrompt.id))} disabled={!canPublish}>发布</Button>
                  {isPublished(selectedPrompt?.status) && <Tag color="green" icon={<CheckCircleOutlined />}>当前生效</Tag>}
                </Space>
                {selectedPrompt?.prompt_sha256 && <Typography.Text type="secondary" style={{ display: 'block', marginTop: 10, fontSize: 11 }}>摘要：{selectedPrompt.prompt_sha256}</Typography.Text>}
                <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid #edf0f5' }}>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 6 }}>真实图片试运行</Typography.Text>
                  <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 10, fontSize: 12 }}>
                    使用当前编辑内容调用一次模型，结果不会写入正式标注。
                  </Typography.Text>
                  <input
                    ref={trialImageInputRef}
                    type="file"
                    accept="image/*"
                    aria-label="试运行图片"
                    style={{ display: 'none' }}
                    onChange={handleTrialImageChange}
                  />
                  <Space wrap>
                    <Button
                      aria-label="选择试运行图片"
                      icon={<UploadOutlined />}
                      onClick={() => trialImageInputRef.current?.click()}
                      disabled={busy}
                    >
                      选择图片
                    </Button>
                    <Button
                      aria-label="试运行一张图片"
                      icon={<PlayCircleOutlined />}
                      loading={trialMutation.isPending}
                      onClick={handleTrialRun}
                      disabled={!trialImage || !promptTextValue.trim() || busy}
                    >
                      试运行一张图片
                    </Button>
                    {trialImage && <Typography.Text type="secondary" style={{ maxWidth: 220 }} ellipsis={{ tooltip: trialImage.name }}>{trialImage.name}</Typography.Text>}
                  </Space>
                </div>
              </WorkPanel>
              <WorkPanel>
                <h3><CloudOutlined style={{ marginRight: 6 }} />最终发送预览</h3>
                <span className="panel-hint">这里显示当前编辑内容经过图片尺寸变量替换后的模型输入文本。</span>
                <PromptPreview aria-label="最终发送给模型的提示词">
                  {finalPromptPreview || '暂无提示词，请先根据契约生成或编辑提示词。'}
                </PromptPreview>
                {trialMutation.isPending && <div style={{ marginTop: 12 }}><Spin size="small" /> <Typography.Text type="secondary" style={{ marginLeft: 8 }}>正在调用模型…</Typography.Text></div>}
                {trialImageUrl && (
                  <TrialVisualPanel aria-label="试运行图片标注预览">
                    <TrialVisualHeader>
                      <Typography.Text strong>试运行图片标注</Typography.Text>
                      {trialMutation.isPending ? (
                        <Tag color="processing">识别中…</Tag>
                      ) : trialResult?.status === 'COMPLETED' ? (
                        <Tag color={trialBoxes.length ? 'green' : 'default'} aria-label="试运行标注框数量">
                          共 {trialBoxes.length} 个拉框
                        </Tag>
                      ) : trialResult?.status === 'FAILED' ? (
                        <Tag color="red">未生成标注</Tag>
                      ) : (
                        <Tag>等待试运行</Tag>
                      )}
                    </TrialVisualHeader>
                    <TrialImageStage
                      role="button"
                      tabIndex={0}
                      aria-label="放大查看试运行图片"
                      data-testid="trial-image-zoom-trigger"
                      onClick={() => trialImageUrl && setTrialZoomOpen(true)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          if (trialImageUrl) setTrialZoomOpen(true);
                        }
                      }}
                    >
                      <TrialImageCanvas>
                        <TrialImage
                          src={trialImageUrl}
                          alt={trialImage?.name || '试运行图片'}
                          onLoad={(event) => {
                            const image = event.currentTarget;
                            setTrialNaturalSize({ width: image.naturalWidth, height: image.naturalHeight });
                          }}
                        />
                        {trialImageWidth > 0 && trialImageHeight > 0 && trialBoxes.map((box, index) => {
                          const color = '#1677ff';
                          const label = box.label || `目标 ${index + 1}`;
                          return (
                            <TrialBox
                              key={`${box.id || 'trial-box'}-${index}`}
                              data-testid="trial-bounding-box"
                              $left={percentWithin(box.x, trialImageWidth)}
                              $top={percentWithin(box.y, trialImageHeight)}
                              $width={percentWithin(box.width, trialImageWidth)}
                              $height={percentWithin(box.height, trialImageHeight)}
                              $color={color}
                            >
                              <TrialBoxLabel data-testid="trial-bounding-box-label" $color={color}>
                                {box.score ? `${label} · ${box.score}` : label}
                              </TrialBoxLabel>
                            </TrialBox>
                          );
                        })}
                      </TrialImageCanvas>
                    </TrialImageStage>
                    {trialResult?.status === 'COMPLETED' && trialBoxes.length === 0 && (
                      <TrialVisualEmpty>模型未检测到契约内目标，当前图片标注了 0 个拉框。</TrialVisualEmpty>
                    )}
                  </TrialVisualPanel>
                )}
                {trialResult && !trialMutation.isPending && (
                  <div style={{ marginTop: 14 }}>
                    <Space size={8} wrap>
                      <Tag color={trialResult.status === 'FAILED' ? 'red' : 'green'}>{trialResult.status === 'FAILED' ? '试运行失败' : '试运行完成'}</Tag>
                      {trialResult.latency_ms != null && <Typography.Text type="secondary">耗时 {trialResult.latency_ms} ms</Typography.Text>}
                      {trialResult.image_width && trialResult.image_height && <Typography.Text type="secondary">图片 {trialResult.image_width}×{trialResult.image_height}</Typography.Text>}
                    </Space>
                    <TrialResult aria-label="试运行结果">
                      {trialResult.error || JSON.stringify(trialResultBody(trialResult), null, 2)}
                    </TrialResult>
                  </div>
                )}
              </WorkPanel>
            </EditorGrid>
            <Alert type="info" showIcon icon={<PlayCircleOutlined />} message="发布后 AI 标注会按契约 ID、revision、操作类型和模型精确读取该提示词；不会使用其他契约的提示词。" style={{ marginTop: 14 }} />
          </>
        )}
      </div>
      </Modal>
      <Modal
        title="试运行图片标注（放大查看）"
        open={trialZoomOpen && Boolean(trialImageUrl)}
        centered
        width="min(1320px, calc(100vw - 32px))"
        footer={null}
        destroyOnClose
        onCancel={() => setTrialZoomOpen(false)}
      >
        <TrialZoomViewport data-testid="trial-image-zoom-view">
          {trialImageUrl && (
            <TrialZoomCanvas>
              <TrialZoomImage
                src={trialImageUrl}
                alt={trialImage?.name || '试运行图片放大预览'}
              />
              {trialImageWidth > 0 && trialImageHeight > 0 && trialBoxes.map((box, index) => {
                const color = '#1677ff';
                const label = box.label || `目标 ${index + 1}`;
                return (
                  <TrialBox
                    key={`zoom-${box.id || 'trial-box'}-${index}`}
                    data-testid="trial-zoom-bounding-box"
                    $left={percentWithin(box.x, trialImageWidth)}
                    $top={percentWithin(box.y, trialImageHeight)}
                    $width={percentWithin(box.width, trialImageWidth)}
                    $height={percentWithin(box.height, trialImageHeight)}
                    $color={color}
                  >
                    <TrialBoxLabel data-testid="trial-zoom-bounding-box-label" $color={color}>
                      {box.score ? `${label} · ${box.score}` : label}
                    </TrialBoxLabel>
                  </TrialBox>
                );
              })}
            </TrialZoomCanvas>
          )}
        </TrialZoomViewport>
        <Space size={8} wrap style={{ marginTop: 10 }}>
          <Tag color={trialBoxes.length ? 'green' : 'default'}>
            共 {trialBoxes.length} 个拉框
          </Tag>
          {trialImageWidth > 0 && trialImageHeight > 0 && (
            <Typography.Text type="secondary">
              图片 {trialImageWidth}×{trialImageHeight}
            </Typography.Text>
          )}
        </Space>
      </Modal>
      <Modal
        title={`历史提示词版本 #${historyPrompt?.id ?? ''}`}
        open={Boolean(historyPrompt)}
        centered
        width={760}
        footer={<Button onClick={() => setHistoryPrompt(null)}>关闭</Button>}
        onCancel={() => setHistoryPrompt(null)}
      >
        {historyPrompt && (
          <>
            <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered style={{ marginBottom: 12 }}>
              <Descriptions.Item label="状态"><Tag>{statusLabel(historyPrompt.status)}</Tag></Descriptions.Item>
              <Descriptions.Item label="契约 revision">v{historyPrompt.contract_revision ?? activeContract?.revision ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="模型">{historyPrompt.model_name || '通用模型绑定'}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{formatDate(historyPrompt.updated_at || historyPrompt.published_at || historyPrompt.created_at)}</Descriptions.Item>
              {historyPrompt.prompt_sha256 && <Descriptions.Item label="摘要" span={2}>{historyPrompt.prompt_sha256}</Descriptions.Item>}
            </Descriptions>
            <Input.TextArea aria-label="历史提示词内容" readOnly value={promptText(historyPrompt)} autoSize={{ minRows: 12, maxRows: 24 }} />
          </>
        )}
      </Modal>
    </>
  );
}
