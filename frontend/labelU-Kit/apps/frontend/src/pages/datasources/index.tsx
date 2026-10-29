import { useState } from 'react';
import { Alert, Button, Pagination, Popconfirm, Table, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { FlexLayout } from '@labelu/components-react';
import { useTranslation } from '@labelu/i18n';
import formatter from '@labelu/formatter';

import CustomEmpty from '@/components/CustomEmpty';
import ServerSearchInput from '@/components/ServerSearchInput';
import type { DataSourceResponse, CreateDataSourceCommand, UpdateDataSourceCommand } from '@/api/types';
import { getDataSources, updateDataSource } from '@/api/services/datasource';
import { datasourceKey } from '@/api/queryKeyFactories/datasource';
import { useCreateDataSourceMutation, useDeleteDataSourceMutation } from '@/api/mutations/datasource';

import DataSourceFormModal from './DataSourceFormModal';
import { Wrapper, Header, Footer } from './style';

// TypeScript 4.8 cannot expand the full i18next resource generic here.
const useDataSourceTranslation = useTranslation as unknown as () => {
  t: (key: string) => string;
};

const DataSources = () => {
  const { t } = useDataSourceTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = searchParams.get('page') ? +searchParams.get('page')! : 1;
  const pageSize = searchParams.get('size') ? +searchParams.get('size')! : 20;
  const [search, setSearch] = useState(searchParams.get('q') || '');
  const [searchDraft, setSearchDraft] = useState(search);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingDs, setEditingDs] = useState<DataSourceResponse | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: datasourceKey.list({ page, size: pageSize, q: search }),
    queryFn: () => getDataSources({ page, size: pageSize, q: search }),
  });

  const createMutation = useCreateDataSourceMutation();
  const deleteMutation = useDeleteDataSourceMutation();
  const queryClient = useQueryClient();

  const updateMutation = useMutation({
    mutationFn: ({ dsId, payload }: { dsId: number; payload: UpdateDataSourceCommand }) =>
      updateDataSource(dsId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: datasourceKey.lists() });
    },
  });

  const handleCreate = () => {
    setEditingDs(null);
    setModalOpen(true);
  };

  const applySearch = (value: string) => {
    const next = value.trim();
    setSearchDraft(value);
    setSearch(next);
    searchParams.set('page', '1');
    if (next) searchParams.set('q', next);
    else searchParams.delete('q');
    setSearchParams(searchParams);
  };

  const handleEdit = (record: DataSourceResponse) => {
    setEditingDs(record);
    setModalOpen(true);
  };

  const handleDelete = (dsId: number) => {
    deleteMutation.mutate(dsId, {
      onSuccess: () => {
        message.success(t('dataSourceDeleted'));
      },
    });
  };

  const handleSubmit = (values: CreateDataSourceCommand | UpdateDataSourceCommand) => {
    if (editingDs) {
      updateMutation.mutate(
        { dsId: editingDs.id, payload: values as UpdateDataSourceCommand },
        {
          onSuccess: () => {
            message.success(t('dataSourceUpdated'));
            setModalOpen(false);
          },
        },
      );
    } else {
      createMutation.mutate(values as CreateDataSourceCommand, {
        onSuccess: () => {
          message.success(t('dataSourceCreated'));
          setModalOpen(false);
        },
      });
    }
  };

  const columns = [
    {
      title: t('dataSourceName'),
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: t('endpoint'),
      dataIndex: 'endpoint',
      key: 'endpoint',
      render: (v: string) => v || '-',
    },
    {
      title: t('bucket'),
      dataIndex: 'bucket',
      key: 'bucket',
    },
    {
      title: t('region'),
      dataIndex: 'region',
      key: 'region',
      render: (v: string) => v || '-',
    },
    {
      title: t('createdAt'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => (v ? formatter.format('dateTime', v, { style: 'YYYY-MM-DD HH:mm' }) : '-'),
    },
    {
      title: t('actions'),
      key: 'actions',
      width: 160,
      render: (_: unknown, record: DataSourceResponse) => (
        <FlexLayout.Item flex gap=".5rem">
          <Button type="link" size="small" onClick={() => handleEdit(record)}>
            {t('editDataSource')}
          </Button>
          <Popconfirm title={t('deleteDataSourceConfirm')} onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger>
              {t('delete')}
            </Button>
          </Popconfirm>
        </FlexLayout.Item>
      ),
    },
  ];

  const total = data?.meta_data?.total ?? 0;

  return (
    <Wrapper flex="column">
      <Header>
        <FlexLayout.Item flex="row" justify="space-between" items="center" gap="1rem" style={{ width: '100%', flexWrap: 'wrap' }}>
          <ServerSearchInput
            value={searchDraft}
            onChange={(value) => {
              setSearchDraft(value);
              if (!value) applySearch('');
            }}
            onSearch={applySearch}
            placeholder="按名称、地址、区域或存储桶搜索"
            ariaLabel="搜索数据源"
            loading={isLoading}
            style={{ width: 320, maxWidth: '100%' }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            {t('createDataSource')}
          </Button>
        </FlexLayout.Item>
      </Header>
      <Alert
        style={{ marginBottom: '1rem' }}
        type="info"
        message={
          <div>
            {t('s3VizTip')}{' '}
            <a href="https://github.com/opendatalab/vis3#features" target="_blank" rel="noreferrer">
              vis3
            </a>
          </div>
        }
      />
      <FlexLayout.Content scroll flex="column">
        <Table
          dataSource={data?.data ?? []}
          columns={columns}
          rowKey="id"
          loading={isLoading}
          pagination={false}
          locale={{ emptyText: <CustomEmpty description={search ? '没有匹配的数据源' : t('noDataSources')} /> }}
        />
      </FlexLayout.Content>
      {total > pageSize && (
        <Footer flex="row" justify="flex-end">
          <Pagination
            current={page}
            total={total}
            pageSize={pageSize}
            onChange={(value, _pageSize) => {
              searchParams.set('page', String(value));
              searchParams.set('size', String(_pageSize));
              setSearchParams(searchParams);
            }}
          />
        </Footer>
      )}
      <DataSourceFormModal
        open={modalOpen}
        editingDataSource={editingDs}
        onCancel={() => setModalOpen(false)}
        onSubmit={handleSubmit}
        loading={createMutation.isPending || updateMutation.isPending}
      />
    </Wrapper>
  );
};

export default DataSources;
