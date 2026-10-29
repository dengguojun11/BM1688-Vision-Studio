import styled from 'styled-components';

export const Cockpit = styled.main`
  --bg: #07101f;
  --panel: rgba(14, 27, 48, 0.88);
  --line: rgba(126, 166, 224, 0.15);
  --text: #edf6ff;
  --muted: #8195b2;
  --cyan: #39d9ff;
  --blue: #6585ff;
  --green: #47e5ad;
  --amber: #ffbf69;
  --red: #ff7188;
  box-sizing: border-box;
  width: calc(100% + 56px);
  min-height: calc(100vh - 120px);
  margin: -28px;
  padding: 26px 28px 32px;
  overflow: hidden;
  color: var(--text);
  background:
    radial-gradient(circle at 50% -20%, rgba(53, 115, 212, 0.18), transparent 40%),
    linear-gradient(rgba(55, 112, 171, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(55, 112, 171, 0.035) 1px, transparent 1px),
    var(--bg);
  background-size: auto, 40px 40px, 40px 40px, auto;
  font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;

  *, *::before, *::after { box-sizing: border-box; }
  button { font: inherit; }
  .ant-drawer-content { background: #0c1729; color: var(--text); }
  .ant-drawer-header { background: #0c1729; border-bottom-color: var(--line); }
  .ant-drawer-title, .ant-drawer-close { color: var(--text); }
  .ant-empty-description { color: var(--muted); }
  .ant-spin-dot-item { background: var(--cyan); }

  @media (max-width: 900px) {
    width: calc(100% + 40px);
    margin: -20px;
    padding: 20px;
  }
  @media (max-width: 600px) {
    width: calc(100% + 24px);
    margin: -16px -12px;
    padding: 16px 12px 24px;
  }

  &.is-fullscreen {
    width: 100%;
    min-height: 100vh;
    margin: 0;
  }
`;

export const Hero = styled.header`
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
  padding: 4px 0 18px;
  border-bottom: 1px solid var(--line);

  &::after {
    content: '';
    position: absolute;
    bottom: -1px;
    left: 0;
    width: 180px;
    height: 1px;
    background: linear-gradient(90deg, var(--cyan), transparent);
    box-shadow: 0 0 12px var(--cyan);
  }

  .eyebrow {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 7px;
    color: var(--cyan);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
  }
  .live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--cyan);
    box-shadow: 0 0 10px var(--cyan);
  }
  h1 { margin: 0; color: #f3f8ff; font-size: clamp(23px, 2vw, 32px); font-weight: 650; letter-spacing: 1px; }
  h1 span { margin-left: 12px; color: #7b91b0; font-size: 12px; font-weight: 400; letter-spacing: 2px; }
  p { max-width: 760px; margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
  .hero-actions { display: flex; flex-shrink: 0; align-items: center; gap: 10px; }
  .ant-btn {
    display: inline-flex; align-items: center; height: 36px; border-color: var(--line);
    color: #b9cbe2; background: rgba(19, 38, 65, 0.8); box-shadow: none;
  }
  .ant-btn:hover { border-color: rgba(57, 217, 255, 0.5) !important; color: var(--cyan) !important; background: rgba(24, 49, 79, 0.9) !important; }

  @media (max-width: 800px) { align-items: flex-start; flex-direction: column; .hero-actions { width: 100%; } h1 span { display: block; margin: 5px 0 0; } }
`;

export const AutomationBadge = styled.div<{ $tone: string }>`
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 13px;
  border: 1px solid ${({ $tone }) => $tone === 'on' ? 'rgba(71,229,173,.25)' : 'rgba(255,191,105,.2)'};
  border-radius: 6px;
  color: ${({ $tone }) => $tone === 'on' ? 'var(--green)' : 'var(--amber)'};
  background: ${({ $tone }) => $tone === 'on' ? 'rgba(71,229,173,.07)' : 'rgba(255,191,105,.06)'};
  font-size: 12px;
  white-space: nowrap;
`;

export const Metrics = styled.section`
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;

  @media (max-width: 1200px) { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 600px) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
`;

export const Metric = styled.div<{ $accent?: string }>`
  position: relative;
  min-height: 92px;
  padding: 16px 17px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: linear-gradient(145deg, rgba(20, 38, 65, 0.9), rgba(11, 24, 43, 0.9));

  &::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 2px; background: ${({ $accent }) => $accent || 'var(--cyan)'}; opacity: .8; }
  .metric-label { display: flex; align-items: center; justify-content: space-between; gap: 6px; color: var(--muted); font-size: 11px; }
  .metric-value { margin-top: 7px; color: #f0f7ff; font-family: "DIN Alternate", Inter, sans-serif; font-size: 28px; font-weight: 650; line-height: 1; }
  .metric-unit { margin-left: 5px; color: #627b9d; font-size: 10px; font-weight: 400; }
`;

