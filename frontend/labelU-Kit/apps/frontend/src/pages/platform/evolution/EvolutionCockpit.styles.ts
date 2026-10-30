/* Hallmark · app screen · model evolution data wall · genre: modern-minimal · theme: existing platform · v2.0.0 */
/* critique: the data wall needs one visual hierarchy, readable evidence rails, and a responsive shell that never leaks sideways. */
import styled from 'styled-components';

export const Cockpit = styled.main`
  --bg: #07101f;
  --panel: rgba(14, 27, 48, 0.92);
  --panel-strong: rgba(18, 37, 61, 0.96);
  --surface-deep: #081322;
  --surface-node: #0c1b2e;
  --surface-input: rgba(5, 16, 30, 0.34);
  --surface-selected: rgba(27, 71, 103, 0.42);
  --surface-hover: rgba(24, 49, 79, 0.92);
  --surface-violet: rgba(43, 37, 78, 0.68);
  --surface-success: rgba(71, 229, 173, 0.08);
  --surface-warning: rgba(255, 191, 105, 0.08);
  --surface-danger: rgba(255, 113, 136, 0.08);
  --line: rgba(126, 166, 224, 0.18);
  --line-soft: rgba(126, 166, 224, 0.1);
  --line-strong: rgba(57, 217, 255, 0.42);
  --line-focus: rgba(57, 217, 255, 0.72);
  --text: #edf6ff;
  --text-strong: #f3f8ff;
  --text-heading: #dbeaff;
  --text-body: #b9cbe2;
  --muted: #8fa7c3;
  --muted-strong: #a9bfd7;
  --muted-soft: #7896b6;
  --subtle: #617c9c;
  --cyan: #39d9ff;
  --cyan-soft: rgba(57, 217, 255, 0.1);
  --blue: #6585ff;
  --blue-soft: rgba(101, 133, 255, 0.12);
  --green: #47e5ad;
  --green-soft: rgba(71, 229, 173, 0.1);
  --amber: #ffbf69;
  --amber-soft: rgba(255, 191, 105, 0.1);
  --red: #ff7188;
  --red-soft: rgba(255, 113, 136, 0.1);
  --violet: #9b8cff;
  --violet-soft: rgba(155, 140, 255, 0.12);
  --scroll-thumb: #294d72;
  --chart-grid: rgba(103, 143, 190, 0.24);
  --shadow-panel: 0 14px 34px rgba(0, 0, 0, 0.16);
  --shadow-glow: 0 0 14px rgba(57, 217, 255, 0.18);
  --shadow-sample: 0 8px 28px rgba(0, 0, 0, 0.35), var(--shadow-glow);
  --bg-radial: rgba(53, 115, 212, 0.16);
  --grid-line: rgba(55, 112, 171, 0.035);
  --font-ui: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
  --font-metric: "DIN Alternate", var(--font-ui);
  box-sizing: border-box;
  width: calc(100% + 56px);
  min-height: calc(100vh - 120px);
  margin: -28px;
  padding: 28px 28px 34px;
  overflow-x: clip;
  overflow-y: visible;
  color: var(--text);
  background:
    radial-gradient(circle at 50% -20%, var(--bg-radial), transparent 42%),
    linear-gradient(var(--grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-line) 1px, transparent 1px),
    var(--bg);
  background-size: auto, 40px 40px, 40px 40px, auto;
  font-family: var(--font-ui);

  *, *::before, *::after { box-sizing: border-box; }
  button { font: inherit; }
  button:focus-visible,
  a:focus-visible,
  [role='button']:focus-visible {
    outline: 2px solid var(--line-focus);
    outline-offset: 3px;
  }
  button:active,
  a:active,
  [role='button']:active { transform: translateY(1px); }
  button:disabled,
  [aria-disabled='true'] { cursor: not-allowed; opacity: 0.58; }

  .ant-drawer-content,
  .ant-drawer-header { color: var(--text); background: var(--surface-node); }
  .ant-drawer-header { border-bottom-color: var(--line); }
  .ant-drawer-title,
  .ant-drawer-close { color: var(--text); }
  .ant-empty-description { color: var(--muted); }
  .ant-spin-dot-item { background: var(--cyan); }

  @media (max-width: 900px) {
    width: calc(100% + 40px);
    margin: -20px;
    padding: 22px 20px 28px;
  }

  @media (max-width: 600px) {
    width: calc(100% + 24px);
    margin: -16px -12px;
    padding: 18px 12px 24px;
  }

  &.is-fullscreen {
    width: 100%;
    min-height: 100vh;
    margin: 0;
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      scroll-behavior: auto !important;
      transition-duration: 0.01ms !important;
    }
    button:active,
    a:active,
    [role='button']:active { transform: none; }
  }
`;

