import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Alert, Descriptions, Empty, Modal, Spin, Table, Typography } from 'antd';
import type { DescriptionsProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  getAiProcess,
  type AiProcessBox,
  type AiProcessDetail,
  type AiProcessItem,
  type AiProcessResponse,
} from '@/api/services/samples';

interface AiDetectionProcessModalProps {
  open: boolean;
  taskId: number;
  sampleId?: number;
  onClose: () => void;
}

const MONO_FONT_FAMILY = 'SFMono-Regular, Menlo, Consolas, "Liberation Mono", "Courier New", monospace';

const preStyle: CSSProperties = {
  margin: 0,
  maxHeight: 260,
  overflow: 'auto',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  fontFamily: MONO_FONT_FAMILY,
  fontSize: 12,
  lineHeight: 1.6,
  background: '#fafafa',
  border: '1px solid #f0f0f0',
  borderRadius: 6,
  padding: 12,
};

function displayText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  return String(value);
}

function formatSize(width?: number | null, height?: number | null): string {
  if (!width || !height) {
    return '-';
  }
  return `${width} × ${height}`;
}

function formatLatency(latencyMs?: number | null): string {
  if (typeof latencyMs !== 'number' || !Number.isFinite(latencyMs)) {
    return '-';
  }
  return `${latencyMs} ms`;
}

function formatSha(sha?: string | null): string {
  if (!sha) {
    return '-';
  }
  return sha.length > 12 ? `${sha.slice(0, 12)}…` : sha;
}

function coordinateSpaceText(space?: string | null): string {
  if (space === 'normalized_0_1000') {
    return '0-1000 归一化（已自动换算）';
  }
  if (space === 'pixels') {
    return '原图像素';
  }
  return displayText(space);
}

function itemText(item: AiProcessItem): string {
  const meta = [item.tool, item.index === null || item.index === undefined ? '' : `#${item.index}`]
    .filter(Boolean)
    .join(' ');
  const reason = item.reason || '未提供原因';
  return meta ? `${meta}：${reason}` : reason;
}

function ReasonList({ items }: { items?: AiProcessItem[] | null }) {
  if (!items || items.length === 0) {
    return <Typography.Text type="secondary">无</Typography.Text>;
  }
  return (
    <ul style={{ margin: 0, paddingLeft: 20 }}>
      {items.map((item, index) => (
        <li key={`${item.index ?? 'item'}-${index}`}>{itemText(item)}</li>
      ))}
    </ul>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ marginTop: 20 }}>
      <Typography.Title level={5} style={{ marginTop: 0, marginBottom: 8 }}>
        {title}
      </Typography.Title>
      {children}
    </section>
  );
}

function PreBlock({ text, emptyText }: { text?: string | null; emptyText: string }) {
  if (!text) {
    return <Typography.Text type="secondary">{emptyText}</Typography.Text>;
  }
  return <pre style={preStyle}>{text}</pre>;
}

function buildBoxColumns(): ColumnsType<AiProcessBox> {
  const numericRender = (value: number | string | null | undefined) =>
    value === null || value === undefined || value === '' ? '-' : String(value);
  return [
    {
      title: '序号',
      key: 'index',
      width: 64,
      render: (_value, _box, index) => index + 1,
    },
    {
      title: '标签',
      dataIndex: 'label',
      render: (value: string | null | undefined) => displayText(value),
    },
    { title: 'x', dataIndex: 'x', render: numericRender },
    { title: 'y', dataIndex: 'y', render: numericRender },
    { title: 'width', dataIndex: 'width', render: numericRender },
    { title: 'height', dataIndex: 'height', render: numericRender },
    { title: 'score', dataIndex: 'score', render: numericRender },
  ];
}