export const MainGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(190px, 0.78fr) minmax(520px, 2.6fr) minmax(250px, 1fr);
  gap: 14px;
  align-items: stretch;

  @media (max-width: 1250px) { grid-template-columns: 220px minmax(0, 1fr); .intelligence-panel { grid-column: 1 / -1; } }
  @media (max-width: 760px) { grid-template-columns: 1fr; .intelligence-panel { grid-column: auto; } }
`;

export const BottomGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(260px, .95fr) minmax(400px, 1.65fr) minmax(260px, 1fr);
  gap: 14px;
  margin-top: 14px;

  @media (max-width: 1250px) { grid-template-columns: 1fr 1.5fr; .events-panel { grid-column: 1 / -1; } }
  @media (max-width: 760px) { grid-template-columns: 1fr; .events-panel { grid-column: auto; } }
`;

export const Panel = styled.section`
  position: relative;
  min-width: 0;
  padding: 17px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: inset 0 1px rgba(255,255,255,.015), 0 10px 30px rgba(0,0,0,.08);

  &::before, &::after { content: ''; position: absolute; width: 8px; height: 8px; pointer-events: none; }
  &::before { top: -1px; left: -1px; border-top: 1px solid rgba(57,217,255,.55); border-left: 1px solid rgba(57,217,255,.55); }
  &::after { right: -1px; bottom: -1px; border-right: 1px solid rgba(57,217,255,.3); border-bottom: 1px solid rgba(57,217,255,.3); }
`;

export const PanelTitle = styled.div`
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  h2 { margin: 0; color: #dbeaff; font-size: 13px; font-weight: 600; letter-spacing: .5px; }
  h2::before { content: ''; display: inline-block; width: 3px; height: 12px; margin-right: 8px; border-radius: 2px; background: var(--cyan); box-shadow: 0 0 8px rgba(57,217,255,.5); vertical-align: -1px; }
  p { margin: 4px 0 0; color: #627b9c; font-size: 10px; }
  .panel-extra { color: #6e86a5; font-size: 10px; white-space: nowrap; }
`;

export const LineageList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 354px;
  padding-right: 2px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #254565 transparent;
