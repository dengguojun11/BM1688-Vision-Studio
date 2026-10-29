import { useEffect, useRef } from 'react';

import { EvoVortexCanvas } from './style';

type Particle = {
  seed: number;
  arm: number;
  base: number;
  lifeSpeed: number;
  size: number;
  alpha: number;
  phase: number;
  scatter: number;
  depth: number;
  dir: number;
  spread: number;
  ex: number;
  ey: number;
  drift: number;
};

type ParticlePosition = {
  x: number;
  y: number;
  angle: number;
  life: number;
  fade: number;
};

const TAU = Math.PI * 2;
const ARM = 11;

function smooth(e0: number, e1: number, value: number) {
  const x = Math.min(1, Math.max(0, (value - e0) / (e1 - e0)));

  return x * x * (3 - 2 * x);
}

function createParticles() {
  return Array.from({ length: 360 }, (_unused, index): Particle => {
    const seed = Math.random();
    const base = Math.pow(Math.random(), 1.42) * 0.52;

    return {
      seed,
      arm: Math.floor((index % ARM) + Math.random() * 3) % ARM,
      base,
      lifeSpeed: 0.0000028 + Math.random() * 0.0000038,
      size: 0.38 + Math.random() * 1.55,
      alpha: 0.05 + Math.random() * 0.42,
      phase: Math.random() * TAU,
      scatter: Math.random(),
      depth: Math.random(),
      dir: Math.random() > 0.42 ? 1 : -1,
      spread: Math.random(),
      ex: 1.08 + Math.random() * 0.7,
      ey: 0.54 + Math.random() * 0.38,
      drift: Math.random(),
    };
  });
}

