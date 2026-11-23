import pytest

pytest.importorskip("mydl_desktop")

from mydl_desktop import detect


def test_extract_url_finds_first_link():
    assert detect.extract_url("see https://example.com/a and http://two.test") == "https://example.com/a"


def test_extract_url_strips_trailing_punctuation():
    assert detect.extract_url("(https://example.com/watch?v=1).") == "https://example.com/watch?v=1"
    assert detect.extract_url("https://example.com/x,'") == "https://example.com/x"


def test_extract_url_returns_empty_when_missing():
    assert detect.extract_url("") == ""
    assert detect.extract_url("no links here") == ""


def test_read_link_file_txt(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("a link: https://example.com/v/1 done", encoding="utf-8")
    assert detect.read_link_file(path) == "https://example.com/v/1"


def test_read_link_file_url(tmp_path):
    path = tmp_path / "site.url"
    path.write_text("[InternetShortcut]\nURL=https://example.com/short\n", encoding="utf-8")
    assert detect.read_link_file(path) == "https://example.com/short"


def test_read_link_file_webloc(tmp_path):
    path = tmp_path / "site.webloc"
    path.write_text(
        '<?xml version="1.0"?><plist><dict><key>URL</key>'
        "<string>https://example.com/apple</string></dict></plist>",
        encoding="utf-8",
    )
    assert detect.read_link_file(path) == "https://example.com/apple"


def test_read_link_file_skips_oversized(tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("https://example.com/x " + "a" * (detect.MAX_SOURCE_BYTES + 1), encoding="utf-8")
    assert detect.read_link_file(path) == ""


def test_read_link_file_missing_returns_empty(tmp_path):
    assert detect.read_link_file(tmp_path / "nope.txt") == ""


def test_folder_watcher_reports_only_new_or_changed(tmp_path):
    folder = tmp_path / "watch"
    folder.mkdir()
    state = tmp_path / "watch.json"
    events = []
    watcher = detect.FolderWatcher(folder, lambda url, source: events.append((url, source)), state)

    link = folder / "one.url"
    link.write_text("URL=https://example.com/one", encoding="utf-8")
    assert watcher.scan() == [("https://example.com/one", "watch folder")]
    assert watcher.scan() == []

    link.write_text("URL=https://example.com/one-longer", encoding="utf-8")
    assert watcher.scan() == [("https://example.com/one-longer", "watch folder")]

    (folder / "ignored.md").write_text("https://example.com/no", encoding="utf-8")
    assert watcher.scan() == []


def test_folder_watcher_persists_seen_state(tmp_path):
    folder = tmp_path / "watch"
    folder.mkdir()
    state = tmp_path / "watch.json"
    (folder / "one.txt").write_text("https://example.com/one", encoding="utf-8")

    first = detect.FolderWatcher(folder, lambda url, source: None, state)
    assert first.scan()

    second = detect.FolderWatcher(folder, lambda url, source: None, state)
    assert second.scan() == []


def test_detection_store_set_current_clear():
    store = detect.DetectionStore()
    assert store.current() is None

    first = store.set("https://example.com/a", "clipboard")
    assert first["id"] == "detected-1"
    assert first["source"] == "clipboard"
    assert store.current()["url"] == "https://example.com/a"

    copy = store.current()
    copy["url"] = "changed"
    assert store.current()["url"] == "https://example.com/a"

    assert store.clear("detected-999") is False
    assert store.clear(first["id"]) is True
    assert store.current() is None


def test_detection_store_truncates_source():
    store = detect.DetectionStore()
    entry = store.set("https://example.com/a", "x" * 100)
    assert len(entry["source"]) == 40


def test_config_env_overrides(tmp_path, monkeypatch):
    from mydl_desktop import config as config_module

    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("SHOREKEEP_CLIPBOARD", "yes")
    monkeypatch.setenv("SHOREKEEP_WATCH_FOLDER", "~/links")

    config = config_module.AgentConfig.load()
    assert config.clipboard_monitor is True
    assert config.watch_folder == "~/links"


def test_config_defaults_detection_off(tmp_path, monkeypatch):
    from mydl_desktop import config as config_module

    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("SHOREKEEP_CLIPBOARD", raising=False)
    monkeypatch.delenv("SHOREKEEP_WATCH_FOLDER", raising=False)

    config = config_module.AgentConfig.load()
    assert config.clipboard_monitor is False
    assert config.watch_folder == ""


def test_detection_runner_starts_and_stops_matching_watchers(tmp_path, monkeypatch):
    from mydl_desktop.config import AgentConfig

    started = []

    class FakeWatcher:
        def __init__(self, *args, **kwargs):
            self.stopped = False
            started.append(self)

        def start(self):
            self.running = True

        def stop(self):
            self.stopped = True

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(detect, "ClipboardWatcher", FakeWatcher)
    monkeypatch.setattr(detect, "FolderWatcher", FakeWatcher)
    monkeypatch.setattr(detect, "clipboard_available", lambda: True)

    folder = tmp_path / "watch"
    runner = detect.DetectionRunner(AgentConfig(state_dir=str(tmp_path / "state"), watch_folder=str(folder)))
    runner.configure(lambda url, source: None)
    config = AgentConfig(state_dir=str(tmp_path / "state"), watch_folder=str(folder))

    runner.apply(config)
    assert len(started) == 1
    assert folder.is_dir()

    runner.apply(config)
    assert len(started) == 1
    assert started[0].stopped is False

    config.watch_folder = ""
    runner.apply(config)
    assert started[0].stopped is True
    runner.stop()


def test_detection_runner_starts_the_clipboard_watcher(tmp_path, monkeypatch):
    from mydl_desktop.config import AgentConfig

    started = []

    class FakeWatcher:
        def __init__(self, *args, **kwargs):
            started.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(detect, "ClipboardWatcher", FakeWatcher)
    monkeypatch.setattr(detect, "clipboard_available", lambda: True)

    config = AgentConfig(state_dir=str(tmp_path / "state"), clipboard_monitor=True)
    runner = detect.DetectionRunner(config)
    runner.configure(lambda url, source: None)
    runner.apply(config)
    assert len(started) == 1
    runner.stop()
