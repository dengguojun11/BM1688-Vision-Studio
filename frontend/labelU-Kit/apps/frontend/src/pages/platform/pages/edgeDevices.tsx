import {
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Descriptions,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';

import { responseStatus, TablePanel } from '../shared';
import { agentBootstrapFailed, deviceConnectionReady } from './edgeDeviceReadiness';

type ValidationChannel = 'ssh' | 'board_agent';

type EdgeDevice = {
  id: string;
  name?: string;
  is_active?: boolean;
  enabled?: boolean;
  host?: string;
  port?: number;
  user?: string;
  password_masked_hint?: string;
  password_configured?: boolean;
  password_runtime_available?: boolean;
  private_key_path_masked?: string;
  private_key_path_configured?: boolean;
  private_key_path_runtime_available?: boolean;
  edge_root?: string;
  remote_base?: string;
  sophon_demo_root?: string;
  auto_after_export?: boolean;
  run_coco_eval?: boolean;
  connect_timeout_sec?: number;
  validation_timeout_sec?: number;
  docker_container?: string;
  docker_use_sudo?: boolean;
  validation_channel?: ValidationChannel;
  board_agent_status?: string;
  board_agent_last_heartbeat_at?: string | null;
  board_agent_environment_ready?: boolean;
  board_agent_message?: string;
  agent_bootstrap_status?: string;
  agent_bootstrap_control_plane_enabled?: boolean;
  agent_control_plane_source?: string;
  agent_control_plane_message?: string;
  agent_control_plane_public_base_url_configured?: boolean;
  agent_bootstrap_stage?: string;
  agent_bootstrap_code?: string;
  agent_bootstrap_message?: string;
  agent_bootstrap_version?: string;
  agent_bootstrap_updated_at?: string | null;
  probe_status?: string;
  probe_message?: string;
  probe_checked_at?: string | null;
  last_probe_status?: string;
  last_probe_message?: string;
  last_probe_checked_at?: string | null;
  last_validation_status?: string;
  last_validation_at?: string | null;
  environment_status?: string;
  environment_message?: string;
  environment_checked_at?: string | null;
  environment_report?: Record<string, any>;
  last_used_at?: string | null;
  scheduling_status?: string;
  is_busy?: boolean;
  active_job_id?: number | null;
  available_for_dispatch?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
};

type EdgeDeviceList = {
  active_device_id?: string;
  devices?: EdgeDevice[];
  total?: number;
  page?: number | null;
  size?: number;
};

type EdgeRuntimeSettings = {
  edge_root?: string;
};

type EdgeDeviceFormValues = {
  name?: string;
  validation_channel: ValidationChannel;
  host?: string;
  port?: number;
  user?: string;
  password?: string;
  private_key_path?: string;
  clear_private_key_path?: boolean;
  edge_root?: string;
  docker_container?: string;
  connect_timeout_sec?: number;
  validation_timeout_sec?: number;
  enabled: boolean;
  auto_after_export: boolean;
  run_coco_eval: boolean;
  docker_use_sudo: boolean;
};

const CHECKING_STATUSES = new Set(['checking', 'installing', 'cleaning']);
const ACTIVE_BOOTSTRAP_STATUSES = new Set([
  'QUEUED',
  'PREFLIGHT',
  'RUNTIME_PROVISION',
  'UPLOADING',
  'INSTALLING',
  'STARTING',
  'WAITING_HEARTBEAT',
]);

const BOOTSTRAP_STATUS_LABELS: Record<string, string> = {
  NOT_STARTED: '未引导',
  PREVIEW: '待确认安装',
  QUEUED: '引导排队中',
  PREFLIGHT: '远端预检中',
  RUNTIME_PROVISION: '准备运行环境',
  UPLOADING: '上传 Agent',
  INSTALLING: '安装系统服务',
  STARTING: '启动 Agent',
  WAITING_HEARTBEAT: '等待 Agent 心跳',
  READY: 'Agent 已就绪',
  FAILED: '引导失败',
};

function usesDeviceProgram(device?: EdgeDevice | null) {
  return device?.validation_channel === 'board_agent';
}

function connectionReady(device: EdgeDevice) {
  return deviceConnectionReady(device);
}

function connectionStatusText(device: EdgeDevice) {
  if (usesDeviceProgram(device)) {
    if (connectionReady(device)) return device.is_busy ? '心跳在线 · 任务执行中' : '心跳在线';
    return device.is_busy ? '任务执行中 · 心跳待确认' : '等待心跳';
  }
  const endpoint = `${device.user || 'linaro'}@${device.host || '未配置'}:${device.port || 22}`;
  return device.is_busy ? `${endpoint} · 任务执行中` : endpoint;
}

function connectionStatusLabel(device: EdgeDevice) {
  if (connectionReady(device)) return '正常';
  return device.is_busy ? '任务执行中，连接待确认' : '未通过';
}

function environmentReady(device: EdgeDevice) {
  if (usesDeviceProgram(device)) return connectionReady(device) && Boolean(device.board_agent_environment_ready);
  return device.environment_status === 'ready' && device.environment_report?.environment_ready === true;
}

function bootstrapStatus(device: EdgeDevice) {
  return String(device.agent_bootstrap_status || 'NOT_STARTED')
    .trim()
    .toUpperCase();
}

function bootstrapLabel(device: EdgeDevice) {
  return BOOTSTRAP_STATUS_LABELS[bootstrapStatus(device)] || bootstrapStatus(device);
}

function bootstrapInProgress(device: EdgeDevice) {
  return ACTIVE_BOOTSTRAP_STATUSES.has(bootstrapStatus(device));
}

export function deviceStatusNeedsPolling(device: EdgeDevice) {
  const environmentStatus = String(device.environment_status || '').toLowerCase();
  const status = bootstrapStatus(device);
  if (CHECKING_STATUSES.has(environmentStatus) || bootstrapInProgress(device)) return true;
  // Creating/updating an SSH device holds the registry mutation lock while
  // the automatic bootstrap request is queued.  A transient lock collision
  // can therefore leave the first response at NOT_STARTED even though a
  // bounded retry is already scheduled on the server.  Keep the fast poll
  // cadence until that durable lifecycle reaches a terminal state so the UI
  // reflects the automatic install without requiring a manual refresh.
  if (
    device.validation_channel === 'ssh' &&
    device.agent_bootstrap_control_plane_enabled === true &&
    status !== 'READY' &&
    status !== 'FAILED'
  ) {
    return true;
  }
  // A board-agent device can become ready without any browser action. Keep
  // watching the registration projection until its first/fresh heartbeat and
  // environment report arrive; SSH devices still require an explicit probe.
  return (
    usesDeviceProgram(device) &&
    bootstrapStatus(device) !== 'FAILED' &&
    (!connectionReady(device) || !environmentReady(device))
  );
}

/**
 * Keep a low-frequency watcher for every healthy Board Agent.  A fresh
 * heartbeat can expire while the page remains open; without this second
 * cadence the UI would continue showing a stale dispatchable projection.
 */
export function deviceHeartbeatNeedsPolling(device: EdgeDevice) {
  return usesDeviceProgram(device) && bootstrapStatus(device) !== 'FAILED';
}

const CONTROL_PLANE_MESSAGE_BY_CODE: Record<string, string> = {
  control_plane_env_disabled: '服务端已关闭 Agent 自动配置，请联系管理员启用控制面。',
  token_env_empty: '服务端 Agent 凭据为空，暂时无法自动配置。',
  token_decrypt_failed: '服务端 Agent 凭据无法解密，请检查服务端密钥配置。',
  secret_key_missing: '服务端缺少安全加密密钥，暂时无法生成 Agent 凭据。',
  public_base_url_missing: '服务端没有可被边端访问的回连地址，请检查服务监听地址和局域网网络。',
  public_base_url_unreachable: '服务端回连地址不可达，请检查局域网地址、端口和防火墙。',
  control_plane_persist_failed: '服务端保存 Agent 自动配置失败，请稍后重试。',
  production_env_required: '生产环境的 Agent 安全配置尚未完成，请联系管理员。',
};

const CONTROL_PLANE_ENV_NAMES = [
  'BOARD_AGENT_ENABLED',
  'BOARD_AGENT_PRIMARY_ENABLED',
  'BOARD_AGENT_TOKEN',
  'BOARD_AGENT_PUBLIC_BASE_URL',
  'EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED',
];

function safeControlPlaneText(value?: string | null) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  // Never echo environment variable names or secret-bearing backend errors.
  if (
    CONTROL_PLANE_ENV_NAMES.some((name) => raw.includes(name)) ||
    /(?:token|secret|password|private\s*key|authorization|bearer|密码|密钥|口令)/i.test(raw)
  ) {
    return '服务端 Agent 自动配置尚未完成，请联系管理员检查控制面配置。';
  }
  return raw;
}