export const Hero = styled.header`
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  margin-bottom: 22px;
  padding: 2px 0 20px;
  border-bottom: 1px solid var(--line);

  &::after {
    content: '';
    position: absolute;
    bottom: -1px;
    left: 0;
    width: 220px;
    height: 1px;
    background: linear-gradient(90deg, var(--cyan), transparent);
    box-shadow: 0 0 12px var(--cyan);
  }

  .hero-copy { min-width: 0; flex: 1; }
  h1 {
    min-width: 0;
    margin: 0;
    overflow-wrap: break-word;
    color: var(--text-strong);
    font-size: 34px;
    font-weight: 650;
    line-height: 1.16;
  }
  h1 span {
    display: inline-block;
    margin-left: 14px;
    color: var(--muted);
    font-size: 12px;
    font-weight: 400;
    line-height: 1.4;
  }
  p {
    max-width: 820px;
    margin: 10px 0 0;
    color: var(--muted);
    font-size: 14px;
    line-height: 1.65;
  }
  .hero-actions {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    flex-wrap: wrap;
  }
  .hero-freshness {
    color: var(--muted-soft);
    font-size: 11px;
    line-height: 1.35;
    white-space: nowrap;
  }
  .ant-btn {
    display: inline-flex;
    align-items: center;
    min-height: 38px;
    border-color: var(--line);
    border-radius: 6px;
    color: var(--text-body);
    background: var(--surface-input);
    box-shadow: none;
  }
  .ant-btn:hover,
  .ant-btn:focus-visible {
    border-color: var(--line-strong) !important;
    color: var(--cyan) !important;
    background: var(--surface-hover) !important;
  }

  @media (max-width: 920px) {
    align-items: flex-start;
    flex-direction: column;
    .hero-actions { width: 100%; justify-content: flex-start; }
    h1 { font-size: 28px; }
    h1 span { display: block; margin: 6px 0 0; }
    .hero-freshness { order: 4; }
  }

  @media (max-width: 420px) {
    .hero-actions .ant-btn { flex: 1 1 auto; }
    h1 { font-size: 24px; }
    p { font-size: 13px; }
  }
`;

export const AutomationBadge = styled.div<{ $tone: string }>`
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 8px 12px;
  border: 1px solid ${({ $tone }) => $tone === 'on' ? 'var(--green-soft)' : 'var(--amber-soft)'};
  border-radius: 6px;
  color: ${({ $tone }) => $tone === 'on' ? 'var(--green)' : 'var(--amber)'};
  background: ${({ $tone }) => $tone === 'on' ? 'var(--surface-success)' : 'var(--surface-warning)'};
  font-size: 12px;
  white-space: nowrap;
`;

export const Metrics = styled.section`
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;

  @media (max-width: 1440px) { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 680px) { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  @media (max-width: 420px) { grid-template-columns: 1fr; }
`;

export const Metric = styled.div<{ $accent?: string }>`
  position: relative;
  min-width: 0;
  min-height: 98px;
  padding: 15px 17px 14px;
  overflow: clip;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: linear-gradient(145deg, var(--panel-strong), var(--panel));
  box-shadow: var(--shadow-panel);

  &::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 2px;
    background: ${({ $accent }) => $accent || 'var(--cyan)'};
    opacity: 0.9;
  }
  .metric-label {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    min-width: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.35;
  }
  .metric-value {
    margin-top: 9px;
    color: var(--text-strong);
    font-family: var(--font-metric);
    font-size: 30px;
    font-weight: 650;
    line-height: 1;
  }
  .metric-unit { margin-left: 5px; color: var(--muted-soft); font-size: 11px; font-weight: 400; }
`;

