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
    0%, 12% { opacity: 0; transform: translate(-14px, 8px) scale(.82); filter: blur(3px); }
    18%, 68% { opacity: 1; transform: translate(0, 0) scale(1); filter: blur(0); }
    78%, 100% { opacity: 0; transform: translate(22px, -4px) scale(.94); filter: blur(1px); }
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
  background: rgba(57, 217, 255, ${({ $active }) => $active ? '.13' : '.05'});

  i {
    position: absolute;
    inset: -1px 0;
    background: linear-gradient(90deg, transparent, rgba(57,217,255,.08), rgba(57,217,255,.7), rgba(157,137,255,.25), transparent);
    background-size: 45% 100%;
    background-repeat: no-repeat;
    animation: ${streamMove} 3.8s linear infinite;
    animation-play-state: ${({ $active }) => $active ? 'running' : 'paused'};
    opacity: ${({ $active }) => $active ? '.75' : '.15'};
  }
  i:nth-child(2) { animation-delay: -1.3s; opacity: .4; }
  i:nth-child(3) { animation-delay: -2.6s; opacity: .25; }
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
    border: 1px solid rgba(211,251,255,.9);
    border-radius: 50%;
    opacity: 0;
    background: #39d9ff;
    box-shadow: 0 0 5px #39d9ff, 0 0 13px rgba(57,217,255,.9), -8px 0 8px rgba(57,217,255,.4), -17px 0 10px rgba(101,133,255,.18);
    animation: ${particleTravel} 6.5s linear infinite;
    animation-delay: calc(var(--particle-index) * -.92s);
    animation-play-state: ${({ $active }) => $active ? 'running' : 'paused'};
  }
  i:nth-child(3n) { width: 4px; height: 4px; top: 28px; background: #a495ff; box-shadow: 0 0 8px #9b8cff, -10px 0 9px rgba(155,140,255,.35); }
`;

export const PacketLayer = styled.div<{ $active: boolean }>`
  position: absolute;
  z-index: 4;
  inset: 0;
  pointer-events: none;
  opacity: ${({ $active }) => $active ? '1' : '.25'};
`;

export const Packet = styled.span<{ $index: number }>`
  position: absolute;
  top: -8px;
  left: 4%;
  padding: 3px 7px;
  border: 1px solid rgba(87,211,255,.22);
  border-radius: 3px;
  opacity: 0;
  color: #86a6c4;
  background: rgba(7,24,41,.94);
  box-shadow: 0 0 14px rgba(57,217,255,.07);
  font-size: 8px;
  white-space: nowrap;
  animation: ${packetTravel} 14s linear infinite;
  animation-delay: ${({ $index }) => `${$index * -4.6}s`};

  b { margin-left: 4px; color: #61dcf4; font-weight: 500; }
`;

export const SampleFlash = styled.div`
  position: absolute;
  z-index: 6;
  bottom: 7px;
  left: 3%;
  display: flex;
  width: 258px;
  height: 68px;
  overflow: hidden;
  border: 1px solid rgba(57,217,255,.35);
  border-radius: 5px;
  opacity: 0;
  background: rgba(7,22,39,.97);
  box-shadow: 0 8px 28px rgba(0,0,0,.35), 0 0 18px rgba(57,217,255,.1);
  animation: ${sampleArrive} 11s ease-in-out infinite;

  .sample-image { position: relative; flex: 0 0 102px; height: 100%; overflow: hidden; }
  .sample-image > img { display: block; width: 100%; height: 100%; object-fit: cover; }
  .sample-live { position: absolute; z-index: 2; top: 4px; left: 4px; padding: 1px 3px; border-radius: 2px; color: #d9fbff; background: rgba(4,20,31,.7); font-size: 6px; letter-spacing: 1px; }
  .sample-scan { position: absolute; z-index: 1; top: 0; right: 0; left: 0; height: 1px; background: #8df1ff; box-shadow: 0 0 6px #39d9ff, 0 3px 10px rgba(57,217,255,.35); animation: ${sampleScan} 2.2s linear infinite; }
  .sample-copy { display: flex; min-width: 0; flex: 1; flex-direction: column; padding: 8px 9px; }
  .sample-copy > span { color: #39d9ff; font-size: 8px; letter-spacing: 1px; }
  .sample-copy strong { margin-top: 4px; overflow: hidden; color: #c9e2f6; font-size: 9px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
  .sample-copy small { margin-top: 4px; overflow: hidden; color: #5e7d9b; font-size: 7px; text-overflow: ellipsis; white-space: nowrap; }
  .sample-copy em { margin-top: auto; color: #758ee9; font-size: 7px; font-style: normal; }

  @media (prefers-reduced-motion: reduce) {
    opacity: 1;
    transform: none;
    animation: none;
  }
`;

export const ReducedMotion = styled.div`
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
  }
`;
