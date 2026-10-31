import { Alert, Button, Empty, Input, Modal, Pagination, Popover, Spin } from 'antd';
import { useNavigate, useRouteLoaderData, useSearchParams } from 'react-router-dom';
import _ from 'lodash';
import styled from 'styled-components';
import { FlexLayout } from '@labelu/components-react';
import { useTranslation } from '@labelu/i18n';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DownOutlined, RightOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons';

import {
  searchTaskImages,
  type TaskImageSearchHit,
} from '@/api/services/task';
import { ResponsiveGrid } from '@/components/ResponsiveGrid';
import type { TasksLoaderResult } from '@/loaders/task.loader';

import TaskCard from './components/taskCard';
import NullTask from './components/nullTask';
import { isInAppIframe } from '@/utils/inAppIframe';

type TranslationHook = () => {
  t: (key: string, options?: Record<string, unknown>) => string;
  i18n: { language: string };
};
const useTranslationCompat = useTranslation as unknown as TranslationHook;

const Wrapper = styled(FlexLayout)<{ $inIframe?: boolean }>`
  height: ${(p) => (p.$inIframe ? '100vh' : 'calc(100vh - var(--header-height))')};
  box-sizing: border-box;
`;

const Body = styled(FlexLayout.Content)`
  display: flex;
  width: 100%;
  min-width: 0;
  overflow-x: hidden;
  min-height: 0;
  background: #f5f7fb;

  @media (max-width: 48rem) {
    flex-direction: column;
    overflow-y: auto;
  }
`;

const Sidebar = styled.aside`
  width: 14.25rem;
  flex: 0 0 14.25rem;
  margin: 1rem 0 1rem 1rem;
  padding: 0.875rem 0.75rem;
  box-sizing: border-box;
  background: #fff;
  border: 1px solid #eef1f6;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgb(15 23 42 / 5%);
  overflow-y: auto;

  @media (max-width: 48rem) {
    width: auto;
    flex: 0 0 auto;
    margin: 0.75rem 0.75rem 0;
    padding: 0.625rem;
    display: flex;
    overflow-x: auto;
    overflow-y: hidden;
  }
`;

const MainPanel = styled(FlexLayout.Content)`
  padding: 0 1.5rem 0 1.25rem;
  width: 0;
  overflow-x: hidden;
  min-width: 0;

  @media (max-width: 48rem) {
    flex: 0 0 auto;
    width: 100%;
    overflow: visible;
    padding: 0 0.75rem;
    box-sizing: border-box;
  }
`;

const CardsWrapper = styled(ResponsiveGrid)`
  height: auto;
  width: 100%;
  min-width: 0;
  overflow-x: hidden;
  grid-template-columns: repeat(3, minmax(16rem, 1fr));
  gap: 0.875rem;

  @media (max-width: 80rem) {
    grid-template-columns: repeat(2, minmax(16rem, 1fr));
  }

  @media (max-width: 54rem) {
    grid-template-columns: minmax(0, 1fr);
  }
`;

const CardsScroll = styled(FlexLayout.Content)`
  width: 100%;
  min-width: 0;
  overflow-x: hidden;
`;

const Footer = styled(FlexLayout.Footer)`
  padding: 1rem 0;
  margin: 0 1.5rem;
`;

const AppVersion = styled(FlexLayout.Footer)`
  text-align: center;
  color: var(--color-text-tertiary);
`;

const TaskCardItem = styled(TaskCard)``;

const SidebarItem = styled.button<{ $active?: boolean }>`
  width: 100%;
  min-height: 2.875rem;
  border: 0;
  border-radius: 6px;
  padding: 0 0.75rem;
  margin-bottom: 0.375rem;
  background: ${(props) => (props.$active ? '#eef5ff' : 'transparent')};
  color: ${(props) => (props.$active ? '#2f6fed' : '#1f2937')};
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  font: inherit;
  font-size: 0.9375rem;
  font-weight: ${(props) => (props.$active ? 600 : 500)};
  text-align: left;
  transition: background-color 0.2s ease, color 0.2s ease;

  &:hover {
    background: ${(props) => (props.$active ? '#eef5ff' : '#f6f8fb')};
  }

  @media (max-width: 48rem) {
    flex: 0 0 9rem;
    margin: 0 0.375rem 0 0;
  }
`;

