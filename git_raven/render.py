"""Pixel-art raven renderer.

Draws a top-down raven on a 192x72 logical grid (upscaled 4x to 768x288),
with dithered shading, a thin outline and a transparent background.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

LOGICAL_W = 192   # canvas width in logical pixels (768px at 4x)
LOGICAL_H = 72    # canvas height in logical pixels (288px at 4x)
SCALE = 4
FRAMES = 160
FRAME_MS = 70

BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]

DEEP = (10, 12, 20)
BASE = (24, 27, 42)
MID = (44, 50, 78)
OUTLINE = (104, 114, 150)
EYE = (232, 236, 255)
BEAK = (58, 62, 82)
BEAK_HI = (112, 120, 150)

# Contribution thresholds for the six growth stages.
STAGE_THRESHOLDS = (0, 150, 500, 1500, 4000, 10000)


@dataclass
class RavenSpec:
    stage: int = 5                       # 0..5 growth stage
    sheen_a: tuple = (96, 80, 220)       # primary sheen colour (top language)
    sheen_b: tuple = (40, 190, 190)      # secondary sheen colour (2nd language)
    streak: int = 0                      # current streak in days
    yesterday: int = 5                   # contributions yesterday
    sparkles: int = 0                    # shiny things collected (stars)
    frames: int = FRAMES
    extras: dict = field(default_factory=dict)


def stage_for(total: int) -> int:
    stage = 0
    for i, t in enumerate(STAGE_THRESHOLDS):
        if total >= t:
            stage = i
    return stage


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _scale(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _pick(levels, t, x, y):
    """Ordered-dither pick from a ramp of colours for t in 0..1."""
    n = len(levels)
    b = (BAYER[y % 4][x % 4] + 0.5) / 16.0 - 0.5
    v = t * (n - 1) + b * 0.9
    return levels[max(0, min(n - 1, int(round(v))))]


def _stage_params(stage: int):
    # scale, wing half-span (in body units), tail length, sheen strength
    table = [
        (1.15, 12.5, 7.0, 0.25),
        (1.40, 14.0, 9.0, 0.45),
        (1.65, 15.5, 11.0, 0.65),
        (1.85, 16.5, 12.5, 0.85),
        (2.05, 17.0, 14.0, 1.0),
        (2.25, 17.5, 15.0, 1.0),
    ]
    return table[max(0, min(5, stage))]


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _angle_lerp(a, b, t):
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + d * t


def _rest_frames(spec: RavenSpec) -> int:
    """Quiet days mean a longer rest on the branch, busy days a shorter one."""
    y = spec.yesterday
    return 64 if y <= 0 else 48 if y < 4 else 34


def _lerp(a, b, t):
    return a + (b - a) * t


def _side_scale(stage: int) -> float:
    """Size of the side-view raven; capped so even adults fit in the strip."""
    return (1.2, 1.4, 1.55, 1.65, 1.75, 1.8)[max(0, min(5, stage))]


def _state(spec: RavenSpec, k: int) -> dict:
    """Where the raven is and what it is doing at frame k.

    Loop: rest on the branch -> take off -> one big swoop -> land -> rest.
    The same side-view sprite is used throughout, so there is no cut.
    """
    n = spec.frames
    k %= n
    R = _rest_frames(spec)
    F = n - R
    s = _side_scale(spec.stage)
    cx, cy = LOGICAL_W / 2, LOGICAL_H / 2
    base_y = _branch_top(cx) + 0.3 - 7.8 * s              # body centre when perched
    C0 = (cx - 0.5 * s, base_y)
    rest_pitch = math.radians(30)
    st = dict(x=C0[0], y=C0[1], pitch=rest_pitch, face=1.0, fold=1.0, alpha=0.0,
              yaw=0.0, blink=False, wag=0.0, legs=True)

    if k < R:                                              # sitting on the branch
        t = k / R
        crouch = max(0.0, (k - (R - 4)) / 4.0)             # last frames: lean in to jump
        st["yaw"] = 0.65 * math.sin(2 * math.pi * 1.5 * t)
        st["blink"] = any(c < t < c + 3.0 / R for c in (0.42, 0.8))
        st["wag"] = 0.22 * math.sin(2 * math.pi * 2 * t)
        st["pitch"] = rest_pitch + 0.25 * crouch
        st["y"] = base_y - 0.12 * s * math.sin(2 * math.pi * 3 * t) + 1.3 * s * crouch
        return st

    u = (k - R) / F
    roam = 0.85 if spec.yesterday <= 0 else 1.0
    amp_x = max(6.0, (LOGICAL_W / 2 - 20) * roam)       # turn around inside the frame
    amp_y = max(6.0, (LOGICAL_H / 2 - 7) * roam)

    def path(uu):
        ease = uu - math.sin(2 * math.pi * uu) / (2 * math.pi)     # slow at both ends
        a_ = 2 * math.pi * ease
        w = _smooth(min(uu, 1 - uu) / 0.2)                          # 0 at take-off / landing
        px = cx + amp_x * math.sin(a_)
        py = cy + amp_y * math.sin(2 * a_)
        return _lerp(C0[0], px, w), _lerp(C0[1], py, w)

    x, y = path(u)
    x0, y0 = path(max(0.0, u - 0.003))
    x1, y1 = path(min(1.0, u + 0.003))
    vx, vy = x1 - x0, y1 - y0
    near = _smooth(min(u, 1 - u) / 0.14)
    face_fly = max(-1.0, min(1.0, vx / (0.0035 * amp_x)))           # flips over in a frame or two
    pitch_fly = 0.7 * max(-0.8, min(0.8, math.atan2(-vy, abs(vx) + 1e-6)))
    st["face"] = _lerp(1.0, face_fly, near)
    st["pitch"] = _lerp(rest_pitch, pitch_fly, near)
    st["fold"] = max(1 - _smooth(u / 0.09), _smooth((u - 0.88) / 0.12))

    fl = 5 if spec.yesterday <= 0 else 7 if spec.yesterday < 4 else 9 if spec.yesterday < 10 else 11
    th = 2 * math.pi * fl * u
    flare = 1 + 0.25 * (1 - near)
    alpha = 0.95 * flare * (math.sin(th) - 0.18 * math.sin(2 * th))
    st["alpha"] = max(-1.15, min(1.15, alpha))
    hop = 1.3 * s * (1 - _smooth(u / 0.05))                         # springs off the branch
    st["x"] = x
    st["y"] = y + hop + 0.9 * s * st["alpha"] * (1 - st["fold"])    # body lifts on the downstroke
    st["wag"] = 0.12 * math.sin(2 * math.pi * 3 * u)
    st["legs"] = st["fold"] > 0.9
    return st


BARK_HI = (132, 100, 72)
BARK = (92, 67, 51)
BARK_DK = (58, 42, 36)
BARK_BLK = (34, 25, 26)
MOSS = (84, 120, 82)
MOSS_DK = (56, 88, 62)
LEAF = (82, 128, 92)
LEAF_DK = (50, 88, 68)
LEAF_HI = (132, 176, 118)


def _branch_geom(x: float):
    """(centre y, half thickness) of the branch at column x."""
    cx, cy = LOGICAL_W / 2, LOGICAL_H / 2
    x0, x1 = cx - 48, cx + 44
    f = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
    thick = 1.5 + 3.7 * (1 - f) ** 0.8
    centre = cy + 1.3 * math.sin((x - cx) * 0.17) + 2.0 * f * f
    return centre, thick


def _branch_top(x: float) -> float:
    c, t = _branch_geom(x)
    return c - t


def _draw_branch(out, k: int = 0, n: int = FRAMES) -> None:
    """A tree trunk on the left with a gnarled branch growing out of it."""
    cx, cy = LOGICAL_W / 2, LOGICAL_H / 2
    tx0, tx1 = cx - 66, cx - 47

    def put(x, y, col):
        x, y = int(x), int(y)
        if 0 <= x < LOGICAL_W and 0 <= y < LOGICAL_H:
            out[x, y] = col + (255,)

    # ---- trunk: full height, slightly wavy, shaded like a cylinder ----
    for y in range(LOGICAL_H):
        left = tx0 + 1.6 * math.sin(y * 0.16) + (0.8 if y > cy else 0)
        right = tx1 + 1.3 * math.sin(y * 0.21 + 1.0) + 2.2 * max(0.0, (y - cy - 14) / 30)  # root flare
        for x in range(int(left), int(right) + 1):
            rel = (x - left) / max(1.0, right - left)
            t = max(0.0, 1.0 - abs(rel - 0.35) * 1.9)
            col = _pick([BARK_DK, BARK, BARK_HI], t, x, y)
            ridge = (x * 3 + int(2 * math.sin(y * 0.3 + x * 0.5))) % 6
            if ridge == 0:
                col = BARK_DK
            if (x * 5 + (y // 7) * 3) % 11 == 0 and (y % 7) < 5:
                col = BARK_BLK                                   # deep cracks
            put(x, y, col)
    # knot hole on the trunk
    kx, ky = tx0 + 8, cy - 15
    for dy in range(-4, 5):
        for dx in range(-3, 4):
            r = (dx / 3.0) ** 2 + (dy / 4.2) ** 2
            if r <= 1.0:
                put(kx + dx, ky + dy, BARK_BLK if r < 0.55 else BARK_DK)

    # ---- branch: thick at the trunk, tapering and drooping a little ----
    x_start, x_end = int(tx1 - 4), int(cx + 44)
    for x in range(x_start, x_end + 1):
        c, th = _branch_geom(x)
        # flare where the branch joins the trunk
        flare = 3.5 * max(0.0, 1 - (x - x_start) / 9.0) ** 2
        y_top, y_bot = c - th - flare, c + th + flare * 0.5
        for y in range(int(y_top), int(y_bot) + 1):
            rel = (y - y_top) / max(1.0, y_bot - y_top)
            col = _pick([BARK_HI, BARK, BARK_DK], rel, x, y)
            if (x * 7 + y * 13) % 11 == 0:
                col = BARK_DK                                    # bark flecks
            if (x // 5) % 3 == 0 and (x % 5) == 0 and rel > 0.25:
                col = BARK_BLK                                   # ring cracks across the branch
            put(x, y, col)
    # moss on the top of the branch near the trunk and on the trunk's shaded side
    for x in range(x_start + 4, x_start + 30):
        top = _branch_top(x)
        if (x * 17) % 5 < 3:
            put(x, top + 0.5, MOSS if (x % 2) else MOSS_DK)
            if (x * 11) % 4 == 0:
                put(x, top - 0.5, MOSS)
    for y in range(int(cy + 4), int(cy + 20)):
        for x in range(int(tx0 + 1), int(tx0 + 5)):
            if (x * 5 + y * 3) % 7 < 2:
                put(x, y, MOSS_DK if (x + y) % 2 else MOSS)

    # ---- twigs ----
    m = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
    d = ImageDraw.Draw(m)
    twigs = [
        [(cx + 27, _branch_top(cx + 27) + 1), (cx + 31, cy - 9), (cx + 37, cy - 15)],
        [(cx + 31, cy - 9), (cx + 27, cy - 16)],
        [(cx + 39, _branch_top(cx + 39) + 1), (cx + 43, cy - 5), (cx + 48, cy - 8)],
        [(cx - 16, _branch_geom(cx - 16)[0] + 2), (cx - 21, cy + 9), (cx - 22, cy + 13)],
        [(cx + 43, _branch_geom(cx + 43)[0]), (cx + 49, cy + 7)],
    ]
    for t in twigs:
        d.line(t, fill=255, width=1)
    px = m.load()
    for y in range(LOGICAL_H):
        for x in range(LOGICAL_W):
            if px[x, y]:
                put(x, y, BARK if (x + y) % 3 else BARK_HI)

    # ---- leaf clusters that sway a little ----
    clusters = [(cx + 37, cy - 15, 0.0), (cx + 27, cy - 16, 1.3), (cx + 48, cy - 8, 2.1),
                (cx - 22, cy + 13, 3.0), (cx + 49, cy + 7, 4.2)]
    for lx, ly, ph in clusters:
        sway = round(0.8 * math.sin(2 * math.pi * 2 * k / n + ph))
        for ox, oy, c in ((0, 0, LEAF), (-2, -1, LEAF_DK), (2, -1, LEAF_HI), (-1, 2, LEAF_DK),
                          (2, 2, LEAF), (-3, 1, LEAF)):
            for dx in (0, 1):
                for dy in (0, 1):
                    put(lx + ox + dx + (sway if oy < 1 else 0), ly + oy + dy, c)


def _norm(v):
    m = math.hypot(v[0], v[1]) or 1.0
    return (v[0] / m, v[1] / m)


def _draw_bird(spec: RavenSpec, k: int, st: dict) -> Image.Image:
    """The raven in side view, facing right, on a transparent layer."""
    s = _side_scale(spec.stage)
    _, wing_l, tail_l, sheen_k = _stage_params(spec.stage)
    bird = Image.new("RGBA", (LOGICAL_W, LOGICAL_H), (0, 0, 0, 0))
    out = bird.load()

    th, fold, alpha = st["pitch"], st["fold"], st["alpha"]
    yaw, blink, wag = st["yaw"], st["blink"], st["wag"]
    d = (math.cos(th), -math.sin(th))               # along the body, towards the head
    m = (math.sin(th), math.cos(th))                # across the body, towards the belly
    C = (st["x"], st["y"])

    def W(a, b):                                     # body-local -> canvas
        return (C[0] + (d[0] * a + m[0] * b) * s, C[1] + (d[1] * a + m[1] * b) * s)

    def circle(cx_, cy_, r, steps=24):
        return [(cx_ + r * math.cos(2 * math.pi * i / steps),
                 cy_ + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]

    body = [W(7.8 * math.cos(2 * math.pi * i / 28), 4.6 * math.sin(2 * math.pi * i / 28))
            for i in range(28)]
    hx = C[0] + d[0] * 8.2 * s + 1.8 * s * yaw
    hy = C[1] + d[1] * 8.2 * s - 1.4 * s - 0.5 * s * abs(yaw)
    head = circle(hx, hy, 3.1 * s)
    tilt = yaw * 0.35 + 0.15 - 0.4 * th * (1 - fold) * 0
    ct, stl = math.cos(tilt), math.sin(tilt)
    beak = [(hx + (px * ct - py * stl) * s, hy + (px * stl + py * ct) * s)
            for px, py in [(2.4, -1.7), (6.4, -0.6), (7.9, 1.2), (6.0, 1.0), (2.6, 1.9)]]

    # tail: hangs behind the branch when perched, streams out behind in flight
    ph = th + 0.28 * fold + 0.5 * wag
    td = (-math.cos(ph), math.sin(ph))
    tp = (math.sin(ph), math.cos(ph))
    B0 = W(-6.0, 0.4)
    Lt = tail_l * 0.95 * s
    E = (B0[0] + td[0] * Lt, B0[1] + td[1] * Lt)
    tail = [(B0[0] - tp[0] * 1.9 * s, B0[1] - tp[1] * 1.9 * s)]
    for f in (-1.0, -0.5, 0.0, 0.5, 1.0):
        w = 2.7 * s * f
        back = 1.0 * s * abs(f) ** 1.6
        tail.append((E[0] + tp[0] * w - td[0] * back, E[1] + tp[1] * w - td[1] * back))
    tail.append((B0[0] + tp[0] * 1.9 * s, B0[1] + tp[1] * 1.9 * s))

    # wings: a leaf shape on an axis that swings from "up" through "down" to "folded back"
    L = wing_l * s * 1.15
    w_fold = _norm((-d[0], -d[1]))
    n_fold = (-w_fold[1], w_fold[0])

    def wing(S, a_eff):
        up = a_eff >= 0
        w_flap = (-0.30, -0.95) if up else (-0.18, 0.98)
        sg = 1 if up else -1
        n_flap = (-w_flap[1] * sg, w_flap[0] * sg)
        w = _norm((_lerp(w_flap[0], w_fold[0], fold), _lerp(w_flap[1], w_fold[1], fold)))
        n = _norm((_lerp(n_flap[0], n_fold[0], fold), _lerp(n_flap[1], n_fold[1], fold)))
        ell = _lerp(max(5.0 * s, L * abs(math.sin(a_eff))), 13.0 * s, fold)
        lead, trail = [], []
        for i in range(10):
            t = i / 9
            off = 1.6 * s * (1 - t) ** 1.2
            lead.append((S[0] + w[0] * t * ell + n[0] * off, S[1] + w[1] * t * ell + n[1] * off))
        for i in range(13):
            t = 1 - i / 12
            width = _lerp(6.8 * (1 - 0.62 * t), 3.0 * (1 - 0.3 * t), fold) * s
            if 0.25 < t < 0.95 and i % 2 == 1:
                width *= 0.78                                      # scalloped primaries
            trail.append((S[0] + w[0] * t * ell - n[0] * width, S[1] + w[1] * t * ell - n[1] * width))
        return lead + trail, (S, w, n, ell)

    S_near = W(3.0, -2.4)
    wing_near, geo_near = wing(S_near, alpha)
    wing_far = geo_far = None
    if fold < 0.7:
        S_far = W(1.2, -2.8)
        wing_far, geo_far = wing(S_far, alpha * 0.75 - 0.22)

    def mk(pts):
        mm = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
        ImageDraw.Draw(mm).polygon(pts, fill=255)
        return mm.load()

    parts = []
    if st["legs"]:
        fy = _branch_top(LOGICAL_W / 2) + 0.3
        legw = max(1, round(0.8 * s))
        legs = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
        ld = ImageDraw.Draw(legs)
        for lx in (C[0] + 0.3 * s, C[0] + 2.8 * s):
            ld.rectangle([lx, C[1] + 3.2 * s, lx + legw - 1, max(C[1] + 3.5 * s, fy + 0.6)], fill=255)
        parts.append(("legs", legs.load()))
    if wing_far:
        parts.append(("wingfar", mk(wing_far)))
    parts += [("tail", mk(tail)), ("body", mk(body)), ("wing", mk(wing_near)),
              ("head", mk(head)), ("beak", mk(beak))]
    owner = {}
    for name, px in parts:
        for y in range(LOGICAL_H):
            for x in range(LOGICAL_W):
                if px[x, y]:
                    owner[(x, y)] = name

    sheen_hi = _mix(spec.sheen_a, (255, 255, 255), 0.25)
    streak_bands = min(4, spec.streak // 7)
    eye = (hx + 1.5 * s, hy - 0.6 * s)

    for (x, y), name in owner.items():
        dx, dy = x + 0.5 - C[0], y + 0.5 - C[1]
        a = (dx * d[0] + dy * d[1]) / s
        top = max(0.0, min(1.0, (C[1] + 4.6 * s - y) / (9.2 * s)))
        sheen = _mix(spec.sheen_a, spec.sheen_b, 0.5 + 0.5 * math.sin(a * 0.4))
        if name == "legs":
            col = BEAK
        elif name == "beak":
            col = BEAK_HI if y < hy + 0.2 * s else BEAK
        elif name in ("body", "head"):
            ramp = [DEEP, BASE, MID, _scale(sheen, 0.55 + 0.3 * sheen_k)]
            col = _pick(ramp, top * (0.5 + 0.5 * sheen_k) * (0.9 if name == "head" else 1.0), x, y)
            if name == "head" and not blink and abs(x + 0.5 - eye[0]) < 0.6 and abs(y + 0.5 - eye[1]) < 0.6:
                col = EYE
        elif name == "tail":
            t_along = min(1.0, max(0.0, math.hypot(x - B0[0], y - B0[1]) / max(Lt, 1)))
            sh = _mix(spec.sheen_a, spec.sheen_b, t_along)
            ramp = [DEEP, BASE, MID, _scale(sh, 0.5 + 0.35 * sheen_k)]
            col = _pick(ramp, 0.25 + t_along * 0.5 * sheen_k, x, y)
        else:                                                           # wings
            S_, w_, n_, ell_ = geo_near if name == "wing" else geo_far
            rx, ry = x + 0.5 - S_[0], y + 0.5 - S_[1]
            along = (rx * w_[0] + ry * w_[1]) / max(ell_, 1.0)
            chord = (rx * n_[0] + ry * n_[1]) / s
            f = int((chord + 8) * 0.9) % 2
            if name == "wingfar":
                col = _pick([DEEP, DEEP, BASE], 0.4 + 0.3 * (1 - along), x, y)
            else:
                ramp = [DEEP, BASE if f else DEEP, MID, _scale(sheen, 0.5 + 0.4 * sheen_k)]
                lit = max(0.0, min(1.0, 0.34 + 0.5 * (1 - along) * (0.5 + 0.5 * sheen_k)
                                   + (0.12 if chord > 0 else 0) + 0.12 * alpha * (1 - fold)))
                col = _pick(ramp, lit, x, y)
                for b_ in range(streak_bands):
                    if abs(along * ell_ / s - (3.0 + 3.0 * b_)) < 0.5 and chord > -2.5:
                        col = sheen_hi
        if name == "wing" and fold < 0.85:
            edge = any(owner.get((x + dx, y + dy)) != "wing"
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            if edge:
                col = _mix(col, OUTLINE, 0.6 * (1 - fold))
        out[x, y] = col + (255,)
    return bird


def _turn(bird: Image.Image, cx: float, face: float) -> Image.Image:
    """Flip / squash the bird horizontally about its centre (a turn-around)."""
    f = face if abs(face) >= 0.18 else (0.18 if face >= 0 else -0.18)
    return bird.transform(bird.size, Image.AFFINE, (1 / f, 0, cx - cx / f, 0, 1, 0), Image.NEAREST)


def render_frame(spec: RavenSpec, k: int) -> Image.Image:
    st = _state(spec, k)
    img = Image.new("RGBA", (LOGICAL_W, LOGICAL_H), (0, 0, 0, 0))
    _draw_branch(img.load(), k % spec.frames, spec.frames)
    bird = _draw_bird(spec, k, st)
    if abs(st["face"] - 1.0) > 1e-6:
        bird = _turn(bird, st["x"], st["face"])
    img.alpha_composite(bird)
    return _finish(img, spec, k)


def _finish(img, spec, k):
    """Outline, sparkles and upscale: shared by flying and perched frames."""
    out = img.load()
    n = spec.frames
    # outline: opaque pixels neighbouring transparent ones get a thin rim
    rim = []
    for y in range(LOGICAL_H):
        for x in range(LOGICAL_W):
            if out[x, y][3] == 0:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < LOGICAL_W and 0 <= ny < LOGICAL_H and out[nx, ny][3]:
                        rim.append((x, y))
                        break
    for x, y in rim:
        out[x, y] = OUTLINE + (255,)

    # sparkles: tiny twinkling "shiny things" for starred repos
    for i in range(min(spec.sparkles, 8)):
        a = 2 * math.pi * i / 8 + 0.7
        sx = int(LOGICAL_W / 2 + LOGICAL_W * 0.4 * math.cos(a))
        sy = int(LOGICAL_H / 2 + LOGICAL_H * 0.4 * math.sin(a))
        phase = (k / n * 4 + i / 8.0) % 1.0
        if phase < 0.5:
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)) if phase < 0.25 else ((0, 0),):
                if 0 <= sx + dx < LOGICAL_W and 0 <= sy + dy < LOGICAL_H:
                    out[sx + dx, sy + dy] = (255, 226, 140, 255)

    return img.resize((LOGICAL_W * SCALE, LOGICAL_H * SCALE), Image.NEAREST)


def render_gif(spec: RavenSpec, path: str) -> None:
    frames = [render_frame(spec, k) for k in range(spec.frames)]
    first, rest = frames[0], frames[1:]
    first.save(path, save_all=True, append_images=rest, duration=FRAME_MS,
               loop=0, disposal=2, optimize=False, transparency=0)
