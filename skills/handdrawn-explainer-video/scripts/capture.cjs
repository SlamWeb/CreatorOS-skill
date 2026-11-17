const { createHash } = require('node:crypto');
const { once } = require('node:events');
const { existsSync } = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const [entry, widthArg, heightArg, fpsArg, framesArg, mode, startArg, endArg] = process.argv.slice(2);
const [width, height, fps, frames, start, end] = [widthArg, heightArg, fpsArg, framesArg, startArg, endArg].map(Number);

function playwright() {
  if (process.env.PLAYWRIGHT_MODULE) return require(process.env.PLAYWRIGHT_MODULE);
  try {
    const lookup = /^https?:/.test(entry) ? [process.cwd(), __dirname] : [path.dirname(entry), __dirname];
    return require(require.resolve('playwright', { paths: lookup }));
  } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND' || !process.env.USERPROFILE) throw error;
    const existing = path.join(process.env.USERPROFILE, '.codex', 'skills', 'motion-showreel', 'scripts', 'node_modules', 'playwright');
    if (!existsSync(existing)) throw error;
    return require(existing);
  }
}

async function main() {
  const { chromium } = playwright();
  const installedChrome = process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe');
  const executablePath = process.env.CHROME_PATH || (installedChrome && existsSync(installedChrome) ? installedChrome : undefined);
  const browser = await chromium.launch({ executablePath, headless: true, args: ['--allow-file-access-from-files'] });
  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    const errors = [];
    const projectOrigin = /^https?:/.test(entry) ? new URL(entry).origin : null;
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (projectOrigin && response.url().startsWith(projectOrigin + '/') && response.status() >= 400 && !response.url().endsWith('/favicon.ico')) {
        errors.push(`${response.status()} ${response.url()}`);
      }
    });
    await page.goto(projectOrigin ? entry : pathToFileURL(entry).href);
    await page.waitForFunction(() => window.READY && typeof window.render === 'function');
    await page.evaluate(async ({ width, height, frames, fps }) => {
      await document.fonts.ready;
      const stage = document.getElementById('stage');
      if (stage?.width !== width || stage?.height !== height || Math.abs(window.DUR - frames / fps) > 1e-7) {
        throw new Error(`Scene and timeline disagree: ${stage?.width}×${stage?.height}, DUR ${window.DUR}`);
      }
      window.captureFrame = async (index, reduced, lossless) => {
        await window.render(index / fps);
        if (stage.width !== width || stage.height !== height) throw new Error(`Stage size changed at frame ${index}`);
        let canvas = stage;
        if (reduced) {
          const thumbnail = document.getElementById('capture-thumbnail');
          canvas = thumbnail || document.createElement('canvas');
          if (!thumbnail) {
            canvas.id = 'capture-thumbnail';
            canvas.hidden = true;
            const scale = Math.min(1, 960 / width, 540 / height);
            canvas.width = Math.max(2, Math.round(width * scale / 2) * 2);
            canvas.height = Math.max(2, Math.round(height * scale / 2) * 2);
            document.body.appendChild(canvas);
          }
          const context = canvas.getContext('2d', { alpha: false });
          context.imageSmoothingQuality = 'high';
          context.drawImage(stage, 0, 0, canvas.width, canvas.height);
        }
        return canvas.toDataURL(lossless ? 'image/png' : 'image/jpeg', 0.97).split(',')[1];
      };
    }, { width, height, frames, fps });

    async function capture(index, reduced, lossless) {
      const result = await page.evaluate(([index, reduced, lossless]) => window.captureFrame(index, reduced, lossless), [index, reduced, lossless]);
      if (errors.length) throw new Error(errors.join('\n'));
      return Buffer.from(result, 'base64');
    }

    if (mode === 'probe') {
      const indices = [...new Set(JSON.parse(startArg))].sort((a, b) => a - b);
      const hashes = new Map();
      const hash = buffer => createHash('sha256').update(buffer).digest('hex');
      for (const index of indices) hashes.set(index, hash(await capture(index, false, true)));
      const randomOrder = indices.map(index => [hash(String(index)), index])
        .sort(([first], [second]) => first.localeCompare(second)).map(([, index]) => index);
      for (const index of randomOrder) {
        if (hash(await capture(index, false, true)) !== hashes.get(index)) {
          throw new Error(`Frame ${index} depends on render order or wall-clock state`);
        }
      }
      console.error(`Deterministic frame hashes verified at ${indices.length} positions`);
      return;
    }

    const before = performance.now();
    let bytes = 0;
    const indices = mode === 'stills' ? JSON.parse(startArg) : Array.from({ length: end - start }, (_, index) => start + index);
    for (const index of indices) {
      const buffer = await capture(index, mode === 'preview', mode === 'png' || mode === 'stills');
      bytes += buffer.length;
      if (!process.stdout.write(buffer)) await once(process.stdout, 'drain');
    }
    const seconds = (performance.now() - before) / 1000;
    console.error(`Frames ${indices[0]}–${indices.at(-1)}: ${indices.length} in ${seconds.toFixed(2)} s (${(indices.length / seconds).toFixed(1)} fps), ${(bytes / 1e6).toFixed(1)} MB piped`);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
