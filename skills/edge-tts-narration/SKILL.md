---
name: edge-tts-narration
description: 用 Microsoft Edge 在线语音和用户选定的三种音色生成旁白、SRT 字幕及词级时间轴，适用于配音和音画同步。
---

# Edge TTS 旁白与字幕

将提供或已获授权撰写的文本合成为配音，交付 WAV、原始 MP3、SRT 和包含真实语音事件的 JSON。先确认文本来源，忠实记录；可以按用户要求撰稿，不把助手撰写的文本标为用户原稿。

音色仅使用以下三种；用户未指定时用云希：

| 音色 | 语言 | 参数 |
|---|---|---|
| 云希（默认） | 普通话男声 | `zh-CN-YunxiNeural` |
| 晓晓 | 普通话女声 | `zh-CN-XiaoxiaoNeural` |
| HiuMaan | 粤语女声 | `zh-HK-HiuMaanNeural` |

## 执行

1. 遵循当前项目的路径和版本规则，使用独立输出目录，不覆盖已有素材。脚本默认 `--rate=+25% --pitch=-2Hz`，适合此前的短视频需求。可在上述三种音色内切换，其他题材及用户有要求时按内容和试听调整语速。
2. 安装依赖时按本技能 `requirements.txt`，优先已有环境。技能内固定了经验证的 `edge-tts` 7.3.1，不依赖系统中可能失效的旧版本；微软接口随时可能变更。需联网，稿件会发送给 Edge 语音服务。
3. 对原文 UTF-8 TXT 执行：

   ```powershell
   python <skill>/scripts/edge_voice.py synthesize --text-file <稿件.txt> --out-dir <新输出目录> --source user-provided
   ```

   可选 `--voice`、`--rate=+25%`、`--pitch=-2Hz`、`--max-chars 14`。助手按用户授权撰稿时使用 `--source assistant-authored`；其他情况选 `imported` 或 `unknown`。
4. 查看生成的 `voice.wav`、`voice.mp3`、`subtitles.srt`、`timing.json`。WAV 由服务返回的 MP3 解码，并非无损来源。JSON 记录源文件哈希、服务、音色、参数、解码时长及 TTS 返回的词边界；字幕按原文标点和长度组句。检查返回状态和输出，试听并核对读错、漏读、断句、时间及片头片尾。时间戳是服务返回的估计，并不保证逐字精确。
5. 若原文与词事件无法可靠匹配，工具保留音频、原始词事件和失败原因，不生成伪对齐 SRT；人工校对或换适当方式重新对齐。插入静音、拼接、剪切或变速后必须重新换算字幕与事件时间；不能沿用原时间轴声称同步。

查询这三种音色当前的在线可用状态：

```powershell
python <skill>/scripts/edge_voice.py voices --locale zh
```

`--locale` 可用 `zh-CN` 或 `zh-HK` 前缀过滤，省略时显示所选的三种音色。服务若暂停提供其中某种，结果会列为 `unavailable`。对口音或声音风格的判断以试听为准。

## 费用与使用权

当前是第三方 `edge-tts` 对 Edge 在线朗读的调用，不需 Azure API 密钥，不声称永久免费或可无限使用。[edge-tts](https://github.com/rany2/edge-tts)；公开发布、参赛或商用时明确说明 Edge 朗读音频再发布授权尚未确认，按用途核实。微软 [Azure 付费档产品条款](https://www.microsoft.com/licensing/terms/en-US/productoffering/MicrosoftAzure/OL) 对预置神经音色输出有单独使用权；Azure 与本技能的免费调用是不同服务，不暗称本技能已有 Azure 付费档授权。

需要完整视频时，配音与字幕作为独立素材交给用户选定的视频制作流程；不要擅自添加音乐或替换现有交付。