const SidebarLabel = styled.span`
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  overflow: hidden;

  > span {
    color: var(--color-text-tertiary);
    font-size: 0.75rem;
    font-weight: 400;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const SidebarSection = styled.section`
  width: 100%;

  @media (max-width: 48rem) {
    width: auto;
    flex: 0 0 auto;
    display: flex;
    align-items: stretch;
  }
`;

const SidebarSectionToggle = styled.button`
  width: 100%;
  min-height: 2rem;
  margin: 0.75rem 0 0.375rem;
  padding: 0 0.375rem;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  font: inherit;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0;
  text-align: left;
  transition: background-color 0.2s ease, color 0.2s ease;

  &:hover {
    background: #f6f8fb;
    color: var(--color-text-secondary);
  }

  &:focus-visible {
    outline: 2px solid #93c5fd;
    outline-offset: 1px;
  }

  .anticon {
    flex: 0 0 auto;
    font-size: 0.6875rem;
  }

  @media (max-width: 48rem) {
    width: 7.5rem;
    flex: 0 0 7.5rem;
    min-height: 2.875rem;
    margin: 0 0.375rem 0 0;
    padding: 0 0.75rem;
  }
`;

const SidebarSectionContent = styled.div`
  width: 100%;

  &[hidden] {
    display: none;
  }

  @media (max-width: 48rem) {
    width: auto;
    flex: 0 0 auto;
    display: flex;

    &[hidden] {
      display: none;
    }
  }
`;

const LabelNavigationNotice = styled.div`
  display: flex;
  align-items: center;
  gap: 0.625rem;
  min-height: 2.5rem;
  margin-top: 1rem;
  padding: 0.5rem 0.75rem;
  box-sizing: border-box;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.875rem;
  font-weight: 500;

  > span:last-child {
    min-width: 0;
    overflow-wrap: anywhere;
  }
`;

const SidebarCount = styled.span<{ $active?: boolean }>`
  flex: 0 0 auto;
  min-width: 2rem;
  height: 1.375rem;
  line-height: 1.3125rem;
  border-radius: 999px;
  border: 1px solid ${(props) => (props.$active ? '#bfdbfe' : '#e5e7eb')};
  background: ${(props) => (props.$active ? '#e8f1ff' : '#fff')};
  color: ${(props) => (props.$active ? '#2f6fed' : 'var(--color-text-secondary)')};
  font-size: 0.75rem;
  font-weight: ${(props) => (props.$active ? 600 : 400)};
  text-align: center;
  padding: 0 0.375rem;
  box-sizing: border-box;
`;

const HeaderRow = styled(FlexLayout.Header)`
  padding: 0 0 1rem;
  gap: 0.75rem;
  flex-wrap: wrap;
  align-items: flex-start;
`;

const HeaderActions = styled.div`
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
`;

const ToolbarSummary = styled.span`
  min-width: 0;
  overflow: hidden;
  color: #64748b;
  font-size: 0.75rem;
  text-overflow: ellipsis;
  white-space: nowrap;

  strong {
    color: #1e293b;
    font-weight: 650;
  }

  @media (max-width: 48rem) {
    flex-basis: 100%;
    order: 3;
  }
`;

const SearchToolbar = styled.div`
  width: 100%;
  margin-top: 0.25rem;

  @media (max-width: 48rem) {
    width: 100%;
  }
`;

const SearchBarRow = styled.div`
  display: grid;
  grid-template-columns: minmax(15rem, 1.15fr) minmax(15rem, 1fr) minmax(15rem, 0.85fr);
  gap: 0.75rem;
  padding: 0.875rem;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  @media (max-width: 66rem) {
    grid-template-columns: repeat(2, minmax(15rem, 1fr));
  }

  @media (max-width: 48rem) {
    grid-template-columns: minmax(0, 1fr);
    padding: 0.75rem;
  }
`;

const SearchField = styled.label`
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.375rem;
  color: #475569;
  font-size: 0.75rem;
  font-weight: 600;

  small {
    color: #94a3b8;
    font-size: 0.6875rem;
    font-weight: 400;
    line-height: 1.35;
  }

  .ant-input-affix-wrapper {
    min-height: 2.25rem;
    border-radius: 6px;
    border-color: #dbe3ef;
    box-shadow: none;

    &:focus-within {
      border-color: #7aa7f8;
      box-shadow: 0 0 0 2px rgb(47 111 237 / 10%);
    }
  }
