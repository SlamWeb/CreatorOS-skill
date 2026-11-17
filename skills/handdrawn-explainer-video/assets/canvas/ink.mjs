const TAU = Math.PI * 2;
const PATHS = new Map();

export const C = {
  paper: '#f7ecd7', light: '#fff5db', ink: '#2b201d', mid: '#71382e',
  red: '#c43e2d', hot: '#dc5037', dark: '#8c2b26', yellow: '#f2b845', sand: '#dfc79f',
};

export function noise(seed) {
  let n = Math.imul(seed ^ (seed >>> 16), 0x45d9f3b);
  n = Math.imul(n ^ (n >>> 16), 0x45d9f3b);
  return ((n ^ (n >>> 16)) >>> 0) / 4294967296;
}

export const mix = (a, b, p) => a + (b - a) * p;
export const clamp = (x) => Math.min(1, Math.max(0, x));
export const ease = (x) => { const p = clamp(x); return p * p * (3 - 2 * p); };
export const out = (x) => 1 - (1 - clamp(x)) ** 3;

function coords(s) {
  const parts = s.match(/[MLCQZ]|[-+]?(?:\d*\.)?\d+/g);
  const first = [Number(parts[1]), Number(parts[2])];
  const points = [first];
  let previous = first;
  for (let i = 3; i < parts.length;) {
    const op = parts[i++];
    const a = previous;
    if (op === 'Z') break;
    if (op === 'L') {
      const end = [Number(parts[i++]), Number(parts[i++])];
      const segments = Math.max(1, Math.ceil(Math.hypot(end[0] - a[0], end[1] - a[1]) / 14));
      for (let step = 1; step <= segments; step++) {
        points.push([mix(a[0], end[0], step / segments), mix(a[1], end[1], step / segments)]);
      }
      previous = end;
      continue;
    }
    const b = [Number(parts[i++]), Number(parts[i++])];
    const c = [Number(parts[i++]), Number(parts[i++])];
    const d = op === 'C' ? [Number(parts[i++]), Number(parts[i++])] : c;
    const distance = Math.hypot(b[0] - a[0], b[1] - a[1]) + Math.hypot(c[0] - b[0], c[1] - b[1]) + Math.hypot(d[0] - c[0], d[1] - c[1]);
    const segments = Math.max(4, Math.min(32, Math.ceil(distance / 11)));
    for (let step = 1; step <= segments; step++) {
      const t = step / segments;
      const r = 1 - t;
      points.push([0, 1].map((k) => op === 'C'
        ? r ** 3 * a[k] + 3 * r * r * t * b[k] + 3 * r * t * t * c[k] + t ** 3 * d[k]
        : r * r * a[k] + 2 * r * t * b[k] + t * t * c[k]));
    }
    previous = d;
  }
  return points;
}

function lookup(s, close) {
  if (typeof s !== 'string') return s;
  if (PATHS.has(s)) return PATHS.get(s);
  const points = coords(s);
  if (close && Math.hypot(points.at(-1)[0] - points[0][0], points.at(-1)[1] - points[0][1]) < 0.1) points.pop();
  if (PATHS.size >= 3072) PATHS.delete(PATHS.keys().next().value);
  PATHS.set(s, points);
  return points;
}

function loop(ctx, points) {
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  for (const [x, y] of points.slice(1)) ctx.lineTo(x, y);
  ctx.closePath();
}

