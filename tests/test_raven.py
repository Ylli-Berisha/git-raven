import datetime as dt

from git_raven.data import summarize
from git_raven.render import RavenSpec, render_frame, stage_for


def test_stage_thresholds():
    assert [stage_for(n) for n in (0, 149, 150, 4000, 99999)] == [0, 0, 1, 4, 5]


def test_frame_is_transparent_768x288():
    img = render_frame(RavenSpec(stage=3), 10)
    assert img.size == (768, 288)
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


def test_rest_pose_animates_and_loops():
    spec = RavenSpec(stage=3, yesterday=6)
    R = 34                                              # rest length for a busy day
    assert render_frame(spec, 0).tobytes() != render_frame(spec, R // 2).tobytes()
    assert render_frame(spec, 0).tobytes() == render_frame(spec, spec.frames).tobytes()


def test_no_view_switch_at_landing_and_takeoff():
    """The bird is the same side-view sprite throughout, so frames either side of the
    landing and take-off change only a little (no pop between two different drawings)."""
    from PIL import ImageChops
    spec = RavenSpec(stage=2, yesterday=6)
    n, R = spec.frames, 34

    def changed(a, b):
        d = ImageChops.difference(render_frame(spec, a).convert("RGBA"),
                                  render_frame(spec, b).convert("RGBA"))
        return sum(1 for px in d.getdata() if px != (0, 0, 0, 0))

    seam = changed(n - 1, 0)
    typical = max(changed(k, k + 1) for k in range(R + 10, R + 40))
    assert seam < typical * 1.2
    assert changed(R - 1, R) < typical * 1.2