export function OrbitField() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const particlesRef = useRef<Particle[]>([]);
  const frameRef = useRef<number>();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const context = canvas.getContext('2d');
    if (!context) return undefined;

    const reset = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      particlesRef.current = createParticles();
    };

    const clear = (width: number, height: number) => {
      context.clearRect(0, 0, width, height);
      context.save();
      context.globalAlpha = 0.08;
      context.fillStyle = '#1f4f96';
      for (let y = 2; y < height; y += 9) {
        for (let x = (y / 9) % 2 ? 4 : 0; x < width; x += 9) {
          if ((x * 13 + y * 7) % 19 < 3) context.fillRect(x, y, 0.7, 0.7);
        }
      }
      context.restore();
    };

    const position = (particle: Particle, time: number, width: number, height: number): ParticlePosition => {
      const cx = width * 0.49;
      const cy = height * 0.49;
      const max = Math.min(width, height) * 0.74;
      const life = (particle.seed + time * particle.lifeSpeed) % 1;
      const easing = 1 - Math.pow(1 - life, 2.2);
      const fade = smooth(0, 0.16, life) * (1 - smooth(0.72, 1, life));
      const baseAngle = particle.arm * (TAU / ARM) + particle.phase * 0.38 + particle.base * 5.2;
      const drift = time * (0.00000032 + particle.drift * 0.00000042) * particle.dir;
      const warp =
        Math.sin(particle.phase + time * 0.000012 + particle.seed * 11) * (0.18 + particle.scatter * 0.36) +
        Math.sin(particle.base + particle.seed * 23 + time * 0.000009) * (0.08 + particle.depth * 0.2);
      const angle = baseAngle + easing * (1.35 + particle.scatter * 2.4) * particle.dir + warp + drift;
      const noise =
        Math.sin(particle.phase * 1.7 + time * 0.000014) * (0.012 + particle.depth * 0.032) +
        Math.cos(particle.seed * 41 + time * 0.000011) * (0.008 + particle.scatter * 0.025);
      const radius = Math.max(0.015, particle.base * 0.42 + easing * (0.15 + particle.spread * 0.72) + noise);
      const rr = max * radius;
      let x = cx + Math.cos(angle) * rr * particle.ex;
      let y = cy + Math.sin(angle) * rr * particle.ey;
      x += Math.sin(time * 0.000008 + particle.phase * 2.1) * max * 0.018 * particle.depth;
      y += Math.cos(time * 0.000007 + particle.phase * 1.8) * max * 0.015 * particle.scatter;

      return { x, y, angle, life, fade };
    };

    const grid = (width: number, height: number) => {
      context.save();
      context.globalAlpha = 0.08;
      context.strokeStyle = '#0b63ff';
      context.lineWidth = 1;
      const gap = Math.max(150, Math.min(220, width / 6));
      for (let x = 0; x <= width; x += gap) {
        context.beginPath();
        context.moveTo(x, 0);
        context.lineTo(x, height);
        context.stroke();
      }
      for (let y = 0; y <= height; y += gap) {
        context.beginPath();
        context.moveTo(0, y);
        context.lineTo(width, y);
        context.stroke();
      }
      context.restore();
    };

    const waves = (time: number, width: number, height: number) => {
      const cx = width * 0.49;
      const cy = height * 0.49;
      const max = Math.min(width, height) * 0.66;
      context.save();
      context.lineWidth = 0.62;
      context.setLineDash([1.2, 11]);
      for (let i = 0; i < 4; i += 1) {
        const progress = (time * 0.000008 + i * 0.25) % 1;
        const radius = 52 + progress * max * 0.88;
        context.globalAlpha = Math.pow(1 - progress, 1.6) * 0.09;
        context.strokeStyle = '#0b63ff';
        context.beginPath();
        context.ellipse(
          cx,
          cy,
          radius * (1.42 + i * 0.04),
          radius * (0.48 + i * 0.035),
          -0.18,
          -Math.PI * 0.45,
          Math.PI * (0.88 + progress * 0.28),
        );
        context.stroke();
      }
      context.restore();
    };

    const draw = (time: number) => {
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      const particles = particlesRef.current;
      const points = particles.map((particle) => position(particle, time, width, height));

      clear(width, height);
      grid(width, height);
      waves(time, width, height);

      context.save();
      for (let arm = 0; arm < ARM; arm += 1) {
        const armParticles = particles
          .map((particle, index) => ({ particle, point: points[index] }))
          .filter((item) => item.particle.arm === arm && item.particle.seed > 0.12)
          .sort((a, b) => a.point.life - b.point.life);
        context.beginPath();
        armParticles.forEach((item, index) => {
          if (!index) {
            context.moveTo(item.point.x, item.point.y);
            return;
          }
          const previous = armParticles[index - 1].point;
          const offset = 10 + item.particle.scatter * 26;
          const mx = (previous.x + item.point.x) / 2 + Math.cos(item.point.angle + item.particle.phase) * offset;
          const my = (previous.y + item.point.y) / 2 + Math.sin(item.point.angle + item.particle.phase) * offset * 0.52;
          context.quadraticCurveTo(mx, my, item.point.x, item.point.y);
        });
        context.globalAlpha = 0.034;
        context.strokeStyle = '#0b63ff';
        context.lineWidth = 0.72;
        context.stroke();
      }
      context.restore();

      context.save();
      context.lineWidth = 0.56;
      for (let i = 0; i < points.length; i += 6) {
        for (let j = i + 4; j < Math.min(points.length, i + 58); j += 5 + ((i + j) % 4)) {
          const gate = (particles[i].seed * 19.13 + particles[j].seed * 7.71 + particles[i].scatter) % 1;
          if (gate < 0.52) continue;
          const a = points[i];
          const b = points[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distance = Math.sqrt(dx * dx + dy * dy);
          const limit = 126 + gate * 64;
          if (distance < limit) {
            context.globalAlpha = Math.max(0, 1 - distance / limit) * 0.18 * Math.min(a.fade, b.fade);
            context.strokeStyle = gate > 0.82 ? '#0b63ff' : '#516783';
            context.beginPath();
            context.moveTo(a.x, a.y);
            context.lineTo(b.x, b.y);
            context.stroke();
          }
        }
      }
      context.restore();

      context.save();
      particles.forEach((particle, index) => {
        if (particle.seed < 0.68) return;
        const point = points[index];
        const tail = 16 + particle.spread * 46;
        context.globalAlpha = particle.alpha * 0.09 * point.fade;
        context.strokeStyle = '#0b63ff';
        context.lineWidth = 0.5;
        context.beginPath();
        context.moveTo(point.x, point.y);
        context.lineTo(point.x - Math.cos(point.angle) * tail, point.y - Math.sin(point.angle) * tail * 0.55);
        context.stroke();
      });
      context.restore();

      context.save();
      points.forEach((point, index) => {
        const particle = particles[index];
        const pulse = 0.86 + Math.sin(time * 0.00009 + particle.phase) * 0.14;
        context.globalAlpha = particle.alpha * pulse * point.fade;
        context.fillStyle = particle.seed > 0.88 ? '#0b63ff' : particle.seed > 0.55 ? '#48627f' : '#1e293b';
        context.beginPath();
        context.arc(point.x, point.y, particle.size * (0.72 + particle.depth * 0.58), 0, TAU);
        context.fill();
      });
      context.restore();

      const cx = width * 0.49;
      const cy = height * 0.49;
      const gradient = context.createRadialGradient(cx, cy, 0, cx, cy, 96);
      gradient.addColorStop(0, 'rgba(255,255,255,.98)');
      gradient.addColorStop(0.16, 'rgba(11,99,255,.09)');
      gradient.addColorStop(0.46, 'rgba(11,99,255,.025)');
      gradient.addColorStop(1, 'rgba(11,99,255,0)');
      context.fillStyle = gradient;
      context.beginPath();
      context.arc(cx, cy, 96, 0, TAU);
      context.fill();

      frameRef.current = requestAnimationFrame(draw);
    };

    reset();
    window.addEventListener('resize', reset);
    frameRef.current = requestAnimationFrame(draw);

    return () => {
      window.removeEventListener('resize', reset);
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, []);

  return <EvoVortexCanvas ref={canvasRef} aria-hidden="true" />;
}