`;

const ImageResultGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
  margin-top: 12px;
`;

const ImageResultCard = styled.button`
  border: 1px solid #eef1f6;
  border-radius: 8px;
  padding: 0;
  background: #fff;
  cursor: pointer;
  overflow: hidden;
  text-align: left;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;

  &:hover {
    border-color: #bfdbfe;
    box-shadow: 0 6px 16px rgb(47 111 237 / 12%);
  }
`;

const ImageResultThumb = styled.div`
  width: 100%;
  aspect-ratio: 16 / 10;
  background: #f3f4f6;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
`;

const ImageResultMeta = styled.div`
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.4;

  .filename {
    color: #1f2937;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .task-name {
    color: var(--color-text-tertiary);
    margin-top: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const TASK_SIDEBAR_SECTION_STATE_KEY = 'labelu:tasks-sidebar-sections:v2';

interface TaskSidebarSectionState {
  labels: boolean;
}

const DEFAULT_TASK_SIDEBAR_SECTION_STATE: TaskSidebarSectionState = {
  labels: false,
};

const readTaskSidebarSectionState = (): TaskSidebarSectionState => {
  if (typeof window === 'undefined') {
    return DEFAULT_TASK_SIDEBAR_SECTION_STATE;
  }
  try {
    const stored = window.localStorage.getItem(TASK_SIDEBAR_SECTION_STATE_KEY);
    if (!stored) {
      return DEFAULT_TASK_SIDEBAR_SECTION_STATE;
    }
    const parsed = JSON.parse(stored) as Partial<TaskSidebarSectionState>;
    return {
      labels: parsed.labels === true,
    };
  } catch {
    return DEFAULT_TASK_SIDEBAR_SECTION_STATE;
  }
};

const persistTaskSidebarSectionState = (state: TaskSidebarSectionState) => {
  try {
    window.localStorage.setItem(TASK_SIDEBAR_SECTION_STATE_KEY, JSON.stringify(state));
  } catch {
    // A blocked storage API should not prevent the sidebar from toggling.
  }
};

const TasksPage = () => {
  const inIframe = isInAppIframe();

  return (
    <Wrapper flex="column" $inIframe={inIframe}>
      <TaskListPanel />
    </Wrapper>
  );
};

const TaskListPanel = () => {
  const navigate = useNavigate();
  const routerLoaderData = useRouteLoaderData('tasks') as TasksLoaderResult;
  const tasksResponse = routerLoaderData?.taskList;
  const tasks = _.get(tasksResponse, 'data', []);
  const meta_data = _.get(tasksResponse, 'meta_data');
  const labeluVersion = _.get(routerLoaderData, ['headers', 'labelu-version']);
  const { t, i18n } = useTranslationCompat();

  const [searchParams, setSearchParams] = useSearchParams();
  const finalPageSize = searchParams.get('size') ? +searchParams.get('size')! : 16;
  const datasetSearch = searchParams.get('dataset_search') || '';
  const taskSearch = searchParams.get('q') || '';

  const [imageQuery, setImageQuery] = useState('');
  const [taskSearchInput, setTaskSearchInput] = useState(taskSearch);
  const [datasetSearchInput, setDatasetSearchInput] = useState(datasetSearch);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchHits, setSearchHits] = useState<TaskImageSearchHit[]>([]);
  const [searchTotal, setSearchTotal] = useState(0);
  const [searchPage, setSearchPage] = useState(1);
  const searchPageSize = 24;
  const searchSeqRef = useRef(0);
  useEffect(() => {
    setDatasetSearchInput(datasetSearch);
  }, [datasetSearch]);
  useEffect(() => {
    setTaskSearchInput(taskSearch);
  }, [taskSearch]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const normalizedTaskSearch = taskSearchInput.trim();
      const normalizedDatasetSearch = datasetSearchInput.trim();
      if (normalizedTaskSearch === taskSearch && normalizedDatasetSearch === datasetSearch) return;
      const next = new URLSearchParams(searchParams);
      next.set('page', '1');
      if (normalizedTaskSearch) next.set('q', normalizedTaskSearch);
      else next.delete('q');
      if (normalizedDatasetSearch) next.set('dataset_search', normalizedDatasetSearch);
      else next.delete('dataset_search');
      setSearchParams(next);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [taskSearchInput, taskSearch, datasetSearchInput, datasetSearch, searchParams, setSearchParams]);
  const runImageSearch = useCallback(
    async (keyword: string, page: number) => {
      const q = keyword.trim();
      if (!q) {
        setSearchHits([]);
        setSearchTotal(0);
        setSearchOpen(false);
        return;
      }
      const seq = ++searchSeqRef.current;
      setSearchLoading(true);
      try {
        const resp = await searchTaskImages({
          q,
          page: page - 1,
          size: searchPageSize,
        });
        if (seq !== searchSeqRef.current) {
          return;
        }
        setSearchHits(resp.data || []);
        setSearchTotal(resp.meta_data?.total ?? 0);
        setSearchOpen(true);
      } catch {
        if (seq === searchSeqRef.current) {
          setSearchHits([]);
          setSearchTotal(0);
        }
      } finally {
        if (seq === searchSeqRef.current) {
          setSearchLoading(false);
        }
      }
    },
    [],
  );

  const debouncedSearch = useMemo(
    () =>
      _.debounce((keyword: string) => {
        setSearchPage(1);
        runImageSearch(keyword, 1);
      }, 350),
    [runImageSearch],
  );

  useEffect(() => () => debouncedSearch.cancel(), [debouncedSearch]);

  useEffect(() => {
    if (imageQuery.trim()) {
      debouncedSearch(imageQuery);
    }
  }, [imageQuery, debouncedSearch]);

  const onImageQueryChange = (value: string) => {
    setImageQuery(value);
    if (!value.trim()) {
      searchSeqRef.current += 1;
      debouncedSearch.cancel();
      setSearchLoading(false);
      setSearchHits([]);
      setSearchTotal(0);
      setSearchOpen(false);
      setSearchPage(1);
      return;
    }
    debouncedSearch(value);
  };

  const openSample = (hit: TaskImageSearchHit) => {
    setSearchOpen(false);
    navigate(`/tasks/${hit.task_id}/samples/${hit.sample_id}`);
  };

  const createTask = () => {
    const next = new URLSearchParams({ isNew: 'true' });
    navigate(`/tasks/0/edit?${next.toString()}`);
  };

  const versionInfo = (
    <div>
      frontend: {_.get(window.__frontend, 'version', 'unknown')}
      <br />
      {_.chain(window.__frontend?.deps ?? {})
        .keys()
        .map((key) => {
          return (
            <div key={key}>
              {key}: {window.__frontend?.deps?.[key]}
            </div>
          );
        })
        .value()}
    </div>
  );

  return (
    <>
      <Body>
        <MainPanel scroll flex="column">
          {window.IS_ONLINE && (
            <Alert
              type="info"
              style={{
                marginTop: '1rem',
              }}
              showIcon
              message={
                <div>
                  {t('demoTips')}
                  <a data-wiz="local-deploy-alert" href="/docs">
                    {t('localDeploy')}
                  </a>
                </div>
              }
            />
          )}
          <HeaderRow flex="row" items="flex-start" justify="space-between">
              <HeaderActions>
                <Button type="primary" icon={<PlusOutlined />} onClick={createTask}>
                  {t('Create Task')}
                </Button>
                <ToolbarSummary>按任务、数据集或标签契约定位内容</ToolbarSummary>
              </HeaderActions>
              <SearchToolbar>
                <SearchBarRow>
                  <SearchField>
                    任务搜索
                    <Input
                      allowClear
                      maxLength={128}
                      prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
                      placeholder="输入任务名称、编号或状态"
                      aria-label="搜索任务"
                      value={taskSearchInput}
                      onChange={(e) => setTaskSearchInput(e.target.value)}
                      onPressEnter={() => {
                        const normalized = taskSearchInput.trim();
                        const next = new URLSearchParams(searchParams);
                        next.set('page', '1');
                        if (normalized) next.set('q', normalized);
                        else next.delete('q');
                        setSearchParams(next);
                      }}
                    />
                    <small>支持模糊匹配任务名称、编号和状态</small>
                  </SearchField>
                  <SearchField>
                    数据集 / 标签契约
                    <Input
                      allowClear
                      prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
                      placeholder="输入数据集名称或标签契约名称"
                      aria-label="搜索数据集或标签契约"
                      value={datasetSearchInput}
                      onChange={(e) => setDatasetSearchInput(e.target.value)}
                    />
                    <small>按数据集名称或标签契约名称模糊搜索</small>
                  </SearchField>
                  <SearchField>
                    图片搜索
                    <Input
                      allowClear
                      prefix={<SearchOutlined style={{ color: '#9ca3af' }} />}
                      placeholder="输入图片文件名"
                      aria-label="搜索图片文件名"
                      value={imageQuery}
                      onChange={(e) => onImageQueryChange(e.target.value)}
                      onPressEnter={() => {
                        debouncedSearch.cancel();
                        setSearchPage(1);
                        runImageSearch(imageQuery, 1);
                      }}
                    />
                    <small>搜索后可直接打开对应图片进行标注</small>
                  </SearchField>
                </SearchBarRow>
              </SearchToolbar>
          </HeaderRow>
          <CardsScroll scroll>
            {meta_data && meta_data?.total > 0 ? (
              <CardsWrapper>
                {tasks.map((cardInfo: any, cardInfoIndex: number) => {
                  return (
                    <TaskCardItem
                      key={cardInfoIndex}
                      cardInfo={cardInfo}
                    />
                  );
                })}
              </CardsWrapper>
            ) : (
              <NullTask />
            )}
          </CardsScroll>
        </MainPanel>
      </Body>
      <Footer flex="row" items="flex-between" justify="space-between">
        <AppVersion>
          <Popover content={versionInfo}>7GAI@{labeluVersion}</Popover>
        </AppVersion>
        {meta_data && searchParams && meta_data?.total > finalPageSize && (
          <Pagination
            current={searchParams.get('page') ? +searchParams.get('page')! : 1}
            total={meta_data?.total ?? 0}
            pageSize={finalPageSize}
            onChange={(value: number, _pageSize: number) => {
              const next = new URLSearchParams(searchParams);
              next.set('size', String(_pageSize));
              next.set('page', String(value));
              setSearchParams(next);
            }}
          />
        )}
      </Footer>
      <Modal
        title="图片搜索结果"
        open={searchOpen && !!imageQuery.trim()}
        centered
        onCancel={() => setSearchOpen(false)}
        footer={null}
        width={880}
        destroyOnClose={false}
      >
        {searchLoading && searchHits.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '2rem 0' }}>
            <Spin />
          </div>
        ) : searchHits.length === 0 ? (
          <Empty description={`未找到包含「${imageQuery.trim()}」的图片`} />
        ) : (
          <>
            <div style={{ fontSize: 13, color: '#6b7280' }}>
              共 {searchTotal} 张匹配「{imageQuery.trim()}」
            </div>
            <ImageResultGrid>
              {searchHits.map((hit) => {
                const thumb = hit.thumbnail_url || hit.url;
                return (
                  <ImageResultCard
                    key={`${hit.task_id}-${hit.sample_id}`}
                    type="button"
                    onClick={() => openSample(hit)}
                    title={hit.filename}
                  >
                    <ImageResultThumb>
                      {thumb ? (
                        <img src={thumb} alt={hit.filename} loading="lazy" />
                      ) : (
                        <span style={{ color: '#9ca3af', fontSize: 12 }}>无预览</span>
                      )}
                    </ImageResultThumb>
                    <ImageResultMeta>
                      <div className="filename">{hit.filename}</div>
                      <div className="task-name">{hit.task_name}</div>
                    </ImageResultMeta>
                  </ImageResultCard>
                );
              })}
            </ImageResultGrid>
            {searchTotal > searchPageSize && (
              <div style={{ marginTop: 16, textAlign: 'right' }}>
                <Pagination
                  size="small"
                  current={searchPage}
                  pageSize={searchPageSize}
                  total={searchTotal}
                  showSizeChanger={false}
                  onChange={(page) => {
                    setSearchPage(page);
                    runImageSearch(imageQuery, page);
                  }}
                />
              </div>
            )}
          </>
        )}
      </Modal>
    </>
  );
};

export default TasksPage;
