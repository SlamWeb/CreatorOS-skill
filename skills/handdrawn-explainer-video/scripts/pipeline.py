import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from time import perf_counter

import imageio_ffmpeg


TOOLS = Path(__file__).resolve().parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
CACHE_VERSION = 3
COLOR = ['-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'iec61966-2-1', '-color_range', 'tv']
COLOR_FILTER = 'setparams=range=limited:color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709'
COLOR_BSF = 'h264_metadata=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=13:matrix_coefficients=1'


def ffmpeg(*arguments, capture=False, stdin=None):
    return subprocess.run(
        [FFMPEG, '-hide_banner', '-nostdin', *map(str, arguments)], stdin=stdin,
        text=True, encoding='utf-8', errors='replace', capture_output=capture, check=True,
    )


def frame_index(chapter, fps):
    for key in ('startFrame', 'start_frame', 'frame'):
        if key in chapter:
            return int(chapter[key])
    for key in ('start', 'startSec', 'startTime', 't0', 'from'):
        if key in chapter:
            return round(float(chapter[key]) * fps)
    raise ValueError(f'Chapter lacks a start frame or time: {chapter}')


def load_timeline(project):
    timeline = json.loads((project / 'timeline.json').read_text(encoding='utf-8-sig'))
    fps, size, frames, duration = (timeline[key] for key in ('fps', 'size', 'frames', 'duration'))
    if (type(fps) is not int or fps < 1 or len(size) != 2
            or any(type(value) is not int or value < 2 or value % 2 for value in size)
            or type(frames) is not int or frames < 1
            or not math.isclose(fps * duration, frames, rel_tol=0, abs_tol=1e-6)):
        raise ValueError('Expected integer FPS, even dimensions and exact whole-frame duration.')
    return timeline


def segments_for(timeline, chunk_seconds):
    fps, frames = timeline['fps'], timeline['frames']
    starts = {0: 'intro'}
    for index, chapter in enumerate(timeline.get('chapters', []), 1):
        start = frame_index(chapter, fps)
        if not 0 <= start < frames:
            raise ValueError(f'Chapter starts outside the timeline at frame {start}')
        starts[start] = str(chapter.get('id', index))
    starts[frames] = 'end'
    boundaries = sorted(starts)
    chunk_size = max(1, round(chunk_seconds * fps))
    return [
        (starts[chapter_start], begin, min(begin + chunk_size, chapter_end))
        for chapter_start, chapter_end in zip(boundaries, boundaries[1:])
        for begin in range(chapter_start, chapter_end, chunk_size)
    ]


def renderer(args, timeline, mode, start, end=0):
    return [
        'node', str(TOOLS / 'capture.cjs'), args.url or str(args.project / 'index.html'),
        *map(str, timeline['size']), str(timeline['fps']), str(timeline['frames']), mode,
        json.dumps(start) if isinstance(start, list) else str(start), str(end),
    ]


def sha256_file(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def source_fingerprint(args):
    digest = hashlib.sha256()
    for tool in (TOOLS / 'pipeline.py', TOOLS / 'capture.cjs'):
        digest.update(tool.read_bytes())
    digest.update(json.dumps([args.url, args.cache_key, args.capture, FFMPEG, os.environ.get('CHROME_PATH')]).encode())
    ignored_dirs = {'audio', 'tools', 'qa', 'reviews', '.git', 'node_modules', '__pycache__'}
    for path in sorted(args.project.rglob('*')):
        relative = path.relative_to(args.project)
        if (path.is_relative_to(args.cache) or set(relative.parts[:-1]) & ignored_dirs
                or path.name.endswith('.qc.json') or path.suffix in {'.wav', '.mp4', '.srt', '.md', '.zip'}):
            continue
        if path.is_file():
            digest.update(str(relative).encode())
            digest.update(sha256_file(path).encode())
    return digest.hexdigest()


def encode_video(args, timeline, destination, start, end, mode):
    fps = timeline['fps']
    draft = args.draft or mode == 'preview'
    input_codec = 'png' if mode == 'png' else 'mjpeg'
    source_range = 'pc'
    source_matrix = 'bt601' if input_codec == 'mjpeg' else 'bt709'
    if input_codec == 'mjpeg':
        # Browser JPEG uses the JFIF BT.601 matrix. Round-trip through RGB
        # before encoding limited-range BT.709 to preserve the canvas colors.
        convert = f'scale=in_range={source_range}:in_color_matrix={source_matrix},format=rgb24,'
    else:
        convert = ''
    convert += f'scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,{COLOR_FILTER}'
    with subprocess.Popen(renderer(args, timeline, mode, start, end), stdout=subprocess.PIPE) as producer:
        try:
            ffmpeg(
                '-y', '-loglevel', 'error', '-filter_threads', '2', '-f', 'image2pipe',
                '-framerate', fps, '-c:v', input_codec, '-threads', '2', '-i', 'pipe:0',
                '-vf', convert, '-an', '-threads', args.encoder_threads,
                '-c:v', 'libx264', '-preset', 'veryfast' if draft else 'fast',
                '-crf', '21' if draft else '16', '-profile:v', 'high',
                '-g', fps * 2, '-sc_threshold', 0, *COLOR, '-bsf:v', COLOR_BSF,
                '-video_track_timescale', math.lcm(48000, fps), destination, stdin=producer.stdout,
            )
        finally:
            producer.stdout.close()
        if producer.wait():
            raise RuntimeError(f'Canvas renderer failed in frames {start}–{end - 1}')


def audio_source(project):
    for path in [project / 'audio' / 'master.wav', project / 'audio' / 'voice.wav', project / 'voice.wav']:
        if path.is_file():
            return path
    raise FileNotFoundError('Expected audio/master.wav or audio/voice.wav')


def render(args, timeline, segments):
    audio = audio_source(args.project)
    width, height = timeline['size']
    cache = args.cache / args.capture / f'{width}x{height}'
    cache.mkdir(parents=True, exist_ok=True)
    revision = source_fingerprint(args)
    selected = set(args.rebuild or [])
    if selected - {chapter for chapter, _, _ in segments}:
        raise ValueError('Unknown chapter specified in --rebuild')

    probes = {0, timeline['frames'] // 2, timeline['frames'] - 1}
    for _, start, _ in segments:
        probes.update([max(0, start - 1), start])
    subprocess.run(renderer(args, timeline, 'probe', sorted(probes)), check=True)

    def render_segment(segment):
        chapter, begin, end = segment
        output = cache / f'{begin:06d}-{end:06d}.mp4'
        sidecar = output.with_suffix('.json')
        if output.is_file() and sidecar.is_file() and chapter not in selected:
            try:
                metadata = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                metadata = {}
            if (metadata.get('version') == CACHE_VERSION and (metadata.get('revision') == revision or selected)
                    and metadata.get('sha256') == sha256_file(output)):
                print(f'Using cache for frames {begin}–{end - 1}', flush=True)
                return output, end - begin
        with tempfile.TemporaryDirectory(prefix='segment-', dir=cache) as temporary:
            temp = Path(temporary) / output.name
            encode_video(args, timeline, temp, begin, end, args.capture)
            metadata_path = temp.with_suffix('.json')
            metadata_path.write_text(json.dumps({'version': CACHE_VERSION, 'revision': revision, 'sha256': sha256_file(temp)}), encoding='utf-8')
            os.replace(temp, output)
            os.replace(metadata_path, sidecar)
        return output, end - begin

    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        encoded = list(workers.map(render_segment, segments))

    with tempfile.TemporaryDirectory(prefix='assembly-', dir=args.cache) as scratch:
        scratch = Path(scratch)
        manifest = scratch / 'concat.txt'
        lines = ['ffconcat version 1.0']
        for path, count in encoded:
            concat_path = path.as_posix().replace("'", "'\\''")
            lines.extend([f"file '{concat_path}'", f"duration {count / timeline['fps']:.6f}"])
        manifest.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        target = scratch / 'video.mp4'
        fps = timeline['fps']
        samples = timeline['frames'] * 48000 // fps
        timescale = math.lcm(48000, fps)
        # Loudness normalization can shift audio PTS. Rebuild them from the output sample count.
        audio_filter = (
            f'loudnorm=I=-14:TP=-2:LRA=11,aresample=48000,apad=whole_len={samples},'
            f'atrim=end_sample={samples},asetpts=N/SR/TB'
        )
        ffmpeg(
            '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', manifest, '-i', audio,
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-video_track_timescale', timescale, '-movie_timescale', timescale,
            '-af', audio_filter,
            '-c:a', 'aac', '-b:a', '320k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart+write_colr', target,
        )
        report = quality_check(timeline, target)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='.video-', suffix='.mp4', dir=args.output.parent, delete=False) as delivery:
            staged = Path(delivery.name)
        try:
            shutil.copyfile(target, staged)
            os.replace(staged, args.output)
        finally:
            staged.unlink(missing_ok=True)
        write_report(args.output, report)


def movie_duration(video):
    def atoms(stream, end):
        while stream.tell() + 8 <= end:
            start = stream.tell()
            size, name = struct.unpack('>I4s', stream.read(8))
            if size == 1:
                size, = struct.unpack('>Q', stream.read(8))
            atom_end = start + size if size else end
            if not stream.tell() <= atom_end <= end:
                raise ValueError('Invalid MP4 atom size')
            yield name, atom_end
            stream.seek(atom_end)

    with video.open('rb') as stream:
        for name, end in atoms(stream, video.stat().st_size):
            if name == b'moov':
                for child, _ in atoms(stream, end):
                    if child == b'mvhd':
                        version = stream.read(1)[0]
                        stream.seek(3 + (16 if version == 1 else 8), os.SEEK_CUR)
                        return struct.unpack('>IQ' if version == 1 else '>II', stream.read(12 if version == 1 else 8))
    raise ValueError('MP4 movie header is missing')


def quality_check(timeline, video):
    fps, count = timeline['fps'], timeline['frames']
    width, height = timeline['size']
    video_log = ffmpeg('-i', video, '-map', '0:v:0', '-an', '-vf', 'blackframe=amount=98:threshold=24,showinfo=checksum=0', '-fps_mode', 'passthrough', '-f', 'null', '-', capture=True).stderr
    audio_log = ffmpeg('-i', video, '-map', '0:a:0', '-vn', '-af', 'loudnorm=print_format=json', '-f', 'null', '-', capture=True).stderr
    video_stream = next(line.strip() for line in video_log.splitlines() if 'Stream #0:0' in line and 'Video:' in line)
    audio_stream = next(line.strip() for line in audio_log.splitlines() if 'Stream #0:1' in line and 'Audio:' in line)
    indices = re.findall(r'Parsed_showinfo[^\n]+\bn:\s*(\d+)\s+pts:\s*(-?\d+)', video_log)
    num, den = map(int, re.search(r'config in time_base:\s*(\d+)/(\d+)', video_log).groups())
    timescale, duration = movie_duration(video)
    audio = json.loads(re.search(r'\{\s*"input_i".*?\}', audio_log, re.DOTALL).group())
    integrated, peak = float(audio['input_i']), float(audio['input_tp'])
    black = re.findall(r'Parsed_blackframe[^\n]+frame:(\d+)\s+pblack:(\d+)', video_log)
    mismatches = [expected for expected, (index, pts) in enumerate(indices) if int(index) != expected or int(pts) * num * fps != expected * den]
    report = {
        'file': video.name, 'duration_seconds': duration / timescale, 'expected_duration_seconds': count / fps,
        'fps': fps, 'video_stream': video_stream, 'audio_stream': audio_stream, 'decoded_frames': len(indices),
        'expected_frames': count, 'frame_timestamp_mismatches': mismatches,
        'black_frames': [int(index) for index, _ in black], 'integrated_lufs': integrated, 'true_peak_dbtp': peak,
    }
    checks = {
        'container duration equals the exact frame duration': duration * fps == count * timescale,
        'H.264 with requested dimensions': bool(re.search(rf'Video: h264\b.*\b{width}x{height}\b', video_stream)),
        'all frames decoded at the exact consecutive timeline timestamps': len(indices) == count and not mismatches,
        'limited-range BT.709 matrix/primaries and sRGB transfer': 'yuv420p(tv, bt709/bt709/iec61966-2-1' in video_stream,
        '48 kHz stereo AAC': bool(re.search(r'Audio: aac\b.*48000 Hz, stereo', audio_stream)),
        'audible narration': math.isfinite(integrated),
        'true peak at or below −1 dBTP': peak <= -1,
    }
    report['checks'] = checks
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        print(json.dumps(report, indent=2, ensure_ascii=False), file=sys.stderr)
        raise ValueError('Quality checks failed: ' + ', '.join(failures))
    return report


def write_report(video, report):
    report['file'] = video.name
    path = video.with_suffix('.qc.json')
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{path} — {report["decoded_frames"]} frames, {report["integrated_lufs"]:.2f} LUFS, {report["true_peak_dbtp"]:.2f} dBTP')


def preview(args, timeline):
    fps, count = timeline['fps'], timeline['frames']
    start = round(args.start * fps)
    end = min(count, start + round(args.seconds * fps))
    if not 0 <= start < end:
        raise ValueError('Preview range must overlap the timeline')
    args.cache.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix='preview-', dir=args.cache) as scratch:
        target = Path(scratch) / 'preview.mp4'
        mode = args.capture if args.command == 'benchmark' else 'preview'
        encode_video(args, timeline, target, start, end, mode)
        if args.command == 'benchmark':
            elapsed = perf_counter() - started
            print(f'{mode}, {end - start} frames in {elapsed:.2f} s incl. browser startup and video encoding ({(end - start) / elapsed:.1f} fps); {target.stat().st_size / 1e6:.2f} MB encoded.')
            return
        try:
            audio = audio_source(args.project)
        except FileNotFoundError:
            os.replace(target, args.cache / 'preview.mp4')
        else:
            ffmpeg('-y', '-loglevel', 'error', '-i', target, '-ss', start / fps, '-i', audio,
                   '-map', '0:v:0', '-map', '1:a:0', '-t', (end - start) / fps,
                   '-c:v', 'copy', '-c:a', 'aac', '-ar', 48000, '-ac', 2, args.cache / 'preview.mp4')
    print(args.cache / 'preview.mp4')


def contact_sheet(args, timeline):
    from PIL import Image, ImageDraw

    fps, total = timeline['fps'], timeline['frames']
    frames = args.keyframes
    if not frames:
        boundaries = sorted({0, total, *(frame_index(chapter, fps) for chapter in timeline.get('chapters', []))})
        frames = [start + min((end - start) // 2, fps * 2) for start, end in zip(boundaries, boundaries[1:])]
        frames = sorted({0, total - 1, *frames})
    if any(not 0 <= frame < total for frame in frames):
        raise ValueError('Keyframe frame indices must fall within the timeline')
    args.cache.mkdir(parents=True, exist_ok=True)
    source_width, source_height = timeline['size']
    scale = min(384 / source_width, 216 / source_height)
    width, height = round(source_width * scale), round(source_height * scale)
    header, columns = 24, 4
    rows = math.ceil(len(frames) / columns)
    sheet = Image.new('RGB', (width * columns, (height + header) * rows), '#171717')
    draw = ImageDraw.Draw(sheet)
    with tempfile.TemporaryDirectory(prefix='stills-', dir=args.cache) as scratch:
        scratch = Path(scratch)
        with subprocess.Popen(renderer(args, timeline, 'stills', frames), stdout=subprocess.PIPE) as producer:
            try:
                ffmpeg('-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'png', '-i', 'pipe:0',
                       '-vf', f'scale={width}:{height}:flags=lanczos', '-threads', 1, scratch / '%05d.png', stdin=producer.stdout)
            finally:
                producer.stdout.close()
            if producer.wait():
                raise RuntimeError('Keyframe capture failed')
        for slot, frame in enumerate(frames):
            x = slot % columns * width
            y = slot // columns * (height + header)
            with Image.open(scratch / f'{slot + 1:05d}.png') as image:
                sheet.paste(image.convert('RGB'), (x, y))
            draw.text((x + 8, y + height + 5), f'{frame / fps:.3f} s  /  frame {frame}', fill='#ededed')
    path = args.cache / 'contact.png'
    sheet.save(path)
    print(path)


def main():
    parser = argparse.ArgumentParser(description='Build and check a Canvas film against its timeline.')
    parser.add_argument('command', choices=['render', 'preview', 'contact', 'benchmark', 'qc'])
    parser.add_argument('--project', type=Path, default=TOOLS.parent)
    parser.add_argument('--url', help='Optional HTTP URL for the scene')
    parser.add_argument('--cache', type=Path, help='Override the default tools/.render cache directory')
    parser.add_argument('--output', type=Path, default=Path('video.mp4'), help='MP4 path relative to the project or absolute')
    parser.add_argument('--draft', action='store_true', help='Render or check a half-size viewing copy at the original FPS')
    parser.add_argument('--cache-key', default='', help='Change this when external visual assets change')
    parser.add_argument('--capture', choices=['jpeg', 'png'], default='jpeg')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--encoder-threads', type=int, default=3)
    parser.add_argument('--chunk-seconds', type=float, default=12)
    parser.add_argument('--rebuild', nargs='+', help='Rebuild these chapter IDs and explicitly retain cached chunks in other chapters')
    parser.add_argument('--start', type=float, default=0, help='Preview/benchmark start in timeline seconds')
    parser.add_argument('--seconds', type=float, default=4, help='Preview/benchmark length in seconds')
    parser.add_argument('--keyframes', type=int, nargs='+', help='Render these exact frame indices for contact sheet')
    args = parser.parse_args()
    if args.workers < 1 or args.encoder_threads < 1 or args.chunk_seconds <= 0:
        parser.error('Workers, encoder threads and chunk length must be positive')
    args.project = args.project.resolve()
    args.output = (args.project / args.output).resolve()
    args.cache = (args.cache or (args.project / 'tools' / '.render')).resolve()
    timeline = load_timeline(args.project)
    if args.draft and args.command in {'render', 'qc'}:
        timeline['size'] = [max(2, (value // 4) * 2) for value in timeline['size']]
    if args.command == 'render':
        render(args, timeline, segments_for(timeline, args.chunk_seconds))
    elif args.command == 'contact':
        contact_sheet(args, timeline)
    elif args.command == 'qc':
        write_report(args.output, quality_check(timeline, args.output))
    else:
        preview(args, timeline)


if __name__ == '__main__':
    main()
