"""Edge narration, source-preserving phrase subtitles, and true word events."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import wave
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "vendor"))
VOICES = {
    "zh-CN-YunxiNeural": {"label": "云希", "locale": "zh-CN"},
    "zh-CN-XiaoxiaoNeural": {"label": "晓晓", "locale": "zh-CN"},
    "zh-HK-HiuMaanNeural": {"label": "HiuMaan", "locale": "zh-HK"},
}
DEFAULT_VOICE = "zh-CN-YunxiNeural"


def load_edge():
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError(
            f"Missing Python dependency: {exc.name}. Install {SKILL / 'requirements.txt'}."
        ) from exc
    return edge_tts


def ffmpeg_path():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("Install ffmpeg or the imageio-ffmpeg package.") from exc


def canonical(value):
    """Compare spoken letters/digits while retaining raw punctuation in output."""
    chars, positions = [], []
    for index, original in enumerate(value):
        for char in unicodedata.normalize("NFKC", original).casefold():
            if unicodedata.category(char)[0] in ("L", "N", "M"):
                chars.append(char)
                positions.append(index)
    return "".join(chars), positions


def map_fragments(text, words):
    """Require a complete match; never approximate timestamps by character count."""
    expected, positions = canonical(text)
    spoken = [(word, canonical(word["text"])[0]) for word in words]
    spoken = [(word, normalized) for word, normalized in spoken if normalized]
    observed = "".join(normalized for _, normalized in spoken)
    if not expected or not spoken:
        raise ValueError("No matchable text or no TTS word events.")
    if expected != observed:
        pos = next((i for i, pair in enumerate(zip(expected, observed))
                    if pair[0] != pair[1]), min(len(expected), len(observed)))
        raise ValueError(
            f"Script and TTS word events differ after punctuation/whitespace "
            f"normalization at character {pos} (script={len(expected)}, events={len(observed)})."
        )

    cursor, pieces = 0, []
    for i, (word, normalized) in enumerate(spoken):
        first = 0 if i == 0 else positions[cursor]
        cursor += len(normalized)
        last = positions[cursor] if cursor < len(positions) else len(text)
        if last <= first:
            raise ValueError("A normalized source character spans more than one word event.")
        pieces.append({
            "text": text[first:last],
            "source_start": first,
            "source_end": last,
            "start": word["start"],
            "end": word["end"],
        })
    return pieces


def display_text(value):
    return re.sub(r"\s+", " ", value).strip()


def make_cues(text, words, audio_duration, max_chars):
    pieces = map_fragments(text, words)
    groups, group = [], []
    for piece in pieces:
        candidate = display_text("".join(p["text"] for p in group) + piece["text"])
        if group and len(candidate) > max_chars:
            groups.append(group)
            group = []
        group.append(piece)
        # Punctuation is attached to the preceding word and retained from the script.
        if re.search(r"[，。！？；、,.!?;：:\r\n]", piece["text"]):
            groups.append(group)
            group = []
    if group:
        groups.append(group)

    cues = []
    for i, group in enumerate(groups):
        start = group[0]["start"]
        end = min(group[-1]["end"] + 0.08, audio_duration)
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0]["start"])
        if not (0 <= start < end <= audio_duration):
            raise ValueError(f"Invalid or overlapping caption timing at group {i + 1}.")
        cues.append({
            "text": display_text("".join(piece["text"] for piece in group)),
            "start": round(start, 6),
            "end": round(end, 6),
            "source_start": group[0]["source_start"],
            "source_end": group[-1]["source_end"],
        })
    if any(not cue["text"] for cue in cues):
        raise ValueError("A subtitle group has no displayable text.")
    return cues


def srt_timestamp(seconds):
    millis = round(seconds * 1000)
    h, millis = divmod(millis, 3_600_000)
    m, millis = divmod(millis, 60_000)
    s, millis = divmod(millis, 1000)
    return f"{h:02}:{m:02}:{s:02},{millis:03}"


def render_srt(cues):
    return "\n\n".join(
        f"{i}\n{srt_timestamp(c['start'])} --> {srt_timestamp(c['end'])}\n{c['text']}"
        for i, c in enumerate(cues, 1)
    ) + "\n"


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_wav(mp3, wav):
    command = [
        ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(mp3), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(wav),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"ffmpeg failed to decode TTS audio: {result.stderr[-500:]}")
    with wave.open(str(wav), "rb") as src:
        if src.getframerate() != 48000 or src.getnchannels() != 2:
            raise RuntimeError("Unexpected WAV format.")
        return src.getnframes() / src.getframerate()


def exclusive_copy(source, destination):
    """Do not replace existing media, including if another process creates a file."""
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst)


async def voices(locale):
    edge = load_edge()
    available = {item["ShortName"]: item for item in await edge.list_voices()}
    selected = {
        name: profile for name, profile in VOICES.items()
        if not locale or profile["locale"].casefold().startswith(locale.casefold())
    }
    result = [
        {
            "name": name,
            "label": profile["label"],
            "locale": profile["locale"],
            "gender": available[name]["Gender"],
            "styles": available[name].get("VoiceTag", {}).get("VoicePersonalities", []),
        }
        for name, profile in selected.items() if name in available
    ]
    print(json.dumps({"provider": f"edge-tts {edge.__version__}",
                      "count": len(result), "voices": result,
                      "unavailable": [name for name in selected if name not in available]},
                     ensure_ascii=False, indent=2))
    return 0


async def synthesize(args):
    edge = load_edge()
    source = args.text_file.resolve()
    raw = source.read_bytes()
    text = raw.decode("utf-8-sig")
    if not text.strip():
        raise ValueError("Input script is empty.")
    if not 4 <= args.max_chars <= 100:
        raise ValueError("--max-chars must be between 4 and 100.")

    target = args.out_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    names = ("voice.mp3", "voice.wav", "subtitles.srt", "timing.json")
    existing = [str(target / name) for name in names if (target / name).exists()]
    if existing:
        raise FileExistsError("Output already exists; choose a new directory: " + ", ".join(existing))

    with tempfile.TemporaryDirectory(prefix=".edge-tts-", dir=target) as temp:
        temp = Path(temp)
        mp3, wav = temp / "voice.mp3", temp / "voice.wav"
        events = []
        communicator = edge.Communicate(
            text, args.voice, rate=args.rate, pitch=args.pitch, boundary="WordBoundary"
        )
        with mp3.open("wb") as media:
            async for event in communicator.stream():
                if event["type"] == "audio":
                    media.write(event["data"])
                elif event["type"] == "WordBoundary":
                    offset, duration = int(event["offset"]), int(event["duration"])
                    events.append({
                        "text": event["text"],
                        "start": round(offset / 10_000_000, 6),
                        "end": round((offset + duration) / 10_000_000, 6),
                        "offset_ticks": offset,
                        "duration_ticks": duration,
                    })
        if mp3.stat().st_size < 500 or not events:
            raise RuntimeError("The service returned no usable audio or word events.")
        duration = decode_wav(mp3, wav)
        alignment_error, cues = None, []
        try:
            for i, event in enumerate(events):
                if not (0 <= event["start"] < event["end"] <= duration + 0.1):
                    raise ValueError(f"Word event {i + 1} falls outside decoded audio.")
                if i and event["start"] < events[i - 1]["start"]:
                    raise ValueError(f"Word event {i + 1} is out of order.")
            cues = make_cues(text, events, duration, args.max_chars)
        except ValueError as exc:
            alignment_error = str(exc)

        info = {
            "version": 1,
            "source": {"type": args.source, "file": str(source), "sha256": hashlib.sha256(raw).hexdigest()},
            "provider": {"id": "edge-tts", "version": edge.__version__, "voice": args.voice,
                         "rate": args.rate, "pitch": args.pitch, "boundary": "WordBoundary"},
            "audio": {"wav": "voice.wav", "mp3": "voice.mp3", "duration_seconds": round(duration, 6),
                      "sample_rate": 48000, "channels": 2, "wav_sha256": file_hash(wav),
                      "mp3_sha256": file_hash(mp3)},
            "alignment": {
                "status": "matched" if alignment_error is None else "unmatched",
                "method": "service-word-boundaries-matched-to-script" if alignment_error is None else "unresolved",
                "detail": alignment_error,
                "note": "TTS-reported word timing; verify against the spoken audio. Changes to audio require retiming.",
            },
            "words": events,
            "cues": cues,
        }
        (temp / "timing.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        if alignment_error is None:
            (temp / "subtitles.srt").write_text(render_srt(cues), encoding="utf-8")

        for name in ("voice.mp3", "voice.wav", "timing.json"):
            exclusive_copy(temp / name, target / name)
        if alignment_error is None:
            exclusive_copy(temp / "subtitles.srt", target / "subtitles.srt")
        print(json.dumps({
            "alignment": info["alignment"], "output": str(target),
            "duration_seconds": info["audio"]["duration_seconds"],
            "words": len(events), "subtitles": len(cues),
        }, ensure_ascii=False, indent=2))
        return 0 if alignment_error is None else 2


def main():
    parser = argparse.ArgumentParser(description="Create Edge TTS audio and timestamped subtitles.")
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("voices", help="Query the three selected voices from the service")
    listing.add_argument("--locale", default="", help="Locale prefix, for example zh or zh-CN")
    synth = actions.add_parser("synthesize", help="Create voice and accurate subtitle timing")
    synth.add_argument("--text-file", type=Path, required=True)
    synth.add_argument("--out-dir", type=Path, required=True)
    synth.add_argument("--voice", choices=tuple(VOICES), default=DEFAULT_VOICE)
    synth.add_argument("--rate", default="+25%")
    synth.add_argument("--pitch", default="-2Hz")
    synth.add_argument("--max-chars", type=int, default=14)
    synth.add_argument("--source", choices=("user-provided", "assistant-authored", "imported", "unknown"),
                       default="unknown")
    args = parser.parse_args()
    try:
        return asyncio.run(voices(args.locale) if args.action == "voices" else synthesize(args))
    except (ValueError, FileExistsError, FileNotFoundError, UnicodeDecodeError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Edge TTS request failed ({type(exc).__name__}); check connectivity and service availability.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
