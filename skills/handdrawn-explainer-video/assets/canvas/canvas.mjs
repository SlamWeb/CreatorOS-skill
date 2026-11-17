import { C, makeBrush, paperTexture, clamp, ease, mix, noise, out } from './ink.mjs';

function drawCaption({ ctx, W, H }, caption, fontFamily) {
  const lines = caption.text.split('\n');
  const maxSize = Math.min(29, W / 22);
  ctx.save();
  ctx.font = '600 ' + maxSize + 'px ' + fontFamily;
  const widest = Math.max(...lines.map(line => ctx.measureText(line).width));
  const size = Math.min(maxSize, maxSize * (W * .88) / (widest + 32));
  const leading = Math.ceil(size * 1.22);
  const boxWidth = Math.min(W * .88, widest * size / maxSize + 28);
  const boxHeight = leading * lines.length + 12;
  const y = H - H * .055 - boxHeight;
  ctx.fillStyle = 'rgba(43, 32, 29, .85)';
  ctx.beginPath();
  ctx.roundRect((W - boxWidth) / 2, y, boxWidth, boxHeight, 9);
  ctx.fill();
  ctx.fillStyle = '#fff5e5';
  ctx.font = '600 ' + size + 'px ' + fontFamily;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  lines.forEach((line, index) => ctx.fillText(line, W / 2, y + 6 + leading * (index + .5)));
  ctx.restore();
}

export async function startFilm(draw, options = {}) {
  const response = await fetch('./timeline.json');
  if (!response.ok && response.status !== 0) throw new Error('Could not load timeline.json');
  const timeline = await response.json();
  const stage = document.querySelector('#stage');
  const ctx = stage.getContext('2d', { alpha: false });
  const H = 540;
  const W = H * timeline.size[0] / timeline.size[1];
  const b = makeBrush(ctx);
  const paper = paperTexture(Math.ceil(W), H);
  const textFont = options.subtitleFont || '"Noto Sans SC", "Microsoft YaHei", sans-serif';
  const ink = { ...C, ...options.colors };
  const api = { ctx, b, W, H, C: ink, clamp, ease, mix, noise, out };
  b.setVariant(0);
  if (options.loadFonts) await options.loadFonts();
  await document.fonts.ready;

  window.DUR = timeline.duration;
  window.render = time => {
    if (stage.width !== window.innerWidth || stage.height !== window.innerHeight) {
      stage.width = window.innerWidth;
      stage.height = window.innerHeight;
    }
    const frame = Math.min(timeline.frames - 1, Math.max(0, Math.floor(time * timeline.fps + 1e-7)));
    const t = frame / timeline.fps;
    const index = timeline.shots.findIndex(shot => shot.startFrame <= frame && frame < shot.endFrame);
    const shot = timeline.shots[index];
    const chapter = timeline.chapters.find(item => item.startFrame <= frame && frame < item.endFrame);
    const elapsed = t - shot.start;
    const duration = shot.end - shot.start;

    ctx.save();
    ctx.scale(stage.width / W, stage.height / H);
    ctx.fillStyle = ink.paper;
    ctx.fillRect(0, 0, W, H);
    ctx.save();
    draw(api, {
      t, frame, duration, elapsed, p: clamp(elapsed / duration), shot, chapter,
      previous: timeline.shots[index - 1], next: timeline.shots[index + 1], timeline,
    });
    ctx.restore();
    ctx.globalCompositeOperation = 'multiply';
    ctx.globalAlpha = options.paperOpacity ?? .12;
    ctx.drawImage(paper, 0, 0, W, H);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    const caption = timeline.captions.find(cue => cue.start <= t && t < cue.end);
    if (caption && options.captions !== false) {
      if (options.drawCaption) options.drawCaption(api, caption);
      else drawCaption(api, caption, textFont);
    }
    ctx.restore();
  };
  window.render(0);
  window.READY = true;
}
