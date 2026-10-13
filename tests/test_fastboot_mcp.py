import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fastboot_mcp_server as fb  # noqa: E402

try:
    import pytest
except ImportError:  # keep the module importable/compilable without pytest
    pytest = None


def test_find_fastboot_prefers_which(monkeypatch):
    monkeypatch.setattr(fb.shutil, "which", lambda name: r"C:\fake\fastboot.exe")
    assert fb._find_fastboot() == r"C:\fake\fastboot.exe"


def test_find_fastboot_falls_back_to_candidate(monkeypatch):
    monkeypatch.setattr(fb.shutil, "which", lambda name: None)
    monkeypatch.setattr(fb, "_candidate_paths", lambda: [r"C:\cand\fastboot.exe"])
    monkeypatch.setattr(fb.os.path, "isfile", lambda p: p == r"C:\cand\fastboot.exe")
    assert fb._find_fastboot() == r"C:\cand\fastboot.exe"


def test_find_fastboot_raises_when_missing(monkeypatch):
    monkeypatch.setattr(fb.shutil, "which", lambda name: None)
    monkeypatch.setattr(fb, "_candidate_paths", lambda: [])
    if pytest is None:
        try:
            fb._find_fastboot()
        except fb.FastbootError:
            return
        raise AssertionError("expected FastbootError")
    with pytest.raises(fb.FastbootError):
        fb._find_fastboot()


def _boom(*args, **kwargs):
    raise AssertionError("run_fastboot must not be called without confirm=True")


def test_flash_requires_confirm(monkeypatch):
    monkeypatch.setattr(fb, "run_fastboot", _boom)
    res = fb.fastboot_flash("boot", "boot.img", confirm=False)
    assert res["executed"] is False
    assert res["confirm_required"] is True
    assert "flash" in res["would_run"]
    assert "boot" in res["would_run"]


def test_erase_requires_confirm(monkeypatch):
    monkeypatch.setattr(fb, "run_fastboot", _boom)
    res = fb.fastboot_erase("userdata", confirm=False)
    assert res["executed"] is False
    assert res["confirm_required"] is True
    assert "erase" in res["would_run"]


def test_flashing_unlock_requires_confirm(monkeypatch):
    monkeypatch.setattr(fb, "run_fastboot", _boom)
    res = fb.fastboot_flashing("unlock", confirm=False)
    assert res["executed"] is False
    assert res["confirm_required"] is True


def test_flashing_get_unlock_ability_is_allowed(monkeypatch):
    calls = {}

    def fake(args, serial=None, timeout_s=fb.DEFAULT_TIMEOUT_S):
        calls["args"] = args
        return {"returncode": 0, "stdout": "get_unlock_ability: 1", "stderr": ""}

    monkeypatch.setattr(fb, "run_fastboot", fake)
    res = fb.fastboot_flashing("get_unlock_ability", confirm=False)
    assert calls["args"] == ["flashing", "get_unlock_ability"]
    assert res["returncode"] == 0


def test_flash_with_confirm_calls_run(monkeypatch):
    calls = {}

    def fake(args, serial=None, timeout_s=fb.DEFAULT_TIMEOUT_S):
        calls["args"] = args
        calls["serial"] = serial
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(fb, "run_fastboot", fake)
    res = fb.fastboot_flash("boot", "boot.img", serial="ABC123", confirm=True)
    assert calls["args"] == ["flash", "boot", "boot.img"]
    assert calls["serial"] == "ABC123"
    assert res["success"] is True
