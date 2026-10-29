import styled from 'styled-components';

import { isInAppIframe } from '@/utils/inAppIframe';

const FrameWrap = styled.div`
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #f5f7fb;
  overflow: hidden;
`;

const TrainingFrame = styled.iframe<{ $inIframe?: boolean }>`
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: ${(p) =>
    p.$inIframe
      ? '100vh'
      : 'calc(100vh - var(--header-height) - 2.75rem)'};
  border: 0;
  background: #f5f7fb;
`;

export default function ModelTrainingPanel() {
  const inIframe = isInAppIframe();
  const panel = new URLSearchParams(window.location.search).get('panel');
  const trainingFrameSrc =
    panel === 'compile'
      ? '/yolo-bm1688-exports?embedded=1'
      : '/yolo-training?embedded=1';

  return (
    <FrameWrap>
      <TrainingFrame
        $inIframe={inIframe}
        title="模型训练"
        src={trainingFrameSrc}
        allow="clipboard-read; clipboard-write"
      />
    </FrameWrap>
  );
}
