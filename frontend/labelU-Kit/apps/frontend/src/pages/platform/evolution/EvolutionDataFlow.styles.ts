/* Hallmark · app screen · model evolution data wall motion · genre: modern-minimal · theme: existing platform · v2.0.0 */
/* critique: motion should clarify the live data path without reducing legibility or breaking reduced-motion mode. */
import styled from 'styled-components';

const streamMove = 'evolutionStreamMove';
const particleTravel = 'evolutionParticleTravel';
const packetTravel = 'evolutionPacketTravel';
const sampleArrive = 'evolutionSampleArrive';
const sampleScan = 'evolutionSampleScan';

export const DataStream = styled.div<{ $active: boolean }>`
  @keyframes evolutionStreamMove {
    from { transform: translateX(-35%); }
    to { transform: translateX(35%); }
  }
  @keyframes evolutionParticleTravel {
    0% { left: 4%; opacity: 0; transform: scale(.6); }
    6% { opacity: 1; }
    48% { transform: scale(1.25); }
    94% { opacity: 1; }
    100% { left: 96%; opacity: 0; transform: scale(.7); }
  }
  @keyframes evolutionPacketTravel {
    0%, 8% { left: 4%; opacity: 0; transform: translateY(4px) scale(.92); }
    14% { opacity: 1; transform: translateY(0) scale(1); }
    72% { opacity: 1; }
    86%, 100% { left: 88%; opacity: 0; transform: translateY(-2px) scale(.96); }
  }
  @keyframes evolutionSampleArrive {
    0%, 100% { opacity: 1; transform: translateX(0); }
    50% { opacity: 1; transform: translateX(4px); }
  }
  @keyframes evolutionSampleScan {
    0% { transform: translateY(0); opacity: 0; }
    15% { opacity: 1; }
    85% { opacity: 1; }
    100% { transform: translateY(65px); opacity: 0; }
  }

  position: absolute;
  z-index: 0;
  top: 29px;
  right: 5%;
  left: 5%;
  height: 1px;
  overflow: visible;
  background: ${({ $active }) => $active ? 'var(--cyan-soft)' : 'var(--line-soft)'};

  i {
    position: absolute;
    inset: -1px 0;
    background: linear-gradient(90deg, transparent, var(--cyan-soft), var(--line-strong), var(--violet-soft), transparent);
    background-size: 45% 100%;
    background-repeat: no-repeat;
    animation: ${streamMove} 3.8s linear infinite;
    animation-play-state: ${({ $active }) => $active ? 'running' : 'paused'};
    opacity: ${({ $active }) => $active ? '.75' : '.15'};
  }
  i:nth-child(2) { animation-delay: -1.3s; opacity: .4; }
  i:nth-child(3) { animation-delay: -2.6s; opacity: .25; }

  @media (prefers-reduced-motion: reduce) { i { animation: none; } }
`;

export const FlowParticleLayer = styled.div<{ $active: boolean }>`
  position: absolute;
  z-index: 3;
  inset: 0;
  pointer-events: none;

  i {
    position: absolute;
    top: 27px;
    left: 4%;
    width: 5px;
    height: 5px;
    border: 1px solid var(--text-strong);
    border-radius: 50%;
    opacity: 0;
    background: var(--cyan);
    box-shadow: 0 0 5px var(--cyan), 0 0 13px var(--cyan-soft);
    animation: ${particleTravel} 6.5s linear infinite;
    animation-delay: calc(var(--particle-index) * -.92s);
    animation-play-state: ${({ $active }) => $active ? 'running' : 'paused'};
  }
  i:nth-child(3n) { width: 4px; height: 4px; top: 28px; background: var(--violet); box-shadow: 0 0 8px var(--violet-soft); }

  @media (prefers-reduced-motion: reduce) { i { animation: none; opacity: ${({ $active }) => $active ? '.65' : '.15'}; } }
`;

export const PacketLayer = styled.div<{ $active: boolean }>`
  position: absolute;
  z-index: 4;
  inset: 0;
  pointer-events: none;
  opacity: ${({ $active }) => $active ? '1' : '.25'};
  @media (prefers-reduced-motion: reduce) { opacity: ${({ $active }) => $active ? '.65' : '.25'}; }
`;

export const Packet = styled.span<{ $index: number }>`
  position: absolute;
  top: -8px;
  left: 4%;
  padding: 4px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 3px;
  opacity: 0;
  color: var(--muted-strong);
  background: var(--surface-deep);
  box-shadow: var(--shadow-glow);
  font-size: 10px;
  white-space: nowrap;
  animation: ${packetTravel} 14s linear infinite;
  animation-delay: ${({ $index }) => `${$index * -4.6}s`};

  b { margin-left: 4px; color: var(--cyan); font-weight: 600; }
`;

export const SampleFlash = styled.div`
  position: absolute;
  z-index: 6;
  right: 3%;
  bottom: 7px;
  left: 3%;
  display: flex;
  width: 258px;
  max-width: calc(100% - 8px);
  height: 68px;
  overflow: clip;
  border: 1px solid var(--line-strong);
  border-radius: 5px;
  opacity: 1;
  background: var(--surface-deep);
  box-shadow: var(--shadow-sample);
  animation: ${sampleArrive} 11s ease-in-out infinite;

  .sample-image { position: relative; flex: 0 0 102px; height: 100%; overflow: clip; }
  .sample-image > img { display: block; width: 100%; height: 100%; object-fit: cover; }
  .sample-live { position: absolute; z-index: 2; top: 4px; left: 4px; padding: 2px 4px; border-radius: 2px; color: var(--text-strong); background: var(--surface-input); font-size: 9px; }
  .sample-scan { position: absolute; z-index: 1; top: 0; right: 0; left: 0; height: 1px; background: var(--text-strong); box-shadow: 0 0 6px var(--cyan), 0 3px 10px var(--cyan-soft); animation: ${sampleScan} 2.2s linear infinite; }
  .sample-copy { display: flex; min-width: 0; flex: 1; flex-direction: column; padding: 8px 9px; }
  .sample-copy > span { overflow: clip; color: var(--cyan); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
  .sample-copy strong { margin-top: 4px; overflow: clip; color: var(--text-body); font-size: 10px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
  .sample-copy small { margin-top: 4px; overflow: clip; color: var(--muted-soft); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
  .sample-copy em { margin-top: auto; color: var(--violet); font-size: 9px; font-style: normal; }

  @media (prefers-reduced-motion: reduce) { opacity: 1; transform: none; animation: none; .sample-scan { animation: none; } }
`;

export const ReducedMotion = styled.div`
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
  }
`;
