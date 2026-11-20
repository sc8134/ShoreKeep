import zipfile

import pytest

from backend.engine.playlist import (
    build_playlist_zip,
    entry_title,
    entry_url,
    is_playlist,
    playlist_entries,
)
from backend.engine.types import SizeLimitExceeded


def make_entry(index: int, **overrides) -> dict:
    entry = {
        "_type": "url",
        "title": f"Video {index}",
        "url": f"https://example.com/watch?v={index}",
    }
    entry.update(overrides)
    return entry


def writer(entry: dict, item_dir, index: int, count: int, size: int = 8):
    item_dir.mkdir(parents=True, exist_ok=True)
    path = item_dir / f"{entry['title']}.mp4"
    path.write_bytes(b"x" * size)
    return [(path.name, path)]


def test_playlist_detection():
    assert is_playlist({"_type": "playlist"})
    assert is_playlist({"_type": "multi_video"})
    assert is_playlist({"entries": [{"url": "https://example.com/a"}]})
    assert not is_playlist({"_type": "video", "formats": []})


def test_entries_cap_and_skip_nested_playlists():
    info = {
        "_type": "playlist",
        "entries": [
            make_entry(1),
            None,
            {"_type": "playlist", "title": "Channel", "url": "https://example.com/c"},
            {"title": "no url"},
            make_entry(2),
            make_entry(3),
        ],
    }
    entries = playlist_entries(info, 2)
    assert [entry["title"] for entry in entries] == ["Video 1", "Video 2"]
    assert entry_url(entries[0]) == "https://example.com/watch?v=1"


def test_entry_helpers():
    assert entry_title({"title": ""}, 2) == "Item 3"
    assert entry_title({}, 0) == "Item 1"
    assert entry_url({"webpage_url": "https://example.com/w"}) == "https://example.com/w"
    assert entry_url({"url": "ftp://example.com/a"}) is None
    # Archive.org entries point webpage_url at the playlist page; url is the file.
    assert entry_url(
        {
            "url": "https://example.com/item.mp4",
            "webpage_url": "https://example.com/details/item",
        }
    ) == "https://example.com/item.mp4"
    assert entry_url({"url": "ftp://example.com/a", "webpage_url": "https://example.com/w"}) == (
        "https://example.com/w"
    )


def test_zip_prefixes_item_numbers(tmp_path):
    result = build_playlist_zip(
        title="Mix",
        entries=[make_entry(1), make_entry(2)],
        work=tmp_path,
        max_total_bytes=0,
        download=writer,
    )
    assert result.saved == 2
    with zipfile.ZipFile(result.path) as archive:
        assert archive.namelist() == ["01 - Video 1.mp4", "02 - Video 2.mp4"]


def test_duplicate_names_are_suffixed(tmp_path):
    def download(entry, item_dir, index, count):
        item_dir.mkdir(parents=True, exist_ok=True)
        path = item_dir / "media.mp4"
        path.write_bytes(b"x")
        return [("Same Title.mp4", path), ("Same Title.mp4", path)]

    result = build_playlist_zip(
        title="Mix",
        entries=[make_entry(1)],
        work=tmp_path,
        max_total_bytes=0,
        download=download,
    )
    with zipfile.ZipFile(result.path) as archive:
        assert archive.namelist() == ["01 - Same Title.mp4", "01 - Same Title (2).mp4"]


def test_failed_item_is_skipped_and_listed(tmp_path):
    def download(entry, item_dir, index, count):
        if index == 1:
            raise ValueError("This video is private")
        return writer(entry, item_dir, index, count)

    result = build_playlist_zip(
        title="Mix",
        entries=[make_entry(1), make_entry(2)],
        work=tmp_path,
        max_total_bytes=0,
        download=download,
    )
    assert result.saved == 1
    assert result.skipped[0][0] == 2
    with zipfile.ZipFile(result.path) as archive:
        manifest = archive.read("_playlist.txt").decode("utf-8")
    assert "Saved 1 of 2 videos." in manifest
    assert "This video is private" in manifest


def test_all_items_failed_raises_first_error(tmp_path):
    def download(entry, item_dir, index, count):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        build_playlist_zip(
            title="Mix",
            entries=[make_entry(1)],
            work=tmp_path,
            max_total_bytes=0,
            download=download,
        )


def test_total_size_limit_is_enforced(tmp_path):
    with pytest.raises(SizeLimitExceeded):
        build_playlist_zip(
            title="Mix",
            entries=[make_entry(1), make_entry(2)],
            work=tmp_path,
            max_total_bytes=10,
            download=writer,
        )
