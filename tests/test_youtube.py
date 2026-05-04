import pytest
from bot.youtube import get_quality_options, _best_audio_format, _get_size


class TestBestAudioFormat:
    def test_picks_highest_abr(self):
        formats = [
            {"format_id": "1", "vcodec": "none", "acodec": "opus", "abr": 128},
            {"format_id": "2", "vcodec": "none", "acodec": "opus", "abr": 256},
        ]
        result = _best_audio_format(formats)
        assert result["format_id"] == "2"

    def test_no_audio(self):
        formats = [
            {"format_id": "1", "vcodec": "avc1", "acodec": "none", "height": 1080},
        ]
        assert _best_audio_format(formats) is None


class TestGetSize:
    def test_filesize(self):
        assert _get_size({"filesize": 1000}) == 1000

    def test_filesize_approx(self):
        assert _get_size({"filesize_approx": 2000}) == 2000

    def test_none_fmt(self):
        assert _get_size(None) is None
