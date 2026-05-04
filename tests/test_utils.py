import pytest
from bot.utils import (
    escape_html,
    extract_video_id,
    format_size,
    generate_unique_filename,
    is_allowed,
    resolve_actual_path,
)


class TestFormatSize:
    def test_none(self):
        assert format_size(None) == "Size Unknown"

    def test_zero(self):
        assert format_size(0) == "0 B"

    def test_bytes(self):
        assert format_size(500) == "500.0 B"

    def test_megabytes(self):
        result = format_size(1024 * 1024 * 1.5)
        assert "MB" in result

    def test_gigabytes(self):
        result = format_size(1024 ** 3 * 2.5)
        assert "GB" in result


class TestEscapeHtml:
    def test_ampersand(self):
        assert escape_html("a & b") == "a &amp; b"

    def test_angle_brackets(self):
        assert escape_html("<script>") == "&lt;script&gt;"

    def test_no_change(self):
        assert escape_html("hello") == "hello"


class TestExtractVideoId:
    def test_standard_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        assert extract_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_no_match(self):
        assert extract_video_id("https://example.com") is None


class TestGenerateUniqueFilename:
    def test_format(self):
        result = generate_unique_filename("abc123", "mp4")
        assert result.startswith("abc123_")
        assert result.endswith(".mp4")

    def test_unique(self):
        a = generate_unique_filename("x", "mp3")
        b = generate_unique_filename("x", "mp3")
        assert a != b
