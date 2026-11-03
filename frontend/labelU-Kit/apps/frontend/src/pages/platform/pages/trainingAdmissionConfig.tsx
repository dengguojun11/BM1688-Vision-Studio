import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Form, InputNumber, Space, Spin, Typography, message } from 'antd';

import request from '@/api/request';

interface TrainingAdmissionConfig {
  version: number;
  min_sample_count: number;
}

export default function TrainingAdmissionConfigPanel() {
  const [form] = Form.useForm<{ min_sample_count: number }>();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['training-admission-config'],
    queryFn: async () => {
      const response = await request.get('/v1/training-admission-config');
      return response.data as TrainingAdmissionConfig;
    },
  });
  const mutation = useMutation({
    mutationFn: async (values: { min_sample_count: number }) => {
      const response = await request.put('/v1/training-admission-config', values);
      return response.data as TrainingAdmissionConfig;
    },
    onSuccess: (data) => {
      message.success(`训练准入最低样本数已更新为 ${data.min_sample_count} 张`);
      form.setFieldsValue({ min_sample_count: data.min_sample_count });
      void queryClient.invalidateQueries({ queryKey: ['training-admission-config'] });
      void queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
    onError: () => message.error('训练准入配置保存失败，请检查后重试'),
  });

  useEffect(() => {
    if (query.data) form.setFieldsValue({ min_sample_count: query.data.min_sample_count });
  }, [form, query.data]);

  if (query.isLoading) return <div style={{ display: 'grid', placeItems: 'center', minHeight: 220 }}><Spin /></div>;

  return (
    <div style={{ maxWidth: 760 }}>
      <Typography.Title level={3} style={{ marginTop: 0 }}>训练准入配置</Typography.Title>
      <Typography.Paragraph type="secondary">
        控制任务最多可以进入训练候选的最低合格标注图片数。任务卡的“可训练”状态和训练任务提交都会使用此配置。
      </Typography.Paragraph>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 18 }}
        message="当前配置"
        description={`最低合格标注图片数：${query.data?.min_sample_count ?? 50} 张（配置版本 v${query.data?.version ?? 1}）`}
      />
      <Form form={form} layout="vertical" onFinish={(values) => mutation.mutate(values)}>
        <Form.Item
          name="min_sample_count"
          label="每个任务最低合格标注图片数"
          rules={[{ required: true, message: '请输入最低样本数' }]}
          extra="必须 ≥ 1。任务已标注样本需全部通过标签和几何审计，且数量达到该值后才显示可训练。"
        >
          <InputNumber min={1} max={100000} precision={0} style={{ width: 240 }} addonAfter="张" />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={mutation.isPending}>保存配置</Button>
          <Button onClick={() => form.setFieldsValue({ min_sample_count: query.data?.min_sample_count ?? 50 })}>重置</Button>
        </Space>
      </Form>
    </div>
  );
}
