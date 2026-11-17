import { clamp, mix } from './ink.mjs';

export function arcPoint(start, control, end, p) {
  return [mix(mix(start[0], control[0], p), mix(control[0], end[0], p), p),
    mix(mix(start[1], control[1], p), mix(control[1], end[1], p), p)];
}

export function liquidStream(api, start, control, end, {front = 1, back = 0, width = 1, time = 0} = {}) {
  const { b } = api;
  if (front - back < .006 || width < .01) return;
  const left = [], right = [], gloss = [];
  for (let i = 0; i <= 20; i++) {
    const t = mix(back, front, i / 20);
    const [x, y] = arcPoint(start, control, end, t);
    const dx = (1 - t) * (control[0] - start[0]) + t * (end[0] - control[0]);
    const dy = (1 - t) * (control[1] - start[1]) + t * (end[1] - control[1]);
    const norm = Math.hypot(dx, dy) || 1;
    const bend = Math.sin(Math.PI * t) * Math.sin(t * 24 - time * 14) * 1.65;
    const radius = (9 - t * 4.9 + .8 * Math.sin(time * 18 - t * 21)) * width;
    const nx = dy / norm, ny = -dx / norm;
    left.push([x + nx * (radius + bend), y + ny * (radius + bend)]);
    right.push([x + nx * (-radius + bend), y + ny * (-radius + bend)]);
    gloss.push([x + nx * (-radius * .36 + bend), y + ny * (-radius * .36 + bend)]);
  }
  b.shape(left.concat(right.reverse()), '#984935', Math.min(2.6, 2.6 * width), 4106, .27, '#653127');
  b.line(gloss, 3 * width, '#efb56b', 4107, .23);
}

// All droplets start at the point of impact and follow individual arcs before flattening back into the surface.
export function splash(api, start, time, strength, seed = 1) {
  const { b, art, noise } = api;
  const [x, y] = start;
  for (let i = 0; i < 9; i++) {
    const p = clamp((time - i * .017) / (.31 + .13 * noise(seed + i * 97)));
    const scale = Math.sin(Math.PI * p) ** .6 * .41 * strength;
    if (scale <= .025) continue;
    const offset = (i - 4) * (9 + noise(seed + i * 3) * 12);
    const height = 18 + noise(seed + i * 5) * 48;
    art.drop(x + offset * p, y - 4 * p * (1 - p) * height, scale, offset * .017,
      i % 3 === 1 ? '#eab370' : '#a7523c', seed + i * 17);
  }
  const damp = Math.sin(Math.PI * clamp(time / .72));
  if (damp > .02) {
    const w = mix(11, 58, clamp(time / .74));
    b.line(`M ${x - w} ${y + 2} Q ${x - w * .35} ${y + 11 * damp} ${x + w} ${y + 1}`, 3.1 * damp * strength, '#e3a05f', seed + 190, .45);
  }
}
