import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DatabaseOutlined,
  EyeOutlined,
  FileImageOutlined,
  ReloadOutlined,
  TagOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { Button, Empty, Image, Pagination, Select, Spin, Tag, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import styled from 'styled-components';

import { getDatasetAnalysis, type DatasetAnalysisResponse, type DatasetDimensionDistribution } from '@/api/services/datasetAnalysis';
import {
  getAccessibleDatasetsPage,
  getTaskLabelImages,
  getTaskLabelSummary,
  type DatasetOption,
  type TaskLabelImageHit,
  type TaskLabelSummary,
} from '@/api/services/task';
import {
  mergeTrainingAnalysisDatasetOptions,
  trainingAnalysisDatasetQuery,
  TRAINING_ANALYSIS_DATASET_PAGE_SIZE,
} from './datasetSelector';

const DATASET_OPTION_PAGE_SIZE = TRAINING_ANALYSIS_DATASET_PAGE_SIZE;

const Page = styled.div`
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #f4f7fb;
`;

const PageInner = styled.div`
  max-width: 1720px;
  margin: 0 auto;
  padding: 16px 24px 32px;
  box-sizing: border-box;

  @media (max-width: 768px) {
    padding: 16px;
  }
`;

const Header = styled.header`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;

  @media (max-width: 720px) {
    flex-direction: column;
  }
`;

const HeaderCopy = styled.div`
  min-width: 0;
`;

const Title = styled.h1`
  margin: 0;
  color: #0f172a;
  font-size: 22px;
  line-height: 1.2;
  font-weight: 800;
`;

const Description = styled.p`
  max-width: 680px;
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.45;
`;

const HeaderAction = styled.div`
  display: flex;
  align-items: flex-end;
  gap: 8px;
  flex-shrink: 0;

  @media (max-width: 720px) {
    width: 100%;

    & > label {
      flex: 1;
    }
  }
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 260px;
  color: #475569;
  font-size: 11px;
  font-weight: 700;

  @media (max-width: 720px) {
    min-width: 0;
  }
`;

const KpiGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;

  @media (max-width: 1120px) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  @media (max-width: 680px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
`;

const KpiCard = styled.section`
  min-height: 82px;
  padding: 11px 13px;
  border: 1px solid #dbe4f0;
  border-radius: 6px;
  background: #fff;
`;

const KpiLabel = styled.div`
  display: flex;
  align-items: center;
  gap: 7px;
  color: #64748b;
  font-size: 11px;
  font-weight: 700;
`;

const KpiValue = styled.div`
  margin-top: 7px;
  color: #0f172a;
  font-size: 23px;
  line-height: 1;
  font-weight: 800;
`;

const KpiHint = styled.div`
  margin-top: 5px;
  color: #94a3b8;
  font-size: 11px;
`;

const Section = styled.section`
  padding: 14px;
  border: 1px solid #dbe4f0;
  border-radius: 8px;
  background: #fff;
`;

const SectionHeader = styled.div`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
`;

const SectionTitle = styled.h2`
  margin: 0;
  color: #172033;
  font-size: 15px;
  line-height: 1.3;
  font-weight: 800;
`;

const SectionHint = styled.div`
  margin-top: 4px;
  color: #94a3b8;
  font-size: 11px;
`;

const DistributionGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;

  @media (max-width: 1260px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  @media (max-width: 760px) {
    grid-template-columns: 1fr;
  }
`;

const DimensionCard = styled.div`
  min-width: 0;
  min-height: 178px;
  padding: 11px;
  border: 1px solid #e7edf5;
  border-radius: 6px;
  background: #fff;
`;

const DimensionHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
`;

const DimensionName = styled.div`
  color: #1e293b;
  font-size: 12px;
  font-weight: 800;
`;

const DimensionCoverage = styled.div`
  color: #64748b;
  font-size: 11px;
  white-space: nowrap;
`;

const BarList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 6px;
`;

const BarRow = styled.div`
  display: grid;
  grid-template-columns: minmax(68px, 0.8fr) minmax(44px, 1.4fr) 54px;
  align-items: center;
  gap: 6px;
  min-width: 0;
`;

const BarLabel = styled.div`
  overflow: hidden;
  color: #475569;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const BarTrack = styled.div`
  height: 6px;
  overflow: hidden;
  border-radius: 4px;
  background: #e9eff7;
`;

const BarFill = styled.div<{ $width: number; $color: string }>`
  width: ${(props) => Math.max(0, Math.min(100, props.$width))}%;
  height: 100%;
  border-radius: 4px;
  background: ${(props) => props.$color};
`;

const BarValue = styled.div`
  color: #0f172a;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-align: right;

  small {
    display: block;
    color: #94a3b8;
    font-size: 9px;
  }
`;

const MissingNote = styled.div`
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding-top: 7px;
  margin-top: 7px;
  border-top: 1px solid #edf2f7;
  color: #b45309;
  font-size: 11px;
  line-height: 1.45;
`;

const DashboardGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1.9fr) minmax(350px, 0.85fr);
  gap: 12px;
  align-items: start;

  @media (max-width: 1180px) {
    grid-template-columns: 1fr;
  }
`;

const AnalyticsMain = styled.section`
  min-width: 0;
`;

const AnalyticsRail = styled.aside`
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
`;

const RailPanel = styled.section`
  min-width: 0;
  padding: 12px;
  border: 1px solid #dbe4f0;
  border-radius: 6px;
  background: #fff;
`;

const RecommendationList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 310px;
  overflow-y: auto;
`;

const Recommendation = styled.div<{ $critical?: boolean }>`
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-left: 3px solid ${(props) => (props.$critical ? '#dc2626' : '#d97706')};
  background: ${(props) => (props.$critical ? '#fff7f7' : '#fffbeb')};
`;

const RecommendationText = styled.div`
  min-width: 0;
  color: #475569;
  font-size: 10px;
  line-height: 1.35;
`;

const RecommendationValue = styled.strong`
  display: block;
  overflow: hidden;
  color: #172033;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const LabelList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 7px;
  max-height: 286px;
  overflow-y: auto;
`;

const LabelStatRow = styled.div`
  min-width: 0;
  padding-bottom: 7px;
  border-bottom: 1px solid #eef2f7;

  &:last-child {
    padding-bottom: 0;
    border-bottom: 0;
  }
`;

const LabelStatHead = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 7px;
`;

const LabelStatName = styled.div`
  overflow: hidden;
  color: #172033;
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const LabelStatValue = styled.div`
  color: #475569;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
`;

const IconButton = styled.button`
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #64748b;
  cursor: pointer;

  &:hover {
    background: #eef4ff;
    color: #2563eb;
  }
`;

const LabelMeter = styled.div`
  height: 5px;
  margin-top: 5px;
  overflow: hidden;
  border-radius: 3px;
  background: #e9eff7;
`;

const LabelMeterFill = styled.div<{ $width: number; $hasGap: boolean }>`
  width: ${(props) => Math.max(0, Math.min(100, props.$width))}%;
  height: 100%;
  border-radius: 3px;
  background: ${(props) => (props.$hasGap ? '#d97706' : '#059669')};
`;

const LabelStatFoot = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  color: #94a3b8;
  font-size: 9px;
`;

const TableWrap = styled.div`
  overflow-x: auto;
`;

const DataTable = styled.table`
  width: 100%;
  min-width: 610px;
  border-collapse: collapse;
  color: #334155;
  font-size: 12px;

  th {
    padding: 9px 10px;
    border-bottom: 1px solid #dbe4f0;
    color: #64748b;
    font-size: 11px;
    font-weight: 800;
    text-align: left;
    white-space: nowrap;
  }

  td {
    padding: 11px 10px;
    border-bottom: 1px solid #eef2f7;
    vertical-align: middle;
  }

  tbody tr:last-child td {
    border-bottom: 0;
  }
`;

const TableName = styled.div`
  max-width: 260px;
  overflow: hidden;
  color: #172033;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const LabelPreview = styled.div`
  padding-top: 16px;
  margin-top: 14px;
  border-top: 1px solid #e7edf5;
`;

const LabelPreviewHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
`;

const LabelPreviewTitle = styled.div`
  color: #172033;
  font-size: 13px;
  font-weight: 800;
`;

const LabelPreviewGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;

  @media (max-width: 1120px) {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  @media (max-width: 720px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
`;

const LabelPreviewItem = styled.div`
  min-width: 0;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #f8fafc;
`;

const LabelPreviewImage = styled(Image)`
  display: block;
  width: 100%;
  height: 104px;
  object-fit: cover;
`;

const LabelPreviewCaption = styled.div`
  overflow: hidden;
  padding: 7px 8px;
  color: #64748b;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const EmptyPanel = styled.div`
  display: grid;
  min-height: 420px;
  place-items: center;
  padding: 32px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  background: #fff;
`;

const formatNumber = (value: number) => new Intl.NumberFormat('zh-CN').format(value || 0);

const LABEL_MIN_IMAGE_SHARE = 0.10;
const TEST_SPLIT_RATIO = 0.10;
const MIN_TEST_INSTANCES = 20;

function calculateLabelTarget(
  totalImages: number,
  imageCount: number,
  instanceCount: number,
) {
  if (totalImages <= 0) return 0;
  const datasetShareTarget = Math.ceil(totalImages * LABEL_MIN_IMAGE_SHARE);
  const instancesPerImage = imageCount > 0 && instanceCount > 0
    ? instanceCount / imageCount
    : 1;
  const evaluationTarget = Math.ceil(MIN_TEST_INSTANCES / (TEST_SPLIT_RATIO * instancesPerImage));
  return Math.max(datasetShareTarget, evaluationTarget);
}

const DIMENSION_COLORS: Record<string, string> = {
  sceneType: '#2563eb',
  weather: '#0f766e',
  lighting: '#d97706',
  quality: '#059669',
  angle: '#7c3aed',
  source: '#db2777',
};

function DimensionPanel({ dimension }: { dimension: DatasetDimensionDistribution }) {
  const rows = dimension.items.slice(0, 5);
  const color = DIMENSION_COLORS[dimension.key] || '#2563eb';
  return (
    <DimensionCard>
      <DimensionHeader>
        <DimensionName>{dimension.label}</DimensionName>
        <DimensionCoverage>
          已覆盖 {formatNumber(dimension.covered_images)} / {formatNumber(dimension.total_images)} 张
        </DimensionCoverage>
      </DimensionHeader>
      {rows.length ? (
        <BarList>
          {rows.map((item) => (
            <BarRow key={`${dimension.key}-${item.value}`}>
              <BarLabel title={item.value}>{item.value}</BarLabel>
              <BarTrack><BarFill $width={item.percentage} $color={color} /></BarTrack>
              <BarValue>{formatNumber(item.count)}<small>{item.percentage}%</small></BarValue>
            </BarRow>
          ))}
        </BarList>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已标注分布" />
      )}
      {dimension.missing_values.length > 0 && (
        <MissingNote title={dimension.missing_values.join('、')}>
          <WarningOutlined />
          <span>缺失 {dimension.missing_values.length} 项：{dimension.missing_values.slice(0, 2).join('、')}</span>
        </MissingNote>
      )}
      {dimension.missing_images > 0 && dimension.missing_values.length === 0 && (
        <MissingNote>
          <WarningOutlined />
          <span>{formatNumber(dimension.missing_images)} 张图片未填写该维度</span>
        </MissingNote>
      )}
    </DimensionCard>
  );
}

export default function TrainingAnalysisPage() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetOption[]>([]);
  const [datasetTotal, setDatasetTotal] = useState(0);
  const [datasetPage, setDatasetPage] = useState(0);
  const [datasetSearch, setDatasetSearch] = useState('');
  const [datasetSearchInput, setDatasetSearchInput] = useState('');
  const [selectedDatasetOption, setSelectedDatasetOption] = useState<DatasetOption | null>(null);
  const [selectedDatasetId, setSelectedDatasetId] = useState<number>();
  const [analysis, setAnalysis] = useState<DatasetAnalysisResponse | null>(null);
  const [labelSummary, setLabelSummary] = useState<TaskLabelSummary['labels']>([]);
  const [labelPreview, setLabelPreview] = useState<{
    label: DatasetAnalysisResponse['labels'][number];
    labelId: number;
    images: TaskLabelImageHit[];
    total: number;
  } | null>(null);
  const [loadingDatasets, setLoadingDatasets] = useState(true);
  const [loadingAnalysis, setLoadingAnalysis] = useState(false);
  const [loadingLabelImages, setLoadingLabelImages] = useState(false);
  const datasetRequestSequence = useRef(0);
  const selectedDatasetIdRef = useRef<number>();

  const loadDatasets = useCallback(async () => {
    const sequence = ++datasetRequestSequence.current;
    setLoadingDatasets(true);
    try {
      const response = await getAccessibleDatasetsPage(trainingAnalysisDatasetQuery(datasetPage, datasetSearch));
      if (sequence !== datasetRequestSequence.current) return;
      const next = response.data || [];
      setDatasets(next);
      setDatasetTotal(Number(response.meta_data?.total ?? next.length));
      // Keep the current selection stable while browsing another page or
      // search result. Only choose the first row on the initial empty state.
      if (!selectedDatasetIdRef.current && next[0]) {
        selectedDatasetIdRef.current = next[0].id;
        setSelectedDatasetOption(next[0]);
        setSelectedDatasetId(next[0].id);
      }
    } catch {
      if (sequence === datasetRequestSequence.current) message.error('数据集加载失败');
    } finally {
      if (sequence === datasetRequestSequence.current) setLoadingDatasets(false);
    }
  }, [datasetPage, datasetSearch]);

  useEffect(() => {
    selectedDatasetIdRef.current = selectedDatasetId;
  }, [selectedDatasetId]);

  useEffect(() => { loadDatasets(); }, [loadDatasets]);

  // Ant Design's Select updates the search value on every keystroke. Debounce
  // the authoritative server query while keeping the input responsive.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const normalized = datasetSearchInput.trim();
      setDatasetPage(0);
      setDatasetSearch((current) => current === normalized ? current : normalized);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [datasetSearchInput]);

  const loadAnalysis = useCallback(async () => {
    if (!selectedDatasetId) {
      setAnalysis(null);
      return;
    }
    setLoadingAnalysis(true);
    setLabelPreview(null);
    try {
      const [response, labelsResponse] = await Promise.all([
        getDatasetAnalysis(selectedDatasetId),
        getTaskLabelSummary({ dataset_id: selectedDatasetId }),
      ]);
      setAnalysis(response.data);
      setLabelSummary(labelsResponse.data?.labels || []);
    } catch {
      setAnalysis(null);
      setLabelSummary([]);
      message.error('数据集分析加载失败');
    } finally {
      setLoadingAnalysis(false);
    }
  }, [selectedDatasetId]);

  const loadLabelImages = useCallback(async (label: DatasetAnalysisResponse['labels'][number]) => {
    if (!selectedDatasetId) return;
    const mappedLabel = labelSummary.find(
      (item) => item.label_value === label.label || item.label_key === label.label,
    );
    if (!mappedLabel) {
      message.info('该标签尚未建立全局标签映射，暂时无法读取图片');
      return;
    }
    setLoadingLabelImages(true);
    try {
      const response = await getTaskLabelImages({
        label_id: mappedLabel.id,
        dataset_id: selectedDatasetId,
        page: 0,
        size: 8,
      });
      setLabelPreview({
        label,
        labelId: mappedLabel.id,
        images: response.data || [],
        total: response.meta_data?.total || 0,
      });
    } catch {
      setLabelPreview(null);
      message.error('标签图片加载失败');
    } finally {
      setLoadingLabelImages(false);
    }
  }, [labelSummary, selectedDatasetId]);

  useEffect(() => { loadAnalysis(); }, [loadAnalysis]);

  const normalizedLabels = useMemo(() => {
    const labels = (analysis?.labels || []).map((item) => {
      const targetCount = calculateLabelTarget(
        analysis?.totals.total_images || 0,
        item.image_count,
        item.instance_count,
      );
      return { ...item, target_count: targetCount, gap_count: Math.max(0, targetCount - item.image_count) };
    });
    const knownLabels = new Set(labels.map((item) => item.label));
    (analysis?.recommendations || []).forEach((item) => {
      if (item.dimension !== 'label' || knownLabels.has(item.value)) return;
      const targetCount = calculateLabelTarget(
        analysis?.totals.total_images || 0,
        item.current_count,
        0,
      );
      labels.push({
        label: item.value,
        image_count: item.current_count,
        instance_count: 0,
        percentage: analysis?.totals.total_images
          ? Math.round((item.current_count / analysis.totals.total_images) * 1000) / 10
          : 0,
        target_count: targetCount,
        gap_count: Math.max(0, targetCount - item.current_count),
      });
      knownLabels.add(item.value);
    });
    return labels;
  }, [analysis?.labels, analysis?.recommendations, analysis?.totals.total_images]);

  const recommendations = useMemo(() => {
    const labelMap = new Map(normalizedLabels.map((item) => [item.label, item]));
    const existing = (analysis?.recommendations || []).map((item) => {
      if (item.dimension !== 'label') return item;
      const label = labelMap.get(item.value);
      if (!label) return item;
      return {
        ...item,
        current_count: label.image_count,
        target_count: label.target_count,
        priority: label.image_count > 0 ? 'warning' : 'critical',
        message: `目标标签“${label.label}”当前有 ${label.image_count} 张图片，建议新增 ${label.gap_count} 张，达到 ${label.target_count} 张`,
      };
    });
    const existingKeys = new Set(existing.map((item) => `${item.dimension}:${item.value}`));
    normalizedLabels.forEach((item) => {
      const key = `label:${item.label}`;
      if (item.gap_count <= 0 || existingKeys.has(key)) return;
      existing.push({
        dimension: 'label',
        dimension_label: '目标标签',
        value: item.label,
        current_count: item.image_count,
        target_count: item.target_count,
        priority: item.image_count > 0 ? 'warning' : 'critical',
        message: `目标标签“${item.label}”当前有 ${item.image_count} 张图片，建议新增 ${item.gap_count} 张，达到 ${item.target_count} 张`,
      });
    });
    const rank: Record<string, number> = { critical: 0, warning: 1, ok: 2 };
    return existing
      .sort((left, right) => (
        (rank[left.priority] ?? 9) - (rank[right.priority] ?? 9)
        || Number(right.dimension === 'label') - Number(left.dimension === 'label')
        || left.current_count - right.current_count
      ))
      .slice(0, 24);
  }, [analysis?.recommendations, normalizedLabels]);

  const summaryMetrics = useMemo(() => {
    const totalImages = analysis?.totals.total_images || 0;
    const percent = (value: number) => totalImages > 0
      ? Math.round((value / totalImages) * 1000) / 10
      : 0;
    const labelsWithGap = normalizedLabels.filter((item) => item.gap_count > 0);
    return {
      annotationCompletion: percent(analysis?.totals.done_images || 0),
      attributeCoverage: percent(analysis?.totals.tagged_images || 0),
      labelGapCount: labelsWithGap.length,
      labelGapImages: labelsWithGap.reduce((sum, item) => sum + item.gap_count, 0),
    };
  }, [analysis?.totals, normalizedLabels]);

  return (
    <Page>
      <PageInner>
        <Header>
          <HeaderCopy>
            <Title>数据集分析</Title>
            <Description>集中查看图片条件与目标标签分布，快速识别训练数据中缺失、偏少和覆盖不足的部分。</Description>
          </HeaderCopy>
          <HeaderAction>
            <Field>
              数据集
              <Select
                showSearch
                filterOption={false}
                searchValue={datasetSearchInput}
                value={selectedDatasetId}
                loading={loadingDatasets}
                placeholder="选择数据集"
                options={mergeTrainingAnalysisDatasetOptions(datasets, selectedDatasetOption).map((dataset) => ({
                  value: dataset.id,
                  label: dataset.dataset_label_contract_revision
                    ? `${dataset.name} · 契约 v${dataset.dataset_label_contract_revision}`
                    : dataset.name,
                }))}
                onSearch={setDatasetSearchInput}
                onChange={(value) => {
                  const nextId = value ? Number(value) : undefined;
                  const selected = nextId
                    ? datasets.find((item) => Number(item.id) === nextId)
                      || (selectedDatasetOption?.id === nextId ? selectedDatasetOption : null)
                    : null;
                  selectedDatasetIdRef.current = nextId;
                  setSelectedDatasetOption(selected);
                  setSelectedDatasetId(nextId);
                  setDatasetSearchInput('');
                  setDatasetSearch('');
                  setDatasetPage(0);
                }}
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
                        {datasetTotal ? `共 ${datasetTotal} 个数据集` : '暂无数据集'}
                      </span>
                      {datasetTotal > DATASET_OPTION_PAGE_SIZE && (
                        <Pagination
                          size="small"
                          current={datasetPage + 1}
                          pageSize={DATASET_OPTION_PAGE_SIZE}
                          total={datasetTotal}
                          showSizeChanger={false}
                          showLessItems
                          onChange={(nextPage) => setDatasetPage(nextPage - 1)}
                        />
                      )}
                    </div>
                  </>
                )}
                notFoundContent={loadingDatasets ? <Spin size="small" /> : '暂无匹配数据集'}
              />
            </Field>
            <Button icon={<ReloadOutlined />} onClick={() => { loadDatasets(); loadAnalysis(); }} loading={loadingDatasets || loadingAnalysis}>刷新</Button>
          </HeaderAction>
        </Header>

        {!selectedDatasetId ? (
          <EmptyPanel><Empty description="请选择数据集" /></EmptyPanel>
        ) : (
          <Spin spinning={loadingAnalysis}>
            {!analysis ? (
              <EmptyPanel><Empty description="暂无数据集分析结果" /></EmptyPanel>
            ) : (
              <>
                <KpiGrid>
                  <KpiCard><KpiLabel><FileImageOutlined />图片总数</KpiLabel><KpiValue>{formatNumber(analysis.totals.total_images)}</KpiValue><KpiHint>{formatNumber(analysis.totals.task_count)} 个任务 · {formatNumber(analysis.totals.image_task_count)} 个图片任务</KpiHint></KpiCard>
                  <KpiCard><KpiLabel><DatabaseOutlined />标注完成率</KpiLabel><KpiValue>{summaryMetrics.annotationCompletion}%</KpiValue><KpiHint>{formatNumber(analysis.totals.done_images)} / {formatNumber(analysis.totals.total_images)} 张已完成</KpiHint></KpiCard>
                  <KpiCard><KpiLabel>属性覆盖率</KpiLabel><KpiValue>{summaryMetrics.attributeCoverage}%</KpiValue><KpiHint>{formatNumber(analysis.totals.tagged_images)} 张已填写全局属性</KpiHint></KpiCard>
                  <KpiCard><KpiLabel><TagOutlined />标签类别 / 实例</KpiLabel><KpiValue>{formatNumber(normalizedLabels.length)} / {formatNumber(analysis.totals.label_instance_count)}</KpiValue><KpiHint>{formatNumber(analysis.totals.labeled_images)} 张图片包含目标</KpiHint></KpiCard>
                  <KpiCard><KpiLabel><WarningOutlined />标签待补</KpiLabel><KpiValue>{formatNumber(summaryMetrics.labelGapCount)} 类</KpiValue><KpiHint>各类别缺口合计 {formatNumber(summaryMetrics.labelGapImages)} 张</KpiHint></KpiCard>
                </KpiGrid>

                <DashboardGrid>
                  <AnalyticsMain>
                    <SectionHeader>
                      <div><SectionTitle>图片条件分布</SectionTitle><SectionHint>统计单位为图片；同一图片的多选属性分别计数</SectionHint></div>
                      <Tag>{analysis.generated_at}</Tag>
                    </SectionHeader>
                    <DistributionGrid>
                      {analysis.dimensions.map((dimension) => <DimensionPanel key={dimension.key} dimension={dimension} />)}
                    </DistributionGrid>
                  </AnalyticsMain>

                  <AnalyticsRail>
                    <RailPanel>
                      <SectionHeader>
                        <div><SectionTitle>目标标签分布</SectionTitle><SectionHint>所有检测标签均按总图10%与测试集20实例线取高</SectionHint></div>
                        <Tag color={summaryMetrics.labelGapCount > 0 ? 'orange' : 'green'}>{summaryMetrics.labelGapCount > 0 ? `${summaryMetrics.labelGapCount} 类待补` : '全部达标'}</Tag>
                      </SectionHeader>
                      {normalizedLabels.length ? (
                        <LabelList>
                          {normalizedLabels.map((item) => {
                            const target = Math.max(1, item.target_count);
                            const progress = Math.min(100, (item.image_count / target) * 100);
                            return (
                              <LabelStatRow key={item.label}>
                                <LabelStatHead>
                                  <LabelStatName title={item.label}>{item.label}</LabelStatName>
                                  <LabelStatValue>{formatNumber(item.image_count)} / {formatNumber(item.target_count)}</LabelStatValue>
                                  <IconButton
                                    type="button"
                                    title={`查看 ${item.label} 图片`}
                                    aria-label={`查看 ${item.label} 图片`}
                                    disabled={loadingLabelImages}
                                    onClick={() => loadLabelImages(item)}
                                  >
                                    <EyeOutlined />
                                  </IconButton>
                                </LabelStatHead>
                                <LabelMeter><LabelMeterFill $width={progress} $hasGap={item.gap_count > 0} /></LabelMeter>
                                <LabelStatFoot>
                                  <span>{formatNumber(item.instance_count)} 个实例 · 图片占比 {item.percentage}%</span>
                                  <span>{item.gap_count > 0 ? `需新增 ${formatNumber(item.gap_count)} 张` : '已达标'}</span>
                                </LabelStatFoot>
                              </LabelStatRow>
                            );
                          })}
                        </LabelList>
                      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无目标标签" />}
                    </RailPanel>

                    <RailPanel>
                      <SectionHeader><div><SectionTitle>优先补充建议</SectionTitle><SectionHint>红色优先，其次补齐偏少条件</SectionHint></div></SectionHeader>
                      {recommendations.length ? (
                        <RecommendationList>
                          {recommendations.map((item, index) => (
                            <Recommendation key={`${item.dimension}-${item.value}-${index}`} $critical={item.priority === 'critical'} title={item.message}>
                              <RecommendationText>
                                <RecommendationValue>{item.dimension_label} · {item.value}</RecommendationValue>
                                {item.target_count > item.current_count ? `建议新增 ${formatNumber(item.target_count - item.current_count)} 张` : item.message}
                              </RecommendationText>
                              <span style={{ color: '#64748b', fontSize: 11, whiteSpace: 'nowrap' }}>{formatNumber(item.current_count)}/{formatNumber(item.target_count)}</span>
                            </Recommendation>
                          ))}
                        </RecommendationList>
                      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前未发现明显缺口" />}
                    </RailPanel>
                  </AnalyticsRail>
                </DashboardGrid>

                {labelPreview && (
                  <Section style={{ marginTop: 12 }}>
                    <LabelPreview style={{ paddingTop: 0, marginTop: 0, borderTop: 0 }}>
                      <LabelPreviewHeader>
                        <div>
                          <LabelPreviewTitle>{labelPreview.label.label} 图片样本</LabelPreviewTitle>
                          <SectionHint>展示 {Math.min(labelPreview.images.length, 8)} / {formatNumber(labelPreview.total)} 张，点击图片可放大</SectionHint>
                        </div>
                        <Button
                          type="link"
                          icon={<FileImageOutlined />}
                          onClick={() => navigate(`/tasks/labels/${labelPreview.labelId}?dataset_id=${selectedDatasetId}`)}
                        >
                          查看全部图片
                        </Button>
                      </LabelPreviewHeader>
                      {labelPreview.images.length ? (
                        <LabelPreviewGrid>
                          {labelPreview.images.map((image) => (
                            <LabelPreviewItem key={`${image.task_id}-${image.sample_id}`}>
                              <LabelPreviewImage src={image.thumbnail_url || image.url || undefined} preview={{ src: image.url || image.thumbnail_url || undefined }} alt={image.filename} />
                              <LabelPreviewCaption title={image.filename}>{image.filename}</LabelPreviewCaption>
                            </LabelPreviewItem>
                          ))}
                        </LabelPreviewGrid>
                      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该标签暂无可显示图片" />}
                    </LabelPreview>
                  </Section>
                )}

                <Section style={{ marginTop: 12 }}>
                  <SectionHeader><div><SectionTitle>任务覆盖明细</SectionTitle><SectionHint>用于定位需要继续补图或补标注的任务</SectionHint></div></SectionHeader>
                  <TableWrap>
                    <DataTable>
                      <thead><tr><th>任务</th><th>状态</th><th>来源</th><th>图片</th><th>已完成</th><th>已标框</th><th>属性覆盖</th></tr></thead>
                      <tbody>
                        {analysis.tasks.map((item) => <tr key={item.task_id}><td><TableName title={item.task_name}>#{item.task_id} {item.task_name}</TableName></td><td><Tag color={item.status === 'FINISHED' ? 'green' : 'default'}>{item.status || '—'}</Tag></td><td>{item.source || '—'}</td><td>{formatNumber(item.total_images)}</td><td>{formatNumber(item.done_images)}</td><td>{formatNumber(item.labeled_images)}</td><td>{item.tag_coverage}%</td></tr>)}
                      </tbody>
                    </DataTable>
                  </TableWrap>
                </Section>
              </>
            )}
          </Spin>
        )}
      </PageInner>
    </Page>
  );
}
