import styled from 'styled-components';

/** 单图标注顶栏：AI 标注与选项保持原生工具栏风格 */
export const AiAnnotateRow = styled.div`
  display: inline-flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
  padding: 0 0.5rem 0 0;
  margin-right: 0.25rem;
  border-right: 1px solid rgba(0, 0, 0, 0.08);
  max-width: 100%;
  box-sizing: border-box;

  .ant-btn {
    padding-inline: 0.5rem;
  }

  .ant-checkbox-wrapper {
    margin-inline-start: 0 !important;
    white-space: nowrap;
  }
`;

export const ToolbarActionsRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
`;

export const NegativeSampleControl = styled.span<{ $active: boolean }>`
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding-left: 0.75rem;
  border-left: 1px solid ${({ $active }) => ($active ? '#1677ff' : 'rgba(0, 0, 0, 0.08)')};

  .ant-checkbox-wrapper {
    color: ${({ $active }) => ($active ? '#0958d9' : 'rgba(0, 0, 0, 0.72)')};
    font-weight: ${({ $active }) => ($active ? 600 : 400)};
  }
`;

export const ImageDeleteOverlay = styled.div`
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 24;

  .ant-btn {
    width: 32px;
    min-width: 32px;
    height: 32px;
    padding: 0;
    border-radius: 999px;
    background: rgba(0, 0, 0, 0.58);
    border-color: rgba(255, 255, 255, 0.22);
    color: #fff;
    box-shadow: 0 10px 24px rgba(0, 0, 0, 0.22);
  }

  .ant-btn:hover,
  .ant-btn:focus {
    color: #fff !important;
    background: rgba(0, 0, 0, 0.74) !important;
    border-color: rgba(255, 255, 255, 0.34) !important;
  }
`;
