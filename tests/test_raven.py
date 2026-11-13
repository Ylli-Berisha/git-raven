import datetime as dt

from git_raven.data import summarize
from git_raven.render import RavenSpec, render_frame, stage_for


def test_stage_thresholds():
    assert [stage_for(n) for n in (0, 149, 150, 4000, 99999)] == [0, 0, 1, 4, 5]


def test_frame_is_transparent_384():
    img = render_frame(RavenSpec(stage=3), 10)
    assert img.size == (384, 384)
    assert img.getpixel((0, 0))[3] == 0
    assert img.getbbox() is not None


def test_loop_closes():
    spec = RavenSpec(stage=2)
    assert render_frame(spec, 0).tobytes() == render_frame(spec, spec.frames).tobytes()


def _user(counts):
    today = dt.date(2026, 11, 13)
    days = [{"date": (today - dt.timedelta(days=i)).isoformat(), "contributionCount": c}
            for i, c in enumerate(counts)]
    return {
        "contributionsCollection": {"contributionCalendar": {
            "totalContributions": sum(counts), "weeks": [{"contributionDays": days}]}},
        "repositories": {"nodes": [{"stargazerCount": 3, "languages": {"edges": [
            {"size": 10, "node": {"name": "C#", "color": "#178600"}}]}}]},
    }, today


def test_streak_and_yesterday():
    user, today = _user([0, 2, 5, 1, 0, 4])   # today empty, then 3 active days
    d = summarize(user, today)
    assert d["streak"] == 3 and d["yesterday"] == 2 and d["stars"] == 3
    assert d["sheen"][0] == (0x17, 0x86, 0x00)
