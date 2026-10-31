// import { ImageToolName, TOOL_NAME, EVideoToolName, EAudioToolName } from '@labelu/annotation';
import type { FormProps, SelectProps, TabsProps } from 'antd';
import { Alert, Modal, Pagination, Popconfirm, Button, Form, Tabs, Select, Switch, Tag, Tooltip, Space, Spin } from 'antd';
import React, { useContext, useEffect, useCallback, useMemo, useRef, useState } from 'react';
import _, { cloneDeep, find } from 'lodash-es';
import { InfoCircleOutlined, PlusOutlined } from '@ant-design/icons';
import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';
import { createGlobalStyle } from 'styled-components';
import { useTranslation } from '@labelu/i18n';

import { MediaType, TaskStatus } from '@/api/types';
import { message } from '@/StaticAnt';
import {
  applyTaskLabelPreset,
  applyTaskLabels,
  getAnnotationLabels,
  getDatasetLabelContract,
  updateTaskConfig,
  type DatasetLabelContractOption,
  type AnnotationLabelOption,
} from '@/api/services/task';
import FancyForm from '@/components/FancyForm';
import FancyInput, { add } from '@/components/FancyInput';
import { EAudioToolName, EGlobalToolName, EVideoToolName, ImageToolName } from '@/enums';
import { TOOL_NAME } from '@/constants/toolName';
import useAccessibleDatasetPicker from '@/hooks/useAccessibleDatasetPicker';

import { TaskCreationContext } from '../../../taskCreation.context';
import { FancyAttributeList } from './customFancy/ListAttribute.fancy';
import { FancyCategoryAttribute } from './customFancy/CategoryAttribute.fancy';
import lineTemplate from './templates/line.template';
import relationTemplate from './templates/relation.template';
import rectTemplate from './templates/rect.template';
import polygonTemplate from './templates/polygon.template';
import cuboidTemplate from './templates/cuboid.template';
import pointTemplate from './templates/point.template';
import tagTemplate from './templates/tag.template';
import textTemplate from './templates/text.template';
import videoSegmentTemplate from './templates/videoSegment.template';
import videoFrameTemplate from './templates/videoFrame.template';
import audioSegmentTemplate from './templates/audioSegment.template';
import audioFrameTemplate from './templates/audioFrame.template';
import { AttributeBox, AttributeFormItem, ConfigForm, TabForm } from './style';
import { getTaskLabelIds } from '../../../taskLabelTool';
import { readPersistedAnnotationConfig } from '../../../annotationConfigPersistence';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
};
const useTranslationCompat = useTranslation as unknown as TranslationHook;

const GlobalStyle = createGlobalStyle`
  .labelu-tool-select-popup .rc-virtual-list-holder {
    max-height: none !important;
  }
`;

const LabelBindingBar = styled.div`
  position: sticky;
  top: 0;
  z-index: 8;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(16rem, 0.75fr);
  gap: 0.75rem;
  margin: 0 0 1rem;
  padding: 0.75rem;
  background: rgb(255 255 255 / 96%);
  border: 1px solid var(--color-border-secondary);
  border-radius: var(--border-radius);
  box-shadow: 0 6px 18px rgb(15 23 42 / 8%);
  backdrop-filter: blur(8px);

  @media (max-width: 48rem) {
    position: relative;
    grid-template-columns: 1fr;
  }
`;

const LabelBindingField = styled.div`
  min-width: 0;

  .ant-select,
  .ant-space-compact {
    width: 100%;
  }

  .ant-select-selector {
    min-height: 2.25rem;
  }
`;

const LabelImportOptions = styled.div`
  display: grid;
  gap: 0.375rem;
  margin-top: 0.5rem;
  padding: 0.5rem 0.625rem;
  border: 1px solid var(--color-border-secondary);
  border-radius: var(--border-radius);
  background: var(--color-fill-quaternary);
`;

const LabelImportRow = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-width: 0;
  color: var(--color-text-secondary);
  font-size: 0.8125rem;

  .label-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const LabelBindingLabel = styled.div`
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin-bottom: 0.375rem;
  color: var(--color-text-secondary);
  font-size: 0.8125rem;
  font-weight: 600;
`;

const LabelBindingHelp = styled.div`
  margin-top: 0.375rem;
  color: var(--color-text-tertiary);
  font-size: 0.75rem;
  line-height: 1.35;
`;

const DatasetContractField = styled(LabelBindingField)`
  grid-column: 1 / -1;
`;

const DatasetContractPreview = styled.div`
  margin-top: 0.625rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid var(--color-border-secondary);
  border-radius: var(--border-radius);
  background: var(--color-fill-quaternary);
`;

// 注册fancyInput自定义输入组件
add('list-attribute', FancyAttributeList);
add('category-attribute', FancyCategoryAttribute);

