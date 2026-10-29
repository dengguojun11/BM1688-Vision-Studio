import { useEffect, useState } from 'react';

import { DataStream, FlowParticleLayer, Packet, PacketLayer, SampleFlash } from './EvolutionDataFlow.styles';
import { evolutionPreviewSamples, type EvolutionPreviewSample } from './previewSamples.generated';

const SAMPLE_ROTATION_MS = 11000;

function RealDatasetSample({ sample }: { sample: EvolutionPreviewSample }) {
  return (
    <>
      <img src={sample.url} alt={`数据集图片：${sample.fileName}`} />
      <span className="sample-scan" aria-hidden="true" />
    </>
  );
}

export default function EvolutionDataFlow({ active, preview }: { active: boolean; preview: boolean }) {
  const [sampleIndex, setSampleIndex] = useState(0);
  const sample = evolutionPreviewSamples[sampleIndex % evolutionPreviewSamples.length];

  useEffect(() => {
    if (!preview || evolutionPreviewSamples.length < 2) return;
    const timer = window.setInterval(() => {
      setSampleIndex((current) => (current + 1) % evolutionPreviewSamples.length);
    }, SAMPLE_ROTATION_MS);
    return () => window.clearInterval(timer);
  }, [preview]);

  useEffect(() => {
    if (!preview || evolutionPreviewSamples.length < 2) return;
    const nextImage = new Image();
    nextImage.src = evolutionPreviewSamples[(sampleIndex + 1) % evolutionPreviewSamples.length].url;
  }, [preview, sampleIndex]);

  return (
    <>
      <DataStream $active={active} aria-hidden="true"><i /><i /><i /></DataStream>
      <FlowParticleLayer $active={active} aria-hidden="true">
        {Array.from({ length: 7 }, (_, index) => <i key={index} style={{ '--particle-index': index } as React.CSSProperties} />)}
      </FlowParticleLayer>
      <PacketLayer $active={active} aria-hidden="true">
        <Packet $index={0}>难例样本 <b>+24</b></Packet>
        <Packet $index={1}>冻结快照 <b>v12</b></Packet>
        <Packet $index={2}>mAP <b>+6.3 pts</b></Packet>
      </PacketLayer>
      {preview && <SampleFlash key={sample.id} data-sample-id={sample.id}>
        <div className="sample-image"><RealDatasetSample sample={sample} /><span className="sample-live">LIVE</span></div>
        <div className="sample-copy"><span>{sample.dataset} · {sample.split}</span><strong>样本接入进化链路</strong><small title={sample.fileName}>{sample.fileName}</small><em>{String(sampleIndex + 1).padStart(2, '0')} / {evolutionPreviewSamples.length} · 数据治理 →</em></div>
      </SampleFlash>}
    </>
  );
}
