"""Turn episode shot times and authoritative subtitles into the Canvas timeline."""
import argparse
from fractions import Fraction
import hashlib
import json
from math import ceil
from pathlib import Path
import re
import wave


STAMP = r"(\d{2,}:\d{2}:\d{2}[,.]\d{3})"
RANGE = re.compile(rf"^{STAMP}\s*-->\s*{STAMP}$")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def seconds(stamp):
    hours, minutes, tail = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(tail.replace(",", "."))


def read_captions(path, duration):
    content = path.read_text(encoding="utf-8-sig").strip()
    captions = []
    previous_end = 0.0
    for block in re.split(r"\n[ \t]*\n", content):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError(f"Invalid SRT block: {block!r}")
        match = RANGE.fullmatch(lines[1].strip())
        if match is None:
            raise ValueError(f"Invalid SRT timing: {lines[1]!r}")
        start, end = map(seconds, match.groups())
        text = "\n".join(lines[2:]).strip()
        if not text or not previous_end <= start < end <= duration + 0.001:
            raise ValueError(f"Overlapping or out-of-range subtitle: {lines[0]}")
        captions.append({"text": text, "start": start, "end": end})
        previous_end = end
    return captions


def sections(rows, fps, frame_count):
    starts = [round(row["at"] * fps) for row in rows]
    if not starts or starts[0] != 0 or any(row["at"] < 0 for row in rows):
        raise ValueError("Provide a section starting at zero.")
    ids = [row["id"] for row in rows]
    if any(not isinstance(name, str) or not name for name in ids) or len(set(ids)) != len(ids):
        raise ValueError("Section IDs must be nonempty and unique.")
    output = []
    for row, start, end in zip(rows, starts, starts[1:] + [frame_count]):
        if end <= start:
            raise ValueError(f"Section {row['id']} has no frames or is out of order.")
        output.append({
            **{key: value for key, value in row.items() if key != "at"},
            "startFrame": start, "endFrame": end, "start": start / fps, "end": end / fps,
        })
    return output


def file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def build(project):
    story = read_json(project / "story.json")
    audio = project / "audio"
    narration = project / "narration.txt"
    wav = audio / "voice.wav"
    timing = read_json(audio / "timing.json")
    metadata = timing.get("audio", {})
    if timing.get("alignment", {}).get("status") == "unmatched":
        raise ValueError("The voice timing reports unresolved subtitle alignment.")
    for path, expected in ((narration, timing.get("source", {}).get("sha256")),
                           (wav, metadata.get("wav_sha256"))):
        if not path.is_file():
            raise FileNotFoundError(path)
        if expected and file_hash(path) != expected:
            raise ValueError(f"{path.name} does not match timing.json.")
    with wave.open(str(wav), "rb") as source:
        duration = Fraction(source.getnframes(), source.getframerate())
    if duration <= 0:
        raise ValueError("Narration WAV is empty.")
    if "duration_seconds" in metadata and abs(float(duration) - metadata["duration_seconds"]) > 0.002:
        raise ValueError("WAV duration does not match timing.json.")
    size = story.get("size", [1920, 1080])
    fps = story.get("fps", 48)
    if len(size) != 2 or any(type(value) is not int or value < 2 or value % 2 for value in size):
        raise ValueError("Canvas size must contain two positive even integers.")
    if type(fps) is not int or fps < 1:
        raise ValueError("Canvas FPS must be a positive integer.")
    frames = ceil(duration * fps)
    timeline = {
        "size": size, "fps": fps, "frames": frames, "duration": frames / fps,
        "audioDuration": float(duration),
        "chapters": sections(story.get("chapters") or [{"id": "episode", "at": 0}], fps, frames),
        "shots": sections(story["shots"], fps, frames),
        "captions": read_captions(audio / "subtitles.srt", float(duration)),
    }
    target = project / "timeline.json"
    target.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{target} ({frames} frames, {len(timeline['captions'])} subtitles)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, nargs="?", default=Path.cwd())
    build(parser.parse_args().project.resolve())
