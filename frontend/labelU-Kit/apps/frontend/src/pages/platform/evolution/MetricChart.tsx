import { useMemo, useState } from 'react';

import type { TrainingMetrics } from '@/api/services/evolution';
import { ChartWrap } from './EvolutionCockpit.styles';

type MetricKey = 'map50_95' | 'map50' | 'train_box_loss' | 'val_box_loss';
type Series = { key: MetricKey; label: string; color: string };

const WIDTH = 680;
const HEIGHT = 220;
const PADDING = { top: 18, right: 18, bottom: 30, left: 42 };

function values(history: TrainingMetrics[], key: MetricKey) {
  return history
    .map((row, index) => ({ x: row.epoch ?? index + 1, y: row[key] }))
    .filter((item): item is { x: number; y: number } => typeof item.y === 'number' && Number.isFinite(item.y));
}

function pathFor(points: Array<{ x: number; y: number }>, minX: number, maxX: number, minY: number, maxY: number) {
  const plotWidth = WIDTH - PADDING.left - PADDING.right;
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
  return points.map((point, index) => {
    const x = PADDING.left + (maxX === minX ? plotWidth / 2 : (point.x - minX) / (maxX - minX) * plotWidth);
    const y = PADDING.top + (maxY === minY ? plotHeight / 2 : (1 - (point.y - minY) / (maxY - minY)) * plotHeight);
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(' ');
}

export default function MetricChart({ history, available = true }: { history: TrainingMetrics[]; available?: boolean }) {
  const [mode, setMode] = useState<'quality' | 'loss'>('quality');
  const series: Series[] = mode === 'quality'
    ? [{ key: 'map50_95', label: 'mAP@50:95', color: '#39d9ff' }, { key: 'map50', label: 'mAP@50', color: '#9b8cff' }]
    : [{ key: 'train_box_loss', label: 'Train Box Loss', color: '#ffbf69' }, { key: 'val_box_loss', label: 'Val Box Loss', color: '#ff7188' }];
  const plotted = useMemo(() => series.map((item) => ({ ...item, points: values(history, item.key) })), [history, mode]);
  const allPoints = plotted.flatMap((item) => item.points);
  const epochs = history.map((row, index) => row.epoch ?? index + 1);
  const minX = Math.min(...epochs, 1);
  const maxX = Math.max(...epochs, 1);
  const rawMin = allPoints.length ? Math.min(...allPoints.map((point) => point.y)) : 0;
  const rawMax = allPoints.length ? Math.max(...allPoints.map((point) => point.y)) : 1;
  const rangePadding = Math.max((rawMax - rawMin) * 0.12, mode === 'quality' ? 0.02 : 0.005);
  const minY = Math.max(0, rawMin - rangePadding);
  const maxY = rawMax + rangePadding;
  const yTicks = Array.from({ length: 5 }, (_, index) => minY + (maxY - minY) * index / 4).reverse();

  return (
    <ChartWrap>
      <div className="chart-toolbar">
        <div className="chart-legend">
          {plotted.map((item) => <span className="legend-item" key={item.key}><i className="legend-dot" style={{ '--legend': item.color } as React.CSSProperties} />{item.label}</span>)}
        </div>
        <div className="chart-legend" role="group" aria-label="曲线指标">
          <button type="button" aria-pressed={mode === 'quality'} onClick={() => setMode('quality')} style={{ border: 0, color: mode === 'quality' ? '#39d9ff' : '#66809f', background: 'transparent', cursor: 'pointer' }}>精度</button>
          <button type="button" aria-pressed={mode === 'loss'} onClick={() => setMode('loss')} style={{ border: 0, color: mode === 'loss' ? '#39d9ff' : '#66809f', background: 'transparent', cursor: 'pointer' }}>损失</button>
        </div>
      </div>
      {!available ? (
        <div className="chart-empty">该作业尚无逐轮指标文件<br />训练 Worker 写入 results.csv 后，曲线会在此出现</div>
      ) : !allPoints.length ? (
        <div className="chart-empty">暂无可绘制的真实训练指标</div>
      ) : (
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={mode === 'quality' ? '模型逐轮精度曲线' : '模型逐轮损失曲线'} preserveAspectRatio="none">
          <defs>
            <linearGradient id="metricArea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#39d9ff" stopOpacity=".18" /><stop offset="1" stopColor="#39d9ff" stopOpacity="0" /></linearGradient>
            <filter id="lineGlow"><feGaussianBlur stdDeviation="2" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
          </defs>
          {yTicks.map((tick) => {
            const y = PADDING.top + (maxY - tick) / Math.max(maxY - minY, Number.EPSILON) * (HEIGHT - PADDING.top - PADDING.bottom);
            return <g key={tick}><line x1={PADDING.left} y1={y} x2={WIDTH - PADDING.right} y2={y} stroke="rgba(103,143,190,.12)" strokeDasharray="3 5" /><text x={PADDING.left - 8} y={y + 3} textAnchor="end">{tick.toFixed(mode === 'quality' ? 2 : 3)}</text></g>;
          })}
          {[minX, Math.round((minX + maxX) / 2), maxX].filter((value, index, array) => array.indexOf(value) === index).map((epoch) => {
            const x = PADDING.left + (maxX === minX ? (WIDTH - PADDING.left - PADDING.right) / 2 : (epoch - minX) / (maxX - minX) * (WIDTH - PADDING.left - PADDING.right));
            return <text key={epoch} x={x} y={HEIGHT - 8} textAnchor="middle">Epoch {epoch}</text>;
          })}
          {plotted.filter((item) => item.points.length).map((item, index) => {
            const path = pathFor(item.points, minX, maxX, minY, maxY);
            const last = item.points[item.points.length - 1];
            const lastX = PADDING.left + (maxX === minX ? (WIDTH - PADDING.left - PADDING.right) / 2 : (last.x - minX) / (maxX - minX) * (WIDTH - PADDING.left - PADDING.right));
            const lastY = PADDING.top + (1 - (last.y - minY) / Math.max(maxY - minY, Number.EPSILON)) * (HEIGHT - PADDING.top - PADDING.bottom);
            return <g key={item.key}>
              {index === 0 && item.points.length > 1 && <path d={`${path} L ${lastX} ${HEIGHT - PADDING.bottom} L ${PADDING.left} ${HEIGHT - PADDING.bottom} Z`} fill="url(#metricArea)" />}
              <path d={path} fill="none" stroke={item.color} strokeWidth="2" vectorEffect="non-scaling-stroke" filter={index === 0 ? 'url(#lineGlow)' : undefined} />
              <circle cx={lastX} cy={lastY} r="3.5" fill={item.color} stroke="#0b192b" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            </g>;
          })}
        </svg>
      )}
    </ChartWrap>
  );
}