function controlPlaneMessage(device: EdgeDevice) {
  const code = String(device.agent_bootstrap_code || '').trim().toLowerCase();
  if (code && CONTROL_PLANE_MESSAGE_BY_CODE[code]) return CONTROL_PLANE_MESSAGE_BY_CODE[code];
  const bootstrapDetail = safeControlPlaneText(device.agent_bootstrap_message);
  if (bootstrapDetail) return bootstrapDetail;
  const configuredMessage = safeControlPlaneText(device.agent_control_plane_message);
  if (configuredMessage) return configuredMessage;
  if (device.agent_control_plane_public_base_url_configured === false) {
    return '等待服务端准备边端可访问的回连地址。';
  }
  if (device.agent_bootstrap_control_plane_enabled === false) {
    return 'Agent 自动配置当前不可用；保存 SSH 配置后可重新触发自动配置。';
  }
  return 'Agent 自动配置状态待更新。';
}

function agentBootstrapControlPlaneDisabled(device: EdgeDevice) {
  // The API returns an explicit false when the server-side enrollment gate is
  // closed.  Keep undefined compatible with older responses while the API is
  // rolling out this capability field.
  return device.agent_bootstrap_control_plane_enabled === false;
}

function agentBootstrapControlPlaneUnavailable(device: EdgeDevice) {
  return (
    agentBootstrapControlPlaneDisabled(device) &&
    !usesDeviceProgram(device) &&
    bootstrapStatus(device) !== 'READY' &&
    !isDispatchable(device)
  );
}

export function bootstrapNeedsAction(device: EdgeDevice) {
  const status = bootstrapStatus(device);
  return (
    !agentBootstrapControlPlaneDisabled(device) &&
    device.is_busy !== true &&
    !bootstrapInProgress(device) &&
    (device.validation_channel === 'ssh'
      ? status !== 'READY'
      : status === 'FAILED')
  );
}

export function bootstrapMessage(device: EdgeDevice) {
  const status = bootstrapStatus(device);
  const bootstrapDetail = safeControlPlaneText(device.agent_bootstrap_message);
  return (
    (agentBootstrapControlPlaneDisabled(device) && !usesDeviceProgram(device) && bootstrapStatus(device) !== 'READY'
      ? controlPlaneMessage(device)
      : bootstrapDetail ||
    (usesDeviceProgram(device) && status === 'NOT_STARTED'
      ? '等待设备程序首次心跳；由设备端 Agent 主动连接'
      : status === 'READY'
      ? '常驻 Agent 已安装并完成引导'
      : status === 'FAILED'
      ? '自动引导失败，请查看错误信息并重试'
      : bootstrapInProgress(device)
      ? BOOTSTRAP_STATUS_LABELS[status] || 'Agent 引导进行中'
      : status === 'NOT_STARTED'
      ? '尚未完成 Agent 引导；可手动启动引导'
      : '等待 Agent 状态更新'))
  );
}

function bootstrapFailed(device: EdgeDevice) {
  return bootstrapStatus(device) === 'FAILED';
}

function bootstrapProgressMessage(device: EdgeDevice) {
  const stage = bootstrapLabel(device);
  const message = bootstrapMessage(device);
  return message === stage ? stage : `${stage}：${message}`;
}

function bootstrapFailureMessage(device: EdgeDevice) {
  const message = bootstrapMessage(device);
  return device.agent_bootstrap_code ? `${message}（${device.agent_bootstrap_code}）` : message;
}

function deviceName(device: EdgeDevice) {
  return device.name || device.host || device.id;
}

function dateText(value?: string | null) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value).replace('T', ' ') : date.toLocaleString();
}

