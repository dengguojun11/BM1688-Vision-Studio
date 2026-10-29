import styled from 'styled-components';

export interface ResponsiveGridProps {
  className?: string;
  style?: React.CSSProperties;
}

const GridWrapper = styled.div`
  display: grid;
  width: 100%;
  min-width: 0;
  gap: 1rem;
  align-content: start;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-auto-rows: minmax(0, auto);

  @media (max-width: 75rem) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  @media (max-width: 56rem) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  @media (max-width: 40rem) {
    grid-template-columns: minmax(0, 1fr);
  }
`;

export function ResponsiveGrid({ children, style, className }: React.PropsWithChildren<ResponsiveGridProps>) {
  return <GridWrapper style={style} className={className}>{children}</GridWrapper>;
}
