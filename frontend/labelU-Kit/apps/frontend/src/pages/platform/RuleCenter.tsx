import { CheckOutlined, DisconnectOutlined, LinkOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Pagination,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { DeviceSignatureEvidence } from './DeviceSignatureEvidence';
import { ruleApiPaths } from './ruleContract';
import { ModuleStatus, moduleDataState } from './shared';

type RuleTemplate = {
  id: number;
  project_id: number;
  template_key: string;
  name: string;
  algorithm_key: string;
  description?: string | null;
  status: string;
};

type RuleVersion = {
  id: number;
  template_id: number;
  version_no: number;
  schema_version: string;
  config: Record<string, unknown>;
  config_sha256: string;
  status: string;
};

type RuleBinding = {
  id: number;
  project_id: number;
  device_id: string;
  rule_version_id: number;
  status: string;
  updated_at?: string | null;
};

type RuleAudit = {
  id: number;
  binding_id: number;
  device_id: string;
  operation: string;
  from_status?: string | null;
  to_status?: string | null;
  reason?: string | null;
  created_at?: string | null;
};

type RuleVersionAudit = {
  id: number;
  version_id: number;
  template_id: number;
  project_id: number;
  version_no: number;
  operation: string;
  from_status?: string | null;
  to_status?: string | null;
  reason?: string | null;
  created_at?: string | null;
};

type Algorithm = {
  algorithm_key: string;
  name?: string;
  display_name?: string;
  rule_schema?: Record<string, unknown>;
};

type EdgeAgent = {
  agent_id: string;
  name: string;
  status?: string | null;
};

const Panel = styled.section`
  padding: 16px;
  border: 1px solid #e3e8f0;
  border-radius: 6px;
  background: #fff;

  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
    flex-wrap: wrap;
  }

  .ant-table-wrapper {
    overflow-x: auto;
  }
`;

function listData<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[];
  const value = payload as { data?: unknown } | null;
  if (value && Array.isArray(value.data)) return value.data as T[];
  if (value && value.data && typeof value.data === 'object') {
    const nested = value.data as { data?: unknown };
    if (Array.isArray(nested.data)) return nested.data as T[];
  }
  return [];
}

type ListResult<T> = { rows: T[]; total: number };

function listResult<T>(payload: unknown): ListResult<T> {
  const rows = listData<T>(payload);
  const value = payload as {
    meta_data?: { total?: unknown };
    data?: { meta_data?: { total?: unknown } };
  } | null;
  const totalValue = value?.meta_data?.total ?? value?.data?.meta_data?.total;
  const total = Number.isFinite(Number(totalValue)) ? Number(totalValue) : rows.length;
  return { rows, total };
}

type RuleSelectOption = { label: string; value: string };

type RuleRemotePagedSelectProps = {
  value?: string;
  onChange?: (value: string | undefined) => void;
  options: RuleSelectOption[];
  total: number;
  page: number;
  pageSize: number;
  searchValue: string;
  loading?: boolean;
  placeholder: string;
  ariaLabel: string;
  countLabel: string;
  onSearch: (value: string) => void;
  onPageChange: (page: number) => void;
  onSelectionChange?: (value: string | undefined) => void;
};

/**
 * A server-backed selector shared by rule algorithms and edge Agents.
 * Pagination stays in the popup so a large registry does not make the form
 * unwieldy, and the controlled search value keeps query state separate from
 * the value selected in the form.
 */
function RuleRemotePagedSelect({
  value,
  onChange,
  options,
  total,
  page,
  pageSize,
  searchValue,
  loading = false,
  placeholder,
  ariaLabel,
  countLabel,
  onSearch,
  onPageChange,
  onSelectionChange,
}: RuleRemotePagedSelectProps) {
  const resolvedValue =
    value === undefined ? undefined : options.find((option) => String(option.value) === String(value))?.value ?? value;

  return (
    <Select
      allowClear
      showSearch
      filterOption={false}
      value={resolvedValue}
      searchValue={searchValue}
      options={options}
      loading={loading}
      placeholder={placeholder}
      aria-label={ariaLabel}
      notFoundContent={loading ? '加载中...' : '暂无匹配项'}
      dropdownRender={(menu) => (
        <>
          {menu}
          <div
            onMouseDown={(event) => event.preventDefault()}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              padding: '8px 10px 2px',
              borderTop: '1px solid #f0f0f0',
            }}
          >
            <span style={{ color: '#64748b', fontSize: 12, whiteSpace: 'nowrap' }}>
              {countLabel}：{total}
            </span>
            {total > pageSize && (
              <Pagination
                size="small"
                current={page + 1}
                pageSize={pageSize}
                total={total}
                showSizeChanger={false}
                showLessItems
                onChange={(nextPage) => onPageChange(nextPage - 1)}
              />
            )}
          </div>
        </>
      )}
      onSearch={onSearch}
      onChange={(nextValue: string | undefined) => {
        onSelectionChange?.(nextValue);
        onChange?.(nextValue);
        // Once a value is selected, start the next open on the first page.
        if (searchValue) onSearch('');
      }}
    />
  );
}