`;

export const LineageItem = styled.button<{ $active: boolean; $tone: string }>`
  width: 100%;
  padding: 12px;
  border: 1px solid ${({ $active }) => $active ? 'rgba(57,217,255,.38)' : 'rgba(126,166,224,.1)'};
  border-radius: 6px;
  color: inherit;
  text-align: left;
  background: ${({ $active }) => $active ? 'linear-gradient(100deg, rgba(36,130,177,.18), rgba(32,57,91,.32))' : 'rgba(5,16,30,.3)'};
  cursor: pointer;
  transition: border-color .18s, background .18s;
  &:hover { border-color: rgba(57,217,255,.35); }
  &:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
  .lineage-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .lineage-name { overflow: hidden; color: ${({ $active }) => $active ? '#e5f8ff' : '#b9c9dd'}; font-size: 12px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
  .lineage-type { margin-top: 6px; color: #607b9e; font-size: 9px; letter-spacing: .6px; }
  .lineage-meta { display: flex; justify-content: space-between; margin-top: 10px; color: #738ba9; font-size: 10px; }
  .lineage-score { color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : 'var(--cyan)'}; }
`;

export const StatusPill = styled.span<{ $tone: string }>`
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 7px;
  border-radius: 3px;
  color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'warning' ? 'var(--amber)' : $tone === 'active' ? 'var(--cyan)' : '#8297b4'};
  background: ${({ $tone }) => $tone === 'success' ? 'rgba(71,229,173,.08)' : $tone === 'danger' ? 'rgba(255,113,136,.08)' : $tone === 'warning' ? 'rgba(255,191,105,.08)' : $tone === 'active' ? 'rgba(57,217,255,.08)' : 'rgba(130,151,180,.08)'};
  font-size: 9px;
  white-space: nowrap;
  &::before { content: ''; width: 4px; height: 4px; border-radius: 50%; background: currentColor; box-shadow: ${({ $tone }) => $tone === 'active' ? '0 0 7px currentColor' : 'none'}; }
`;

export const EvolutionFlow = styled.div`
  position: relative;
  padding: 18px 12px 15px;
  overflow: hidden;
  border: 1px solid rgba(111,154,209,.1);
  border-radius: 6px;
  background: rgba(4,14,27,.35);

  &.has-preview { padding-bottom: 83px; }
`;

export const StageTrack = styled.div`
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: repeat(10, minmax(40px, 1fr));
  gap: 0;
  overflow-x: auto;
  padding: 1px 0 5px;
`;

export const Stage = styled.div<{ $tone: string }>`
  position: relative;
  min-width: 48px;
  color: #6b82a0;
  text-align: center;
  font-size: 9px;
  &:not(:last-child)::after { content: ''; position: absolute; top: 10px; left: calc(50% + 12px); width: calc(100% - 24px); height: 1px; background: ${({ $tone }) => $tone === 'success' || $tone === 'active' ? 'rgba(57,217,255,.45)' : 'rgba(116,148,188,.16)'}; }
  .stage-node { position: relative; z-index: 1; display: grid; place-items: center; width: 21px; height: 21px; margin: 0 auto 7px; border: 1px solid ${({ $tone }) => $tone === 'success' ? 'rgba(71,229,173,.6)' : $tone === 'danger' ? 'rgba(255,113,136,.6)' : $tone === 'active' ? 'rgba(57,217,255,.7)' : 'rgba(116,148,188,.25)'}; border-radius: 50%; color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'active' ? 'var(--cyan)' : '#58718f'}; background: #0c1b2e; font-size: 9px; box-shadow: ${({ $tone }) => $tone === 'active' ? '0 0 14px rgba(57,217,255,.22)' : 'none'}; }
  .stage-label { white-space: nowrap; color: ${({ $tone }) => $tone === 'active' ? '#c8f5ff' : $tone === 'success' ? '#8ebdad' : 'inherit'}; }
  .stage-count { display: block; margin-top: 3px; color: #4e6786; font-size: 8px; }
`;

export const ModelJourney = styled.div`
  margin-top: 16px;
  .journey-caption { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 12px; color: #66809f; font-size: 10px; }
  .journey-caption strong { color: #adc4df; font-weight: 500; }
`;

export const VersionTrack = styled.div`
  display: flex;
  align-items: stretch;
  min-height: 148px;
  padding: 5px 1px 8px;
  overflow-x: auto;
`;

export const VersionConnector = styled.div`
  display: flex;
  flex: 0 0 34px;
  align-items: center;
  justify-content: center;
  color: #315575;
  &::before { content: ''; width: 21px; height: 1px; background: currentColor; }
  &::after { content: ''; width: 5px; height: 5px; border-top: 1px solid currentColor; border-right: 1px solid currentColor; transform: rotate(45deg); }
`;

export const VersionNode = styled.button<{ $tone: string; $selected: boolean }>`
  position: relative;
  flex: 0 0 142px;
  min-height: 136px;
  padding: 12px;
  border: 1px solid ${({ $selected }) => $selected ? 'rgba(57,217,255,.6)' : 'rgba(115,156,205,.16)'};
  border-radius: 7px;
  color: inherit;
  text-align: left;
  background: ${({ $selected }) => $selected ? 'rgba(27,71,103,.38)' : 'rgba(12,26,45,.8)'};
  cursor: pointer;
  &:hover { border-color: rgba(57,217,255,.45); }
  &:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
  .version-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  .version-name { color: #d8e9fb; font-size: 12px; font-weight: 600; }
  .version-round { color: #506b8c; font-size: 9px; }
  .version-job { overflow: hidden; margin-top: 7px; color: #7891af; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
  .version-map { margin-top: 12px; color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : '#d7eaff'}; font-size: 22px; font-weight: 650; }
  .version-map-label { color: #587493; font-size: 8px; font-weight: 400; }
  .version-stage { margin-top: 9px; color: #7e96b3; font-size: 9px; }
  .version-progress { height: 2px; margin-top: 9px; overflow: hidden; border-radius: 2px; background: #172d47; }
  .version-progress > i { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--cyan)); box-shadow: 0 0 7px var(--cyan); }
  .champion { position: absolute; top: -8px; right: 8px; padding: 2px 6px; border: 1px solid rgba(71,229,173,.25); border-radius: 3px; color: var(--green); background: #0d2430; font-size: 8px; }
`;

export const InsightBlock = styled.div`
  padding: 12px;
  border: 1px solid rgba(139,120,255,.18);
  border-radius: 6px;
  background: linear-gradient(135deg, rgba(93,69,170,.12), rgba(16,32,54,.35));
  .ai-label { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; color: #b9aaff; font-size: 9px; font-weight: 600; letter-spacing: 1px; }
  .ai-summary { display: -webkit-box; overflow: hidden; color: #aebed2; font-size: 11px; line-height: 1.65; -webkit-line-clamp: 4; -webkit-box-orient: vertical; }
  .ai-meta { margin-top: 9px; color: #647a9a; font-size: 9px; }
`;

export const GateList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-top: 15px;
`;

export const GateRow = styled.div<{ $tone: string }>`
  display: grid;
  grid-template-columns: 20px 1fr auto;
  align-items: center;
  gap: 8px;
  color: #8fa4bf;
  font-size: 10px;
  .gate-icon { display: grid; place-items: center; width: 18px; height: 18px; border-radius: 4px; color: ${({ $tone }) => $tone === 'success' ? 'var(--green)' : $tone === 'danger' ? 'var(--red)' : $tone === 'warning' ? 'var(--amber)' : '#617c9c'}; background: rgba(105,142,185,.07); }
  .gate-count { color: #5f7897; font-size: 9px; }
`;

export const JobList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 7px;
  max-height: 268px;
  overflow-y: auto;
`;

export const JobItem = styled.button<{ $selected: boolean }>`
  width: 100%;
  padding: 10px;
  border: 1px solid ${({ $selected }) => $selected ? 'rgba(57,217,255,.35)' : 'rgba(126,166,224,.08)'};
  border-radius: 5px;
  color: inherit;
  text-align: left;
  background: ${({ $selected }) => $selected ? 'rgba(35,91,125,.16)' : 'rgba(4,14,27,.25)'};
  cursor: pointer;
  &:focus-visible { outline: 2px solid var(--cyan); }
  .job-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .job-name { overflow: hidden; color: #b9cce2; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  .job-meta { display: flex; justify-content: space-between; margin-top: 7px; color: #587391; font-size: 9px; }
  .job-progress { height: 3px; margin-top: 8px; overflow: hidden; border-radius: 3px; background: #172c44; }
  .job-progress i { display: block; height: 100%; background: linear-gradient(90deg, #596fff, var(--cyan)); }
`;

export const ChartWrap = styled.div`
  .chart-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 4px; }
  .chart-legend { display: flex; flex-wrap: wrap; gap: 12px; color: #7890ad; font-size: 9px; }
  .legend-item { display: flex; align-items: center; gap: 5px; }
  .legend-dot { width: 12px; height: 2px; background: var(--legend); }
  svg { display: block; width: 100%; height: 210px; overflow: visible; }
  text { fill: #54708e; font-family: Inter, sans-serif; font-size: 9px; }
  .chart-empty { display: grid; place-items: center; height: 210px; color: #5d7694; font-size: 11px; text-align: center; }
`;

export const EventList = styled.div`
  position: relative;
  display: flex;
  flex-direction: column;
  max-height: 268px;
  overflow-y: auto;
`;

export const EventItem = styled.div`
  position: relative;
  padding: 0 0 15px 17px;
  border-left: 1px solid rgba(97,137,185,.15);
  &:last-child { padding-bottom: 0; }
  &::before { content: ''; position: absolute; top: 3px; left: -3px; width: 5px; height: 5px; border-radius: 50%; background: #4f779f; }
  .event-title { color: #aebfd3; font-size: 10px; line-height: 1.4; }
  .event-summary { display: -webkit-box; margin-top: 4px; overflow: hidden; color: #617b99; font-size: 9px; line-height: 1.5; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
  .event-time { margin-top: 5px; color: #405c7b; font-size: 8px; }
`;

export const EmptyState = styled.div`
  display: grid;
  place-items: center;
  min-height: 130px;
  padding: 20px;
  color: #5e7896;
  font-size: 11px;
  line-height: 1.8;
  text-align: center;
`;

export const ErrorState = styled.div`
  display: grid;
  place-items: center;
  min-height: 420px;
  text-align: center;
  h2 { margin: 16px 0 8px; color: #dbeaff; font-size: 18px; }
  p { max-width: 460px; margin: 0 0 18px; color: var(--muted); font-size: 12px; line-height: 1.8; }
`;

export const Detail = styled.div`
  color: #a9bdd4;
  .detail-hero { padding: 16px; border: 1px solid var(--line); border-radius: 7px; background: rgba(22,42,68,.55); }
  .detail-hero h3 { margin: 10px 0 4px; color: #e4f1ff; font-size: 18px; }
  .detail-hero p { margin: 0; color: #6f88a6; font-size: 11px; }
  .detail-score { margin-top: 16px; color: var(--cyan); font-size: 32px; font-weight: 650; }
  .detail-score small { color: #67819f; font-size: 10px; font-weight: 400; }
  dl { display: grid; grid-template-columns: 110px 1fr; gap: 0; margin: 18px 0; border-top: 1px solid var(--line); }
  dt, dd { margin: 0; padding: 11px 0; border-bottom: 1px solid var(--line); font-size: 11px; }
  dt { color: #617c9c; }
  dd { overflow-wrap: anywhere; color: #b5c8dd; }
  h4 { margin: 20px 0 10px; color: #d8e8fa; font-size: 12px; }
  pre { padding: 12px; overflow: auto; border: 1px solid var(--line); border-radius: 5px; color: #9fb6cf; background: #081322; font-size: 10px; }
`;

export const ScreenFooter = styled.footer`
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-top: 14px;
  color: #405c7a;
  font-size: 9px;
  letter-spacing: .3px;
`;