function schedulingText(device: EdgeDevice) {
  if (device.is_busy) return `执行任务${device.active_job_id ? ` #${device.active_job_id}` : ''}`;
  if (isDispatchable(device)) return '空闲可调度';
  if (device.enabled === false) return '已停用';
  if (!connectionReady(device)) return '连接不可用';
  if (!environmentReady(device)) return '环境未就绪';
  return '等待检查';
}

export function isDispatchable(
  device: Pick<EdgeDevice, 'available_for_dispatch' | 'is_busy' | 'agent_bootstrap_status'>,
) {
  // A list refresh can briefly carry the previous availability projection
  // while an Agent bootstrap state has already been persisted.  The active
  // bootstrap state owns dispatch readiness during that transition window.
  return (
    device.is_busy !== true &&
    !agentBootstrapFailed(device) &&
    !ACTIVE_BOOTSTRAP_STATUSES.has(String(device.agent_bootstrap_status || '').trim().toUpperCase()) &&
    device.available_for_dispatch === true
  );
}

export function readinessReason(device: EdgeDevice) {
  if (device.is_busy) return `设备正在执行边端任务${device.active_job_id ? ` #${device.active_job_id}` : ''}`;
  if (device.enabled === false) return '设备已停用';
  if (agentBootstrapControlPlaneUnavailable(device)) {
    return controlPlaneMessage(device);
  }
  // Agent bootstrap owns the readiness explanation while it is active.  The
  // SSH probe and legacy environment fields can remain stale until the worker
  // finishes, so checking them first hides the actionable stage/heartbeat.
  if (bootstrapFailed(device)) {
    return `Board Agent 引导失败：${bootstrapFailureMessage(device)}`;
  }
  if (bootstrapInProgress(device)) {
    return `Board Agent 引导进行中：${bootstrapProgressMessage(device)}`;
  }
  if (usesDeviceProgram(device)) {
    if (!connectionReady(device)) return device.board_agent_message || '设备程序尚未上报心跳';
    if (!environmentReady(device)) return device.board_agent_message || '设备尚未上报 BM1688 环境就绪状态';
  } else {
    if (!device.password_configured && !device.private_key_path_configured) {
      return '尚未配置 SSH 密码或私钥路径';
    }
    if (
      !device.password_runtime_available &&
      !device.private_key_path_runtime_available
    ) {
      return '已配置 SSH 凭据，但当前服务无法读取密码或私钥路径';
    }
    if (!connectionReady(device)) return device.last_probe_message || device.probe_message || 'SSH 连接尚未通过';
    if (!environmentReady(device)) return device.environment_message || 'BM1688 环境尚未就绪';
  }
  return '连接、凭据和 BM1688 环境均已就绪';
}

export function readinessTitle(device: EdgeDevice) {
  if (device.is_busy) return `当前设备正在执行边端任务${device.active_job_id ? ` #${device.active_job_id}` : ''}`;
  if (agentBootstrapControlPlaneUnavailable(device)) {
    return 'Agent 自动配置未完成';
  }
  if (bootstrapFailed(device)) {
    return isDispatchable(device) ? '当前设备可执行边端检查，但 Agent 引导失败' : 'Board Agent 引导失败';
  }
  if (bootstrapInProgress(device)) {
    return `Board Agent 引导进行中：${bootstrapLabel(device)}`;
  }
  if (isDispatchable(device)) return '当前设备可执行边端检查';
  return '当前设备尚未就绪';
}

function actionError(error: any) {
  if (responseStatus(error) === 403) return '当前账号没有边端设备管理权限';
  const raw = error?.response?.data?.msg || error?.response?.data?.message || '';
  if (
    CONTROL_PLANE_ENV_NAMES.some((name) => String(raw).includes(name)) ||
    /(?:token|secret|password|private\s*key|authorization|bearer|密码|密钥|口令)/i.test(String(raw))
  ) {
    return '操作失败，请稍后重试';
  }
  return raw || '操作失败，请稍后重试';
}

export function shouldAutoConfigureAgent(
  values: Pick<EdgeDeviceFormValues, 'validation_channel' | 'host' | 'user' | 'password' | 'private_key_path'>,
  existing?: Pick<EdgeDevice, 'password_configured' | 'private_key_path_configured'> | null,
) {
  if (values.validation_channel !== 'ssh') return false;
  const hasCredential = Boolean(
    values.password?.trim() ||
      values.private_key_path?.trim() ||
      existing?.password_configured ||
      existing?.private_key_path_configured,
  );
  return Boolean(values.host?.trim() && (values.user?.trim() || 'linaro') && hasCredential);
}

export function deviceSaveSuccessMessage(
  saved:
    | Pick<
        EdgeDevice,
        | 'validation_channel'
        | 'agent_bootstrap_control_plane_enabled'
        | 'agent_bootstrap_status'
        | 'agent_bootstrap_code'
        | 'agent_bootstrap_message'
        | 'agent_control_plane_message'
        | 'agent_control_plane_public_base_url_configured'
      >
    | null
    | undefined,
  editing: boolean,
) {
  const prefix = editing ? '设备配置已更新' : '设备已新增';
  if (!saved) return prefix;
  if (saved?.validation_channel === 'board_agent') return `${prefix}，等待设备 Agent 心跳`;

  const status = String(saved?.agent_bootstrap_status || 'NOT_STARTED').trim().toUpperCase();
  if (status === 'FAILED') {
    const detail =
      safeControlPlaneText(saved?.agent_bootstrap_message) ||
      safeControlPlaneText(saved?.agent_control_plane_message) ||
      CONTROL_PLANE_MESSAGE_BY_CODE[String(saved?.agent_bootstrap_code || '').trim().toLowerCase()] ||
      '请查看设备详情后重试';
    return `${prefix}，自动配置失败：${detail}`;
  }
  if (saved?.agent_bootstrap_control_plane_enabled === true) {
    if (status === 'READY') return `${prefix}，Board Agent 已就绪`;
    if (status === 'NOT_STARTED' || ACTIVE_BOOTSTRAP_STATUSES.has(status)) {
      return `${prefix}，Agent 自动配置已排队`;
    }
    return `${prefix}，Agent 自动配置已提交`;
  }
  if (saved?.agent_control_plane_message || saved?.agent_control_plane_public_base_url_configured === false) {
    return `${prefix}，自动配置未完成：${controlPlaneMessage(saved as EdgeDevice)}`;
  }
  // Compatibility fallback for older servers that do not return the control
  // plane projection yet.
  return `${prefix}，SSH 环境检查已排队`;
}

