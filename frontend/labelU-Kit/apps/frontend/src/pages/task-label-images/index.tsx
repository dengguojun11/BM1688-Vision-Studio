import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Empty, Image, Modal, Pagination, Tag } from 'antd';
import { useEffect, useState } from 'react';
import { useLoaderData, useNavigate, useSearchParams } from 'react-router-dom';
import styled from 'styled-components';

import type { TaskLabelImagesLoaderResult } from '@/loaders/task.loader';
import ServerSearchInput from '@/components/ServerSearchInput';

import { fitContainSize } from './previewGeometry';

const Page = styled.main`
  min-height: 100%;
  padding: 1.25rem 1.5rem 2rem;
  box-sizing: border-box;
  background: #f5f7fb;
`;

const Header = styled.header`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
  flex-wrap: wrap;
`;

const Heading = styled.div`
  min-width: 0;

  h1 {
    margin: 0;
    color: #111827;
    font-size: 1.25rem;
    font-weight: 600;
  }

  p {
    margin: 0.35rem 0 0;
    color: #6b7280;
    font-size: 0.875rem;
  }
`;

const Grid = styled.div`
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.75rem;

  @media (max-width: 75rem) {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  @media (max-width: 56rem) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  @media (max-width: 40rem) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  @media (max-width: 28rem) {
    grid-template-columns: minmax(0, 1fr);
  }
`;

const Card = styled.article`
  min-width: 0;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
`;

const Stage = styled.button<{ $hasRatio: boolean }>`
  position: relative;
  width: 100%;
  min-height: ${(props) => (props.$hasRatio ? '0' : '7rem')};
  padding: 0;
  overflow: hidden;
  border: 0;
  background: #111827;
  cursor: zoom-in;

  .ant-image,
  .ant-image-img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .ant-image {
    cursor: inherit;
  }

  &:focus-visible {
    outline: 3px solid #1677ff;
    outline-offset: -3px;
  }
`;

const Box = styled.div<{ $x: number; $y: number; $width: number; $height: number; $color: string }>`
  position: absolute;
  left: ${(props) => `${props.$x}%`};
  top: ${(props) => `${props.$y}%`};
  width: ${(props) => `${props.$width}%`};
  height: ${(props) => `${props.$height}%`};
  box-sizing: border-box;
  border: 2px solid ${(props) => props.$color || '#1677ff'};
  pointer-events: none;
`;

const Meta = styled.div`
  min-width: 0;
  padding: 0.75rem;

  .filename {
    overflow: hidden;
    color: #1f2937;
    font-weight: 500;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .source {
    margin-top: 0.25rem;
    overflow: hidden;
    color: #6b7280;
    font-size: 0.75rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

const EmptyWrap = styled.div`
  padding: 4rem 0;
  background: #fff;
  border-radius: 8px;
