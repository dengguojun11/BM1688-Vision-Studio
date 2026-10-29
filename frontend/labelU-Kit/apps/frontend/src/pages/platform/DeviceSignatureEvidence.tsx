import { EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Modal, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useEffect, useState } from 'react';
import styled from 'styled-components';

import request from '@/api/request';
import ServerSearchInput from '@/components/ServerSearchInput';
import { usePageVisible } from './polling';
import { ruleApiPaths } from './ruleContract';
import { listData, listTotal, QueueHint, QueueToolbar, responseStatus } from './shared';

export type DeviceSignatureReport = {
  id: number;
  report_id: string;
  sequence: number;
  project_id: number;
  device_id: string;
  rule_binding_id: number;
  rule_version_id: number;
  payload_sha256: string;
  key_id: string;
  key_state: string;
  reported_status: string;
  effective_status: string;
  failure_code?: string | null;
  reason?: string | null;
  reported_at?: string | null;
  received_at?: string | null;
};

const EvidencePanel = styled.section<{ $embedded: boolean }>`
  margin-top: ${({ $embedded }) => ($embedded ? '0' : '18px')};
  padding-top: ${({ $embedded }) => ($embedded ? '0' : '18px')};
  border-top: ${({ $embedded }) => ($embedded ? '0' : '1px solid #e8edf4')};

  .ant-table-wrapper {
    overflow-x: auto;
  }
`;

