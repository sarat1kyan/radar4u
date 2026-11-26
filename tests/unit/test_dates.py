from datetime import date, time

from radar.nlu.dates import parse_dates

T = date(2026, 11, 26)  # Thursday


def test_tomorrow_multilingual():
    for word in ("tomorrow", "завтра", "վաղը", "ხვალ", "ертең", "morgen", "demain", "domani", "mañana"):
        assert parse_dates(f"table {word}", T).date_from == date(2026, 11, 27), word


def test_explicit_range_rolls_year():
    p = parse_dates("from December 21 to December 27", T)
    assert (p.date_from, p.date_to) == (date(2026, 12, 21), date(2026, 12, 27))
    p = parse_dates("с 3 по 5 марта", T)
    assert (p.date_from, p.date_to) == (date(2027, 3, 3), date(2027, 3, 5))


def test_numeric_day_first():
    assert parse_dates("21.12", T).date_from == date(2026, 12, 21)
    assert parse_dates("rated 4.5+", T).date_from is None


def test_weekday_and_weekend():
    assert parse_dates("on Saturday", T).date_from == date(2026, 11, 28)
    p = parse_dates("this weekend", T)
    assert (p.date_from, p.date_to) == (date(2026, 11, 28), date(2026, 11, 29))


def test_next_month_window():
    p = parse_dates("next month", T)
    assert (p.date_from, p.date_to, p.month_window) == (date(2026, 12, 1), date(2026, 12, 31), True)


def test_times():
    assert parse_dates("tomorrow at 18:00.", T).time_from == time(18, 0)
    assert parse_dates("at 7pm", T).time_from == time(19, 0)
    p = parse_dates("arriving before 14:00 tomorrow", T)
    assert p.arrive_by and p.time_to == time(14, 0)
    p = parse_dates("between 18:00 and 20:00", T)
    assert (p.time_from, p.time_to) == (time(18), time(20))


def test_nights():
    p = parse_dates("3 nights from 21.12", T)
    assert p.nights == 3 and p.date_to == date(2026, 12, 24)