`;

const PreviewTitle = styled.div`
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.5rem;
  padding-right: 1.75rem;

  .filename {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ant-tag {
    flex: none;
    margin: 0;
  }
`;

const PreviewViewport = styled.div`
  display: flex;
  width: 100%;
  height: clamp(18rem, 72vh, 48rem);
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: #111827;

  @media (max-width: 40rem) {
    height: min(68vh, 38rem);
  }
`;

const PreviewCanvas = styled.div`
  position: relative;
  flex: none;
  overflow: hidden;
`;

const PreviewImage = styled.img`
  display: block;
  width: 100%;
  height: 100%;
`;

function percent(value: unknown, total: unknown) {
  const numericValue = Number(value);
  const numericTotal = Number(total);
  if (!Number.isFinite(numericValue) || !Number.isFinite(numericTotal) || numericTotal <= 0) return 0;
  return Math.max(0, Math.min(100, (numericValue / numericTotal) * 100));
}

export default function TaskLabelImagesPage() {
  const { labelImages, label } = useLoaderData() as TaskLabelImagesLoaderResult;
  const [searchParams, setSearchParams] = useSearchParams();
  const [previewImage, setPreviewImage] = useState<any>(null);
  const [previewSize, setPreviewSize] = useState({ width: 0, height: 0 });
  const [previewViewport, setPreviewViewport] = useState<HTMLDivElement | null>(null);
  const navigate = useNavigate();
  const response = labelImages as any;
  const images = response?.data || [];
  const meta = response?.meta_data;
  const page = Number(searchParams.get('page') || 0);
  const size = Number(searchParams.get('size') || 24);
  const search = searchParams.get('q') || '';
  const [searchDraft, setSearchDraft] = useState(search);
  const labelId = Number(label?.id || 0);
  const previewWidth = Number(previewImage?.width || 0);
  const previewHeight = Number(previewImage?.height || 0);
  const hasPreviewRatio = previewWidth > 0 && previewHeight > 0;
  const isPreviewOpen = Boolean(previewImage);

  useEffect(() => {
    setSearchDraft(search);
  }, [search]);

  const applySearch = (value: string) => {
    const next = new URLSearchParams(searchParams);
    const normalized = value.trim();
    next.set('page', '0');
    if (normalized) next.set('q', normalized);
    else next.delete('q');
    setSearchParams(next);
  };

  useEffect(() => {
    if (!isPreviewOpen || !previewViewport || !hasPreviewRatio) {
      setPreviewSize((currentSize) =>
        currentSize.width === 0 && currentSize.height === 0 ? currentSize : { width: 0, height: 0 },
      );
      return;
    }

    const updatePreviewSize = () => {
      const nextSize = fitContainSize(
        previewViewport.clientWidth,
        previewViewport.clientHeight,
        previewWidth,
        previewHeight,
      );
      setPreviewSize((currentSize) =>
        currentSize.width === nextSize.width && currentSize.height === nextSize.height ? currentSize : nextSize,
      );
    };

    updatePreviewSize();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(updatePreviewSize);
    if (observer) {
      observer.observe(previewViewport);
    } else {
      window.addEventListener('resize', updatePreviewSize);
    }

    return () => {
      if (observer) {
        observer.disconnect();
      } else {
        window.removeEventListener('resize', updatePreviewSize);
      }
    };
  }, [hasPreviewRatio, isPreviewOpen, previewHeight, previewViewport, previewWidth]);

  const createTaskFromLabel = () => {
    if (!labelId) return;
    navigate(`/tasks/0/edit?isNew=true&source_label_id=${labelId}`);
  };

  return (
    <Page>
      <Header>
        <Heading>
          <h1>{label?.label_key || label?.label_value || '标签图片'}</h1>
          <p>
            {label?.label_value || ''} · 共 {meta?.total || 0} 张图片，仅显示该标签的标注
          </p>
        </Heading>
        <Button type="primary" icon={<PlusOutlined />} onClick={createTaskFromLabel} disabled={!labelId}>
          使用此标签创建任务
        </Button>
        <ServerSearchInput
          value={searchDraft}
          onChange={(value) => {
            setSearchDraft(value);
            if (!value) applySearch('');
          }}
          onSearch={applySearch}
          placeholder="搜索图片文件名、任务或编号"
          ariaLabel="搜索标签图片"
          style={{ width: 280, maxWidth: '100%' }}
        />
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>
          返回任务列表
        </Button>
      </Header>

      {images.length === 0 ? (
        <EmptyWrap>
          <Empty description="没有找到包含该标签的图片" />
        </EmptyWrap>
      ) : (
        <>
          <Grid>
            {images.map((image: any) => {
              const width = Number(image.width || 0);
              const height = Number(image.height || 0);
              return (
                <Card key={`${image.task_id}-${image.sample_id}`}>
                  <Stage
                    type="button"
                    $hasRatio={width > 0 && height > 0}
                    style={width > 0 && height > 0 ? { aspectRatio: `${width} / ${height}` } : undefined}
                    aria-label={`预览图片 ${image.filename}`}
                    data-testid={`label-image-preview-trigger-${image.sample_id}`}
                    onClick={() => setPreviewImage(image)}
                  >
                    {image.url || image.thumbnail_url ? (
                      <Image
                        src={image.url || image.thumbnail_url}
                        alt={image.filename}
                        loading="lazy"
                        preview={false}
                      />
                    ) : null}
                    {width > 0 &&
                      height > 0 &&
                      (image.annotations || []).map((annotation: any, index: number) => (
                        <Box
                          key={`${annotation.id || 'box'}-${index}`}
                          data-testid="label-image-thumbnail-box"
                          $x={percent(annotation.x, width)}
                          $y={percent(annotation.y, height)}
                          $width={percent(annotation.width, width)}
                          $height={percent(annotation.height, height)}
                          $color={annotation.color || label?.color || '#1677ff'}
                        />
                      ))}
                  </Stage>
                  <Meta>
                    <div className="filename" title={image.filename}>
                      {image.filename}
                    </div>
                    <div className="source" title={image.task_name}>
                      {image.task_name}
                    </div>
                    <Tag color={label?.color || 'blue'}>{label?.label_value || label?.label_key}</Tag>
                  </Meta>
                </Card>
              );
            })}
          </Grid>
          {meta?.total > size && (
            <div style={{ marginTop: '1rem', textAlign: 'right' }}>
              <Pagination
                current={page + 1}
                pageSize={size}
                total={meta.total}
                onChange={(nextPage, nextSize) => {
                  const next = new URLSearchParams(searchParams);
                  next.set('page', String(nextPage - 1));
                  next.set('size', String(nextSize));
                  setSearchParams(next);
                }}
              />
            </div>
          )}
        </>
      )}

      <Modal
        open={isPreviewOpen}
        centered
        footer={null}
        width="min(72rem, calc(100vw - 2rem))"
        title={
          previewImage ? (
            <PreviewTitle>
              <span className="filename" title={previewImage.filename}>
                {previewImage.filename}
              </span>
              <Tag color={label?.color || 'blue'}>{label?.label_value || label?.label_key}</Tag>
            </PreviewTitle>
          ) : null
        }
        onCancel={() => setPreviewImage(null)}
        destroyOnClose
      >
        <PreviewViewport ref={setPreviewViewport} data-testid="label-image-preview-viewport">
          {previewImage && (previewImage.url || previewImage.thumbnail_url) ? (
            <PreviewCanvas
              data-testid="label-image-preview-canvas"
              style={
                hasPreviewRatio && previewSize.width > 0 && previewSize.height > 0
                  ? { width: previewSize.width, height: previewSize.height }
                  : { width: '100%', height: '100%' }
              }
            >
              <PreviewImage
                src={previewImage.url || previewImage.thumbnail_url}
                alt={`预览 ${previewImage.filename}`}
                style={{ objectFit: hasPreviewRatio ? 'fill' : 'contain' }}
              />
              {hasPreviewRatio &&
                previewSize.width > 0 &&
                previewSize.height > 0 &&
                (previewImage.annotations || []).map((annotation: any, index: number) => (
                  <Box
                    key={`${annotation.id || 'preview-box'}-${index}`}
                    data-testid="label-image-preview-box"
                    $x={percent(annotation.x, previewWidth)}
                    $y={percent(annotation.y, previewHeight)}
                    $width={percent(annotation.width, previewWidth)}
                    $height={percent(annotation.height, previewHeight)}
                    $color={annotation.color || label?.color || '#1677ff'}
                  />
                ))}
            </PreviewCanvas>
          ) : null}
        </PreviewViewport>
      </Modal>
    </Page>
  );
}
