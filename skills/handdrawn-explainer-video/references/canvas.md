# Canvas 工具

用这套工具时，把 [画布文件](../assets/canvas/) 复制到每集工程根目录，把 [脚本](../scripts/) 复制到该工程的 `tools/`。将定稿放为 `narration.txt`，并把同版 `voice.wav`、`subtitles.srt`、`timing.json` 放在 `audio/`。工程使用 Python 3.11、imageio-ffmpeg、Node.js、Playwright 和 Chrome/Chromium。关键帧联络表还用 Pillow。Playwright 可由本工程安装或通过 `PLAYWRIGHT_MODULE` 指向已经安装的包，浏览器也可通过 `CHROME_PATH` 指定。

在 `story.json` 记录分镜。 `shots` 由若干 `{"id":"…","at":0}` 构成，`at` 为秒，首镜从 0 开始，其余按时间递增，末镜到音频末尾。`chapters` 可用同样的结构来标出渲染分段，也可省略。按需要给分镜增加文字、动作或衔接字段。可用 `size`、`fps` 设置成片规格，默认 1920 × 1080 和 48 fps。

在 `film.mjs` 调用 `startFilm(draw, options)`，按 `shot.id` 或自己需要的字段绘制。以下片段只展示接口，分镜与图形由本集决定。

```js
import { startFilm } from './canvas.mjs';

await startFilm(({ b, W, H, C, ease }, { p }) => {
  b.dot(W * (.3 + .4 * ease(p)), H * .4, 24, 27, C.red);
});
```

绘制函数接收当前镜头、前后镜头、全片秒数、镜内秒数和进度，输出当前这一帧。字幕和纸纹由画布叠加。使用固定种子和时间驱动动作，让任意一帧可独立重渲染。 `ink.mjs` 提供笔刷、形状和纸纹，`liquids.mjs` 可用于曲流、飞溅。按内容设计主体形状和动作，检查本集汉字在实际编码画面里的字形。

在工程目录执行。

```powershell
python tools/prepare.py
python tools/pipeline.py preview --start 8 --seconds 4
python tools/pipeline.py render --draft --output reviews/round-1.mp4
python tools/pipeline.py render --draft --output reviews/round-2.mp4
python tools/pipeline.py render
python tools/pipeline.py qc
```

两次样片命令之间按意见修改场景。每次改了分镜或输入时先重新运行 `prepare.py`。样片缩小画幅像素，保留实际时轴和口播。正式渲染默认生成 `video.mp4` 和同名质量记录。使用 `contact --keyframes <帧号 ...>` 可查看关键帧和做构图检查。
