"""rename_group and groups_in_common as standalone tools (the "~" GC rename after a client accepts
a listing; the one-GC-per-client check before any manual build), on the group-flow fake."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backends  # noqa: E402
import viber_send  # noqa: E402
import viber_ui  # noqa: E402
from test_regressions import GroupFlowBackend, _obs  # noqa: E402


def _install(monkeypatch, fake):
    monkeypatch.setattr(backends, "_backend", fake)
    monkeypatch.setattr(viber_send, "backend", fake)
    monkeypatch.setattr(viber_ui.time, "sleep", lambda s: None)
    monkeypatch.setattr(viber_send.time, "sleep", lambda s: None)
    return fake


def test_rename_group_renames_the_open_group_and_checks_the_header(monkeypatch):
    fake = _install(monkeypatch, GroupFlowBackend())
    fake.page, fake.group_name, fake.rename_text = "group", "JANE-WH-(~, LAS PINAS)", "JANE-WH-(~, LAS PINAS)"
    out = viber_ui.rename_group("JANE-WH-(240SQM, LAS PINAS)", expect_current="JANE-WH-(~, LAS PINAS)")
    assert out["renamed"] is True and out["from"] == "JANE-WH-(~, LAS PINAS)" and out["note"] is None
    assert fake.group_name == "JANE-WH-(240SQM, LAS PINAS)"


def test_rename_group_accepts_the_truncated_name_the_sidebar_shows(monkeypatch):
    fake = _install(monkeypatch, GroupFlowBackend())
    fake.page, fake.group_name, fake.rename_text = "group", "MON-WH-(2000SQM, BULACAN)", "MON-WH-(2000SQM, BULACAN)"
    out = viber_ui.rename_group("MON-WH-(1500SQM, BULACAN)", expect_current="MON-WH-(2000SQM,...")
    assert out["renamed"] is True and fake.group_name == "MON-WH-(1500SQM, BULACAN)"


def test_rename_group_refuses_another_group_or_a_one_to_one_chat(monkeypatch):
    fake = _install(monkeypatch, GroupFlowBackend())
    fake.page, fake.group_name = "group", "KM-WH-(240SQM, PASIG)"
    with pytest.raises(viber_ui.UIError, match="not 'JANE-WH"):
        viber_ui.rename_group("JANE-WH-(240SQM, LAS PINAS)", expect_current="JANE-WH-(~, LAS PINAS)")
    assert fake.clicks == [] and fake.group_name == "KM-WH-(240SQM, PASIG)"
    fake.page = "chat"  # Rafael's 1:1 chat
    with pytest.raises(viber_ui.UIError, match="isn't a group"):
        viber_ui.rename_group("RAFAEL-WH-(300SQM, PASIG)")
    assert fake.clicks == []
    with pytest.raises(viber_ui.UIError, match="empty"):
        viber_ui.rename_group("  ")


def test_groups_in_common_reads_the_open_one_to_one_chat(monkeypatch):
    fake = _install(monkeypatch, GroupFlowBackend())
    out = viber_ui.groups_in_common(fake.NUMBER)
    assert out == {"chat": "Rafael", "groups": [], "existing_client": False}
    assert fake.page == "main"  # back on the panel's main page, nothing created
    original = fake.observe

    def observe(region=None):
        return [line if line["text"] != "No groups in common" else _obs("KM-WH-(240SQM, PASIG)", 0.72, 0.2)
                for line in original(region)]
    fake.observe = observe
    out = viber_ui.groups_in_common()
    assert out["groups"] == ["KM-WH-(240SQM, PASIG)"] and out["existing_client"] is True


def test_groups_in_common_refuses_groups_and_the_wrong_number(monkeypatch):
    fake = _install(monkeypatch, GroupFlowBackend())
    fake.page = "group"
    with pytest.raises(viber_ui.UIError, match="is a group"):
        viber_ui.groups_in_common()
    fake.page = "chat"
    with pytest.raises(viber_ui.UIError, match="isn't \\+639990000000's 1:1 chat"):
        viber_ui.groups_in_common("+639990000000")
    assert fake.clicks == []
