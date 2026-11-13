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


def _state(spec: RavenSpec, k: int) -> dict:
    """Where the raven is and what it is doing at frame k.

    Loop: rest on the branch -> take off -> one figure-eight -> land -> rest.
    """
    n = spec.frames
    k %= n
    R = _rest_frames(spec)
    F = n - R
    cxm, cym = LOGICAL_W / 2, LOGICAL_H / 2
    st = dict(x=cxm, y=cym, h=0.0, phi=0.0, fold=1.0, yaw=0.0, blink=False, wag=0.0)

    if k < R:                                   # resting on the branch
        t = k / R
        st["yaw"] = 0.65 * math.sin(2 * math.pi * 1.5 * t)       # looks around
        st["blink"] = any(c < t < c + 3.0 / R for c in (0.42, 0.8))
        st["wag"] = 0.22 * math.sin(2 * math.pi * 2 * t)          # tail twitch
        return st

    u = (k - R) / F                             # 0..1 through the flight
    amp_x = max(6.0, (LOGICAL_W / 2 - 4) * (0.85 if spec.yesterday <= 0 else 1.0))
    amp_y = max(6.0, (LOGICAL_H / 2 - 4) * (0.85 if spec.yesterday <= 0 else 1.0))
    ease = u - math.sin(2 * math.pi * u) / (2 * math.pi)          # slow at both ends
    a = 2 * math.pi * ease
    st["x"] = cxm + amp_x * math.sin(a)
    st["y"] = cym + amp_y * math.sin(2 * a)

    heading = math.atan2(2 * amp_y * math.cos(2 * a), amp_x * math.cos(a))
    near_end = _smooth(min(u, 1 - u) / 0.16)
    st["h"] = _angle_lerp(0.0, heading, near_end)                 # face along the branch

    st["fold"] = max(1 - _smooth(u / 0.07), _smooth((u - 0.90) / 0.10))
    y = spec.yesterday
    flaps = 5 if y <= 0 else 7 if y < 4 else 9 if y < 10 else 11
    th = 2 * math.pi * flaps * u
    flare = 1 + 0.25 * (1 - near_end)                              # big beats at take-off / landing
    st["phi"] = 0.95 * flare * (math.sin(th) - 0.18 * math.sin(2 * th))
    st["phi"] = max(-1.15, min(1.15, st["phi"]))
    st["yaw"] = 0.0
    st["wag"] = 0.12 * math.sin(2 * math.pi * 3 * u)
    return st


BARK_HI = (126, 96, 70)
BARK = (90, 66, 50)
BARK_DK = (58, 42, 36)
LEAF = (74, 116, 90)
LEAF_DK = (48, 82, 66)


def _draw_branch(out) -> None:
    """A branch seen from above, running left-right through the middle."""
    cx, cy = LOGICAL_W / 2, LOGICAL_H / 2
    half = 40
    top, bot = [], []
    for i in range(-half, half + 1, 2):
        wob = 0.9 * math.sin(i * 0.28)
        taper = 3.4 - 1.4 * (abs(i) / half) ** 2
        top.append((cx + i, cy + wob - taper))
        bot.append((cx + i, cy + wob + taper))
    m = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
    d = ImageDraw.Draw(m)
    d.polygon(top + bot[::-1], fill=255)
    # twigs
    d.line([(cx + 16, cy - 3), (cx + 22, cy - 9), (cx + 27, cy - 11)], fill=255, width=1)
    d.line([(cx - 20, cy + 3), (cx - 25, cy + 8)], fill=255, width=1)
    px = m.load()
    for y in range(LOGICAL_H):
        for x in range(LOGICAL_W):
            if not px[x, y]:
                continue
            rel = (y - (cy - 4.5)) / 9.0                  # 0 top .. 1 bottom: top lit
            level = [BARK_HI, BARK, BARK_DK]
            col = _pick(level, max(0.0, min(1.0, rel)), x, y)
            if (x * 7 + y * 13) % 11 == 0:
                col = BARK_DK                              # bark flecks
            out[x, y] = col + (255,)
    # leaves on the twig ends
    for lx, ly, c in ((cx + 27, cy - 11, LEAF), (cx + 25, cy - 13, LEAF_DK),
                      (cx + 29, cy - 12, LEAF_DK), (cx - 25, cy + 8, LEAF),
                      (cx - 27, cy + 9, LEAF_DK)):
        for dx in (0, 1):
            for dy in (0, 1):
                X, Y = int(lx) + dx, int(ly) + dy
                if 0 <= X < LOGICAL_W and 0 <= Y < LOGICAL_H:
                    out[X, Y] = c + (255,)


