from datetime import datetime
from zoneinfo import ZoneInfo

from radar.core.provenance import Tri
from radar.providers.osm_hours import describe_day, is_open, next_change, open_for

Z = ZoneInfo("Asia/Yerevan")


def at(d, h, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=Z)  # 23 Nov 2026 is a Monday


def test_basic_and_override():
    v = "Mo-Fr 09:00-18:00; Sa 10:00-14:00; Su off"
    assert is_open(v, at(23, 10)) is Tri.YES
    assert is_open(v, at(23, 19)) is Tri.NO
    assert is_open(v, at(28, 13, 30)) is Tri.YES
    assert is_open(v, at(29, 12)) is Tri.NO


def test_after_midnight():
    v = "Mo-Su 20:00-02:00"
    assert is_open(v, at(24, 1, 30)) is Tri.YES
    assert is_open(v, at(24, 3)) is Tri.NO


def test_unsupported_is_unknown_not_guess():
    assert is_open("sunrise-sunset", at(23, 10)) is Tri.UNKNOWN
    assert is_open('"at events"', at(23, 10)) is Tri.UNKNOWN
    assert is_open(None, at(23, 10)) is Tri.UNKNOWN
    assert is_open("24/7", at(23, 3)) is Tri.YES


def test_open_for_duration():
    v = "Mo-Su 12:00-20:00"
    assert open_for(v, at(23, 18), 90) is Tri.YES
    assert open_for(v, at(23, 19), 90) is Tri.NO


def test_describe():
    assert describe_day("Mo-Fr 09:00-18:00", 0) == "09:00-18:00"
    assert describe_day("Su off", 6) == "closed"


def test_real_world_forms():
    # holiday markers in day lists, comma separated rules, spaces after commas
    assert is_open("Mo-Su,PH 11:00-23:00", at(23, 12)) is Tri.YES
    assert is_open("PH,Mo-Su 12:00-02:00", at(24, 1)) is Tri.YES
    assert is_open("Mo-Fr 14:00-24:00, Sa,Su,PH 14:00-02:00", at(28, 23)) is Tri.YES
    assert is_open("Th, Sa 18:00-22:00", at(26, 19)) is Tri.YES
    assert is_open("Th, Sa 18:00-22:00", at(27, 19)) is Tri.NO
    # date exceptions are ignored rather than discarding the whole schedule
    assert is_open("Mo,Su off; Tu-Fr 12:00-23:00; Sa 15:00-23:00; Sep 01-15 off", at(24, 13)) is Tri.YES


def test_open_ended():
    v = "Mo-Su 17:00+"
    assert is_open(v, at(23, 18)) is Tri.YES
    assert is_open(v, at(23, 16)) is Tri.NO
    state, change = next_change(v, at(23, 18))
    assert state is Tri.YES and change is None  # closing time unknown, never invented
    assert describe_day(v, 0) == "17:00+"


def test_next_change_labels():
    state, change = next_change("Mo-Su 12:00-23:00", at(23, 19))
    assert state is Tri.YES and change.hour == 23
    state, change = next_change("Mo-Su 12:00-23:00", at(23, 9))
    assert state is Tri.NO and change.hour == 12
