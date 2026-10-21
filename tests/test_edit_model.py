import pytest

from studiovideo.edit.model import Timeline


def tl3():
    t = Timeline()
    t.add_segment(1, 0, 10)
    t.add_segment(2, 5, 8)
    t.add_segment(1, 20, 22)
    return t


def test_duration_and_locate():
    t = tl3()
    assert t.duration == 15
    assert t.locate(0) == (0, 0)
    assert t.locate(11) == (1, 6)
    assert t.locate(14) == (2, 21)
    assert t.locate(99) == (2, 22)
    assert t.segment_start(2) == 13


def test_split_move_remove():
    t = tl3()
    left, right = t.split_at(4)
    assert (left.src_in, left.src_out, right.src_in, right.src_out) == (0, 4, 4, 10)
    assert len(t.segments) == 4 and t.duration == 15
    assert t.split_at(4.02) is None  # trop près d'une coupe
    j = t.move_segment(right.id, -1)
    assert j == 0 and t.segments[0].id == right.id
    t.remove_segment(right.id)
    assert t.duration == 9


def test_trim_bounds():
    t = tl3()
    s = t.segments[1]
    t.trim(s.id, src_in=6, src_out=100, source_duration=9)
    assert (s.src_in, s.src_out) == (6, 9)
    with pytest.raises(ValueError):
        t.trim(s.id, src_out=6.05)


def test_roundtrip_and_aspect():
    t = tl3()
    t.add_text("Bonjour", 1, 2, color="#FF0000")
    t.set_aspect("1:1")
    t.video.mirror = True
    u = Timeline.from_dict(t.to_dict())
    assert u.to_dict() == t.to_dict()
    assert (u.format.width, u.format.height) == (1080, 1080)
    u.set_aspect("4:5", 720)
    assert (u.format.width, u.format.height) == (720, 900)


def test_problems():
    t = Timeline()
    assert t.problems(lambda i: None)
    t.add_segment(1, 0, 5)
    row = {"missing": 0, "kind": "video", "duration_s": 4.0, "file_name": "a.mp4"}
    assert any("dépasse" in p for p in t.problems(lambda i: row))
    t.segments[0].src_out = 4
    assert t.problems(lambda i: row) == []