function buildSummaryItems(
  record: AiProcessResponse,
  process: AiProcessDetail,
): NonNullable<DescriptionsProps['items']> {
  const contract = process.contract_prompt || {};
  const contractRevision = contract.contract_revision;
  const contractId = contract.contract_id;
  const contractValue =
    contractRevision === null || contractRevision === undefined
      ? '-'
      : contractId === null || contractId === undefined
      ? `revision ${contractRevision}`
      : `#${contractId} · revision ${contractRevision}`;
  const boxCount =
    typeof process.box_count === 'number' && Number.isFinite(process.box_count)
      ? process.box_count
      : process.boxes?.length;

  return [
    { key: 'model', label: '模型', children: displayText(process.model) },
    { key: 'provider', label: '服务提供方', children: displayText(process.provider) },
    { key: 'protocol', label: '接口协议', children: displayText(process.request_protocol) },
    { key: 'endpoint', label: '服务地址', children: displayText(process.endpoint_host) },
    {
      key: 'image',
      label: '原图尺寸',
      children: formatSize(process.image_width, process.image_height),
    },
    {
      key: 'modelImage',
      label: '模型输入尺寸',
      children: formatSize(process.model_image_width, process.model_image_height),
    },
    {
      key: 'coordinate',
      label: '坐标空间',
      children: coordinateSpaceText(process.coordinate_space),
    },
    { key: 'latency', label: '耗时', children: formatLatency(process.latency_ms) },
    { key: 'boxCount', label: '框数', children: displayText(boxCount) },
    {
      key: 'promptBinding',
      label: '提示词绑定',
      children: `#${displayText(contract.prompt_binding_id)} · sha256 ${formatSha(contract.prompt_sha256)}`,
    },
    { key: 'contract', label: '契约 revision', children: contractValue },
    { key: 'createdAt', label: '生成时间', children: displayText(record.created_at) },
  ];
}

export default function AiDetectionProcessModal({ open, taskId, sampleId, onClose }: AiDetectionProcessModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [record, setRecord] = useState<AiProcessResponse | null>(null);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    if (!taskId || !sampleId) {
      setRecord(null);
      setError('缺少任务或图片信息，无法查询 AI 过程');
      setLoading(false);
      return undefined;
    }

    let disposed = false;
    setLoading(true);
    setError(null);
    setRecord(null);
    void getAiProcess(taskId, sampleId)
      .then((response) => {
        if (!disposed) {
          setRecord(response?.data ?? null);
        }
      })
      .catch((requestError: any) => {
        if (!disposed) {
          const message = requestError?.response?.data?.msg || requestError?.message || 'AI 过程加载失败';
          setError(message);
        }
      })
      .finally(() => {
        if (!disposed) {
          setLoading(false);
        }
      });

    return () => {
      disposed = true;
    };
  }, [open, sampleId, taskId]);

  const boxColumns = useMemo(() => buildBoxColumns(), []);
  const summaryItems = useMemo(() => (record?.process ? buildSummaryItems(record, record.process) : []), [record]);

  const process = record?.process ?? null;
  const boxes = useMemo(
    () => (record?.process?.boxes ?? []).map((box, index) => ({ ...box, key: `box-${index}` })),
    [record],
  );

  let body: ReactNode;
  if (loading) {
    body = (
      <div style={{ textAlign: 'center', padding: '48px 0' }}>
        <Spin />
        <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
          正在加载 AI 过程...
        </Typography.Text>
      </div>
    );
  } else if (error) {
    body = <Alert type="error" showIcon message="AI 过程加载失败" description={error} />;
  } else if (!process) {
    body = <Alert type="info" showIcon message="本次运行未记录详细过程（旧版本数据或未执行成功）" />;
  } else {
    body = (
      <div data-testid="ai-detection-process-content">
        <Descriptions bordered size="small" column={2} items={summaryItems} />

        <Section title="最终发送给大模型的提示词">
          <PreBlock text={process.instruction} emptyText="无提示词记录" />
        </Section>

        <Section title="模型原始返回">
          <PreBlock text={process.raw_response_text} emptyText="无原始返回记录" />
        </Section>

        <Section title="解析后的目标框">
          {boxes.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次运行未解析出目标框" />
          ) : (
            <Table size="small" rowKey="key" columns={boxColumns} dataSource={boxes} pagination={false} />
          )}
        </Section>

        <Section title="丢弃 / 修复项">
          <Typography.Text strong>丢弃项</Typography.Text>
          <ReasonList items={process.dropped_items} />
          <Typography.Text strong style={{ display: 'block', marginTop: 8 }}>
            修复项
          </Typography.Text>
          <ReasonList items={process.repaired_items} />
        </Section>
      </div>
    );
  }

  return (
    <Modal title="AI目标检测过程" open={open} onCancel={onClose} footer={null} width={900} destroyOnClose>
      {body}
    </Modal>
  );
}