export function makeBrush(ctx) {
  let variant = 0;

  function contour(path, id, close, amount) {
    const samples = lookup(path, close);
    const phase = noise(id * 137 + variant * 4123) * TAU;
    const second = noise(id * 1871 + variant * 711) * TAU;
    const shiftX = (noise(id * 23 + variant * 391 + 77) - .5) * amount;
    const shiftY = (noise(id * 17 + variant * 561 + 21) - .5) * amount;
    return samples.map(([x, y], i) => {
      const t = i / (close ? samples.length : samples.length - 1);
      const normal = amount * (.61 * Math.sin(phase + t * TAU * 3) + .22 * Math.sin(second + t * TAU * 7));
      const from = samples[close ? (i - 1 + samples.length) % samples.length : Math.max(0, i - 1)];
      const to = samples[close ? (i + 1) % samples.length : Math.min(i + 1, samples.length - 1)];
      const n = Math.hypot(to[0] - from[0], to[1] - from[1]) || 1;
      return [x + shiftX + normal * (to[1] - from[1]) / n, y + shiftY - normal * (to[0] - from[0]) / n];
    });
  }

  function pressure(points, width, color, close, id) {
    const left = [];
    const right = [];
    const n = points.length;
    for (let i = 0; i < n; i++) {
      const [x, y] = points[i];
      const a = points[close ? (i + n - 1) % n : Math.max(0, i - 1)];
      const b = points[close ? (i + 1) % n : Math.min(i + 1, n - 1)];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const length = Math.hypot(dx, dy) || 1;
      const t = i / (close ? n : n - 1);
      const taper = close ? 1 : .14 + .86 * Math.sin(Math.PI * t) ** .46;
      const variation = .91 + .17 * Math.sin(TAU * 3 * t + id) + .07 * Math.cos(TAU * 6 * t + id * 2);
      const radius = width * .5 * variation * taper;
      const nx = radius * dy / length;
      const ny = -radius * dx / length;
      left.push([x + nx, y + ny]);
      right.push([x - nx, y - ny]);
    }
    ctx.fillStyle = color;
    if (close) {
      loop(ctx, left);
      ctx.moveTo(right[0][0], right[0][1]);
      for (const [x, y] of right.slice(1)) ctx.lineTo(x, y);
      ctx.closePath();
      ctx.fill('evenodd');
    } else {
      loop(ctx, left.concat(right.reverse()));
      ctx.fill();
    }
  }

  function shape(path, fill, width = 0, id = 1, amount = 1.1, ink = C.ink) {
    const points = contour(path, id, true, amount);
    if (fill) {
      ctx.save();
      if (width > 2) ctx.translate(amount * .5, -.5 * amount);
      loop(ctx, points);
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.restore();
    }
    if (width > 0) pressure(points, width, ink, true, id);
    return points;
  }

  function line(path, width, ink = C.ink, id = 1, amount = .7) {
    pressure(contour(path, id, false, amount), width, ink, false, id);
  }

  function clip(path, id, amount = 1) {
    loop(ctx, contour(path, id, true, amount));
    ctx.clip();
  }

  function dot(x, y, rx, ry, fill, angle = 0) {
    if (rx < .15 || ry < .15) return;
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.ellipse(x, y, rx, ry, angle, 0, TAU);
    ctx.fill();
  }

  function group(x, y, sx, sy, angle, draw) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.scale(sx, sy);
    draw();
    ctx.restore();
  }

  return { shape, line, clip, dot, group, setVariant(n) { variant = n % 4; } };
}

export function paperTexture(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const g = canvas.getContext('2d');
  const grain = g.createImageData(width, height);
  const cols = Math.ceil(width / 38) + 1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = y * width + x;
      const gx = x / 38;
      const gy = y / 29;
      const node = Math.floor(gx) + cols * Math.floor(gy);
      const a = ease(gx % 1);
      const b = ease(gy % 1);
      const cloud = mix(mix(noise(node + 15), noise(node + 16), a), mix(noise(node + cols + 15), noise(node + cols + 16), a), b);
      const luma = Math.round(203 + 58 * (noise(i + 991) - .5) + 44 * (cloud - .5));
      grain.data.set([luma, luma - 3, luma - 10, 255], i * 4);
    }
  }
  g.putImageData(grain, 0, 0);
  for (let i = 0; i < 6700; i++) {
    const x = noise(i * 15 + 126) * width;
    const y = noise(i * 37 + 115) * height;
    const len = .6 + 9 * noise(i * 21 + 31) ** 3;
    g.strokeStyle = i % 7 ? 'rgba(74,44,20,.51)' : 'rgba(255,254,233,.85)';
    g.lineWidth = .4 + .75 * noise(i * 17 + 13);
    g.beginPath();
    g.moveTo(x, y);
    g.quadraticCurveTo(x + len * .54, y + len * .12, x + len, y + len * (noise(i * 13) - .5));
    g.stroke();
  }
  return canvas;
}