def _perched_frame(spec: RavenSpec, k: int, st: dict) -> Image.Image:
    """Side-view raven sitting on the branch, facing right."""
    s, _, tail_l, sheen_k = _stage_params(spec.stage)
    s = min(s, 1.9)
    R = _rest_frames(spec)
    img = Image.new("RGBA", (LOGICAL_W, LOGICAL_H), (0, 0, 0, 0))
    out = img.load()
    _draw_branch(out)

    cx = LOGICAL_W / 2
    fy = LOGICAL_H / 2 - 3.3                       # top surface of the branch
    yaw, blink, wag = st["yaw"], st["blink"], st["wag"]
    crouch = max(0.0, (k - (R - 4)) / 4.0)         # last frames: lean in to jump
    th = math.radians(30) + 0.25 * crouch
    d = (math.cos(th), -math.sin(th))
    m = (math.sin(th), math.cos(th))
    breathe = 0.03 * math.sin(2 * math.pi * 3 * k / R)
    C = (cx - 0.5 * s, fy - (7.8 + breathe * 4) * s + 1.3 * s * crouch)

    def W(a, b):                                    # body-local -> canvas
        return (C[0] + (d[0] * a + m[0] * b) * s, C[1] + (d[1] * a + m[1] * b) * s)

    def circle(cx_, cy_, r, steps=24):
        return [(cx_ + r * math.cos(2 * math.pi * i / steps),
                 cy_ + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]

    body = [W(7.8 * math.cos(2 * math.pi * i / 28), 4.6 * math.sin(2 * math.pi * i / 28))
            for i in range(28)]
    hx = C[0] + d[0] * 8.2 * s + 1.8 * s * yaw
    hy = C[1] + d[1] * 8.2 * s - 1.4 * s - 0.5 * s * abs(yaw) + 1.0 * s * crouch
    head = circle(hx, hy, 3.1 * s)
    tilt = yaw * 0.35 + 0.15
    ct, stl = math.cos(tilt), math.sin(tilt)
    beak = [(hx + (px * ct - py * stl) * s, hy + (px * stl + py * ct) * s)
            for px, py in [(2.4, -1.7), (6.4, -0.6), (7.9, 1.2), (6.0, 1.0), (2.6, 1.9)]]

    # tail: hangs down and back behind the branch
    ph = 0.8 + 0.5 * wag
    td = (-math.cos(ph), math.sin(ph))
    tp = (math.sin(ph), math.cos(ph))
    B0 = W(-6.0, 0.4)
    Lt = tail_l * 0.95 * s
    E = (B0[0] + td[0] * Lt, B0[1] + td[1] * Lt)
    tail = [(B0[0] - tp[0] * 1.9 * s, B0[1] - tp[1] * 1.9 * s)]
    for f in (-1.0, -0.5, 0.0, 0.5, 1.0):
        w = 2.7 * s * f
        back = 1.0 * s * abs(f) ** 1.6
        tail.append((E[0] + tp[0] * w + td[0] * -back, E[1] + tp[1] * w + td[1] * -back))
    tail.append((B0[0] + tp[0] * 1.9 * s, B0[1] + tp[1] * 1.9 * s))

    wing = [W(a, b) for a, b in [(4.5, -1.2), (2.0, -4.2), (-2.5, -4.6), (-7.5, -3.2),
                                  (-13.0, 0.8), (-8.0, 2.0), (-2.0, 2.6), (3.0, 1.8)]]

    def mk(pts):
        mm = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
        ImageDraw.Draw(mm).polygon(pts, fill=255)
        return mm.load()

    legw = max(1, round(0.8 * s))
    legs = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
    ld = ImageDraw.Draw(legs)
    for lx in (C[0] + 0.3 * s, C[0] + 2.8 * s):
        ld.rectangle([lx, C[1] + 3.2 * s, lx + legw - 1, fy + 0.6], fill=255)
    legs = legs.load()

    parts = [("legs", legs), ("tail", mk(tail)), ("body", mk(body)), ("wing", mk(wing)),
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
        b = (dx * m[0] + dy * m[1]) / s
        top = max(0.0, min(1.0, (C[1] + 4.6 * s - y) / (9.2 * s)))     # 1 at the back
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
        else:                                                           # folded wing
            f = int((b + 6) * 0.9) % 2
            ramp = [DEEP, BASE if f else DEEP, MID, _scale(sheen, 0.5 + 0.4 * sheen_k)]
            col = _pick(ramp, 0.2 + 0.5 * max(0.0, -b / 5.0) * (0.5 + 0.5 * sheen_k), x, y)
            for n_ in range(streak_bands):
                if abs(a - (1.0 - 3.0 * n_)) < 0.5 and b < 0.5:
                    col = sheen_hi
        out[x, y] = col + (255,)

    # a little puff of feathers hides the cut between the two views
    if k < 2 or k >= R - 1:
        pp = (k + 1) / 2.0 if k < 2 else 1.0
        for i in range(6):
            ang = 2 * math.pi * i / 6 + 0.4
            r = (4 + 4 * pp) * s
            px_, py_ = int(C[0] + r * math.cos(ang)), int(C[1] + 3 * s + r * math.sin(ang) * 0.6)
            if 0 <= px_ < LOGICAL_W and 0 <= py_ < LOGICAL_H:
                out[px_, py_] = MID + (255,)

    return _finish(img, spec, k)


def render_frame(spec: RavenSpec, k: int) -> Image.Image:
    n = spec.frames
    s, wing_l, tail_l, sheen_k = _stage_params(spec.stage)
    st = _state(spec, k)
    if k % n < _rest_frames(spec):
        return _perched_frame(spec, k % n, st)
    cx, cy, h, phi = st["x"], st["y"], st["h"], st["phi"]
    fold, yaw, blink = st["fold"], st["yaw"], st["blink"]
    ch, sh = math.cos(h), math.sin(h)

    def to_world(pts):
        return [(cx + u * ch - v * sh, cy + u * sh + v * ch) for u, v in pts]

    def mask(pts):
        m = Image.new("L", (LOGICAL_W, LOGICAL_H), 0)
        ImageDraw.Draw(m).polygon(to_world(pts), fill=255)
        return m

    def ellipse(a, b, u0=0.0, v0=0.0, steps=28):
        return [(u0 + a * math.cos(2 * math.pi * i / steps),
                 v0 + b * math.sin(2 * math.pi * i / steps))
                for i in range(steps)]

    # ---- geometry in local (u forward, v right) units, scaled by s ----
    def head_rot(pts):                      # turn the head about the neck
        cyw, syw = math.cos(yaw), math.sin(yaw)
        return [(6.0 + (u - 6.0) * cyw - v * syw, (u - 6.0) * syw + v * cyw) for u, v in pts]

    body = mask([(u * s, v * s) for u, v in ellipse(7.2, 3.6, 0.0)])
    head = mask([(u * s, v * s) for u, v in head_rot(ellipse(3.3, 3.0, 8.0))])
    beak = mask([(u * s, v * s) for u, v in head_rot(
                 [(10.0, -1.7), (13.0, -1.0), (15.2, -0.25), (15.2, 0.25), (13.0, 1.0), (10.0, 1.7)])])
    eyes = head_rot([(8.9, -1.25), (8.9, 1.25)])

    wag = st["wag"]
    tl = tail_l
    tail_pts = [(-4.0, -1.8)]
    feathers = 5
    for i in range(feathers + 1):          # scalloped, wedge-shaped end
        f = -1.0 + 2.0 * i / feathers
        reach = tl - 1.3 * abs(f) ** 2.2
        tail_pts.append((-4.0 - reach, f * (2.4 + 2.6 * (tl / 15.0)) + wag * tl * 0.6))
        if i < feathers:
            f2 = -1.0 + 2.0 * (i + 0.5) / feathers
            tail_pts.append((-4.0 - reach + 1.3, f2 * (2.4 + 2.6 * (tl / 15.0)) + wag * tl * 0.6))
    tail_pts.append((-4.0, 1.8))
    tail = mask([(u * s, v * s) for u, v in tail_pts])

    wings = {}
    cphi = math.cos(phi)
    sweep = math.sin(phi) * 4.5
    for sgn in (-1, 1):
        L = max(4.8, wing_l * cphi * (1 - 0.8 * fold))
        tip_u = (-1.5 - sweep) * (1 - fold) + (-8.5) * fold          # tuck back when perched
        lead, trail = [], []
        steps = 9
        for i in range(steps + 1):
            t = i / steps
            lead.append((4.2 - (4.2 - tip_u) * t ** 1.5, 2.4 + (L - 2.4) * t))
        tn = 14
        for i in range(tn + 1):
            t = i / tn                      # 0 = tip, 1 = body
            v = L - (L - 3.2 * cphi) * t
            u = (tip_u - 2.6) + (-6.2 - (tip_u - 2.6)) * t ** 0.9
            if t < 0.78 and i % 2 == 1:     # primaries: scalloped notches
                u += 1.7
            trail.append((u, v))
        pts = lead + trail
        wings[sgn] = mask([(u * s, v * sgn * s) for u, v in pts]), L

    if fold > 0.5:                          # folded wings lie over the body
        layers = [("tail", tail), ("body", body), ("wing-1", wings[-1][0]),
                  ("wing1", wings[1][0]), ("head", head), ("beak", beak)]
    else:
        layers = [("tail", tail), ("wing-1", wings[-1][0]), ("wing1", wings[1][0]),
                  ("body", body), ("head", head), ("beak", beak)]

    owner = [[None] * LOGICAL_W for _ in range(LOGICAL_H)]
    for name, m in layers:
        px = m.load()
        for y in range(LOGICAL_H):
            for x in range(LOGICAL_W):
                if px[x, y]:
                    owner[y][x] = name

    sheen_hi_a = _mix(spec.sheen_a, (255, 255, 255), 0.25)
    sheen_hi_b = _mix(spec.sheen_b, (255, 255, 255), 0.25)

    img = Image.new("RGBA", (LOGICAL_W, LOGICAL_H), (0, 0, 0, 0))
    out = img.load()
    _draw_branch(out)

    def local(x, y):
        dx, dy = x + 0.5 - cx, y + 0.5 - cy
        return (dx * ch + dy * sh) / s, (-dx * sh + dy * ch) / s

    streak_bands = min(4, spec.streak // 7)

    for y in range(LOGICAL_H):
        for x in range(LOGICAL_W):
            name = owner[y][x]
            if name is None:
                continue
            u, v = local(x, y)
            light = 0.5 + 0.5 * max(-1.0, min(1.0, (-(x - cx) - (y - cy)) / (14 * s)))
            if name == "beak":
                col = BEAK_HI if abs(v) < 0.35 else BEAK
            elif name in ("body", "head"):
                sheen = _mix(spec.sheen_a, spec.sheen_b, 0.5 + 0.5 * math.sin(u * 0.5))
                ramp = [DEEP, BASE, MID, _scale(sheen, 0.55 + 0.3 * sheen_k)]
                t = light * (0.55 + 0.45 * sheen_k) if name == "body" else light * 0.8
                col = _pick(ramp, t, x, y)
                if name == "head":
                    # eyes
                    if not blink and any(abs(v - ev) < 0.45 and abs(u - eu) < 0.6
                                         for eu, ev in eyes):
                        col = EYE
            elif name == "tail":
                t_along = min(1.0, max(0.0, (-u - 5.0) / tail_l))
                stripe = int((v + 6.0) * 1.2) % 2
                sheen = _mix(spec.sheen_a, spec.sheen_b, t_along)
                ramp = [DEEP, BASE if stripe else DEEP, MID,
                        _scale(sheen, 0.5 + 0.35 * sheen_k)]
                col = _pick(ramp, light * 0.5 + t_along * 0.35 * sheen_k, x, y)
            else:
                sgn = -1 if name == "wing-1" else 1
                L = wings[sgn][1]
                av = abs(v)
                chord = (u + 6.0) / 14.0           # 0 trailing .. 1 leading
                f = int(av * 0.9) % 2              # feather banding
                w = max(0.0, min(1.0, chord))
                sheen = _mix(spec.sheen_a, spec.sheen_b, min(1.0, av / max(L, 1)))
                ramp = [DEEP, BASE if f else DEEP, MID,
                        _scale(sheen, 0.5 + 0.4 * sheen_k)]
                lit = max(0.0, min(1.0, light + 0.2 * phi))    # upstroke catches the light
                col = _pick(ramp, lit * 0.35 + w * 0.55 * (0.5 + 0.5 * sheen_k), x, y)
                for b in range(streak_bands):
                    if abs(av - (5.0 + 3.0 * b)) < 0.55 and w > 0.35:
                        col = sheen_hi_a if b % 2 == 0 else sheen_hi_b
            out[x, y] = col + (255,)

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
