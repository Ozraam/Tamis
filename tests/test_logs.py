import logging

from tamis import logs


def test_warnings_and_errors_go_to_the_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "path", None)
    monkeypatch.setattr(logs.faulthandler, "enable", lambda *args: None)  # pytest's own stays
    monkeypatch.setattr(logs, "_fault_file", None)
    handlers = list(logs.log.handlers)
    try:
        path = logs.setup(str(tmp_path / "Logs"))
        assert path == logs.path
        logging.getLogger("tamis.loader").warning("Could not decode %s", "IMG_1.CR2")
        logging.getLogger("tamis.loader").info("not kept")
        for handler in logs.log.handlers:
            handler.flush()
        with open(path, encoding="utf-8") as file:
            text = file.read()
        assert "WARNING tamis.loader: Could not decode IMG_1.CR2" in text
        assert "not kept" not in text
    finally:
        for handler in logs.log.handlers[len(handlers):]:
            handler.close()
        if logs._fault_file:
            logs._fault_file.close()
        logs.log.handlers[:] = handlers


def test_an_unwritable_log_folder_is_no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "path", None)
    monkeypatch.setattr(logs.faulthandler, "enable", lambda *args: None)
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert logs.setup(str(blocker / "Logs")) is None
    assert logs.path is None
