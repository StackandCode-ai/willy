"""PC file operations (pc_client/tools/file_ops.py) on a scratch folder."""

import zipfile

from pc_client.tools.file_ops import manage_file


def test_write_read_move_copy_rename_zip_unzip(tmp_path):
    src = tmp_path / "notes.txt"
    assert manage_file("write", str(src), content="hello")["success"]
    assert manage_file("write", str(src), content="again")["success"] is False  # no silent overwrite
    assert manage_file("read", str(src))["content"] == "hello"

    docs = tmp_path / "Docs"
    moved = manage_file("move", str(src), str(docs))
    assert moved["success"] and (docs / "notes.txt").exists() and not src.exists()
    assert manage_file("copy", str(docs / "notes.txt"), str(tmp_path))["success"]
    assert manage_file("rename", str(tmp_path / "notes.txt"), "renamed.txt")["success"]
    assert (tmp_path / "renamed.txt").read_text() == "hello"

    zipped = manage_file("zip", str(docs))
    assert zipped["success"] and zipfile.is_zipfile(zipped["path"])
    out = tmp_path / "out"
    assert manage_file("unzip", zipped["path"], str(out))["success"]
    assert (out / "Docs" / "notes.txt").read_text() == "hello"
    listing = manage_file("list", str(tmp_path))
    assert listing["success"] and {i["name"] for i in listing["items"]} >= {"Docs", "renamed.txt"}


def test_unzip_refuses_paths_outside_the_target(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escaped.txt", "x")
    res = manage_file("unzip", str(bad), str(tmp_path / "dest"))
    assert res["success"] is False and not (tmp_path / "escaped.txt").exists()


def test_missing_file_is_reported(tmp_path):
    res = manage_file("read", str(tmp_path / "nope.txt"))
    assert res["success"] is False


def test_wallpaper_refuses_missing_or_non_picture_files(tmp_path, monkeypatch):
    import ctypes
    from pc_client.tools import file_ops

    calls = []
    monkeypatch.setattr(ctypes.windll.user32, "SystemParametersInfoW", lambda *a: calls.append(a) or 1, raising=False)
    assert file_ops.set_wallpaper(str(tmp_path / "missing.jpg"))["success"] is False
    (tmp_path / "doc.txt").write_text("x")
    assert file_ops.set_wallpaper(str(tmp_path / "doc.txt"))["success"] is False
    assert calls == []  # Windows was never asked
