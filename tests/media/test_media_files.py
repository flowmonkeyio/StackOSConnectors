from pathlib import Path

import pytest

from stackos_connectors.shared.media import write_media_file


def test_explicit_directory_and_ordered_files(tmp_path):
    files = []
    first = write_media_file(b"first", output_dir=tmp_path, prefix="image", ext="png", files=files)
    second = write_media_file(
        b"second", output_dir=tmp_path, prefix="video", ext="mp4", files=files
    )
    assert [file.path for file in files] == [first["path"], second["path"]]
    assert [file.mime_type for file in files] == ["image/png", "video/mp4"]
    assert [file.size_bytes for file in files] == [5, 6]
    assert Path(first["path"]).parent == tmp_path
    assert Path(second["path"]).read_bytes() == b"second"
    assert "url" not in first
    assert "artifact_id" not in first


@pytest.mark.parametrize(
    "prefix,ext",
    [("../escape", "png"), ("/escape", "png"), ("a\\b", "png"), ("ok", "../png"), ("ok", "/png")],
)
def test_writer_rejects_path_components(tmp_path, prefix, ext):
    files = []
    with pytest.raises(ValueError):
        write_media_file(b"x", output_dir=tmp_path, prefix=prefix, ext=ext, files=files)
    assert not files
    assert list(tmp_path.iterdir()) == []


def test_writer_requires_output_directory():
    with pytest.raises(ValueError, match="output_dir"):
        write_media_file(b"x", output_dir=None, prefix="image", ext="png", files=[])


def test_write_failure_keeps_only_completed_files(tmp_path, monkeypatch):
    import stackos_connectors.shared.media as media

    files = []
    completed = write_media_file(
        b"first", output_dir=tmp_path, prefix="image", ext="png", files=files
    )

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(media.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_media_file(b"second", output_dir=tmp_path, prefix="image", ext="png", files=files)
    assert [file.path for file in files] == [completed["path"]]
    assert len(list(tmp_path.iterdir())) == 1


def test_writer_replaces_symlink_without_writing_outside(tmp_path):
    import hashlib

    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    target = tmp_path / f"image-{hashlib.sha256(b'image').hexdigest()[:32]}.png"
    target.symlink_to(outside)
    files = []
    write_media_file(b"image", output_dir=tmp_path, prefix="image", ext="png", files=files)
    assert outside.read_bytes() == b"untouched"
    assert not target.is_symlink()
    assert target.read_bytes() == b"image"