export const MainGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 2.45fr) minmax(280px, 0.95fr);
  gap: 14px;
  align-items: stretch;

  @media (max-width: 1350px) {
    grid-template-columns: 220px minmax(0, 1fr);
    .intelligence-panel { grid-column: 1 / -1; }
  }
  @media (max-width: 760px) {
    grid-template-columns: 1fr;
    .intelligence-panel { grid-column: auto; }
  }
`;

export const BottomGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(260px, 0.95fr) minmax(0, 1.65fr) minmax(260px, 1fr);
  gap: 14px;
  margin-top: 14px;

  @media (max-width: 1350px) {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.45fr);
    .events-panel { grid-column: 1 / -1; }
  }
  @media (max-width: 760px) {
    grid-template-columns: 1fr;
    .events-panel { grid-column: auto; }
  }
`;

export const Panel = styled.section`
  position: relative;
  min-width: 0;
  min-height: 100%;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: var(--shadow-panel);

  &.flow-panel { background: var(--panel-strong); }
  &::before,
  &::after {
    content: '';
    position: absolute;
    width: 9px;
    height: 9px;
    pointer-events: none;
  }
  &::before { top: -1px; left: -1px; border-top: 1px solid var(--line-strong); border-left: 1px solid var(--line-strong); }
  &::after { right: -1px; bottom: -1px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }

  @media (max-width: 600px) { padding: 15px; }
`;

export const PanelTitle = styled.div`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;

  h2 {
    min-width: 0;
    margin: 0;
    overflow-wrap: break-word;
    color: var(--text-heading);
    font-size: 14px;
    font-weight: 600;
    line-height: 1.3;
  }
  h2::before {
    content: '';
    display: inline-block;
    width: 3px;
    height: 13px;
    margin-right: 8px;
    border-radius: 2px;
    background: var(--cyan);
    box-shadow: var(--shadow-glow);
    vertical-align: -1px;
  }
  p { margin: 4px 0 0; color: var(--muted-soft); font-size: 11px; line-height: 1.35; }
  .panel-extra { color: var(--muted-soft); font-size: 11px; white-space: nowrap; }
`;

export const LineageList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 396px;
  min-width: 0;
  padding-right: 3px;
  overflow-y: auto;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: var(--scroll-thumb) transparent;
`;

export const LineageItem = styled.button<{ $active: boolean; $tone: string }>`
  width: 100%;
  min-width: 0;
  padding: 12px;
  border: 1px solid ${({ $active }) => $active ? 'var(--line-strong)' : 'var(--line-soft)'};
  border-radius: 6px;
  color: inherit;
  text-align: left;
  background: ${({ $active }) => $active ? 'var(--surface-selected)' : 'var(--surface-input)'};
  cursor: pointer;
  transition: border-color 180ms ease, background-color 180ms ease, transform 120ms ease;

  &:hover { border-color: var(--line-strong); background: var(--surface-hover); }
  &:focus-visible { outline: 2px solid var(--line-focus); outline-offset: 3px; }
  .lineage-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
  .lineage-name { min-width: 0; overflow: clip; color: ${({ $active }) => $active ? 'var(--text-strong)' : 'var(--text-body)'}; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
  .lineage-type { margin-top: 7px; overflow-wrap: anywhere; color: var(--muted-soft); font-size: 10px; line-height: 1.35; }
  .lineage-meta { display: flex; justify-content: space-between; gap: 8px; margin-top: 10px; color: var(--muted); font-size: 11px; line-height: 1.3; }
  .lineage-score { color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : 'var(--cyan)'}; white-space: nowrap; }
`;

export const StatusPill = styled.span<{ $tone: string }>`
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 3px 7px;
  border-left: 2px solid currentColor;
  border-radius: 3px;
  color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'warning' ? 'var(--amber)' : $tone === 'active' ? 'var(--cyan)' : 'var(--muted-strong)'};
  background: ${({ $tone }) => $tone === 'success' ? 'var(--surface-success)' : $tone === 'danger' ? 'var(--surface-danger)' : $tone === 'warning' ? 'var(--surface-warning)' : $tone === 'active' ? 'var(--cyan-soft)' : 'var(--surface-input)'};
  font-size: 10px;
  line-height: 1.2;
  white-space: nowrap;
