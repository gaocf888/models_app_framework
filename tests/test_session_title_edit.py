import pytest

from app.conversation.session_catalog import normalize_edited_title, title_edit_max_runes


def test_normalize_edited_title_strips_and_folds_ws():
    assert normalize_edited_title("  a  b  ") == "a b"


def test_normalize_edited_title_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        normalize_edited_title("")
    with pytest.raises(ValueError, match="empty"):
        normalize_edited_title("   \n\t ")


def test_normalize_edited_title_truncates():
    max_r = title_edit_max_runes()
    long_s = "字" * (max_r + 10)
    out = normalize_edited_title(long_s)
    assert len(list(out)) <= max_r + 1  # + ellipsis
    assert out.endswith("…")
