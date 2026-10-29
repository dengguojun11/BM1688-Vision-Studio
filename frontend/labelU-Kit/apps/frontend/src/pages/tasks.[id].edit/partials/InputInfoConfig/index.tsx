import React, { memo, useContext } from 'react';
import { Form, Input, Pagination, Select, Spin } from 'antd';
import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';
import { useTranslation } from '@labelu/i18n';

import { MediaType } from '@/api/types';
import useAccessibleDatasetPicker from '@/hooks/useAccessibleDatasetPicker';

import { TaskCreationContext } from '../../taskCreation.context';

const Inner = styled(FlexLayout)`
  width: 740px;
  margin: auto;
`;

const InputInfoConfig = () => {
  const { basicFormInstance } = useContext(TaskCreationContext);
  const useTranslationCompat = useTranslation as unknown as () => {
    t: (key: string, options?: Record<string, unknown>) => string;
  };
  const { t } = useTranslationCompat();
  const watchedDatasetId = Form.useWatch('dataset_id', basicFormInstance);
  const selectedDatasetId = Number(watchedDatasetId || 0) > 0 ? Number(watchedDatasetId) : undefined;
  const datasetPicker = useAccessibleDatasetPicker({
    value: selectedDatasetId,
    queryKey: 'task-edit-basic',
  });
  const datasets = datasetPicker.options;
  const datasetDropdownFooter = (
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
        {datasetPicker.total ? `共 ${datasetPicker.total} 个数据集` : '暂无数据集'}
      </span>
      {datasetPicker.total > datasetPicker.pageSize && (
        <Pagination
          size="small"
          current={datasetPicker.page + 1}
          pageSize={datasetPicker.pageSize}
          total={datasetPicker.total}
          showSizeChanger={false}
          showLessItems
          onChange={(nextPage) => datasetPicker.onPageChange(nextPage - 1)}
        />
      )}
    </div>
  );

  return (
    <FlexLayout padding="1rem" flex="column">
      <Inner flex="column">
        <h2>{t('basicConfig')}</h2>
        <FlexLayout.Content>
          <Form form={basicFormInstance} labelCol={{ span: 3 }} wrapperCol={{ span: 21 }}>
            <Form.Item name="dataset_label_contract_id" hidden>
              <Input />
            </Form.Item>
            <Form.Item
              label={t('taskName')}
              name="name"
              required
              rules={[{ required: true, message: t('taskNameRequired') }]}
            >
              <Input placeholder={t('taskName50CharactersPlease')} maxLength={50} />
            </Form.Item>
            <Form.Item
              label={t('mediaType')}
              name="media_type"
              rules={[{ required: true, message: t('mediaTypeRequired') }]}
            >
              <Select
                placeholder={t('selectMediaTypePlease')}
                options={[
                  {
                    label: t('image'),
                    value: MediaType.IMAGE,
                  },
                  {
                    label: t('video'),
                    value: MediaType.VIDEO,
                  },
                  {
                    label: t('audio'),
                    value: MediaType.AUDIO,
                  },
                ]}
              />
            </Form.Item>
            <Form.Item
              label="归属数据集"
              name="dataset_id"
              extra="数据集决定模型类别；任务只能使用该数据集标签契约的全部或部分标签。"
            >
              <Select
                allowClear
                showSearch
                filterOption={false}
                searchValue={datasetPicker.searchValue}
                aria-label="搜索或选择数据集"
                placeholder={datasets.length ? '选择逻辑数据集（建议）' : '暂无可用数据集'}
                loading={datasetPicker.loading}
                notFoundContent={datasetPicker.loading ? <Spin size="small" /> : '暂无匹配数据集'}
                options={datasets.map((item) => ({
                  value: item.id,
                  label: `${item.name} · 标签契约 v${item.dataset_label_contract_revision || 0}`,
                }))}
                dropdownRender={(menu) => <>{menu}{datasetDropdownFooter}</>}
                onSearch={datasetPicker.onSearch}
                onChange={(value) => {
                  const dataset = datasetPicker.onChange(value);
                  if (dataset?.dataset_label_contract_id) {
                    basicFormInstance.setFieldValue(
                      'dataset_label_contract_id',
                      dataset.dataset_label_contract_id,
                    );
                  } else {
                    basicFormInstance.setFieldValue('dataset_label_contract_id', undefined);
                  }
                }}
              />
            </Form.Item>
            {selectedDatasetId && (
              <Form.Item shouldUpdate noStyle>
                {() => {
                  const dataset = datasetPicker.selectedDataset;
                  if (!dataset) return null;
                  return (
                    <div style={{ margin: '-4px 0 12px 25%', color: '#64748b', fontSize: 12 }}>
                      当前契约标签：
                      {(dataset.dataset_labels || [])
                        .map((item) => item.label_value || item.label_key)
                        .filter(Boolean)
                        .join('、') || '尚未配置，请先在“数据治理”中维护'}
                    </div>
                  );
                }}
              </Form.Item>
            )}
            <Form.Item label={t('taskDescription')} name="description">
              <Input.TextArea placeholder={t('taskDescription500CharactersPlease')} maxLength={500} rows={6} />
            </Form.Item>
            <Form.Item label={t('taskTip')} name="tips">
              <Input.TextArea placeholder={t('taskTipPlaceholder')} maxLength={1000} rows={6} />
            </Form.Item>
          </Form>
        </FlexLayout.Content>
      </Inner>
    </FlexLayout>
  );
};
export default memo(InputInfoConfig);
