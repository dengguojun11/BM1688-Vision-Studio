import styled, { createGlobalStyle } from 'styled-components';
import { Link } from 'react-router-dom';
import { PRODUCT_LOGO_SRC } from '@/utils/product';

export const EvoLoginGlobalStyle = createGlobalStyle`
  *,
  *::before,
  *::after {
    box-sizing: border-box;
  }

  html,
  body {
    width: 100%;
    height: 100%;
    margin: 0;
    overflow: hidden;
  }

  body {
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
      'Microsoft YaHei', sans-serif;
    color: #0f172a;
    background: #fbfdff;
    -webkit-font-smoothing: antialiased;
    text-rendering: geometricPrecision;
  }
`;

export const EvoLoginShell = styled.main`
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  background: radial-gradient(
      circle at 50% 48%,
      rgba(255, 255, 255, 0.98),
      rgba(250, 252, 255, 0.98) 58%,
      rgba(246, 249, 253, 0.96) 100%
    ),
    #fbfdff;
`;

export const EvoVortexCanvas = styled.canvas`
  position: fixed;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
`;

export const EvoFrame = styled.section`
  position: absolute;
  inset: 36px 42px;
  z-index: 1;
  border: 1px solid rgba(34, 111, 255, 0.2);
  background: linear-gradient(rgba(34, 111, 255, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34, 111, 255, 0.055) 1px, transparent 1px),
    radial-gradient(circle at 10% 8%, rgba(255, 255, 255, 0.46), rgba(255, 255, 255, 0) 32%);
  background-size: 100% 1px, 1px 100%, auto;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.68);

  &::before {
    content: '';
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0.42;
    mix-blend-mode: multiply;
    background-image: radial-gradient(rgba(7, 68, 145, 0.045) 0.8px, transparent 0.8px),
      radial-gradient(rgba(7, 68, 145, 0.025) 0.7px, transparent 0.7px);
    background-size: 4px 4px, 7px 7px;
  }

  @media (max-width: 1180px) {
    inset: 24px;
  }

  @media (max-width: 860px) {
    inset: 0;
    overflow-y: auto;
  }
`;

export const EvoTopBar = styled.header`
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: 330px 1fr 390px;
  min-height: 96px;
  border-bottom: 1px solid rgba(34, 111, 255, 0.24);

  @media (max-width: 1180px) {
    grid-template-columns: 300px 1fr 360px;
  }

  @media (max-width: 860px) {
    grid-template-columns: 1fr;
  }
`;

export const EvoLogoArea = styled.div`
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 48px;
  border-right: 1px solid rgba(34, 111, 255, 0.24);
`;

export const EvoBrandMark = styled.img.attrs({ src: PRODUCT_LOGO_SRC, alt: '7G 标志' })`
  display: block;
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  object-fit: contain;
`;

export const EvoBrandWord = styled.div``;

export const EvoBrandTitle = styled.div`
  color: #0b63ff;
  font-size: 26px;
  font-weight: 800;
  line-height: 1;
  letter-spacing: 0.18em;
`;

export const EvoBrandSubTitle = styled.div`
  margin-top: 4px;
  color: #0b63ff;
  font-size: 14px;
  line-height: 1.2;
  letter-spacing: 0.12em;
`;

export const EvoTopEmpty = styled.div`
  border-right: 1px solid rgba(34, 111, 255, 0.24);

  @media (max-width: 860px) {
    display: none;
  }
`;

export const EvoNav = styled.nav`
  display: flex;
  align-items: stretch;
  justify-content: flex-end;

  a {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 120px;
    padding: 0 28px;
    border-left: 1px solid rgba(34, 111, 255, 0.18);
    color: #0b63ff;
    font-size: 15px;
    font-weight: 500;
    letter-spacing: 0.18em;
    text-decoration: none;
  }

  a:first-child::after {
    content: '';
    position: absolute;
    bottom: -1px;
    left: 50%;
    width: 18px;
    height: 2px;
    transform: translateX(-50%);
    background: #0b63ff;
  }

  @media (max-width: 860px) {
    display: none;
  }
`;