function formatTime(value?: string | null) {
  if (!value) return '-';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function truncateEvidence(value?: string | null, visible = 12) {
  if (!value) return '-';
  return value.length > visible ? `${value.slice(0, visible)}...` : value;
}

export function deviceSignatureEvidenceState({
  loading,
  errorStatus,
  rowCount,
}: {
  loading: boolean;
  errorStatus?: number;
  rowCount: number;
}) {
  if (errorStatus === 403) return 'forbidden';
  if (errorStatus) return 'error';
  if (loading) return 'loading';
  return rowCount ? 'ready' : 'empty';
}

function resultColor(value?: string | null) {
  const normalized = String(value || '').toUpperCase();
  if (normalized === 'ACK') return 'green';
  if (normalized === 'REJECTED') return 'red';
  return 'default';
}

function effectiveColor(value?: string | null) {
  const normalized = String(value || '').toUpperCase();
  if (normalized.startsWith('VERIFIED')) return 'green';
  if (normalized.startsWith('REJECTED')) return 'red';
  return 'blue';
}

function keyColor(value?: string | null) {
  const normalized = String(value || '').toUpperCase();
  if (normalized === 'ACTIVE') return 'green';
  if (normalized === 'ROTATING') return 'gold';
  if (['REVOKED', 'EXPIRED'].includes(normalized)) return 'red';
  return 'default';
}

export function useDeviceSignatureReports(
  projectId?: number,
  search = '',
  page = 0,
  pageSize = 20,
) {
  const pageVisible = usePageVisible();
  return useQuery({
    queryKey: ['rule-device-signature-reports', projectId, search, page, pageSize],
    // 规则验签接口仍按历史规则绑定要求 project_id；该字段只存在于请求，不展示在一期页面。
    queryFn: async () => {
      const response = await request.get(ruleApiPaths(projectId as number).deviceSignatureReports, {
        params: { page, size: pageSize, ...(search ? { q: search } : {}) },
      });
      const rows = listData(response) as DeviceSignatureReport[];
      return { rows, total: listTotal(response, rows.length) };
    },
    enabled: Boolean(projectId),
    staleTime: 10_000,
    retry: (failureCount, error) => responseStatus(error) !== 403 && failureCount < 1,
    refetchInterval: pageVisible ? 30_000 : false,
  });
}

export function DeviceSignatureEvidence({
  projectId,
  embedded = false,
}: {
  projectId?: number;
  embedded?: boolean;
}) {
  const [searchDraft, setSearchDraft] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const query = useDeviceSignatureReports(projectId, search, page, pageSize);
  const [selected, setSelected] = useState<DeviceSignatureReport | null>(null);
  useEffect(() => {
    if (!selected) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setSelected(null);
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [selected]);
  const rows = query.data?.rows || [];
  const total = query.data?.total ?? rows.length;
  const applySearch = (value: string) => {
    setSearchDraft(value);
    setSearch(value.trim());
    setPage(0);
  };
  const status = responseStatus(query.error);
  const state = deviceSignatureEvidenceState({ loading: query.isLoading, errorStatus: status, rowCount: rows.length });

  const columns: ColumnsType<DeviceSignatureReport> = [
    {
      title: '设备结果',
      dataIndex: 'reported_status',
      key: 'reported_status',
      width: 106,
      render: (value) => <Tag color={resultColor(value)}>{String(value || 'UNKNOWN').toUpperCase()}</Tag>,
    },
    {
      title: '生效状态',
      dataIndex: 'effective_status',
      key: 'effective_status',
      width: 176,
      render: (value) => <Tag color={effectiveColor(value)}>{String(value || 'UNKNOWN').toUpperCase()}</Tag>,
    },
    {
      title: '密钥状态',
      dataIndex: 'key_state',
      key: 'key_state',
      width: 110,
      render: (value) => <Tag color={keyColor(value)}>{String(value || 'UNKNOWN').toUpperCase()}</Tag>,
    },
    { title: '设备', dataIndex: 'device_id', key: 'device_id', width: 150, ellipsis: true },
    { title: '序列号', dataIndex: 'sequence', key: 'sequence', width: 82 },
    {
      title: '报告 / Payload',
      key: 'evidence',
      width: 220,
      render: (_, row) => (
        <div>
          <Typography.Text title={row.report_id}>{truncateEvidence(row.report_id, 16)}</Typography.Text>
          <br />
          <Typography.Text type="secondary" title={row.payload_sha256}>{truncateEvidence(row.payload_sha256, 16)}</Typography.Text>
        </div>
      ),
    },
    { title: '原因', dataIndex: 'reason', key: 'reason', width: 180, ellipsis: true, render: (value, row) => value || row.failure_code || '-' },
    { title: '接收时间', dataIndex: 'received_at', key: 'received_at', width: 174, render: formatTime },
    {
      title: '操作',
      key: 'action',
      fixed: 'right',
      width: 90,
      render: (_, row) => (
        <Button type="link" size="small" icon={<EyeOutlined />} aria-label={`查看验签报告 ${row.report_id}`} onClick={() => setSelected(row)}>
          查看
        </Button>
      ),
    },
  ];

  const content = state === 'forbidden' || state === 'error' ? (
    <Alert
      style={{ marginTop: 14 }}
      type={state === 'forbidden' ? 'warning' : 'error'}
      showIcon
      message={state === 'forbidden' ? '无权限查看设备验签证据' : '设备验签证据暂不可用'}
      description={state === 'forbidden' ? '当前账号无权查看验签报告，请联系管理人员。' : '报告读取失败，请稍后重试。'}
      action={<Button size="small" icon={<ReloadOutlined />} onClick={() => query.refetch()}>重试</Button>}
    />
  ) : state === 'loading' ? (
    <div role="status" aria-label="正在加载设备验签证据" style={{ padding: '28px 0', textAlign: 'center' }}><Spin /></div>
  ) : state === 'ready' ? (
    <Table<DeviceSignatureReport>
      rowKey="id"
      size="small"
      columns={columns}
      dataSource={rows}
      pagination={{
        current: page + 1,
        pageSize,
        total,
        showSizeChanger: true,
        showTotal: (value) => `共 ${value} 条验签报告`,
        onChange: (nextPage, nextSize) => {
          if (nextSize !== pageSize) {
            setPageSize(nextSize);
            setPage(0);
          } else {
            setPage(Math.max(0, nextPage - 1));
          }
        },
      }}
      scroll={{ x: 1288 }}
    />
  ) : (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无设备验签报告" />
  );

  return (
    <EvidencePanel aria-label="设备验签证据" $embedded={embedded}>
      <QueueToolbar
        style={{ marginTop: 0 }}
        children={<div style={{ display: 'contents' }}>
          <div>
            <h2 style={{ margin: 0 }}>设备验签证据</h2>
            <QueueHint>核对设备 ACK/REJECTED、实际生效状态与签名报文审计链</QueueHint>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <ServerSearchInput
              value={searchDraft}
              placeholder="搜索设备验签报告"
              ariaLabel="搜索设备验签报告"
              loading={query.isFetching}
              style={{ width: 260 }}
              onChange={(value) => { setSearchDraft(value); if (!value) applySearch(''); }}
              onSearch={applySearch}
            />
            <Button icon={<ReloadOutlined />} loading={query.isFetching} onClick={() => query.refetch()}>刷新证据</Button>
          </div>
        </div>}
      />
      {content}
      <Modal
        title="设备验签报告详情"
        width={720}
        open={Boolean(selected)}
        centered
        keyboard
        onCancel={() => setSelected(null)}
        footer={null}
        destroyOnClose
        styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' } }}
      >
        {selected && (
          <>
            <Descriptions title="设备与结果" bordered size="small" column={1}>
              <Descriptions.Item label="设备">{selected.device_id}</Descriptions.Item>
              <Descriptions.Item label="设备结果"><Tag color={resultColor(selected.reported_status)}>{selected.reported_status}</Tag></Descriptions.Item>
              <Descriptions.Item label="生效状态"><Tag color={effectiveColor(selected.effective_status)}>{selected.effective_status}</Tag></Descriptions.Item>
              <Descriptions.Item label="绑定 / 版本">{selected.rule_binding_id} / {selected.rule_version_id}</Descriptions.Item>
            </Descriptions>
            <Descriptions title="密钥状态" bordered size="small" column={1} style={{ marginTop: 20 }}>
              <Descriptions.Item label="密钥 ID">{selected.key_id}</Descriptions.Item>
              <Descriptions.Item label="密钥状态"><Tag color={keyColor(selected.key_state)}>{selected.key_state}</Tag></Descriptions.Item>
            </Descriptions>
            <Descriptions title="报文审计" bordered size="small" column={1} style={{ marginTop: 20 }}>
              <Descriptions.Item label="报告 ID">{selected.report_id}</Descriptions.Item>
              <Descriptions.Item label="序列号">{selected.sequence}</Descriptions.Item>
              <Descriptions.Item label="Payload SHA-256"><Typography.Text copyable>{selected.payload_sha256}</Typography.Text></Descriptions.Item>
            </Descriptions>
            <Descriptions title="时间与原因" bordered size="small" column={1} style={{ marginTop: 20 }}>
              <Descriptions.Item label="失败代码">{selected.failure_code || '-'}</Descriptions.Item>
              <Descriptions.Item label="原因">{selected.reason || '-'}</Descriptions.Item>
              <Descriptions.Item label="设备上报">{formatTime(selected.reported_at)}</Descriptions.Item>
              <Descriptions.Item label="平台接收">{formatTime(selected.received_at)}</Descriptions.Item>
            </Descriptions>
          </>
        )}
      </Modal>
    </EvidencePanel>
  );
}