const ENVIRONMENT_CHECKS: Array<[string, string]> = [
  ['系统架构', 'architecture_ok'],
  ['BM1688 设备节点', 'bm1688_device_ok'],
  ['芯片运行库', 'libsophon_ok'],
  ['容器服务', 'docker_ok'],
  ['运行镜像', 'docker_image_ok'],
  ['运行容器', 'container_ok'],
  ['推理环境', 'python_runtime_ok'],
  ['验证工具', 'validator_ok'],
];

function EnvironmentChecks({ device }: { device: EdgeDevice }) {
  const report = device.environment_report || {};
  if (usesDeviceProgram(device)) {
    return (
      <Alert
        type={environmentReady(device) ? 'success' : 'warning'}
        showIcon
        message={environmentReady(device) ? '设备已上报 BM1688 环境就绪' : '等待设备程序上报环境状态'}
        description={device.board_agent_message || '设备程序在线后会主动上报环境和能力。'}
      />
    );
  }
  if (!Object.keys(report).length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未执行环境检查" />;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8 }}>
      {ENVIRONMENT_CHECKS.map(([label, key]) => {
        const value = report[key];
        return (
          <div
            key={key}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 12,
              padding: '9px 10px',
              border: '1px solid #e5e7eb',
              borderRadius: 6,
            }}
          >
            <span>{label}</span>
            <Tag color={value === true ? 'green' : value === false ? 'red' : 'default'}>
              {value === true ? '通过' : value === false ? '未通过' : '未检查'}
            </Tag>
          </div>
        );
      })}
    </div>
  );
}