export const EvoBody = styled.div`
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(420px, 1.22fr) 360px;
  align-items: center;
  min-height: calc(100vh - 96px - 72px);
  padding: 34px 64px 42px 72px;

  @media (max-width: 1180px) {
    grid-template-columns: minmax(320px, 1fr) minmax(320px, 0.65fr);
  }

  @media (max-width: 860px) {
    grid-template-columns: 1fr;
    min-height: auto;
    gap: 40px;
    padding: 32px 28px 96px;
  }
`;

export const EvoHero = styled.div`
  max-width: 520px;
  transform: translateY(4px);
`;

export const EvoEyebrow = styled.div`
  margin-bottom: 36px;
  color: #0b63ff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.16em;

  &::after {
    content: '';
    display: block;
    width: 42px;
    height: 2px;
    margin-top: 14px;
    background: #0b63ff;
  }
`;

export const EvoHeroTitle = styled.h1`
  margin: 0;
  color: #0b63ff;
  font-family: 'Noto Serif SC', 'Songti SC', STSong, SimSun, serif;
  font-size: clamp(52px, 5.2vw, 84px);
  font-weight: 700;
  line-height: 1.18;
  letter-spacing: 0.02em;
`;

export const EvoDivider = styled.div`
  width: 48px;
  height: 1px;
  margin: 42px 0 28px;
  background: #0b63ff;
`;

export const EvoHeroText = styled.p`
  max-width: 440px;
  margin: 0;
  color: #0b63ff;
  font-family: 'Noto Serif SC', 'Songti SC', STSong, SimSun, serif;
  font-size: 20px;
  line-height: 1.9;
  letter-spacing: 0.04em;
`;

export const EvoCommandMark = styled.span`
  display: inline-block;
  min-width: 0.72em;
  margin-left: 2px;
  color: rgba(11, 99, 255, 0.78);
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-weight: 700;
  line-height: 1;
  text-align: left;
  text-shadow: 0 0 14px rgba(11, 99, 255, 0.18);
`;

export const EvoInlineCommand = styled.div`
  display: inline-flex;
  align-items: center;
  min-width: 236px;
  min-height: 22px;
  margin-top: 18px;
  padding: 4px 9px;
  border-left: 1px solid rgba(11, 99, 255, 0.42);
  color: rgba(11, 99, 255, 0.72);
  background: rgba(255, 255, 255, 0.34);
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 0.04em;
  white-space: nowrap;

  &::after {
    content: '';
    width: 6px;
    height: 14px;
    margin-left: 3px;
    background: currentColor;
    animation: evo-caret 1s steps(2, start) infinite;
  }

  @keyframes evo-caret {
    0%,
    46% {
      opacity: 1;
    }

    47%,
    100% {
      opacity: 0;
    }
  }
`;

export const EvoCenterSpacer = styled.div`
  @media (max-width: 1180px) {
    display: none;
  }
`;

export const EvoKeywords = styled.div`
  position: absolute;
  left: 72px;
  bottom: 46px;
  color: #0b63ff;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.38em;

  &::after {
    content: '';
    display: block;
    width: 1px;
    height: 18px;
    margin-top: 12px;
    background: #0b63ff;
  }

  @media (max-width: 860px) {
    position: static;
    margin: -8px 28px 28px;
    line-height: 1.6;
  }
`;

export const EvoCard = styled.aside`
  position: relative;
  justify-self: end;
  width: 100%;
  max-width: 342px;
  padding: 36px 34px 30px;
  border: 1px solid rgba(34, 111, 255, 0.24);
  background: rgba(255, 255, 255, 0.42);
  box-shadow: 0 20px 80px rgba(36, 84, 142, 0.06);
  backdrop-filter: blur(8px);

  &::before {
    content: '';
    position: absolute;
    left: -1px;
    top: -1px;
    width: 18px;
    height: 18px;
    border-top: 2px solid #0b63ff;
    border-left: 2px solid #0b63ff;
  }

  @media (max-width: 860px) {
    justify-self: stretch;
    max-width: none;
  }
`;

