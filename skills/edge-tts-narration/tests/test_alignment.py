import importlib.util
import asyncio
import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "edge_voice.py"
spec = importlib.util.spec_from_file_location("edge_voice", MODULE_PATH)
edge_voice = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge_voice)


def word(text, start, end):
    return {"text": text, "start": start, "end": end}


class SubtitleAlignmentTests(unittest.TestCase):
    def test_preserves_original_punctuation_and_real_word_boundaries(self):
        text = "如果每天喝可乐，身体会怎样？"
        words = [
            word("如果", 0.1, 0.4), word("每天", 0.42, 0.7),
            word("喝", 0.72, 0.8), word("可乐", 0.8, 1.1),
            word("身体", 1.3, 1.6), word("会", 1.61, 1.7),
            word("怎样", 1.71, 2.0),
        ]
        cues = edge_voice.make_cues(text, words, audio_duration=2.4, max_chars=14)
        self.assertEqual([c["text"] for c in cues], ["如果每天喝可乐，", "身体会怎样？"])
        self.assertEqual(cues[0]["start"], 0.1)
        self.assertEqual(cues[1]["start"], 1.3)
        self.assertLessEqual(cues[0]["end"], cues[1]["start"])
        self.assertEqual("".join(text[c["source_start"]:c["source_end"]] for c in cues), text)

    def test_refuses_to_make_a_caption_when_word_events_differ(self):
        with self.assertRaisesRegex(ValueError, "differ"):
            edge_voice.make_cues(
                "今天喝可乐。", [word("今天", 0.0, 0.4), word("喝水", 0.5, 1.0)],
                audio_duration=1.2, max_chars=14,
            )

    def test_repeated_words_are_matched_in_order(self):
        text = "云希，云希！"
        words = [word("云", 0.1, 0.3), word("希", 0.3, 0.5),
                 word("云", 0.8, 1.0), word("希", 1.0, 1.2)]
        cues = edge_voice.make_cues(text, words, audio_duration=1.5, max_chars=14)
        self.assertEqual([c["text"] for c in cues], ["云希，", "云希！"])
        self.assertEqual([c["start"] for c in cues], [0.1, 0.8])

    def test_normalizes_letters_and_numbers_but_keeps_source(self):
        text = "AI 提速 25%。"
        words = [word("ai", 0.1, 0.3), word("提速", 0.4, 0.7), word("25%", 0.8, 1.2)]
        cues = edge_voice.make_cues(text, words, audio_duration=1.5, max_chars=14)
        self.assertEqual(cues[0]["text"], text)
        self.assertIn("00:00:00,100 --> 00:00:01,280", edge_voice.render_srt(cues))


class VoiceSelectionTests(unittest.TestCase):
    def test_listing_only_selected_voices_and_reports_unavailable(self):
        catalog = [
            {"ShortName": "zh-CN-YunxiNeural", "Gender": "Male"},
            {"ShortName": "zh-CN-XiaoxiaoNeural", "Gender": "Female"},
            {"ShortName": "zh-CN-YunyangNeural", "Gender": "Male"},
        ]
        service = SimpleNamespace(__version__="test", list_voices=AsyncMock(return_value=catalog))
        output = io.StringIO()
        with patch.object(edge_voice, "load_edge", return_value=service), contextlib.redirect_stdout(output):
            self.assertEqual(asyncio.run(edge_voice.voices("zh")), 0)
        listing = json.loads(output.getvalue())
        self.assertEqual([item["name"] for item in listing["voices"]],
                         ["zh-CN-YunxiNeural", "zh-CN-XiaoxiaoNeural"])
        self.assertEqual(listing["unavailable"], ["zh-HK-HiuMaanNeural"])

    def test_synthesis_rejects_unselected_voice_before_accessing_files(self):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "synthesize", "--text-file", "does-not-exist.txt",
             "--out-dir", "unused", "--voice", "zh-CN-YunyangNeural"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