export default function EdgeDevicesPanel() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [keyword, setKeyword] = useState('');
  const [keywordDraft, setKeywordDraft] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [detailDevice, setDetailDevice] = useState<EdgeDevice | null>(null);
  const [editingDevice, setEditingDevice] = useState<EdgeDevice | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [deleteDevice, setDeleteDevice] = useState<EdgeDevice | null>(null);
  const [form] = Form.useForm<EdgeDeviceFormValues>();
  const validationChannel = Form.useWatch('validation_channel', form) || 'ssh';

  const edgeRuntimeSettingsQuery = useQuery<EdgeRuntimeSettings>({
    queryKey: ['edge-runtime-settings'],
    queryFn: async () => (await request.get('/v1/yolo-bm1688-exports/edge-ssh-settings')).data,
    staleTime: 60_000,
    retry: 1,
  });
  const canonicalEdgeRoot = edgeRuntimeSettingsQuery.data?.edge_root?.trim() || '';

  const devicesQuery = useQuery<EdgeDeviceList>({
    queryKey: ['edge-devices', page, pageSize, keyword, statusFilter],
    queryFn: async () =>
      (
        await request.get('/v1/edge-devices', {
          params: {
            page,
            size: pageSize,
            q: keyword || undefined,
            status: statusFilter === 'all' ? undefined : statusFilter,
          },
        })
      ).data,
    staleTime: 10_000,
    refetchInterval: (query) => {
      const data = query.state.data as EdgeDeviceList | undefined;
      if (data?.devices?.some(deviceStatusNeedsPolling)) return 3_000;
      return data?.devices?.some(deviceHeartbeatNeedsPolling) ? 30_000 : false;
    },
  });
  const devices = devicesQuery.data?.devices || [];
  const activeDevice = devices.find((device) => device.id === devicesQuery.data?.active_device_id || device.is_active);
  const total = devicesQuery.data?.total ?? devices.length;

  useEffect(() => {
    if (!detailDevice) return;
    const latest = devices.find((device) => device.id === detailDevice.id);
    if (latest && latest !== detailDevice) setDetailDevice(latest);
  }, [detailDevice, devices]);

  const filteredDevices = useMemo(() => {
    // Keep a local status guard for rolling upgrades where an older server
    // ignores the new ``status`` query parameter.  Search itself is owned by
    // the server so it works across pages and large registries.
    return devices.filter((device) => {
      if (statusFilter === 'ready' && !isDispatchable(device)) return false;
      if (statusFilter === 'blocked' && isDispatchable(device)) return false;
      if (statusFilter === 'active' && device.id !== activeDevice?.id) return false;
      if (statusFilter === 'disabled' && device.enabled !== false) return false;
      return true;
    });
  }, [activeDevice?.id, devices, statusFilter]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['edge-devices'] });
  };

  const saveMutation = useMutation({
    mutationFn: async (values: EdgeDeviceFormValues) => {
      const body = {
        ...values,
        name: values.name?.trim() || null,
        host: values.host?.trim() || '',
        user: values.user?.trim() || 'linaro',
        docker_container: values.docker_container?.trim() || 'bm1688',
      } as Record<string, unknown>;
      // The server owns the canonical remote root.  Never send the legacy
      // UI fallback, which can differ from EDGE_REMOTE_ROOT in production.
      if (canonicalEdgeRoot) body.edge_root = canonicalEdgeRoot;
      else delete body.edge_root;
      body.auto_configure_agent = shouldAutoConfigureAgent(values, editingDevice);
      if (editingDevice) {
        body.password_secret_action = values.password ? 'set' : 'keep';
        if (!values.password) delete body.password;
        if (values.clear_private_key_path) {
          body.private_key_path_secret_action = 'clear';
          body.clear_private_key_path = true;
          delete body.private_key_path;
        } else if (values.private_key_path?.trim()) {
          body.private_key_path_secret_action = 'set';
          body.private_key_path = values.private_key_path.trim();
        } else {
          body.private_key_path_secret_action = 'keep';
          delete body.private_key_path;
        }
        return (await request.patch(`/v1/edge-devices/${editingDevice.id}`, body)).data;
      }
      if (!values.private_key_path?.trim()) delete body.private_key_path;
      // A filtered page can be empty even when the registry already contains
      // devices.  Only auto-select the first device when the unfiltered list
      // is known to be empty.
      body.set_active = !keyword && statusFilter === 'all' && total === 0;
      return (await request.post('/v1/edge-devices', body)).data;
    },
    onSuccess: async (saved: EdgeDevice) => {
      message.success(deviceSaveSuccessMessage(saved, Boolean(editingDevice)));
      setEditorOpen(false);
      setEditingDevice(null);
      form.resetFields();
      await refresh();
      if (saved?.id) setDetailDevice(saved);
    },
    onError: (error) => message.error(actionError(error)),
  });

  const setActiveMutation = useMutation({
    mutationFn: async (device: EdgeDevice) =>
      (await request.put('/v1/edge-devices/active', { device_id: device.id })).data,
    onSuccess: async () => {
      message.success('已设为当前边端设备');
      await refresh();
    },
    onError: (error) => message.error(actionError(error)),
  });

  const probeMutation = useMutation({
    mutationFn: async (device: EdgeDevice) => (await request.post(`/v1/edge-devices/${device.id}/probe`)).data,
    onSuccess: async (result: any, device) => {
      const ok = result?.status === 'ok';
      message[ok ? 'success' : 'error'](
        ok
          ? usesDeviceProgram(device)
            ? '设备在线状态已刷新'
            : 'SSH 连接通过，环境检查已启动'
          : result?.message || '连接探测未通过',
      );
      await refresh();
    },
    onError: (error) => message.error(actionError(error)),
  });

  const checkMutation = useMutation({
    mutationFn: async (device?: EdgeDevice) =>
      device
        ? (await request.post(`/v1/edge-devices/${device.id}/environment-check`)).data
        : (await request.post('/v1/edge-devices/environment-check-all', undefined, { params: { only_pending: true } }))
            .data,
    onSuccess: async (_, device) => {
      message.success(device ? '设备环境检查已排队' : '未就绪设备已进入检查队列');
      await refresh();
    },
    onError: (error) => message.error(actionError(error)),
  });

  const bootstrapMutation = useMutation({
    mutationFn: async (device: EdgeDevice) =>
      (
        await request.post(`/v1/edge-devices/${encodeURIComponent(device.id)}/agent-bootstrap`, {
          device_id: device.id,
          confirm_install: true,
          wait_for_heartbeat: true,
          provision_runtime: true,
          require_environment_ready: true,
        })
      ).data,
    onSuccess: async (_result, device) => {
      message.success(`已启动 ${deviceName(device)} 的 Board Agent 引导`);
      await refresh();
    },
    onError: (error) => message.error(actionError(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: async (device: EdgeDevice) => (await request.delete(`/v1/edge-devices/${device.id}`)).data,
    onSuccess: async () => {
      message.success('边端设备已删除');
      setDeleteDevice(null);
      setDetailDevice(null);
      await refresh();
    },
    onError: (error) => message.error(actionError(error)),
  });

  const openEditor = (device?: EdgeDevice) => {
    if (device?.is_busy) {
      message.info('设备正在执行边端任务，暂不能编辑连接或运行参数');
      return;
    }
    const current = device || null;
    setEditingDevice(current);
    form.setFieldsValue({
      name: current?.name || '',
      validation_channel: current?.validation_channel || 'ssh',
      host: current?.host || '',
      port: current?.port || 22,
      user: current?.user || 'linaro',
      password: '',
      private_key_path: '',
      clear_private_key_path: false,
      edge_root: canonicalEdgeRoot || current?.edge_root || '',
      docker_container: current?.docker_container || 'bm1688',
      connect_timeout_sec: current?.connect_timeout_sec || 30,
      validation_timeout_sec: current?.validation_timeout_sec || 7200,
      enabled: current?.enabled !== false,
      auto_after_export: current?.auto_after_export !== false,
      run_coco_eval: Boolean(current?.run_coco_eval),
      docker_use_sudo: current?.docker_use_sudo !== false,
    });
    setDetailDevice(null);
    setEditorOpen(true);
  };

  useEffect(() => {
    if (!editorOpen || !canonicalEdgeRoot) return;
    form.setFieldValue('edge_root', canonicalEdgeRoot);
  }, [canonicalEdgeRoot, editorOpen, form]);

  const columns: ColumnsType<EdgeDevice> = [
    {
      title: '设备',
      key: 'device',
      width: 190,
      render: (_, device) => (
        <div>
          <Space size={6} wrap>
            <strong>{deviceName(device)}</strong>
            {device.id === activeDevice?.id ? <Tag color="blue">当前</Tag> : null}
          </Space>
          <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
            {device.id}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '连接方式',
      key: 'connection',
      width: 180,
      render: (_, device) => (
        <div>
          <Tag color={connectionReady(device) ? 'green' : 'default'}>
            {usesDeviceProgram(device) ? '设备主动连接' : 'SSH 连接'}
          </Tag>
          <Typography.Text type="secondary" style={{ display: 'block', marginTop: 3, fontSize: 12 }}>
            {connectionStatusText(device)}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: '连接凭据',
      key: 'credential',
      width: 140,
      render: (_, device) =>
        usesDeviceProgram(device) ? (
          <Tag>无需密码</Tag>
        ) : (
          <Space direction="vertical" size={2}>
            <Tag color={device.password_runtime_available ? 'green' : device.password_configured ? 'gold' : 'red'}>
              {device.password_runtime_available
                ? '密码可用'
                : device.password_configured
                ? '密码不可用'
                : '未配置密码'}
            </Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {device.password_masked_hint || '-'}
            </Typography.Text>
            {device.private_key_path_configured ? (
              <Tag color={device.private_key_path_runtime_available ? 'green' : 'gold'}>
                {device.private_key_path_runtime_available ? '私钥路径可用' : '私钥路径不可读'}
              </Tag>
            ) : null}
          </Space>
        ),
    },
    {
      title: 'BM1688 环境',
      key: 'environment',
      width: 180,
      render: (_, device) => (
        <div>
          <Tag
            color={
              environmentReady(device)
                ? 'green'
                : CHECKING_STATUSES.has(String(device.environment_status || ''))
                ? 'blue'
                : 'gold'
            }
          >
            {environmentReady(device)
              ? '环境就绪'
              : CHECKING_STATUSES.has(String(device.environment_status || ''))
              ? '检查中'
              : '未就绪'}
          </Tag>
          <Typography.Text
            ellipsis={{ tooltip: device.environment_message }}
            type="secondary"
            style={{ display: 'block', marginTop: 3, maxWidth: 170, fontSize: 12 }}
          >
            {usesDeviceProgram(device)
              ? device.board_agent_message || '等待设备上报'
              : device.environment_message || '尚未检查'}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: 'Agent 引导',
      key: 'agent-bootstrap',
      width: 170,
      render: (_, device) => {
        const status = bootstrapStatus(device);
        const color =
          status === 'READY' ? 'green' : status === 'FAILED' ? 'red' : bootstrapInProgress(device) ? 'blue' : 'gold';
        return (
          <div>
            <Tag color={color}>
              {usesDeviceProgram(device) && status === 'NOT_STARTED' ? '设备主动连接' : bootstrapLabel(device)}
            </Tag>
            <Typography.Text
              ellipsis={{ tooltip: bootstrapMessage(device) }}
              type="secondary"
              style={{ display: 'block', marginTop: 3, maxWidth: 160, fontSize: 12 }}
            >
              {bootstrapMessage(device)}
            </Typography.Text>
          </div>
        );
      },
    },
    {
      title: '调度状态',
      key: 'schedule',
      width: 130,
      render: (_, device) => (
        <Tag color={device.is_busy ? 'blue' : isDispatchable(device) ? 'green' : 'default'}>
          {schedulingText(device)}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 250,
      render: (_, device) => (
        <Space size={2}>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => setDetailDevice(device)}>
            查看
          </Button>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            disabled={device.is_busy === true}
            title={device.is_busy ? '设备正在执行边端任务，暂不能编辑' : undefined}
            onClick={() => openEditor(device)}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            loading={probeMutation.isPending && probeMutation.variables?.id === device.id}
            disabled={device.is_busy === true}
            title={device.is_busy ? '设备正在执行边端任务，任务结束后再操作' : undefined}
            onClick={() => probeMutation.mutate(device)}
          >
            探测
          </Button>
          {bootstrapNeedsAction(device) ? (
            <Button
              type="link"
              size="small"
              loading={bootstrapMutation.isPending && bootstrapMutation.variables?.id === device.id}
              onClick={() => bootstrapMutation.mutate(device)}
            >
              {bootstrapStatus(device) === 'FAILED' ? '重试引导' : '安装 Agent'}
            </Button>
          ) : null}
          <Dropdown
            menu={{
              items: [
                ...(device.id === activeDevice?.id
                  ? []
                  : [
                      {
                        key: 'active',
                        label: '设为当前设备',
                        icon: <CheckCircleOutlined />,
                        disabled: device.is_busy === true,
                        title: device.is_busy ? '设备正在执行边端任务，任务结束后再操作' : undefined,
                      },
                    ]),
                {
                  key: 'delete',
                  label: '删除设备',
                  danger: true,
                  icon: <DeleteOutlined />,
                  disabled: device.is_busy === true,
                  title: device.is_busy ? '设备正在执行边端任务，任务结束后再操作' : undefined,
                },
              ],
              onClick: ({ key }) => {
                if (device.is_busy === true) return;
                if (key === 'active') setActiveMutation.mutate(device);
                else if (key === 'delete') setDeleteDevice(device);
              },
            }}
          >
            <Button type="text" size="small" icon={<MoreOutlined />} aria-label={`更多操作：${deviceName(device)}`} />
          </Dropdown>
        </Space>
      ),
    },
  ];

  return (
    <>
      <TablePanel>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            gap: 16,
            alignItems: 'flex-start',
            flexWrap: 'wrap',
          }}
        >
          <div>
            <h2>
              <SettingOutlined style={{ marginRight: 8 }} />
              边端设备管理
            </h2>
            <p>统一配置用于 BM1688 真机检查的设备。模型中心只选择和查看检查结果，不重复维护设备。</p>
          </div>
          <Space wrap>
            <Button onClick={() => navigate('/admin')}>返回系统管理</Button>
            <Button icon={<UnorderedListOutlined />} onClick={() => navigate('/edge-task-center')}>
              边端任务中心
            </Button>
            <Button
              icon={<ReloadOutlined />}
              loading={devicesQuery.isFetching}
              onClick={() => void devicesQuery.refetch()}
            >
              刷新状态
            </Button>
            <Button loading={checkMutation.isPending} onClick={() => checkMutation.mutate(undefined)}>
              检查未就绪设备
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor()}>
              新增设备
            </Button>
          </Space>
        </div>

        {activeDevice ? (
          <Alert
            style={{ marginTop: 16 }}
            type={activeDevice.is_busy ? 'info' : isDispatchable(activeDevice) ? 'success' : 'warning'}
            showIcon
            message={readinessTitle(activeDevice)}
            description={`${deviceName(activeDevice)}：${readinessReason(activeDevice)}`}
            action={
              <Space size={6}>
                {bootstrapNeedsAction(activeDevice) ? (
                  <Button
                    size="small"
                    loading={bootstrapMutation.isPending}
                    onClick={() => bootstrapMutation.mutate(activeDevice)}
                  >
                    {bootstrapStatus(activeDevice) === 'FAILED' ? '重试引导' : '安装 Agent'}
                  </Button>
                ) : null}
                <Button size="small" onClick={() => setDetailDevice(activeDevice)}>
                  查看设备
                </Button>
              </Space>
            }
          />
        ) : (
          <Alert
            style={{ marginTop: 16 }}
            type="warning"
            showIcon
            message="尚未设置当前设备"
            description="新增第一台设备后会自动设为当前设备。"
          />
        )}

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: 10,
            marginTop: 16,
          }}
        >
          <Statistic title="设备总数" value={total} />
          <Statistic title="连接正常" value={devices.filter(connectionReady).length} />
          <Statistic title="环境就绪" value={devices.filter(environmentReady).length} />
          <Statistic title="可调度" value={devices.filter(isDispatchable).length} />
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginTop: 18, flexWrap: 'wrap' }}>
          <Space wrap>
            <ServerSearchInput
              value={keywordDraft}
              onChange={(value) => {
                setKeywordDraft(value);
                if (!value.trim()) {
                  setKeyword('');
                  setPage(0);
                }
              }}
              onSearch={(value) => {
                const normalized = value.trim();
                setKeywordDraft(value);
                setKeyword(normalized);
                setPage(0);
              }}
              placeholder="搜索设备名称、地址或账号"
              ariaLabel="搜索边端设备"
              style={{ width: 260, maxWidth: '100%' }}
            />
            <Select
              value={statusFilter}
              onChange={(value) => {
                setStatusFilter(value);
                setPage(0);
              }}
              style={{ width: 150 }}
              options={[
                { value: 'all', label: '全部状态' },
                { value: 'ready', label: '可调度' },
                { value: 'blocked', label: '未就绪' },
                { value: 'active', label: '当前设备' },
                { value: 'disabled', label: '已停用' },
              ]}
            />
          </Space>
          <Typography.Text type="secondary">共 {total} 台</Typography.Text>
        </div>

        {devicesQuery.error ? (
          <Alert
            style={{ marginTop: 14 }}
            type="error"
            showIcon
            message={responseStatus(devicesQuery.error) === 403 ? '当前账号没有边端设备查看权限' : '边端设备加载失败'}
            action={
              <Button size="small" onClick={() => void devicesQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : (
          <Table<EdgeDevice>
            rowKey="id"
            size="small"
            loading={devicesQuery.isLoading}
            dataSource={filteredDevices}
            columns={columns}
            pagination={{
              current: page + 1,
              pageSize,
              total,
              showSizeChanger: true,
              onChange: (nextPage, nextSize) => {
                const resolvedSize = nextSize || pageSize;
                setPage(resolvedSize !== pageSize ? 0 : nextPage - 1);
                setPageSize(resolvedSize);
              },
            }}
            scroll={{ x: 1240 }}
            locale={{ emptyText: keyword ? '没有匹配的边端设备' : '暂无边端设备' }}
          />
        )}
      </TablePanel>

      <Modal
        title={detailDevice ? `设备详情 · ${deviceName(detailDevice)}` : '设备详情'}
        open={Boolean(detailDevice)}
        width={820}
        centered
        footer={
          detailDevice ? (
            <Space wrap>
              {detailDevice.id !== activeDevice?.id ? (
                <Button
                  disabled={detailDevice.is_busy === true}
                  title={detailDevice.is_busy ? '设备正在执行边端任务，任务结束后再操作' : undefined}
                  onClick={() => setActiveMutation.mutate(detailDevice)}
                >
                  设为当前设备
                </Button>
              ) : null}
              <Button
                loading={probeMutation.isPending}
                disabled={detailDevice.is_busy === true}
                title={detailDevice.is_busy ? '设备正在执行边端任务，任务结束后再操作' : undefined}
                onClick={() => probeMutation.mutate(detailDevice)}
              >
                {usesDeviceProgram(detailDevice) ? '刷新在线状态' : '连接探测'}
              </Button>
              {!usesDeviceProgram(detailDevice) ? (
                <Button
                  loading={checkMutation.isPending}
                  disabled={detailDevice.is_busy === true}
                  title={detailDevice.is_busy ? '设备正在执行边端任务，任务结束后再操作' : undefined}
                  onClick={() => checkMutation.mutate(detailDevice)}
                >
                  检查并准备环境
                </Button>
              ) : null}
              {bootstrapNeedsAction(detailDevice) ? (
                <Button loading={bootstrapMutation.isPending} onClick={() => bootstrapMutation.mutate(detailDevice)}>
                  {bootstrapStatus(detailDevice) === 'FAILED' ? '重试 Agent 引导' : '安装 Board Agent'}
                </Button>
              ) : null}
              <Button
                type="primary"
                disabled={detailDevice.is_busy === true}
                title={detailDevice.is_busy ? '设备正在执行边端任务，暂不能编辑' : undefined}
                onClick={() => openEditor(detailDevice)}
              >
                编辑设备
              </Button>
            </Space>
          ) : null
        }
        onCancel={() => setDetailDevice(null)}
      >
        {detailDevice ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type={detailDevice.is_busy ? 'info' : isDispatchable(detailDevice) ? 'success' : 'warning'}
              showIcon
              message={
                detailDevice.is_busy
                  ? '设备正在执行边端任务'
                  : isDispatchable(detailDevice)
                  ? '设备可调度'
                  : '设备未就绪'
              }
              description={readinessReason(detailDevice)}
            />
            <Alert
              type={
                agentBootstrapControlPlaneDisabled(detailDevice) &&
                !usesDeviceProgram(detailDevice) &&
                bootstrapStatus(detailDevice) !== 'READY'
                  ? 'warning'
                  : bootstrapStatus(detailDevice) === 'FAILED'
                  ? 'error'
                  : bootstrapStatus(detailDevice) === 'READY'
                  ? 'success'
                  : 'info'
              }
              showIcon
              message={
                agentBootstrapControlPlaneDisabled(detailDevice) &&
                !usesDeviceProgram(detailDevice) &&
                bootstrapStatus(detailDevice) !== 'READY'
                  ? 'Board Agent：自动配置未完成'
                  : `Board Agent：${bootstrapLabel(detailDevice)}`
              }
              description={
                detailDevice.agent_bootstrap_code
                  ? `${bootstrapMessage(detailDevice)}（${detailDevice.agent_bootstrap_code}）`
                  : bootstrapMessage(detailDevice)
              }
            />
            <Descriptions title="连接与凭据" bordered size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="连接方式">
                {usesDeviceProgram(detailDevice) ? '设备主动连接' : 'SSH 连接'}
              </Descriptions.Item>
              <Descriptions.Item label="连接状态">
                {connectionStatusLabel(detailDevice)}
              </Descriptions.Item>
              <Descriptions.Item label="设备地址">
                {usesDeviceProgram(detailDevice) ? '不适用' : detailDevice.host || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="SSH 账号">
                {usesDeviceProgram(detailDevice) ? '不适用' : detailDevice.user || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="密码状态">
                {usesDeviceProgram(detailDevice)
                  ? '无需密码'
                  : detailDevice.password_runtime_available
                  ? '密码可用'
                  : detailDevice.password_configured
                  ? '密码不可用'
                  : '未配置密码'}
              </Descriptions.Item>
              <Descriptions.Item label="私钥路径">
                {usesDeviceProgram(detailDevice)
                  ? '不适用'
                  : detailDevice.private_key_path_configured
                  ? detailDevice.private_key_path_runtime_available
                    ? '已配置且可读（路径已隐藏）'
                    : '已配置但服务端不可读'
                  : '未配置'}
              </Descriptions.Item>
              <Descriptions.Item label="最近探测">
                {dateText(
                  usesDeviceProgram(detailDevice)
                    ? detailDevice.board_agent_last_heartbeat_at
                    : detailDevice.last_probe_checked_at || detailDevice.probe_checked_at,
                )}
              </Descriptions.Item>
            </Descriptions>
            <div>
              <h3 style={{ margin: '0 0 10px' }}>BM1688 环境检查</h3>
              <EnvironmentChecks device={detailDevice} />
            </div>
            <Descriptions title="运行设置" bordered size="small" column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="边端工作目录" span={2}>
                {detailDevice.edge_root || canonicalEdgeRoot || '由服务端配置'}
              </Descriptions.Item>
              <Descriptions.Item label="运行容器">{detailDevice.docker_container || '宿主机'}</Descriptions.Item>
              <Descriptions.Item label="导出后自动检查">
                {detailDevice.auto_after_export ? '开启' : '关闭'}
              </Descriptions.Item>
              <Descriptions.Item label="连接超时">{detailDevice.connect_timeout_sec || 30} 秒</Descriptions.Item>
              <Descriptions.Item label="检查超时">{detailDevice.validation_timeout_sec || 7200} 秒</Descriptions.Item>
              <Descriptions.Item label="最近使用">{dateText(detailDevice.last_used_at)}</Descriptions.Item>
              <Descriptions.Item label="最近边端检查">{dateText(detailDevice.last_validation_at)}</Descriptions.Item>
            </Descriptions>
          </Space>
        ) : null}
      </Modal>

      <Modal
        title={editingDevice ? '编辑边端设备' : '新增边端设备'}
        open={editorOpen}
        width={720}
        centered
        destroyOnClose
        okText={editingDevice ? '保存' : '新增'}
        cancelText="取消"
        confirmLoading={saveMutation.isPending}
        onCancel={() => {
          if (!saveMutation.isPending) {
            setEditorOpen(false);
            setEditingDevice(null);
          }
        }}
        onOk={() => void form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', columnGap: 16 }}>
            <Form.Item name="name" label="设备名称" rules={[{ max: 64, message: '设备名称不能超过 64 个字符' }]}>
              <Input placeholder="例如：龙岗项目 3 号设备" />
            </Form.Item>
            <Form.Item name="validation_channel" label="连接方式" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'ssh', label: '平台通过 SSH 连接' },
                  { value: 'board_agent', label: '设备程序主动连接' },
                ]}
              />
            </Form.Item>
            {validationChannel === 'ssh' ? (
              <>
                <Form.Item
                  name="host"
                  label="设备地址"
                  rules={[{ required: true, whitespace: true, message: '请输入设备地址' }]}
                >
                  <Input placeholder="IP 地址或主机名" autoComplete="off" />
                </Form.Item>
                <Form.Item name="port" label="SSH 端口" rules={[{ required: true, message: '请输入 SSH 端口' }]}>
                  <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item
                  name="user"
                  label="SSH 账号"
                  rules={[{ required: true, whitespace: true, message: '请输入 SSH 账号' }]}
                >
                  <Input autoComplete="username" />
                </Form.Item>
                <Form.Item
                  name="password"
                  label="SSH 密码"
                  extra={
                    editingDevice
                      ? '留空表示继续使用已保存的密码。'
                      : '密码只在保存时提交，服务端加密存储，不会在页面返回明文。'
                  }
                  rules={[]}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Form.Item
                  name="private_key_path"
                  label="SSH 私钥路径（服务端）"
                  extra={
                    editingDevice?.private_key_path_configured
                      ? '留空表示继续使用已保存的私钥路径；路径必须是 LabelU 服务端可读取的文件。'
                      : '可与密码二选一；只提交服务端路径，不上传私钥内容。'
                  }
                  rules={[
                    {
                      validator: async (_, value) => {
                        const hasPassword = Boolean(form.getFieldValue('password')?.trim());
                        const hasExistingCredential = Boolean(
                          editingDevice?.password_configured || editingDevice?.private_key_path_configured,
                        );
                        const clearingKey = Boolean(form.getFieldValue('clear_private_key_path'));
                        if (!hasPassword && !String(value || '').trim() && !hasExistingCredential) {
                          throw new Error('请输入 SSH 密码或私钥路径');
                        }
                        if (!hasPassword && !String(value || '').trim() && clearingKey && !editingDevice?.password_configured) {
                          throw new Error('清除私钥后必须保留 SSH 密码');
                        }
                      },
                    },
                  ]}
                >
                  <Input placeholder="例如：/opt/labelu/.ssh/id_ed25519" autoComplete="off" />
                </Form.Item>
                {editingDevice?.private_key_path_configured ? (
                  <Form.Item name="clear_private_key_path" valuePropName="checked" style={{ marginTop: 30 }}>
                    <Switch checkedChildren="清除已保存私钥" unCheckedChildren="保留已保存私钥" />
                  </Form.Item>
                ) : null}
              </>
            ) : (
              <Alert
                style={{ gridColumn: '1 / -1', marginBottom: 16 }}
                type="info"
                showIcon
                message="设备程序主动连接"
                description="这种方式不需要填写地址、端口、账号和密码；需要先在 BM1688 设备上部署配套程序，由设备主动上报心跳并领取检查任务。"
              />
            )}
          </div>

          <Typography.Title level={5}>运行设置</Typography.Title>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', columnGap: 16 }}>
            <Form.Item name="edge_root" label="边端工作目录">
              <Input disabled placeholder="由服务端配置" />
            </Form.Item>
            <Form.Item name="docker_container" label="运行容器名称">
              <Input placeholder="留空表示在宿主机运行" />
            </Form.Item>
            <Form.Item name="connect_timeout_sec" label="连接超时（秒）">
              <InputNumber min={1} max={3600} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="validation_timeout_sec" label="检查超时（秒）">
              <InputNumber min={1} max={86400} style={{ width: '100%' }} />
            </Form.Item>
          </div>
          <Space size={[24, 10]} wrap>
            <Form.Item name="enabled" label="启用设备" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="auto_after_export" label="编译完成后自动检查" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="run_coco_eval" label="执行标准精度评估" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="docker_use_sudo" label="容器命令使用管理员权限" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title="删除边端设备"
        open={Boolean(deleteDevice)}
        centered
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: deleteMutation.isPending }}
        onCancel={() => !deleteMutation.isPending && setDeleteDevice(null)}
        onOk={() => deleteDevice && deleteMutation.mutate(deleteDevice)}
      >
        <p>确认删除“{deleteDevice ? deviceName(deleteDevice) : ''}”？正在执行任务或当前使用中的设备不能删除。</p>
      </Modal>
    </>
  );
}