`;

export const EvolutionFlow = styled.div`
  position: relative;
  min-width: 0;
  min-height: 132px;
  padding: 18px 12px 15px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-deep);

  &.has-preview { padding-bottom: 83px; }
`;

export const StageTrack = styled.div`
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: repeat(10, minmax(48px, 1fr));
  gap: 0;
  min-width: 640px;
  overflow-x: auto;
  padding: 1px 0 6px;
  scrollbar-width: thin;
  scrollbar-color: var(--scroll-thumb) transparent;
  scroll-snap-type: x proximity;
  overscroll-behavior-x: contain;
`;

export const Stage = styled.div<{ $tone: string }>`
  position: relative;
  min-width: 0;
  color: var(--muted-soft);
  text-align: center;
  font-size: 10px;
  line-height: 1.25;
  scroll-snap-align: start;

  &:not(:last-child)::after {
    content: '';
    position: absolute;
    top: 10px;
    left: calc(50% + 12px);
    width: calc(100% - 24px);
    height: 1px;
    background: ${({ $tone }) => $tone === 'success' || $tone === 'active' ? 'var(--line-strong)' : 'var(--line-soft)'};
  }
  .stage-node {
    position: relative;
    z-index: 1;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    margin: 0 auto 7px;
    border: 1px solid ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'active' ? 'var(--cyan)' : 'var(--line)'};
    border-radius: 50%;
    color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'active' ? 'var(--cyan)' : 'var(--muted-soft)'};
    background: var(--surface-node);
    font-size: 10px;
  }
  .stage-label { display: block; overflow-wrap: anywhere; color: ${({ $tone }) => $tone === 'active' ? 'var(--text-strong)' : $tone === 'success' ? 'var(--green)' : 'inherit'}; font-size: 11px; }
  .stage-count { display: block; margin-top: 4px; color: var(--muted-soft); font-size: 10px; }
`;

export const ModelJourney = styled.div`
  margin-top: 17px;
  .journey-caption { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 12px; color: var(--muted); font-size: 11px; line-height: 1.35; }
  .journey-caption strong { color: var(--text-body); font-weight: 600; white-space: nowrap; }

  @media (max-width: 520px) {
    .journey-caption { align-items: flex-start; flex-direction: column; }
  }
`;

export const VersionTrack = styled.div`
  display: flex;
  align-items: stretch;
  min-height: 154px;
  min-width: 0;
  padding: 5px 1px 8px;
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scroll-thumb) transparent;
  scroll-snap-type: x proximity;
  overscroll-behavior-x: contain;

  @media (max-width: 1450px) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    overflow-x: visible;
  }
  @media (max-width: 560px) { grid-template-columns: 1fr; }
`;

export const VersionConnector = styled.div`
  display: flex;
  flex: 0 0 34px;
  align-items: center;
  justify-content: center;
  color: var(--scroll-thumb);
  &::before { content: ''; width: 21px; height: 1px; background: currentColor; }
  &::after { content: ''; width: 5px; height: 5px; border-top: 1px solid currentColor; border-right: 1px solid currentColor; transform: rotate(45deg); }
  @media (max-width: 1450px) { display: none; }
