"""Pixel-art raven renderer.

Draws a top-down raven on a 96x96 logical grid (upscaled 4x to 384x384),
with dithered shading, a thin outline and a transparent background.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

LOGICAL = 96
SCALE = 4
FRAMES = 120
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


def _path(spec: RavenSpec, k: int, n: int):
    """Position (x, y) and heading at frame k of n on a closed loop."""
    y = spec.yesterday
    if y <= 0:
        amp_x, amp_y = 14.0, 9.0
    elif y < 4:
        amp_x, amp_y = 20.0, 12.0
    else:
        amp_x, amp_y = 26.0, 15.0

    _, wing_l, _, _ = _stage_params(spec.stage)
    reach = wing_l * _stage_params(spec.stage)[0] + 3          # keep wings in frame
    amp_x = max(4.0, min(amp_x, LOGICAL / 2 - reach))
    amp_y = max(4.0, min(amp_y, LOGICAL / 2 - reach))

    def pos(t):
        a = 2 * math.pi * t / n
        return (LOGICAL / 2 + amp_x * math.sin(a),
                LOGICAL / 2 + amp_y * math.sin(2 * a) * 0.9)

    x0, y0 = pos(k)
    x1, y1 = pos(k + 0.25)
    return x0, y0, math.atan2(y1 - y0, x1 - x0)


def _flap(spec: RavenSpec, k: int, n: int) -> float:
    y = spec.yesterday
    if y <= 0:
        return 0.12 * math.sin(2 * math.pi * 2 * k / n)   # glide
    flaps = 6 if y < 4 else 9 if y < 10 else 12
    return 0.95 * math.sin(2 * math.pi * flaps * k / n)


def render_frame(spec: RavenSpec, k: int) -> Image.Image:
    n = spec.frames
    s, wing_l, tail_l, sheen_k = _stage_params(spec.stage)
    cx, cy, h = _path(spec, k, n)
    phi = _flap(spec, k, n)
    ch, sh = math.cos(h), math.sin(h)

    def to_world(pts):
        return [(cx + u * ch - v * sh, cy + u * sh + v * ch) for u, v in pts]

    def mask(pts):
        m = Image.new("L", (LOGICAL, LOGICAL), 0)
        ImageDraw.Draw(m).polygon(to_world(pts), fill=255)
        return m

    def ellipse(a, b, u0=0.0, v0=0.0, steps=28):
        return [(u0 + a * math.cos(2 * math.pi * i / steps),
                 v0 + b * math.sin(2 * math.pi * i / steps))
                for i in range(steps)]

    # ---- geometry in local (u forward, v right) units, scaled by s ----
    body = mask([(u * s, v * s) for u, v in ellipse(7.2, 3.6, 0.0)])
    head = mask([(u * s, v * s) for u, v in ellipse(3.3, 3.0, 8.0)])
    beak = mask([(u * s, v * s) for u, v in
                 [(10.0, -1.7), (13.0, -1.0), (15.2, -0.25), (15.2, 0.25), (13.0, 1.0), (10.0, 1.7)]])

    wag = 0.18 * math.sin(2 * math.pi * 3 * k / n)
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
        L = wing_l * cphi
        tip_u = -1.5 - sweep
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

    layers = [("tail", tail), ("wing-1", wings[-1][0]), ("wing1", wings[1][0]),
              ("body", body), ("head", head), ("beak", beak)]

    owner = [[None] * LOGICAL for _ in range(LOGICAL)]
    for name, m in layers:
        px = m.load()
        for y in range(LOGICAL):
            for x in range(LOGICAL):
                if px[x, y]:
                    owner[y][x] = name

    sheen_hi_a = _mix(spec.sheen_a, (255, 255, 255), 0.25)
    sheen_hi_b = _mix(spec.sheen_b, (255, 255, 255), 0.25)

    img = Image.new("RGBA", (LOGICAL, LOGICAL), (0, 0, 0, 0))
    out = img.load()

    def local(x, y):
        dx, dy = x + 0.5 - cx, y + 0.5 - cy
        return (dx * ch + dy * sh) / s, (-dx * sh + dy * ch) / s

    streak_bands = min(4, spec.streak // 7)

    for y in range(LOGICAL):
        for x in range(LOGICAL):
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
                    if abs(abs(v) - 1.25) < 0.45 and abs(u - 8.9) < 0.6:
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
                col = _pick(ramp, light * 0.35 + w * 0.55 * (0.5 + 0.5 * sheen_k), x, y)
                for b in range(streak_bands):
                    if abs(av - (5.0 + 3.0 * b)) < 0.55 and w > 0.35:
                        col = sheen_hi_a if b % 2 == 0 else sheen_hi_b
            out[x, y] = col + (255,)

    # outline: opaque pixels neighbouring transparent ones get a thin rim
    rim = []
    for y in range(LOGICAL):
        for x in range(LOGICAL):
            if out[x, y][3] == 0:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < LOGICAL and 0 <= ny < LOGICAL and out[nx, ny][3]:
                        rim.append((x, y))
                        break
    for x, y in rim:
        out[x, y] = OUTLINE + (255,)

    # sparkles: tiny twinkling "shiny things" for starred repos
    for i in range(min(spec.sparkles, 8)):
        a = 2 * math.pi * i / 8 + 0.7
        sx = int(LOGICAL / 2 + LOGICAL * 0.4 * math.cos(a))
        sy = int(LOGICAL / 2 + LOGICAL * 0.4 * math.sin(a))
        phase = (k / n * 4 + i / 8.0) % 1.0
        if phase < 0.5:
            for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)) if phase < 0.25 else ((0, 0),):
                if 0 <= sx + dx < LOGICAL and 0 <= sy + dy < LOGICAL:
                    out[sx + dx, sy + dy] = (255, 226, 140, 255)

    return img.resize((LOGICAL * SCALE, LOGICAL * SCALE), Image.NEAREST)


def render_gif(spec: RavenSpec, path: str) -> None:
    frames = [render_frame(spec, k) for k in range(spec.frames)]
    first, rest = frames[0], frames[1:]
    first.save(path, save_all=True, append_images=rest, duration=FRAME_MS,
               loop=0, disposal=2, optimize=False, transparency=0)
