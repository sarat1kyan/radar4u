from datetime import datetime
from zoneinfo import ZoneInfo

from radar.core.provenance import Tri
from radar.providers.osm_hours import describe_day, is_open, open_for

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
    assert is_open("Mo-Fr 09:00-18:00; PH off", at(23, 10)) is Tri.UNKNOWN
    assert is_open("sunrise-sunset", at(23, 10)) is Tri.UNKNOWN
    assert is_open(None, at(23, 10)) is Tri.UNKNOWN
    assert is_open("24/7", at(23, 3)) is Tri.YES


def test_open_for_duration():
    v = "Mo-Su 12:00-20:00"
    assert open_for(v, at(23, 18), 90) is Tri.YES
    assert open_for(v, at(23, 19), 90) is Tri.NO


def test_describe():
    assert describe_day("Mo-Fr 09:00-18:00", 0) == "09:00-18:00"
    assert describe_day("Su off", 6) == "closed"