`;

export const VersionNode = styled.button<{ $tone: string; $selected: boolean }>`
  position: relative;
  flex: 0 0 146px;
  min-width: 0;
  min-height: 142px;
  padding: 13px;
  border: 1px solid ${({ $selected }) => $selected ? 'var(--line-focus)' : 'var(--line-soft)'};
  border-radius: 7px;
  color: inherit;
  text-align: left;
  background: ${({ $selected }) => $selected ? 'var(--surface-selected)' : 'var(--surface-node)'};
  cursor: pointer;
  scroll-snap-align: start;
  transition: border-color 180ms ease, background-color 180ms ease, transform 120ms ease;

  &:hover { border-color: var(--line-strong); background: var(--surface-hover); }
  &:focus-visible { outline: 2px solid var(--line-focus); outline-offset: 3px; }
  .version-top { display: flex; align-items: center; justify-content: space-between; gap: 7px; margin-bottom: 10px; }
  .version-name { color: var(--text-heading); font-size: 13px; font-weight: 650; }
  .version-job { min-width: 0; margin-top: 7px; overflow: clip; color: var(--muted-strong); font-size: 11px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
  .version-map { margin-top: 13px; color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : 'var(--text-strong)'}; font-size: 24px; font-weight: 650; line-height: 1; }
  .version-map-label { color: var(--muted-soft); font-size: 9px; font-weight: 400; }
  .version-stage { margin-top: 10px; color: var(--muted-strong); font-size: 10px; line-height: 1.3; }
  .version-progress { height: 3px; margin-top: 10px; overflow: clip; border-radius: 3px; background: var(--line-soft); }
  .version-progress > i { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--cyan)); box-shadow: var(--shadow-glow); }
  .champion { position: absolute; top: -9px; right: 9px; padding: 3px 6px; border: 1px solid var(--green-soft); border-radius: 3px; color: var(--green); background: var(--surface-node); font-size: 9px; }

  @media (max-width: 1450px) { width: 100%; min-width: 0; }
`;

export const InsightBlock = styled.div`
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--violet-soft);
  border-radius: 6px;
  background: var(--surface-violet);
  .ai-label { display: flex; align-items: center; gap: 6px; margin-bottom: 9px; color: var(--violet); font-size: 10px; font-weight: 600; }
  .ai-summary { display: -webkit-box; overflow: clip; color: var(--text-body); font-size: 12px; line-height: 1.65; -webkit-line-clamp: 5; -webkit-box-orient: vertical; }
  .ai-meta { margin-top: 10px; color: var(--muted-soft); font-size: 10px; line-height: 1.45; }
`;

export const GateList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-top: 16px;
`;

export const GateRow = styled.div<{ $tone: string }>`
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--muted-strong);
  font-size: 11px;
  line-height: 1.35;
  .gate-icon { display: grid; place-items: center; width: 19px; height: 19px; border-radius: 4px; color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'warning' ? 'var(--amber)' : 'var(--muted-soft)'}; background: var(--surface-input); }
  .gate-count { display: inline-flex; flex-wrap: wrap; justify-content: flex-end; gap: 3px 8px; color: var(--muted-soft); font-size: 10px; line-height: 1.35; text-align: right; }
  .gate-count-pass { color: var(--green); }
  .gate-count-pending { color: var(--muted-soft); }
  .gate-count-fail { color: var(--red); }

  @media (max-width: 460px) {
    grid-template-columns: 22px minmax(0, 1fr);
    .gate-count { grid-column: 2; text-align: left; }
  }
`;

export const JobList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 286px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scroll-thumb) transparent;
`;

export const JobItem = styled.button<{ $selected: boolean }>`
  width: 100%;
  min-width: 0;
  padding: 11px;
  border: 1px solid ${({ $selected }) => $selected ? 'var(--line-strong)' : 'var(--line-soft)'};
  border-radius: 5px;
  color: inherit;
  text-align: left;
  background: ${({ $selected }) => $selected ? 'var(--surface-selected)' : 'var(--surface-input)'};
  cursor: pointer;
  transition: border-color 180ms ease, background-color 180ms ease, transform 120ms ease;
  &:hover { border-color: var(--line-strong); background: var(--surface-hover); }
  &:focus-visible { outline: 2px solid var(--line-focus); outline-offset: 3px; }
  .job-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
  .job-name { min-width: 0; overflow: clip; color: var(--text-body); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  .job-meta { display: flex; justify-content: space-between; gap: 8px; margin-top: 8px; color: var(--muted-soft); font-size: 10px; line-height: 1.35; }
  .job-progress { height: 3px; margin-top: 9px; overflow: clip; border-radius: 3px; background: var(--line-soft); }
  .job-progress i { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--cyan)); }