function apiMessage(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: { message?: string; detail?: string } } })
    ?.response;
  if (response?.status === 403) return '当前账号没有管理该项目规则的权限';
  if (response?.status === 409)
    return response.data?.message || response.data?.detail || '规则状态已变化，请刷新后重试';
  if (response?.status === 422) return response.data?.message || response.data?.detail || '规则配置未通过校验';
  return response?.data?.message || response?.data?.detail || '请求失败，请稍后重试';
}

function ruleConfigSummary(config: Record<string, unknown>) {
  const labels: Record<string, string> = {
    threshold: '阈值',
    confidence_threshold: '置信度阈值',
    score_threshold: '分数阈值',
    fence: '电子围栏',
    geofence: '电子围栏',
    crossing_line: '越界线',
    water_area: '水域区域',
    zones: '区域集合',
    cooldown_seconds: '冷却时间(秒)',
  };
  const entries = Object.entries(config || {}).filter(([, value]) => value !== null && value !== undefined);
  if (!entries.length) return <span style={{ color: '#94a3b8' }}>未配置规则参数</span>;
  return (
    <Descriptions size="small" column={2} bordered>
      {entries.slice(0, 12).map(([key, value]) => (
        <Descriptions.Item key={key} label={labels[key] || key}>
          {typeof value === 'object' ? JSON.stringify(value) : String(value)}
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}

function idempotencyKey(operation: string): string {
  const random =
    typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `7gai-rule-${operation}-${random}`;
}

const RULE_SELECTOR_PAGE_SIZE = 20;

export default function RuleCenter({ projectId }: { projectId?: number }) {
  const queryClient = useQueryClient();
  const [selectedTemplate, setSelectedTemplate] = useState<RuleTemplate | null>(null);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [bindingOpen, setBindingOpen] = useState(false);
  const [bindingAction, setBindingAction] = useState<'bind' | 'rollback'>('bind');
  const [templateForm] = Form.useForm();
  const [versionForm] = Form.useForm();
  const [bindingForm] = Form.useForm();
  const tabsHostRef = useRef<HTMLDivElement>(null);
  const [offline, setOffline] = useState(() => typeof navigator !== 'undefined' && !navigator.onLine);
  const [templateSearchDraft, setTemplateSearchDraft] = useState('');
  const [templateSearch, setTemplateSearch] = useState('');
  const [templatePage, setTemplatePage] = useState(0);
  const [templatePageSize, setTemplatePageSize] = useState(20);
  const [versionSearchDraft, setVersionSearchDraft] = useState('');
  const [versionSearch, setVersionSearch] = useState('');
  const [versionPage, setVersionPage] = useState(0);
  const [versionPageSize, setVersionPageSize] = useState(20);
  const [bindingSearchDraft, setBindingSearchDraft] = useState('');
  const [bindingSearch, setBindingSearch] = useState('');
  const [bindingPage, setBindingPage] = useState(0);
  const [bindingPageSize, setBindingPageSize] = useState(20);
  const [auditSearchDraft, setAuditSearchDraft] = useState('');
  const [auditSearch, setAuditSearch] = useState('');
  const [auditPage, setAuditPage] = useState(0);
  const [auditPageSize, setAuditPageSize] = useState(20);
  const [versionAuditSearchDraft, setVersionAuditSearchDraft] = useState('');
  const [versionAuditSearch, setVersionAuditSearch] = useState('');
  const [versionAuditPage, setVersionAuditPage] = useState(0);
  const [versionAuditPageSize, setVersionAuditPageSize] = useState(20);
  const [algorithmPage, setAlgorithmPage] = useState(0);
  const [algorithmSearch, setAlgorithmSearch] = useState('');
  const [algorithmSearchValue, setAlgorithmSearchValue] = useState('');
  const [selectedAlgorithmOption, setSelectedAlgorithmOption] = useState<Algorithm | null>(null);
  const [agentPage, setAgentPage] = useState(0);
  const [agentSearch, setAgentSearch] = useState('');
  const [agentSearchValue, setAgentSearchValue] = useState('');
  const [selectedAgentOption, setSelectedAgentOption] = useState<EdgeAgent | null>(null);

  useEffect(() => {
    setSelectedTemplate(null);
    setTemplateSearchDraft('');
    setTemplateSearch('');
    setTemplatePage(0);
    setVersionSearchDraft('');
    setVersionSearch('');
    setVersionPage(0);
    setBindingSearchDraft('');
    setBindingSearch('');
    setBindingPage(0);
    setAuditSearchDraft('');
    setAuditSearch('');
    setAuditPage(0);
    setVersionAuditSearchDraft('');
    setVersionAuditSearch('');
    setVersionAuditPage(0);
    setAlgorithmPage(0);
    setAlgorithmSearch('');
    setAlgorithmSearchValue('');
    setSelectedAlgorithmOption(null);
    setAgentPage(0);
    setAgentSearch('');
    setAgentSearchValue('');
    setSelectedAgentOption(null);
  }, [projectId]);

  useEffect(() => {
    const markOnline = () => setOffline(false);
    const markOffline = () => setOffline(true);
    window.addEventListener('online', markOnline);
    window.addEventListener('offline', markOffline);
    return () => {
      window.removeEventListener('online', markOnline);
      window.removeEventListener('offline', markOffline);
    };
  }, []);

  useEffect(() => {
    const moreButton = tabsHostRef.current?.querySelector<HTMLButtonElement>('.ant-tabs-nav-more');
    if (moreButton) {
      moreButton.setAttribute('aria-label', '更多规则页签');
      moreButton.setAttribute('title', '更多规则页签');
    }
  });

  const templatesQuery = useQuery({
    queryKey: ['rule-templates', projectId, templateSearch, templatePage, templatePageSize],
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number).templates, {
        params: { page: templatePage, size: templatePageSize, ...(templateSearch ? { q: templateSearch } : {}) },
      });
      return listResult<RuleTemplate>(response);
    },
    enabled: Boolean(projectId),
    staleTime: 15_000,
  });
  const algorithmsQuery = useQuery({
    queryKey: ['rule-algorithms', 'active', algorithmSearch, algorithmPage],
    queryFn: async () => {
      const response = await request.get('/v1/algorithms', {
        params: {
          status: 'ACTIVE',
          task_type: 'rule_template',
          page: algorithmPage,
          size: RULE_SELECTOR_PAGE_SIZE,
          ...(algorithmSearch ? { q: algorithmSearch } : {}),
        },
      });
      return listResult<Algorithm>(response);
    },
    enabled: Boolean(projectId),
    staleTime: 60_000,
  });
  const versionsQuery = useQuery({
    queryKey: ['rule-versions', selectedTemplate?.id, versionSearch, versionPage, versionPageSize],
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number, selectedTemplate?.id).versions as string, {
        params: { page: versionPage, size: versionPageSize, ...(versionSearch ? { q: versionSearch } : {}) },
      });
      return listResult<RuleVersion>(response);
    },
    enabled: Boolean(selectedTemplate?.id),
    staleTime: 10_000,
  });
  // The table is intentionally paged and searchable, but binding/rollback
  // needs the complete published-version set. Keep that selector independent
  // from the current table page so a version on page 2 remains selectable.
  const publishedVersionsQuery = useQuery({
    queryKey: ['rule-published-versions', selectedTemplate?.id],
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number, selectedTemplate?.id).versions as string, {
        params: { page: 0, size: 500 },
      });
      return listResult<RuleVersion>(response);
    },
    enabled: Boolean(selectedTemplate?.id),
    staleTime: 10_000,
  });
  const bindingsQuery = useQuery({
    queryKey: ['rule-bindings', projectId, bindingSearch, bindingPage, bindingPageSize],
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number).bindings, {
        params: { page: bindingPage, size: bindingPageSize, ...(bindingSearch ? { q: bindingSearch } : {}) },
      });
      return listResult<RuleBinding>(response);
    },
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const auditsQuery = useQuery({
    queryKey: ['rule-audits', projectId, auditSearch, auditPage, auditPageSize],
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number).bindingAudits, {
        params: { page: auditPage, size: auditPageSize, ...(auditSearch ? { q: auditSearch } : {}) },
      });
      return listResult<RuleAudit>(response);
    },
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const versionAuditsQuery = useQuery({
    queryKey: ['rule-version-audits', projectId, versionAuditSearch, versionAuditPage, versionAuditPageSize],
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number).versionAudits, {
        params: {
          page: versionAuditPage,
          size: versionAuditPageSize,
          ...(versionAuditSearch ? { q: versionAuditSearch } : {}),
        },
      });
      return listResult<RuleVersionAudit>(response);
    },
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const agentsQuery = useQuery({
    queryKey: ['edge-agents', projectId, agentSearch, agentPage],
    queryFn: async () => {
      const response = await request.get('/v1/edge-agents/monitor', {
        params: {
          project_id: projectId,
          page: agentPage,
          size: RULE_SELECTOR_PAGE_SIZE,
          ...(agentSearch ? { q: agentSearch } : {}),
        },
      });
      return listResult<EdgeAgent>(response);
    },
    enabled: Boolean(projectId),
    staleTime: 15_000,
  });

  const invalidateRules = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['rule-templates', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['rule-versions', selectedTemplate?.id] }),
      queryClient.invalidateQueries({ queryKey: ['rule-published-versions', selectedTemplate?.id] }),
      queryClient.invalidateQueries({ queryKey: ['rule-bindings', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['rule-audits', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['rule-version-audits', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['rule-algorithms', 'active'] }),
      queryClient.invalidateQueries({ queryKey: ['edge-agents', projectId] }),
    ]);
  };

  const createTemplate = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      request.post('/v1/rule-templates', { ...values, project_id: projectId }),
    onSuccess: async () => {
      message.success('规则模板已创建');
      setTemplateOpen(false);
      templateForm.resetFields();
      setSelectedAlgorithmOption(null);
      setAlgorithmSearchValue('');
      setAlgorithmSearch('');
      setAlgorithmPage(0);
      await invalidateRules();
    },
    onError: (error) => message.error(apiMessage(error)),
  });
  const createVersion = useMutation({
    mutationFn: (values: { schema_version: string; config_json: string }) => {
      let config: Record<string, unknown>;
      try {
        config = JSON.parse(values.config_json) as Record<string, unknown>;
      } catch {
        throw new Error('INVALID_JSON');
      }
      return request.post(`/v1/rule-templates/${selectedTemplate?.id}/versions`, {
        schema_version: values.schema_version,
        config,
      });
    },
    onSuccess: async () => {
      message.success('规则版本已创建');
      setVersionOpen(false);
      versionForm.resetFields();
      await invalidateRules();
    },
    onError: (error) =>
      message.error(
        error instanceof Error && error.message === 'INVALID_JSON' ? '配置必须是有效的 JSON 对象' : apiMessage(error),
      ),
  });
  const publishVersion = useMutation({
    mutationFn: (version: RuleVersion) =>
      request.post(`/v1/rule-templates/versions/${version.id}/publish`, { idempotency_key: idempotencyKey('publish') }),
    onSuccess: async () => {
      message.success('规则版本已发布');
      await invalidateRules();
    },
    onError: (error) => message.error(apiMessage(error)),
  });
  const bindVersion = useMutation({
    mutationFn: (values: { device_id: string; rule_version_id: number; reason?: string }) =>
      request.post(
        `/v1/rule-templates/bindings/${encodeURIComponent(values.device_id)}${
          bindingAction === 'rollback' ? '/rollback' : ''
        }`,
        {
          rule_version_id: values.rule_version_id,
          reason: values.reason,
          idempotency_key: idempotencyKey('bind'),
        },
        { params: { project_id: projectId } },
      ),
    onSuccess: async () => {
      message.success(bindingAction === 'rollback' ? '设备规则已回滚' : '规则已绑定到设备');
      setBindingOpen(false);
      bindingForm.resetFields();
      setSelectedAgentOption(null);
      setAgentSearchValue('');
      setAgentSearch('');
      setAgentPage(0);
      await invalidateRules();
    },
    onError: (error) => message.error(apiMessage(error)),
  });
  const unbindRule = useMutation({
    mutationFn: (binding: RuleBinding) =>
      request.post(`/v1/rule-templates/bindings/${binding.id}/unbind`, {
        idempotency_key: idempotencyKey('unbind'),
      }),
    onSuccess: async () => {
      message.success('设备规则已解绑');
      await invalidateRules();
    },
    onError: (error) => message.error(apiMessage(error)),
  });

  const templateRows = templatesQuery.data?.rows || [];
  const templateTotal = templatesQuery.data?.total ?? templateRows.length;
  const templateOptions = useMemo(() => {
    const byId = new Map<number, RuleTemplate>();
    [...templateRows, selectedTemplate].forEach((row) => {
      if (row?.id) byId.set(row.id, row);
    });
    return Array.from(byId.values());
  }, [selectedTemplate, templateRows]);
  const selectedVersions = versionsQuery.data?.rows || [];
  const versionTotal = versionsQuery.data?.total ?? selectedVersions.length;
  const bindingRows = bindingsQuery.data?.rows || [];
  const bindingTotal = bindingsQuery.data?.total ?? bindingRows.length;
  const auditRows = auditsQuery.data?.rows || [];
  const auditTotal = auditsQuery.data?.total ?? auditRows.length;
  const versionAuditRows = versionAuditsQuery.data?.rows || [];
  const versionAuditTotal = versionAuditsQuery.data?.total ?? versionAuditRows.length;
  const algorithmRows = algorithmsQuery.data?.rows || [];
  const algorithmTotal = algorithmsQuery.data?.total ?? algorithmRows.length;
  const algorithmOptions = useMemo(() => {
    const byKey = new Map<string, Algorithm>();
    [...algorithmRows, selectedAlgorithmOption].forEach((row) => {
      if (row?.algorithm_key) byKey.set(row.algorithm_key, row);
    });
    return Array.from(byKey.values());
  }, [algorithmRows, selectedAlgorithmOption]);
  const agentRows = agentsQuery.data?.rows || [];
  const agentTotal = agentsQuery.data?.total ?? agentRows.length;
  const agentOptions = useMemo(() => {
    const byId = new Map<string, EdgeAgent>();
    [...agentRows, selectedAgentOption].forEach((row) => {
      if (row?.agent_id) byId.set(row.agent_id, row);
    });
    return Array.from(byId.values());
  }, [agentRows, selectedAgentOption]);
  const publishedVersions = useMemo(
    () => (publishedVersionsQuery.data?.rows || selectedVersions).filter((version) => version.status === 'ACTIVE'),
    [publishedVersionsQuery.data?.rows, selectedVersions],
  );
  const error =
    templatesQuery.error ||
    algorithmsQuery.error ||
    versionsQuery.error ||
    publishedVersionsQuery.error ||
    bindingsQuery.error ||
    auditsQuery.error ||
    versionAuditsQuery.error ||
    agentsQuery.error;
  const ruleLoading =
    Boolean(projectId) &&
    [
      templatesQuery,
      algorithmsQuery,
      versionsQuery,
      publishedVersionsQuery,
      bindingsQuery,
      auditsQuery,
      versionAuditsQuery,
      agentsQuery,
    ].some((query) => query.isLoading);
  const dataState = moduleDataState({
    loading: ruleLoading,
    error,
    rowCount: templateTotal,
    online: !offline,
  });

  const applyTemplateSearch = (value: string) => {
    setTemplateSearchDraft(value);
    setTemplateSearch(value.trim());
    setTemplatePage(0);
  };
  const applyVersionSearch = (value: string) => {
    setVersionSearchDraft(value);
    setVersionSearch(value.trim());
    setVersionPage(0);
  };
  const applyBindingSearch = (value: string) => {
    setBindingSearchDraft(value);
    setBindingSearch(value.trim());
    setBindingPage(0);
  };
  const applyAuditSearch = (value: string) => {
    setAuditSearchDraft(value);
    setAuditSearch(value.trim());
    setAuditPage(0);
  };
  const applyVersionAuditSearch = (value: string) => {
    setVersionAuditSearchDraft(value);
    setVersionAuditSearch(value.trim());
    setVersionAuditPage(0);
  };
  const applyAlgorithmSearch = (value: string) => {
    setAlgorithmSearchValue(value);
    setAlgorithmSearch(value.trim());
    setAlgorithmPage(0);
  };
  const applyAgentSearch = (value: string) => {
    setAgentSearchValue(value);
    setAgentSearch(value.trim());
    setAgentPage(0);
  };

  const templateColumns: ColumnsType<RuleTemplate> = [
    { title: '模板', dataIndex: 'name', key: 'name', ellipsis: true },
    { title: '模板标识', dataIndex: 'template_key', key: 'template_key', ellipsis: true },
    { title: '算法', dataIndex: 'algorithm_key', key: 'algorithm_key', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value) => <Tag color={value === 'ACTIVE' ? 'green' : 'default'}>{value}</Tag>,
    },
  ];
  const versionColumns: ColumnsType<RuleVersion> = [
    {
      title: '版本',
      dataIndex: 'version_no',
      key: 'version_no',
      render: (value, row) => `v${value} / ${row.schema_version}`,
    },
    {
      title: '配置摘要',
      dataIndex: 'config_sha256',
      key: 'config_sha256',
      ellipsis: true,
      render: (value) => String(value).slice(0, 16),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value) => <Tag color={value === 'ACTIVE' ? 'green' : 'blue'}>{value}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_, row) =>
        row.status === 'DRAFT' ? (
          <Button
            size="small"
            icon={<CheckOutlined />}
            disabled={offline}
            loading={publishVersion.isPending}
            onClick={() => publishVersion.mutate(row)}
          >
            发布
          </Button>
        ) : null,
    },
  ];
  const bindingColumns: ColumnsType<RuleBinding> = [
    { title: '设备', dataIndex: 'device_id', key: 'device_id', ellipsis: true },
    { title: '规则版本', dataIndex: 'rule_version_id', key: 'rule_version_id' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (value) => <Tag color={value === 'ACTIVE' ? 'green' : 'default'}>{value}</Tag>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      render: (value) => (value ? new Date(value).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      render: (_, row) =>
        row.status === 'ACTIVE' ? (
          <Space size={4}>
            <Button
              size="small"
              icon={<LinkOutlined />}
              disabled={!selectedTemplate || !publishedVersions.length}
              onClick={() => {
                setBindingAction('rollback');
                bindingForm.setFieldsValue({ device_id: row.device_id });
                setSelectedAgentOption(
                  agentOptions.find((agent) => agent.agent_id === row.device_id) || {
                    agent_id: row.device_id,
                    name: row.device_id,
                  },
                );
                setAgentSearchValue('');
                setAgentSearch('');
                setAgentPage(0);
                setBindingOpen(true);
              }}
            >
              回滚
            </Button>
            <Button
              danger
              size="small"
              icon={<DisconnectOutlined />}
              disabled={offline}
              loading={unbindRule.isPending}
              onClick={() => unbindRule.mutate(row)}
            >
              解绑
            </Button>
          </Space>
        ) : null,
    },
  ];
  const auditColumns: ColumnsType<RuleAudit> = [
    { title: '设备', dataIndex: 'device_id', key: 'device_id', ellipsis: true },
    { title: '操作', dataIndex: 'operation', key: 'operation' },
    {
      title: '状态变化',
      key: 'transition',
      render: (_, row) => `${row.from_status || '-'} -> ${row.to_status || '-'}`,
    },
    { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true, render: (value) => value || '-' },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value) => (value ? new Date(value).toLocaleString() : '-'),
    },
  ];
  const versionAuditColumns: ColumnsType<RuleVersionAudit> = [
    { title: '版本', key: 'version', render: (_, row) => `模板 ${row.template_id} / v${row.version_no}` },
    { title: '操作', dataIndex: 'operation', key: 'operation' },
    {
      title: '状态变化',
      key: 'transition',
      render: (_, row) => `${row.from_status || '-'} -> ${row.to_status || '-'}`,
    },
    { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true, render: (value) => value || '-' },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value) => (value ? new Date(value).toLocaleString() : '-'),
    },
  ];

  if (!projectId) {
    return (
      <>
        <ModuleStatus module="rules" dataState="pending" />
        <Alert type="warning" showIcon message="暂无可访问项目" description="当前账号没有可用的项目权限。" />
      </>
    );
  }

  return (
    <>
      <ModuleStatus module="rules" dataState={dataState} rowCount={templateTotal} />
      <Panel>
        {offline && (
          <Alert
            style={{ marginBottom: 14 }}
            type="warning"
            showIcon
            message="当前设备处于离线状态"
            description="规则变更不会在离线时自动提交，请恢复网络后重试。"
          />
        )}
        {error && (
          <Alert
            style={{ marginBottom: 14 }}
            type="error"
            showIcon
            message={apiMessage(error)}
            action={
              <Button size="small" icon={<ReloadOutlined />} onClick={() => invalidateRules()}>
                重试
              </Button>
            }
          />
        )}
        <div ref={tabsHostRef}>
          <Tabs
            items={[
              {
                key: 'templates',
                label: '模板与版本',
                children: (
                  <>
                    <div className="toolbar">
                      <Space wrap>
                        <ServerSearchInput
                          value={templateSearchDraft}
                          placeholder="搜索规则模板"
                          ariaLabel="搜索规则模板"
                          loading={templatesQuery.isFetching}
                          style={{ width: 260 }}
                          onChange={(value) => {
                            setTemplateSearchDraft(value);
                            if (!value) applyTemplateSearch('');
                          }}
                          onSearch={applyTemplateSearch}
                        />
                        <Select
                          allowClear
                          showSearch
                          optionFilterProp="label"
                          style={{ minWidth: 240 }}
                          placeholder="选择规则模板"
                          loading={templatesQuery.isLoading}
                          value={selectedTemplate?.id}
                          options={templateOptions.map((row) => ({
                            label: `${row.name} (${row.template_key})`,
                            value: row.id,
                          }))}
                          onChange={(id) => setSelectedTemplate(templateOptions.find((row) => row.id === id) || null)}
                        />
                      </Space>
                      <Space wrap>
                        <Button icon={<PlusOutlined />} disabled={offline} onClick={() => setTemplateOpen(true)}>
                          新建模板
                        </Button>
                        <Button
                          type="primary"
                          icon={<PlusOutlined />}
                          disabled={offline || !selectedTemplate}
                          onClick={() => setVersionOpen(true)}
                        >
                          新建版本
                        </Button>
                      </Space>
                    </div>
                    {!selectedTemplate ? (
                      <Table<RuleTemplate>
                        rowKey="id"
                        size="middle"
                        loading={templatesQuery.isFetching}
                        columns={templateColumns}
                        dataSource={templateRows}
                        pagination={{
                          current: templatePage + 1,
                          pageSize: templatePageSize,
                          total: templateTotal,
                          showSizeChanger: true,
                          showTotal: (total) => `共 ${total} 个模板`,
                          onChange: (nextPage, nextSize) => {
                            if (nextSize !== templatePageSize) {
                              setTemplatePageSize(nextSize);
                              setTemplatePage(0);
                            } else {
                              setTemplatePage(Math.max(0, nextPage - 1));
                            }
                          },
                        }}
                        locale={{
                          emptyText: (
                            <Empty
                              image={Empty.PRESENTED_IMAGE_SIMPLE}
                              description={templateSearch ? '没有匹配的规则模板' : '暂无规则模板'}
                            />
                          ),
                        }}
                        onRow={(row) => ({ onClick: () => setSelectedTemplate(row), style: { cursor: 'pointer' } })}
                      />
                    ) : (
                      <>
                        <div className="toolbar">
                          <ServerSearchInput
                            value={versionSearchDraft}
                            placeholder={`搜索 ${selectedTemplate.name} 的规则版本`}
                            ariaLabel="搜索规则版本"
                            loading={versionsQuery.isFetching}
                            style={{ width: 300 }}
                            onChange={(value) => {
                              setVersionSearchDraft(value);
                              if (!value) applyVersionSearch('');
                            }}
                            onSearch={applyVersionSearch}
                          />
                          <span style={{ color: '#64748b', fontSize: 12 }}>
                            模板：{selectedTemplate.name} / 共 {versionTotal} 个版本
                          </span>
                        </div>
                        <Table<RuleVersion>
                          rowKey="id"
                          size="middle"
                          loading={versionsQuery.isFetching}
                          columns={versionColumns}
                          dataSource={selectedVersions}
                          pagination={{
                            current: versionPage + 1,
                            pageSize: versionPageSize,
                            total: versionTotal,
                            showSizeChanger: true,
                            showTotal: (total) => `共 ${total} 个版本`,
                            onChange: (nextPage, nextSize) => {
                              if (nextSize !== versionPageSize) {
                                setVersionPageSize(nextSize);
                                setVersionPage(0);
                              } else {
                                setVersionPage(Math.max(0, nextPage - 1));
                              }
                            },
                          }}
                          locale={{
                            emptyText: (
                              <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={versionSearch ? '没有匹配的规则版本' : '暂无规则版本'}
                              />
                            ),
                          }}
                          expandable={{ expandedRowRender: (row) => ruleConfigSummary(row.config) }}
                        />
                      </>
                    )}
                  </>
                ),
              },
              {
                key: 'bindings',
                label: '设备绑定',
                children: (
                  <>
                    <div className="toolbar">
                      <ServerSearchInput
                        value={bindingSearchDraft}
                        placeholder="搜索设备规则绑定"
                        ariaLabel="搜索设备规则绑定"
                        loading={bindingsQuery.isFetching}
                        style={{ width: 280 }}
                        onChange={(value) => {
                          setBindingSearchDraft(value);
                          if (!value) applyBindingSearch('');
                        }}
                        onSearch={applyBindingSearch}
                      />
                      <Button
                        type="primary"
                        icon={<LinkOutlined />}
                        disabled={offline || !publishedVersions.length}
                        onClick={() => {
                          setBindingAction('bind');
                          setSelectedAgentOption(null);
                          setAgentSearchValue('');
                          setAgentSearch('');
                          setAgentPage(0);
                          setBindingOpen(true);
                        }}
                      >
                        绑定规则
                      </Button>
                    </div>
                    <Table<RuleBinding>
                      rowKey="id"
                      size="middle"
                      loading={bindingsQuery.isFetching}
                      columns={bindingColumns}
                      dataSource={bindingRows}
                      pagination={{
                        current: bindingPage + 1,
                        pageSize: bindingPageSize,
                        total: bindingTotal,
                        showSizeChanger: true,
                        showTotal: (total) => `共 ${total} 条绑定`,
                        onChange: (nextPage, nextSize) => {
                          if (nextSize !== bindingPageSize) {
                            setBindingPageSize(nextSize);
                            setBindingPage(0);
                          } else {
                            setBindingPage(Math.max(0, nextPage - 1));
                          }
                        },
                      }}
                      locale={{
                        emptyText: (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={bindingSearch ? '没有匹配的设备规则绑定' : '暂无设备规则绑定'}
                          />
                        ),
                      }}
                    />
                  </>
                ),
              },
              {
                key: 'audits',
                label: '变更审计',
                children: (
                  <>
                    <h3>版本发布</h3>
                    <div className="toolbar">
                      <ServerSearchInput
                        value={versionAuditSearchDraft}
                        placeholder="搜索版本发布审计"
                        ariaLabel="搜索版本发布审计"
                        loading={versionAuditsQuery.isFetching}
                        style={{ width: 280 }}
                        onChange={(value) => {
                          setVersionAuditSearchDraft(value);
                          if (!value) applyVersionAuditSearch('');
                        }}
                        onSearch={applyVersionAuditSearch}
                      />
                      <span style={{ color: '#64748b', fontSize: 12 }}>共 {versionAuditTotal} 条记录</span>
                    </div>
                    <Table<RuleVersionAudit>
                      rowKey="id"
                      size="middle"
                      loading={versionAuditsQuery.isFetching}
                      columns={versionAuditColumns}
                      dataSource={versionAuditRows}
                      pagination={{
                        current: versionAuditPage + 1,
                        pageSize: versionAuditPageSize,
                        total: versionAuditTotal,
                        showSizeChanger: true,
                        showTotal: (total) => `共 ${total} 条记录`,
                        onChange: (nextPage, nextSize) => {
                          if (nextSize !== versionAuditPageSize) {
                            setVersionAuditPageSize(nextSize);
                            setVersionAuditPage(0);
                          } else {
                            setVersionAuditPage(Math.max(0, nextPage - 1));
                          }
                        },
                      }}
                      locale={{
                        emptyText: (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={versionAuditSearch ? '没有匹配的版本发布记录' : '暂无版本发布记录'}
                          />
                        ),
                      }}
                    />
                    <h3 style={{ marginTop: 24 }}>设备绑定</h3>
                    <div className="toolbar">
                      <ServerSearchInput
                        value={auditSearchDraft}
                        placeholder="搜索设备绑定审计"
                        ariaLabel="搜索设备绑定审计"
                        loading={auditsQuery.isFetching}
                        style={{ width: 280 }}
                        onChange={(value) => {
                          setAuditSearchDraft(value);
                          if (!value) applyAuditSearch('');
                        }}
                        onSearch={applyAuditSearch}
                      />
                      <span style={{ color: '#64748b', fontSize: 12 }}>共 {auditTotal} 条记录</span>
                    </div>
                    <Table<RuleAudit>
                      rowKey="id"
                      size="middle"
                      loading={auditsQuery.isFetching}
                      columns={auditColumns}
                      dataSource={auditRows}
                      pagination={{
                        current: auditPage + 1,
                        pageSize: auditPageSize,
                        total: auditTotal,
                        showSizeChanger: true,
                        showTotal: (total) => `共 ${total} 条记录`,
                        onChange: (nextPage, nextSize) => {
                          if (nextSize !== auditPageSize) {
                            setAuditPageSize(nextSize);
                            setAuditPage(0);
                          } else {
                            setAuditPage(Math.max(0, nextPage - 1));
                          }
                        },
                      }}
                      locale={{
                        emptyText: (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description={auditSearch ? '没有匹配的设备绑定记录' : '暂无设备规则变更记录'}
                          />
                        ),
                      }}
                    />
                  </>
                ),
              },
              {
                key: 'signatures',
                label: '设备验签',
                children: <DeviceSignatureEvidence projectId={projectId} embedded />,
              },
            ]}
          />
        </div>

        <Modal
          title="新建规则模板"
          open={templateOpen}
          centered
          onCancel={() => setTemplateOpen(false)}
          onOk={() => templateForm.submit()}
          confirmLoading={createTemplate.isPending}
          destroyOnClose
        >
          <Form
            form={templateForm}
            layout="vertical"
            onFinish={(values) => {
              if (!offline) createTemplate.mutate(values);
            }}
          >
            <Form.Item name="name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
              <Input maxLength={160} />
            </Form.Item>
            <Form.Item
              name="template_key"
              label="模板标识"
              rules={[{ required: true, pattern: /^[a-z][a-z0-9_-]*$/, message: '使用小写字母、数字、下划线或连字符' }]}
            >
              <Input maxLength={128} />
            </Form.Item>
            <Form.Item name="algorithm_key" label="规则算法" rules={[{ required: true, message: '请选择规则算法' }]}>
              <RuleRemotePagedSelect
                page={algorithmPage}
                pageSize={RULE_SELECTOR_PAGE_SIZE}
                total={algorithmTotal}
                searchValue={algorithmSearchValue}
                loading={algorithmsQuery.isLoading || algorithmsQuery.isFetching}
                placeholder="搜索并选择规则算法"
                ariaLabel="搜索规则算法"
                countLabel="算法"
                options={algorithmOptions.map((row) => ({
                  label: row.display_name || row.name || row.algorithm_key,
                  value: row.algorithm_key,
                }))}
                onSelectionChange={(key) =>
                  setSelectedAlgorithmOption(algorithmOptions.find((row) => row.algorithm_key === key) || null)
                }
                onSearch={applyAlgorithmSearch}
                onPageChange={setAlgorithmPage}
              />
            </Form.Item>
            <Form.Item name="description" label="说明">
              <Input.TextArea maxLength={4000} rows={3} />
            </Form.Item>
          </Form>
        </Modal>

        <Modal
          title="新建规则版本"
          open={versionOpen}
          centered
          onCancel={() => setVersionOpen(false)}
          onOk={() => versionForm.submit()}
          confirmLoading={createVersion.isPending}
          destroyOnClose
        >
          <Form
            form={versionForm}
            layout="vertical"
            initialValues={{ schema_version: 'v1', config_json: '{}' }}
            onFinish={(values) => {
              if (!offline) createVersion.mutate(values);
            }}
          >
            <Form.Item name="schema_version" label="Schema 版本" rules={[{ required: true }]}>
              <Input maxLength={32} />
            </Form.Item>
            <Form.Item name="config_json" label="规则配置 JSON" rules={[{ required: true, message: '请输入规则配置' }]}>
              <Input.TextArea rows={10} />
            </Form.Item>
          </Form>
        </Modal>

        <Modal
          title={bindingAction === 'rollback' ? '回滚设备规则' : '绑定设备规则'}
          open={bindingOpen}
          centered
          onCancel={() => setBindingOpen(false)}
          onOk={() => bindingForm.submit()}
          confirmLoading={bindVersion.isPending}
          destroyOnClose
        >
          <Form
            form={bindingForm}
            layout="vertical"
            onFinish={(values) => {
              if (!offline) bindVersion.mutate(values);
            }}
          >
            <Form.Item name="device_id" label="设备" rules={[{ required: true, message: '请选择设备' }]}>
              <RuleRemotePagedSelect
                page={agentPage}
                pageSize={RULE_SELECTOR_PAGE_SIZE}
                total={agentTotal}
                searchValue={agentSearchValue}
                loading={agentsQuery.isLoading || agentsQuery.isFetching}
                placeholder="搜索并选择边端 Agent"
                ariaLabel="搜索边端 Agent"
                countLabel="Agent"
                options={agentOptions.map((row) => ({
                  label: `${row.name} (${row.agent_id})`,
                  value: row.agent_id,
                }))}
                onSelectionChange={(agentId) =>
                  setSelectedAgentOption(agentOptions.find((row) => row.agent_id === agentId) || null)
                }
                onSearch={applyAgentSearch}
                onPageChange={setAgentPage}
              />
            </Form.Item>
            <Form.Item
              name="rule_version_id"
              label="已发布版本"
              rules={[{ required: true, message: '请选择规则版本' }]}
            >
              <Select
                showSearch
                optionFilterProp="label"
                options={publishedVersions.map((row) => ({
                  label: `${selectedTemplate?.name} v${row.version_no}`,
                  value: row.id,
                }))}
              />
            </Form.Item>
            <Form.Item name="reason" label="变更原因">
              <Input.TextArea maxLength={4000} rows={3} />
            </Form.Item>
          </Form>
        </Modal>
      </Panel>
    </>
  );
}
