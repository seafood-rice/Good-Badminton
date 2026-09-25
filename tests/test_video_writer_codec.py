"""The analysed-video writer must not gamble on a broken H.264 encoder.

setup_video_writer tried the fourccs ("avc1", "h264", "mp4v") in that order.
On this machine OpenCV cannot load openh264 ("Incorrect library version
loaded"), and constructing a 3840x2160 avc1 writer then NEVER RETURNS -- it
printed its failure at t=0.003 s and was still inside the constructor minutes
later, with the analysis stuck at "initializing" and a 0-byte temp video.
mp4v opens the same writer in 0.011 s.

Browser compatibility is delivered by the ffmpeg transcode after the run, not
by OpenCV, so leading with the codec that always works costs nothing.
"""
import pytest

from badminton_analysis.media import video_audio


def test_mp4v_is_tried_first():
    assert video_audio.VIDEO_FOURCC_ORDER[0] == "mp4v"


def test_the_h264_fourccs_remain_as_fallbacks():
    """Kept so a platform where mp4v fails can still produce a video."""
    assert "avc1" in video_audio.VIDEO_FOURCC_ORDER
    assert "h264" in video_audio.VIDEO_FOURCC_ORDER


def test_the_order_is_overridable(monkeypatch):
    monkeypatch.setenv("BADMINTON_VIDEO_FOURCC", "avc1,mp4v")
    assert video_audio.fourcc_order() == ["avc1", "mp4v"]


def test_an_empty_override_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("BADMINTON_VIDEO_FOURCC", "   ")
    assert video_audio.fourcc_order() == list(video_audio.VIDEO_FOURCC_ORDER)


def test_override_entries_are_trimmed(monkeypatch):
    monkeypatch.setenv("BADMINTON_VIDEO_FOURCC", " mp4v , avc1 ")
    assert video_audio.fourcc_order() == ["mp4v", "avc1"]


def test_the_first_working_codec_is_used(monkeypatch, tmp_path):
    tried = []

    class _Writer:
        def __init__(self, opened):
            self._opened = opened

        def isOpened(self):
            return self._opened

    def fake_writer(path, fourcc, fps, size):
        tried.append(fourcc)
        return _Writer(True)

    monkeypatch.setattr(video_audio.cv2, "VideoWriter", fake_writer)
    monkeypatch.setattr(video_audio.cv2, "VideoWriter_fourcc",
                        lambda *chars: "".join(chars))

    out = str(tmp_path / "sub" / "temp.mp4")
    writer = video_audio.setup_video_writer(1920, 1080, 30.0, out)

    assert writer.isOpened()
    assert tried == ["mp4v"], "a working first codec must end the search"


def test_a_failing_codec_falls_through(monkeypatch, tmp_path):
    tried = []

    class _Writer:
        def __init__(self, opened):
            self._opened = opened

        def isOpened(self):
            return self._opened

    def fake_writer(path, fourcc, fps, size):
        tried.append(fourcc)
        return _Writer(fourcc == "avc1")

    monkeypatch.setattr(video_audio.cv2, "VideoWriter", fake_writer)
    monkeypatch.setattr(video_audio.cv2, "VideoWriter_fourcc",
                        lambda *chars: "".join(chars))

    video_audio.setup_video_writer(1920, 1080, 30.0,
                                   str(tmp_path / "sub" / "temp.mp4"))
    assert tried[0] == "mp4v"
    assert "avc1" in tried


def test_all_codecs_failing_raises(monkeypatch, tmp_path):
    class _Writer:
        def isOpened(self):
            return False

    monkeypatch.setattr(video_audio.cv2, "VideoWriter",
                        lambda *a, **k: _Writer())
    monkeypatch.setattr(video_audio.cv2, "VideoWriter_fourcc",
                        lambda *chars: "".join(chars))

    with pytest.raises(RuntimeError, match="Unable to create video writer"):
        video_audio.setup_video_writer(1920, 1080, 30.0,
                                       str(tmp_path / "sub" / "temp.mp4"))
