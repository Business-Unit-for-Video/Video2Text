import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


fake_faster_whisper = types.ModuleType("faster_whisper")
fake_faster_whisper.WhisperModel = object
sys.modules.setdefault("faster_whisper", fake_faster_whisper)

SCRIPT = Path(__file__).parents[1] / "scripts" / "transcribe_single_video.py"
spec = importlib.util.spec_from_file_location("transcribe_single_video", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Segment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class FakeModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, _path, **_kwargs):
        self.calls += 1
        end = 1799 if self.calls < 3 else 1045
        info = types.SimpleNamespace(language="zh", language_probability=1)
        return iter([Segment(0, end, f"part {self.calls}")]), info


class TranscriptionValidationTests(unittest.TestCase):
    def test_rejects_original_partial_result(self):
        result = {
            "segments": 1365,
            "plain_text": "partial",
            "audio_duration": 4646.612,
            "transcript_end": 2401.660,
        }
        with self.assertRaisesRegex(RuntimeError, "transcription is incomplete"):
            module.validate_transcription(result)

    def test_accepts_complete_result(self):
        result = {
            "segments": 2000,
            "plain_text": "complete",
            "audio_duration": 4646.612,
            "transcript_end": 4645.0,
            "coverage_ratio": 4645.0 / 4646.612,
        }
        module.validate_transcription(result)

    def test_rejects_truncated_download(self):
        with self.assertRaisesRegex(RuntimeError, "downloaded audio is incomplete"):
            module.validate_download_duration({"duration": 4646.612}, 2401.660)

    @patch.object(module, "run")
    def test_long_audio_is_transcribed_in_separate_chunks(self, run_mock):
        run_mock.return_value = types.SimpleNamespace(stdout="")
        model = FakeModel()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(module, "TMP_DIR", Path(temp_dir)):
                result = module.transcribe_audio(model, Path("audio.mp3"), 4646.612)
        self.assertEqual(model.calls, 3)
        self.assertEqual(result["segments"], 3)
        self.assertGreater(result["transcript_end"], 4600)
        self.assertIn("[60:00.000 --> 77:25.000] part 3", result["timestamp_text"])


if __name__ == "__main__":
    unittest.main()