const globalTools = [EGlobalToolName.Tag, EGlobalToolName.Text];
const graphicTools = [
  ImageToolName.Rect,
  ImageToolName.Point,
  ImageToolName.Polygon,
  ImageToolName.Line,
  ImageToolName.Cuboid,
  ImageToolName.Relation,
];
const videoAnnotationTools = [EVideoToolName.VideoSegmentTool, EVideoToolName.VideoFrameTool];
const audioAnnotationTools = [EAudioToolName.AudioSegmentTool, EAudioToolName.AudioFrameTool];

const toolMapping = {
  [MediaType.IMAGE]: graphicTools.map((item) => {
    return {
      label: TOOL_NAME[item],
      value: item,
    };
  }),
  [MediaType.VIDEO]: videoAnnotationTools.map((item) => {
    return {
      label: TOOL_NAME[item],
      value: item,
    };
  }),
  [MediaType.AUDIO]: audioAnnotationTools.map((item) => {
    return {
      label: TOOL_NAME[item],
      value: item,
    };
  }),
};

const getDefaultActiveTool = (mediaType?: MediaType) => {
  switch (mediaType) {
    case MediaType.IMAGE:
      return ImageToolName.Rect;
    case MediaType.VIDEO:
      return EVideoToolName.VideoSegmentTool;
    case MediaType.AUDIO:
      return EAudioToolName.AudioSegmentTool;
    default:
      return undefined;
  }
};

const templateMapping: Record<string, any> = {
  [ImageToolName.Line]: lineTemplate,
  [ImageToolName.Rect]: rectTemplate,
  [ImageToolName.Polygon]: polygonTemplate,
  [ImageToolName.Point]: pointTemplate,
  [ImageToolName.Cuboid]: cuboidTemplate,
  [ImageToolName.Relation]: relationTemplate,
  [EGlobalToolName.Tag]: tagTemplate,
  [EGlobalToolName.Text]: textTemplate,
  [EVideoToolName.VideoSegmentTool]: videoSegmentTemplate,
  [EVideoToolName.VideoFrameTool]: videoFrameTemplate,
  [EAudioToolName.AudioSegmentTool]: audioSegmentTemplate,
  [EAudioToolName.AudioFrameTool]: audioFrameTemplate,
};

interface RectLabelPresetOption {
  id: string;
  name: string;
  presetDbId: number;
}

function annotationKindLabel(value: unknown): string {
  const kind = String(value || '')
    .trim()
    .toLowerCase();
  if (kind === 'polygon') return '多边形';
  if (kind === 'bbox' || kind === 'rect') return '矩形框';
  return kind || '未设置';
}