`;

export const ChartWrap = styled.div`
  min-width: 0;
  .chart-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
  .chart-legend { display: flex; flex-wrap: wrap; gap: 12px; color: var(--muted-strong); font-size: 11px; }
  .legend-item { display: flex; align-items: center; gap: 5px; }
  .legend-dot { width: 12px; height: 2px; background: var(--legend); }
  svg { display: block; width: 100%; height: 230px; overflow: visible; }
  text { fill: var(--muted-soft); font-family: var(--font-ui); font-size: 11px; }
  .chart-empty { display: grid; place-items: center; min-height: 154px; padding: 22px 12px; color: var(--muted-strong); font-size: 12px; line-height: 1.65; text-align: center; }
  .chart-empty button { margin-top: 10px; }
  .chart-status { margin-top: 6px; color: var(--amber); font-size: 11px; }
  .chart-mode-button { min-height: 28px; padding: 3px 7px; border: 1px solid transparent; border-radius: 4px; color: var(--muted-strong); background: transparent; cursor: pointer; }
  .chart-mode-button:hover { color: var(--cyan); background: var(--cyan-soft); }
  .chart-mode-button:focus-visible { outline: 2px solid var(--line-focus); outline-offset: 2px; }
  .chart-mode-button[aria-pressed='true'] { color: var(--cyan); background: var(--cyan-soft); }

  @media (max-width: 520px) { svg { height: 200px; } .chart-empty { min-height: 132px; } }
`;

export const EventList = styled.div`
  position: relative;
  display: flex;
  flex-direction: column;
  max-height: 286px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--scroll-thumb) transparent;
`;

export const EventItem = styled.div`
  position: relative;
  padding: 0 0 16px 18px;
  border-left: 1px solid var(--line-soft);
  &:last-child { padding-bottom: 0; }
  &::before { content: ''; position: absolute; top: 3px; left: -3px; width: 5px; height: 5px; border-radius: 50%; background: var(--scroll-thumb); }
  .event-title { color: var(--text-body); font-size: 12px; line-height: 1.45; }
  .event-summary { display: -webkit-box; margin-top: 5px; overflow: clip; color: var(--muted-strong); font-size: 11px; line-height: 1.55; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .event-time { margin-top: 6px; color: var(--muted-soft); font-size: 10px; line-height: 1.35; }
`;

export const EmptyState = styled.div`
  display: grid;
  place-items: center;
  min-height: 138px;
  padding: 22px;
  color: var(--muted-strong);
  font-size: 12px;
  line-height: 1.8;
  text-align: center;
`;

export const ErrorState = styled.div`
  display: grid;
  place-items: center;
  min-height: 420px;
  text-align: center;
  h2 { margin: 16px 0 8px; color: var(--text-heading); font-size: 19px; }
  p { max-width: 460px; margin: 0 0 18px; color: var(--muted); font-size: 13px; line-height: 1.8; }
`;

export const Detail = styled.div`
  color: var(--muted-strong);
  .detail-hero { padding: 17px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface-selected); }
  .detail-hero h3 { margin: 11px 0 5px; color: var(--text-strong); font-size: 19px; line-height: 1.35; overflow-wrap: anywhere; }
  .detail-hero p { margin: 0; color: var(--muted-soft); font-size: 12px; line-height: 1.45; }
  .detail-score { margin-top: 17px; color: var(--cyan); font-size: 34px; font-weight: 650; line-height: 1; }
  .detail-score small { color: var(--muted-soft); font-size: 11px; font-weight: 400; }
  dl { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 0; margin: 19px 0; border-top: 1px solid var(--line); }
  dt, dd { min-width: 0; margin: 0; padding: 11px 0; border-bottom: 1px solid var(--line); font-size: 12px; line-height: 1.45; }
  dt { color: var(--muted-soft); }
  dd { overflow-wrap: anywhere; color: var(--text-body); }
  h4 { margin: 21px 0 10px; color: var(--text-heading); font-size: 13px; }
  pre { padding: 13px; overflow: auto; border: 1px solid var(--line); border-radius: 5px; color: var(--muted-strong); background: var(--surface-deep); font-size: 11px; }
`;

export const GovernanceNote = styled.div`
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--line-soft);
  color: var(--subtle);
  font-size: 11px;
  line-height: 1.7;
`;

export const ScreenFooter = styled.footer`
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
  color: var(--muted-soft);
  font-size: 11px;
  line-height: 1.4;

  @media (max-width: 600px) { align-items: flex-start; flex-direction: column; gap: 5px; }
`;