export const EvoCardTitle = styled.h2`
  margin: 0 0 8px;
  color: #0b63ff;
  font-family: 'Noto Serif SC', 'Songti SC', STSong, SimSun, serif;
  font-size: 30px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: 0.04em;
`;

export const EvoCardSubTitle = styled.div`
  margin-bottom: 26px;
  color: #0b63ff;
  font-family: 'Noto Serif SC', 'Songti SC', STSong, SimSun, serif;
  font-size: 15px;
  line-height: 1.6;
`;

export const EvoFormWrapper = styled.div`
  .ant-form-item {
    margin: 0;
  }
`;

export const EvoField = styled.div`
  margin-bottom: 20px;
`;

export const EvoLabel = styled.label`
  display: block;
  margin-bottom: 8px;
  color: #0b63ff;
  font-size: 13px;
  font-weight: 600;
`;

export const EvoControl = styled.div`
  position: relative;
  height: 42px;
  border: 1px solid rgba(34, 111, 255, 0.28);
  background: rgba(255, 255, 255, 0.62);

  > svg {
    position: absolute;
    left: 12px;
    top: 50%;
    width: 20px;
    height: 20px;
    transform: translateY(-50%);
    color: rgba(11, 99, 255, 0.72);

    path[stroke] {
      stroke: currentColor;
      stroke-opacity: 1;
    }

    path[fill]:not([fill='none']) {
      fill: currentColor;
      fill-opacity: 1;
    }
  }

  input {
    width: 100%;
    height: 100%;
    padding: 0 72px 0 40px;
    border: 0;
    outline: none;
    color: #0f172a;
    background: transparent;
  }

  input::placeholder {
    color: rgba(25, 57, 95, 0.36);
  }
`;

export const EvoPasswordToggle = styled.button`
  position: absolute;
  top: 50%;
  right: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  color: rgba(11, 99, 255, 0.68);
  background: transparent;
  transform: translateY(-50%);
  cursor: pointer;

  &:hover {
    color: #0b63ff;
  }

  &:focus-visible {
    outline: 2px solid rgba(11, 99, 255, 0.48);
    outline-offset: 2px;
  }

  svg {
    width: 18px;
    height: 18px;
  }
`;

export const EvoForget = styled.div`
  display: block;
  margin: -6px 0 24px;
  text-align: right;

  a {
    color: #0b63ff;
    font-size: 13px;
    font-weight: 500;
    text-decoration: none;
  }
`;

export const EvoLoginButton = styled.button`
  width: 100%;
  height: 44px;
  border: 0;
  border-radius: 3px;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.08em;
  background: #0b63ff;
  box-shadow: 0 14px 28px rgba(11, 99, 255, 0.18);
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.72;
  }
`;

export const EvoRegisterText = styled.div`
  margin-top: 22px;
  color: rgba(11, 44, 86, 0.62);
  font-size: 14px;
  text-align: center;

  a {
    margin-left: 8px;
    color: #0b63ff;
    font-weight: 600;
    text-decoration: none;
  }
`;

export const EvoRegisterButton = styled(Link)`
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 42px;
  margin-top: 12px;
  border: 1px solid rgba(11, 99, 255, 0.52);
  border-radius: 3px;
  color: #0b63ff;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-decoration: none;
  background: rgba(255, 255, 255, 0.58);
  transition: background 160ms ease, border-color 160ms ease, color 160ms ease;

  &:hover {
    border-color: #0b63ff;
    color: #084dbd;
    background: rgba(11, 99, 255, 0.08);
  }
`;