const FormConfig = () => {
  const {
    annotationFormInstance,
    basicFormInstance,
    onAnnotationFormChange,
    onTaskConfigPatched,
    task,
    isCreateNewTask,
    labelDatasetImportIds,
    setLabelDatasetImportIds,
  } = useContext(TaskCreationContext);
  // const routerLoaderData = useRouteLoaderData('task') as TaskLoaderResult;
  const [activeTool, setActiveTool] = useState<string | undefined>(getDefaultActiveTool(task?.media_type));
  const activeToolRef = useRef(activeTool);
  const [activeGlobalTool, setActiveGlobalTool] = useState<string | undefined>(globalTools[0]);
  // 选中的所有工具
  const [selectedTools, setSelectedTools] = useState<any[]>([]);
  // 选中的标记工具
  const [selectedAnnotationTools, setSelectedAnnotationTools] = useState<any[]>([]);
  // 选中的全局工具
  const [selectedGlobalTools, setSelectedGlobalTools] = useState<string[]>([]);
  const [hasAttributes, setHasAttributes] = useState(false);
  const [selectedRectPresetSlug, setSelectedRectPresetSlug] = useState<string>();
  const [applyingRectPreset, setApplyingRectPreset] = useState(false);
  const [systemLabels, setSystemLabels] = useState<AnnotationLabelOption[]>([]);
  const [selectedLabelIds, setSelectedLabelIds] = useState<number[]>([]);
  const [applyingLabels, setApplyingLabels] = useState(false);
  const [selectedDatasetId, setSelectedDatasetId] = useState<number>();
  const [datasetContractModalOpen, setDatasetContractModalOpen] = useState(false);
  const [pendingDatasetId, setPendingDatasetId] = useState<number>();
  const [datasetContract, setDatasetContract] = useState<DatasetLabelContractOption | null>(null);
  const [datasetContractLoading, setDatasetContractLoading] = useState(false);
  const [datasetContractApplying, setDatasetContractApplying] = useState(false);
  const [datasetContractError, setDatasetContractError] = useState<string>();
  const { t } = useTranslationCompat();
  const watchedDatasetId = Form.useWatch('dataset_id', basicFormInstance);
  const datasetPicker = useAccessibleDatasetPicker({
    value: selectedDatasetId,
    queryKey: 'task-edit-annotation',
    enabled: task?.media_type === MediaType.IMAGE,
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

  const config = _.get(task, 'config');
  const taskStatus = _.get(task, 'status');
  const taskDoneAmount = _.get(task, 'stats.done');
  const { tools } = config || {};
  const rectPresetOptions = useMemo<RectLabelPresetOption[]>(() => {
    const presets = _.get(config, 'rectLabelPresets') as unknown;
    if (!Array.isArray(presets)) {
      return [];
    }

    return presets
      .map((item) => {
        if (!item || typeof item !== 'object') {
          return null;
        }

        const slug = typeof item.id === 'string' ? item.id : '';
        const presetDbId = typeof item.presetDbId === 'number' ? item.presetDbId : 0;
        const name = typeof item.name === 'string' ? item.name : slug;

        if (!slug || presetDbId <= 0) {
          return null;
        }

        return {
          id: slug,
          name,
          presetDbId,
        };
      })
      .filter(Boolean) as RectLabelPresetOption[];
  }, [config]);

  // 进行中和已完成的任务不允许删除工具
  const isGlobalToolDeletable = useMemo(() => {
    const isNew = !find(tools, { tool: activeGlobalTool });

    if (isNew) {
      return true;
    }

    if ([TaskStatus.INPROGRESS, TaskStatus.FINISHED].includes(taskStatus as TaskStatus) || taskDoneAmount) {
      return false;
    }

    return true;
  }, [tools, activeGlobalTool, taskStatus, taskDoneAmount]);

  const isToolDeletable = useMemo(() => {
    const isNew = !find(tools, { tool: activeTool });

    if (isNew) {
      return true;
    }

    if ([TaskStatus.INPROGRESS, TaskStatus.FINISHED].includes(taskStatus as TaskStatus) || taskDoneAmount) {
      return false;
    }

    return true;
  }, [tools, activeTool, taskStatus, taskDoneAmount]);

  const updateActiveTool = useCallback((toolName: string | undefined) => {
    activeToolRef.current = toolName;
    setActiveTool(toolName);
  }, []);

  const syncSelectedLabelsForTool = useCallback(
    (nextTools: any[] | undefined, toolName: string | undefined) => {
      const labelIds = getTaskLabelIds(nextTools, toolName);
      setSelectedLabelIds(labelIds);
      setLabelDatasetImportIds((current) => current.filter((id) => labelIds.includes(id)));
    },
    [setLabelDatasetImportIds],
  );

  const syncFormStateFromConfig = useCallback(
    (nextConfig: any, preferredActiveTool?: string) => {
      const toolNames = _.chain(nextConfig?.tools).compact().map('tool').value();
      setSelectedTools(toolNames);

      const annotationToolNames = _.filter(toolNames, (item) => !globalTools.includes(item));
      setSelectedAnnotationTools(annotationToolNames);
      const requestedActiveTool = preferredActiveTool || activeToolRef.current;
      const nextActiveTool = annotationToolNames.includes(requestedActiveTool)
        ? requestedActiveTool
        : annotationToolNames[0];
      updateActiveTool(nextActiveTool);

      const globalToolNames = _.filter(toolNames, (item) => globalTools.includes(item));
      setSelectedGlobalTools(globalToolNames);
      setActiveGlobalTool(globalToolNames[0]);
      setHasAttributes(nextConfig?.commonAttributeConfigurable ?? false);
      setSelectedRectPresetSlug(_.get(nextConfig, 'defaultRectPresetId') || undefined);
      syncSelectedLabelsForTool(nextConfig?.tools, nextActiveTool);
    },
    [syncSelectedLabelsForTool, updateActiveTool],
  );

  useEffect(() => {
    syncFormStateFromConfig(config);
  }, [config, syncFormStateFromConfig]);

  useEffect(() => {
    if (task?.media_type !== MediaType.IMAGE) {
      return;
    }
    let active = true;
    getAnnotationLabels()
      .then((response) => {
        if (active) setSystemLabels(Array.isArray(response.data) ? response.data : []);
      })
      .catch(() => {
        if (active) setSystemLabels([]);
      });
    return () => {
      active = false;
    };
  }, [task?.media_type]);

  useEffect(() => {
    const value = Number(watchedDatasetId || 0);
    setSelectedDatasetId(value > 0 ? value : undefined);
  }, [watchedDatasetId]);

  useEffect(() => {
    if (!datasetContractModalOpen || !pendingDatasetId) {
      return;
    }
    let active = true;
    setDatasetContractLoading(true);
    setDatasetContractError(undefined);
    setDatasetContract(null);
    getDatasetLabelContract(pendingDatasetId)
      .then((contract) => {
        if (active) setDatasetContract(contract);
      })
      .catch(() => {
        if (active) setDatasetContractError('数据集标签契约读取失败，请刷新后重试');
      })
      .finally(() => {
        if (active) setDatasetContractLoading(false);
      });
    return () => {
      active = false;
    };
  }, [datasetContractModalOpen, pendingDatasetId]);

  // ======================== 以下为新增代码 ========================
  const handleToolItemClick: SelectProps['onChange'] = (key) => {
    setSelectedTools((pre) => [...pre, key]);

    if (globalTools.includes(key as EGlobalToolName)) {
      setSelectedGlobalTools((pre) => [...pre, key]);
      setActiveGlobalTool(key);
    } else {
      updateActiveTool(key);
      syncSelectedLabelsForTool([], key);
      setSelectedAnnotationTools((pre) => [...pre, key]);
    }

    if (typeof onAnnotationFormChange === 'function') {
      setTimeout(onAnnotationFormChange);
    }
  };

  const handleRemoveTool = useCallback(
    (toolName: ImageToolName | EGlobalToolName) => () => {
      const newTools = selectedTools.filter((item) => item !== toolName);
      setSelectedTools(newTools);

      if (globalTools.includes(toolName as EGlobalToolName)) {
        const newGlobalTools = newTools.filter((item) => globalTools.includes(item));
        setSelectedGlobalTools(newGlobalTools);
        setActiveGlobalTool(newGlobalTools[0]);
      } else {
        const newAnnotationTools = newTools.filter((item) => !globalTools.includes(item));
        setSelectedAnnotationTools(newAnnotationTools);
        updateActiveTool(newAnnotationTools[0]);
      }

      // 因为antd的form的特殊性，删除数组元素时，需要手动调用setFieldsValue
      const prevValues = cloneDeep(annotationFormInstance.getFieldsValue());

      setTimeout(() => {
        annotationFormInstance.setFieldsValue({
          ...prevValues,
          tools: prevValues.tools.filter((item: any) => item.tool !== toolName),
        });

        const nextTools = prevValues.tools.filter((item: any) => item.tool !== toolName);
        syncSelectedLabelsForTool(nextTools, activeToolRef.current);

        if (typeof onAnnotationFormChange === 'function') {
          setTimeout(onAnnotationFormChange);
        }
      });
    },
    [annotationFormInstance, onAnnotationFormChange, selectedTools, syncSelectedLabelsForTool, updateActiveTool],
  );

  const toolsMenu = useMemo(() => {
    if (!task?.media_type) {
      return [];
    }

    const toolOptions = toolMapping[task.media_type!];

    return [
      {
        label: t('global'),
        options: _.map(globalTools, (toolName) => ({
          disabled: selectedTools.includes(toolName),
          value: toolName,
          label: <span>{TOOL_NAME[toolName]}</span>,
        })),
      },
      {
        label: t('tools'),
        options: _.map(toolOptions as Array<{ value: string; label: string }>, ({ value, label }) => {
          let disabled = selectedTools.includes(value);

          if (
            !selectedTools.includes('rectTool') &&
            !selectedTools.includes('polygonTool') &&
            value === 'relationTool'
          ) {
            disabled = true;
          }

          return {
            disabled,
            value: value,
            label: (
              <FlexLayout gap="0.2rem">
                {label}
                {value === 'relationTool' && (
                  <Tooltip title={t('relationToolTooltip')}>
                    <InfoCircleOutlined className="ml-2" />
                  </Tooltip>
                )}
              </FlexLayout>
            ),
          };
        }),
      },
    ];
  }, [selectedTools, task?.media_type, t]);

  const tabItems: TabsProps['items'] = useMemo(() => {
    return _.chain(selectedTools)
      .filter((tool) => {
        return !globalTools.includes(tool);
      })
      .map((tool) => {
        const fancyFormTemplate = templateMapping[tool] || null;
        const index = _.findIndex(selectedTools, (item) => item === tool);
        return {
          key: tool,
          label: TOOL_NAME[tool],
          forceRender: true,
          children: (
            <TabForm flex="column" gap=".5rem">
              {isToolDeletable && (
                <FlexLayout.Header flex justify="flex-end">
                  <Popconfirm title={t('deleteToolConfirm')} onConfirm={handleRemoveTool(tool as ImageToolName)}>
                    <Button type="link" danger>
                      {t('deleteTool')}
                    </Button>
                  </Popconfirm>
                </FlexLayout.Header>
              )}
              <FancyForm template={fancyFormTemplate} name={['tools', index]} />
            </TabForm>
          ),
        };
      })
      .value();
  }, [isToolDeletable, handleRemoveTool, selectedTools, t]);

  const tabGlobalItems: TabsProps['items'] = useMemo(() => {
    return _.chain(selectedTools)
      .filter((tool) => {
        return globalTools.includes(tool);
      })
      .map((tool) => {
        const fancyFormTemplate = templateMapping[tool] || null;
        const index = _.findIndex(selectedTools, (item) => item === tool);
        return {
          key: tool,
          label: TOOL_NAME[tool],
          forceRender: true,
          children: (
            <TabForm flex="column" gap=".5rem">
              {isGlobalToolDeletable && (
                <FlexLayout.Header flex justify="flex-end">
                  <Popconfirm title={t('deleteToolConfirm')} onConfirm={handleRemoveTool(tool as ImageToolName)}>
                    <Button type="link" danger>
                      {t('deleteTool')}
                    </Button>
                  </Popconfirm>
                </FlexLayout.Header>
              )}
              <FancyForm template={fancyFormTemplate} name={['tools', index]} />
            </TabForm>
          ),
        };
      })
      .value();
  }, [isGlobalToolDeletable, handleRemoveTool, selectedTools, t]);

  // TODO: 增加表单数据类型
  const handleFormValuesChange: FormProps['onValuesChange'] = useCallback(
    (changedValue: any) => {
      if ('commonAttributeConfigurable' in changedValue) {
        if (!changedValue.commonAttributeConfigurable) {
          annotationFormInstance.setFieldValue('attributes', []);
        }
        setHasAttributes(changedValue.commonAttributeConfigurable);
      }
    },
    [annotationFormInstance],
  );

  const handleRectPresetChange = useCallback(
    async (presetSlug: string) => {
      const matchedPreset = rectPresetOptions.find((item) => item.id === presetSlug);
      setSelectedRectPresetSlug(presetSlug);

      if (!matchedPreset) {
        annotationFormInstance.setFieldValue('selectedRectPresetId', undefined);
        return;
      }

      annotationFormInstance.setFieldValue('selectedRectPresetId', matchedPreset.presetDbId);

      if (!task?.id) {
        return;
      }

      setApplyingRectPreset(true);
      try {
        const response = await applyTaskLabelPreset(task.id, {
          preset_id: matchedPreset.presetDbId,
        });
        const nextRawConfig = response.data?.config;
        const nextConfig =
          typeof nextRawConfig === 'string'
            ? JSON.parse(nextRawConfig)
            : nextRawConfig && typeof nextRawConfig === 'object'
            ? nextRawConfig
            : {};

        annotationFormInstance.setFieldsValue({
          ...nextConfig,
          selectedRectPresetId: matchedPreset.presetDbId,
        });
        syncFormStateFromConfig(nextConfig);
        onTaskConfigPatched(nextConfig);
        onAnnotationFormChange();
      } finally {
        setApplyingRectPreset(false);
      }
    },
    [
      annotationFormInstance,
      onAnnotationFormChange,
      onTaskConfigPatched,
      rectPresetOptions,
      syncFormStateFromConfig,
      task?.id,
    ],
  );

  const handleDirectLabelsChange = useCallback(
    async (labelIds: number[], datasetBinding?: { datasetId: number; contractId: number }) => {
      const normalized = Array.from(new Set(labelIds.map(Number).filter((value) => value > 0)));
      if (!normalized.length) {
        return;
      }
      setSelectedLabelIds(normalized);
      setLabelDatasetImportIds((current) => current.filter((id) => normalized.includes(id)));
      if (!task?.id) return;

      setApplyingLabels(true);
      try {
        if (datasetBinding && task?.id) {
          await updateTaskConfig(task.id, {
            dataset_id: datasetBinding.datasetId,
            dataset_label_contract_id: datasetBinding.contractId,
            task_label_scope_mode: 'FULL',
          });
        }
        const currentConfig = readPersistedAnnotationConfig(annotationFormInstance);
        const response = await applyTaskLabels(task.id, {
          label_ids: normalized,
          config: JSON.stringify(currentConfig),
          // A dataset contract can contain multiple geometry kinds. Leaving
          // tool_name unset lets the service materialize each kind into its
          // corresponding annotation tool instead of using the active tab for
          // every label.
          ...(datasetBinding ? {} : { tool_name: activeTool }),
          scope_mode: datasetBinding ? 'FULL' : undefined,
        });
        const nextRawConfig = response.data?.config;
        const nextConfig =
          typeof nextRawConfig === 'string'
            ? JSON.parse(nextRawConfig)
            : nextRawConfig && typeof nextRawConfig === 'object'
            ? nextRawConfig
            : {};
        annotationFormInstance.setFieldsValue(nextConfig);
        syncFormStateFromConfig(nextConfig, activeTool);
        onTaskConfigPatched(nextConfig);
        onAnnotationFormChange();
      } finally {
        setApplyingLabels(false);
      }
    },
    [
      annotationFormInstance,
      activeTool,
      onAnnotationFormChange,
      onTaskConfigPatched,
      setLabelDatasetImportIds,
      syncFormStateFromConfig,
      task?.id,
      updateTaskConfig,
    ],
  );

  const currentDataset = datasetPicker.selectedDataset;
  const pendingDataset = datasetPicker.resolve(pendingDatasetId);
  const currentContractLabelNames = (currentDataset?.dataset_labels || [])
    .map((item) => {
      const name = item.label_key || item.label_value;
      return name ? `${name} · ${annotationKindLabel(item.annotation_kind)}` : '';
    })
    .filter(Boolean) as string[];

  const closeDatasetContractModal = useCallback(() => {
    if (datasetContractApplying) return;
    setDatasetContractModalOpen(false);
    setPendingDatasetId(undefined);
    setDatasetContract(null);
    setDatasetContractError(undefined);
  }, [datasetContractApplying]);

  const handleDatasetContractApply = useCallback(async () => {
    if (!pendingDatasetId || !datasetContract?.id || !datasetContract.label_ids?.length) {
      return;
    }
    setDatasetContractApplying(true);
    try {
      basicFormInstance.setFieldsValue({
        dataset_id: pendingDatasetId,
        dataset_label_contract_id: datasetContract.id,
        task_label_scope_mode: 'FULL',
      });
      setSelectedDatasetId(pendingDatasetId);
      await handleDirectLabelsChange(datasetContract.label_ids, {
        datasetId: pendingDatasetId,
        contractId: datasetContract.id,
      });
      message.success(`已读取「${pendingDataset?.name || '数据集'}」标签契约，并应用到标注工具`);
      closeDatasetContractModal();
    } catch (error: any) {
      message.error(
        error?.response?.data?.msg ||
          error?.response?.data?.message ||
          error?.message ||
          '标签契约应用失败，请稍后重试',
      );
    } finally {
      setDatasetContractApplying(false);
    }
  }, [
    basicFormInstance,
    closeDatasetContractModal,
    datasetContract,
    handleDirectLabelsChange,
    pendingDataset?.name,
    pendingDatasetId,
  ]);

  // ========================= end ==============================

  return (
    <ConfigForm
      form={annotationFormInstance}
      labelCol={{ span: 6 }}
      wrapperCol={{ span: 18 }}
      colon={false}
      initialValues={config}
      onValuesChange={handleFormValuesChange}
      validateTrigger="onBlur"
    >
      <GlobalStyle />
      {task?.media_type === MediaType.IMAGE && (
        <LabelBindingBar>
          <DatasetContractField>
            <LabelBindingLabel>
              <span>数据集标签契约</span>
              <Tooltip title="读取数据集管理中已创建的标签契约，按契约顺序自动生成当前标注工具标签。">
                <InfoCircleOutlined />
              </Tooltip>
            </LabelBindingLabel>
            <Space.Compact style={{ width: '100%' }}>
              <Select
                value={selectedDatasetId}
                showSearch
                filterOption={false}
                searchValue={datasetPicker.searchValue}
                aria-label="搜索或选择数据集标签契约"
                placeholder={datasets.length ? '选择已配置标签契约的数据集' : '暂无可用数据集'}
                loading={datasetPicker.loading}
                notFoundContent={datasetPicker.loading ? <Spin size="small" /> : '暂无匹配数据集'}
                options={datasets.map((item) => ({
                  value: item.id,
                  disabled: !item.dataset_label_contract_id,
                  label: item.dataset_label_contract_id
                    ? `${item.name} · 契约 v${item.dataset_label_contract_revision || 0}`
                    : `${item.name} · 尚未配置标签契约`,
                }))}
                dropdownRender={(menu) => <>{menu}{datasetDropdownFooter}</>}
                onSearch={datasetPicker.onSearch}
                onChange={(value) => {
                  const dataset = datasetPicker.onChange(value);
                  if (!dataset) return;
                  setPendingDatasetId(Number(dataset.id));
                  setDatasetContractModalOpen(true);
                }}
              />
              <Button
                type="primary"
                disabled={!selectedDatasetId}
                loading={datasetContractApplying}
                onClick={() => {
                  if (selectedDatasetId) {
                    setPendingDatasetId(selectedDatasetId);
                    setDatasetContractModalOpen(true);
                  }
                }}
              >
                读取契约
              </Button>
            </Space.Compact>
            <DatasetContractPreview>
              {currentDataset ? (
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <span>
                    当前数据集：{currentDataset.name} · 契约 v{currentDataset.dataset_label_contract_revision || 0}
                  </span>
                  <span>
                    当前契约标签：{currentContractLabelNames.length ? currentContractLabelNames.join('、') : '尚未配置'}
                  </span>
                </Space>
              ) : (
                <span>请先选择一个已配置标签契约的数据集，系统会自动读取标签。</span>
              )}
            </DatasetContractPreview>
            <LabelBindingHelp>
              只读取数据集契约，不修改数据集；确认后会覆盖当前任务标注工具中的任务标签。
            </LabelBindingHelp>
          </DatasetContractField>
          <LabelBindingField>
            <LabelBindingLabel>
              <span>* 任务标签</span>
              <Tooltip title="直接选择当前任务允许标注的独立标签；系统会创建版本化标签契约。">
                <InfoCircleOutlined />
              </Tooltip>
            </LabelBindingLabel>
            <Select
              mode="multiple"
              value={selectedLabelIds}
              placeholder="选择一个或多个独立标签"
              maxTagCount="responsive"
              loading={applyingLabels}
              disabled={applyingLabels}
              optionFilterProp="label"
              options={systemLabels.map((item) => ({
                value: item.id,
                label: `${item.label_key} (${item.label_value}) · ${annotationKindLabel(item.annotation_kind)}`,
              }))}
              onChange={(values) => void handleDirectLabelsChange(values)}
            />
            {isCreateNewTask && selectedLabelIds.length > 0 && (
              <LabelImportOptions>
                <LabelImportRow>
                  <span className="label-name">新建任务时导入所选标签的图片与标注</span>
                </LabelImportRow>
                {systemLabels
                  .filter((item) => selectedLabelIds.includes(item.id))
                  .map((item) => (
                    <LabelImportRow key={item.id}>
                      <span
                        className="label-name"
                        title={`${item.label_key} (${item.label_value}) · ${annotationKindLabel(item.annotation_kind)}`}
                      >
                        {item.label_key} ({item.label_value}) · {annotationKindLabel(item.annotation_kind)}
                      </span>
                      <Switch
                        size="small"
                        checked={labelDatasetImportIds.includes(item.id)}
                        onChange={(checked) => {
                          setLabelDatasetImportIds((current) =>
                            checked
                              ? Array.from(new Set([...current, item.id]))
                              : current.filter((id) => id !== item.id),
                          );
                        }}
                      />
                    </LabelImportRow>
                  ))}
              </LabelImportOptions>
            )}
            <LabelBindingHelp>标签决定任务的类别契约；修改后会同步标注工具中的类别。</LabelBindingHelp>
          </LabelBindingField>
          {rectPresetOptions.length > 0 && (
            <LabelBindingField>
              <LabelBindingLabel>
                <span>预设快捷应用</span>
                <Tooltip title="可选。预设只用于一次性带入标签，任务最终仍保存独立标签契约。">
                  <InfoCircleOutlined />
                </Tooltip>
              </LabelBindingLabel>
              <Space.Compact>
                <Select
                  value={selectedRectPresetSlug}
                  placeholder="从预设快速带入标签"
                  loading={applyingRectPreset}
                  disabled={applyingRectPreset}
                  options={rectPresetOptions.map((item) => ({
                    label: item.name,
                    value: item.id,
                  }))}
                  onChange={(value) => void handleRectPresetChange(value)}
                />
                <Button href="/annotation-settings" target="_blank" rel="noreferrer">
                  管理预设
                </Button>
              </Space.Compact>
              <LabelBindingHelp>只负责快速带入，不会把任务重新绑定为预设。</LabelBindingHelp>
            </LabelBindingField>
          )}
          {rectPresetOptions.length > 0 && (
            <Form.Item name="selectedRectPresetId" hidden>
              <FancyInput type="text" />
            </Form.Item>
          )}
        </LabelBindingBar>
      )}
      <Modal
        title="读取数据集标签契约"
        open={datasetContractModalOpen}
        centered
        destroyOnClose
        width={620}
        okText="读取并应用"
        cancelText="取消"
        confirmLoading={datasetContractApplying}
        okButtonProps={{ disabled: !datasetContract?.label_ids?.length || Boolean(datasetContractError) }}
        onCancel={closeDatasetContractModal}
        onOk={() => void handleDatasetContractApply()}
      >
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <div>
            <strong>{pendingDataset?.name || '未选择数据集'}</strong>
            {datasetContract && (
              <span style={{ marginLeft: 12, color: 'var(--color-text-secondary)' }}>
                标签契约 v{datasetContract.revision} · {datasetContract.status === 'FROZEN' ? '已冻结' : '生效中'}
              </span>
            )}
          </div>
          {datasetContractLoading ? (
            <div style={{ minHeight: 140, display: 'grid', placeItems: 'center' }}>
              <Spin />
            </div>
          ) : datasetContractError ? (
            <Alert type="error" showIcon message={datasetContractError} />
          ) : !datasetContract ? (
            <Alert type="warning" showIcon message="该数据集尚未配置标签契约，不能直接生成标注工具标签" />
          ) : (
            <>
              <Alert
                type="info"
                showIcon
                message={`将按契约顺序读取 ${datasetContract.label_ids.length} 个标签`}
                description="确认后会更新当前任务的数据集绑定，并按每个标签的几何类型自动生成矩形框和多边形工具；数据集契约本身不会被修改。"
              />
              <Space wrap>
                {datasetContract.labels.map((label, index) => (
                  <Tag key={`${label.label_id || label.label_key || label.label_value}-${index}`} color={label.color}>
                    {index + 1}. {label.label_key || label.label_value || `标签 ${index + 1}`}
                    {label.label_key && label.label_value ? `（${label.label_value}）` : ''}
                    {` · ${annotationKindLabel(label.annotation_kind)}`}
                  </Tag>
                ))}
              </Space>
            </>
          )}
        </Space>
      </Modal>
      {false && task?.media_type === MediaType.IMAGE && (
        <Form.Item label="任务标签" tooltip="直接选择当前任务允许标注的全局标签；系统会创建版本化标签契约。" required>
          <Select
            mode="multiple"
            value={selectedLabelIds}
            placeholder="请选择一个或多个全局标签"
            loading={applyingLabels}
            disabled={applyingLabels}
            optionFilterProp="label"
            options={systemLabels.map((item) => ({
              value: item.id,
              label: `${item.label_key} (${item.label_value})`,
            }))}
            onChange={(values) => void handleDirectLabelsChange(values)}
          />
        </Form.Item>
      )}
      {false && task?.media_type === MediaType.IMAGE && rectPresetOptions.length > 0 && (
        <>
          <Form.Item label="预设快捷应用" tooltip="可选。预设只用于一次性带入标签，任务最终仍保存独立标签契约。">
            <Space.Compact style={{ width: '100%' }}>
              <Select
                value={selectedRectPresetSlug}
                placeholder="可选：从预设快速带入标签"
                loading={applyingRectPreset}
                disabled={applyingRectPreset}
                options={rectPresetOptions.map((item) => ({
                  label: item.name,
                  value: item.id,
                }))}
                onChange={(value) => void handleRectPresetChange(value)}
              />
              <Button href="/annotation-settings" target="_blank" rel="noreferrer">
                管理预设
              </Button>
            </Space.Compact>
          </Form.Item>
          <Form.Item name="selectedRectPresetId" hidden>
            <FancyInput type="text" />
          </Form.Item>
        </>
      )}
      <Form.Item label={t('annotationTool')}>
        <Select
          placeholder={t('addTool')}
          popupClassName="labelu-tool-select-popup"
          options={toolsMenu}
          onSelect={handleToolItemClick}
        >
          <PlusOutlined />
        </Select>
      </Form.Item>
      {selectedGlobalTools.length > 0 && (
        <Form.Item label={t('global')} tooltip={t('globalTooltip')}>
          <div className="formTabBox">
            <Tabs
              type="card"
              size="small"
              activeKey={activeGlobalTool}
              destroyInactiveTabPane={false}
              onTabClick={(tabKey) => {
                setActiveGlobalTool(tabKey);
              }}
              items={tabGlobalItems}
            />
          </div>
        </Form.Item>
      )}
      {selectedAnnotationTools.length > 0 && (
        <Form.Item label={t('labelTools')} tooltip={t('labelTooltip')}>
          <div className="formTabBox">
            <Tabs
              type="card"
              size="small"
              activeKey={activeTool}
              destroyInactiveTabPane={false}
              onTabClick={(tabKey) => {
                updateActiveTool(tabKey);
                const currentConfig = readPersistedAnnotationConfig(annotationFormInstance);
                syncSelectedLabelsForTool(currentConfig?.tools, tabKey);
              }}
              items={tabItems}
            />
          </div>
        </Form.Item>
      )}
      {selectedAnnotationTools.length > 0 && (
        <Form.Item
          label={<span className="formTitle">{t('genericLabels')}</span>}
          name="commonAttributeConfigurable"
          tooltip={t('genericLabelTooltip')}
          hidden={globalTools.includes(activeTool as EGlobalToolName)}
        >
          <FancyInput type="boolean" />
        </Form.Item>
      )}

      {hasAttributes && !globalTools.includes(activeTool as EGlobalToolName) && (
        <Form.Item wrapperCol={{ offset: 4 }}>
          <AttributeBox>
            <AttributeFormItem name="attributes">
              <FancyInput type="list-attribute" fullField={['attributes']} />
            </AttributeFormItem>
          </AttributeBox>
        </Form.Item>
      )}

      {task?.media_type === MediaType.IMAGE && (
        <Form.Item
          label={t('DrawingOutCanvas')}
          name="drawOutsideTarget"
          tooltip={t('DrawingOutCanvasTooltip')}
          hidden={!graphicTools.includes(activeTool as ImageToolName)}
        >
          <FancyInput type="boolean" />
        </Form.Item>
      )}
    </ConfigForm>
  );
};

export default React.memo(FormConfig);
