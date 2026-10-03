"""Fuzzy file finding for spoken names (pc_client/tools/media_tools.py)."""

from pc_client.tools import media_tools
from pc_client.tools.media_tools import _score, find_files


def test_spoken_names_match_messy_file_names():
    messy = "Sarvam_Maya_2025_720p_JHS_WEB_DL_MULTi_DDP5_1_H_264_PMi_XDMovies.mkv"
    assert _score("sarva maya", messy) >= 0.8
    assert _score("sarvamaya", messy) > 0.5          # speech-to-text glued the words
    assert _score("report", "Report.pdf") == 1.0
    assert _score("holiday", "budget.xlsx") == 0.0


def test_find_files_uses_the_folder_hint_and_kind(tmp_path, monkeypatch):
    tg = tmp_path / "Downloads" / "Telegram Desktop"
    tg.mkdir(parents=True)
    (tg / "Sarvam_Maya_2025_720p.mkv").write_bytes(b"x")
    (tg / "sarvam maya notes.txt").write_text("x")
    (tmp_path / "Downloads" / "other.mkv").write_bytes(b"x")
    monkeypatch.setattr("pc_client.tools.file_ops._known_folders", lambda: {
        "downloads": tmp_path / "Downloads", "desktop": tmp_path / "nope", "documents": tmp_path / "nope",
        "videos": tmp_path / "nope", "music": tmp_path / "nope", "pictures": tmp_path / "nope"})
    res = find_files("sarvamaya", "telegram desktop", "video")
    assert res["success"] and [f["name"] for f in res["files"]] == ["Sarvam_Maya_2025_720p.mkv"]
    assert find_files("doesnotexist", "telegram desktop")["success"] is False


def test_ambiguous_names_ask_instead_of_guessing(tmp_path, monkeypatch):
    for n in ("Episode 1.mkv", "Episode 2.mkv"):
        (tmp_path / n).write_bytes(b"x")
    opened = []
    monkeypatch.setattr(media_tools, "open_with", lambda *a, **k: opened.append(a) or {"success": True})
    res = media_tools.open_file_smart(name="episode", folder=str(tmp_path))
    assert res["error"] == "AMBIGUOUS" and not opened
