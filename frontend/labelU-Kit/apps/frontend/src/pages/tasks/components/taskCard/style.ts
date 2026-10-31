import styled from 'styled-components';

export const Row = styled.div`
  display: flex;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  gap: 0.5rem;

  > * {
    min-width: 0;
  }
`;

export const CardTopline = styled(Row)`
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
`;

const CardMeta = styled.div`
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 0.5rem;
`;

export const CardIdentity = styled(CardMeta)`
  flex: 1 1 auto;
`;

export const CardActionGroup = styled.div`
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 0.125rem;

  .ant-btn {
    color: #64748b;
  }

  .ant-btn:hover {
    color: var(--color-primary);
    background: #eff6ff;
  }
`;

export const MediaBadge = styled.span<{ $color: string; $bg: string }>`
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: 12rem;
  height: 1.35rem;
  padding: 0 0.45rem;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 999px;
  color: ${(props) => props.$color};
  background-color: ${(props) => props.$bg};
  font-size: 0.6875rem;
  font-weight: 600;
  line-height: 1.35rem;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

export const CardWrapper = styled.div`
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  width: 100%;
  min-height: 14.25rem;
  min-width: 0;
  box-sizing: border-box;
  padding: 0.55rem 0.75rem 0.5rem;
  overflow: hidden;
  border: 1px solid #e3e8f0;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 2px 8px rgb(15 23 42 / 3%);
  cursor: pointer;
  transition: border-color 140ms ease, box-shadow 140ms ease, transform 140ms ease;

  &:hover,
  &:focus-visible {
    border-color: #9bbcf7;
    box-shadow: 0 8px 22px rgb(37 99 235 / 10%);
    outline: none;
    transform: translateY(-1px);

  }

  &:focus-visible {
    box-shadow: 0 0 0 3px rgb(37 99 235 / 18%);
  }
`;

export const TaskTitleBlock = styled.div`
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 0.2rem;
`;

export const TaskName = styled.div`
  min-width: 0;
  overflow: hidden;
  color: #142033;
  font-size: 0.9rem;
  font-weight: 650;
  line-height: 1.2;
  overflow-wrap: anywhere;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
`;

export const TaskId = styled.span`
  color: #94a3b8;
  font-size: 0.625rem;
  font-variant-numeric: tabular-nums;
`;

export const TagRow = styled.div`
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 0.25rem;
  flex-wrap: wrap;
`;

export const MetricGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.25rem;
  padding: 0.32rem 0.45rem;
  border: 1px solid #edf1f6;
  border-radius: 6px;
  background: #f8fafc;
`;

export const MetricItem = styled.div`
  min-width: 0;

  .metric-label {
    display: block;
    margin-bottom: 0.05rem;
    color: #94a3b8;
    font-size: 0.625rem;
  }

  .metric-value {
    display: block;
    overflow: hidden;
    color: #1e293b;
    font-size: 0.75rem;
    font-weight: 650;
    line-height: 1.2;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

export const ProgressSummary = styled.div`
  display: flex;
  flex-direction: column;
  gap: 0.15rem;

  .progress-label {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    color: #64748b;
    font-size: 0.6875rem;
  }

  .progress-value {
    color: #334155;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
`;

export const ProgressTrack = styled.div`
  width: 100%;
  height: 0.3rem;
  overflow: hidden;
  border-radius: 999px;
  background: #e8edf4;
`;

export const ProgressFill = styled.div<{ $percent: number; $complete?: boolean }>`
  width: ${(props) => `${Math.max(0, Math.min(100, props.$percent))}%`};
  height: 100%;
  border-radius: inherit;
  background: ${(props) => (props.$complete ? '#16a34a' : 'var(--color-primary)')};
  transition: width 180ms ease;
`;

export const CardFooter = styled(Row)`
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: auto;
  padding-top: 0.35rem;
  border-top: 1px solid #f0f2f5;
`;

export const FooterMeta = styled.div`
  min-width: 0;
  color: #94a3b8;
  font-size: 0.625rem;
  line-height: 1.30;

  .creator {
    max-width: 8.5rem;
    overflow: hidden;
    color: #64748b;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
`;

export const FooterAction = styled.div`
  flex: 0 0 auto;

  .ant-btn {
    border-radius: 5px;
    font-size: 0.6875rem;
  }
`;

export const LoadingMask = styled.div`
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  background: rgb(255 255 255 / 90%);
  color: var(--color-primary);
  pointer-events: none;
`;

export const LoadingText = styled.span`
  color: #64748b;
  font-size: 0.8125rem;
`;
