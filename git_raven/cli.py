from __future__ import annotations

import argparse

from .render import RavenSpec, render_gif, stage_for


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="git-raven",
                                description="Draw a pixel-art raven from GitHub activity.")
    p.add_argument("user", nargs="?", help="GitHub username (omit with --demo)")
    p.add_argument("-o", "--output", default="raven.gif")
    p.add_argument("--demo", action="store_true", help="skip the API, use sample data")
    p.add_argument("--stage", type=int, choices=range(6), help="override growth stage")
    p.add_argument("--streak", type=int)
    p.add_argument("--yesterday", type=int)
    args = p.parse_args(argv)

    if args.demo:
        d = {"total": 5000, "streak": 21, "yesterday": 6, "stars": 12,
             "languages": ["C#", "Python"], "sheen": [(104, 33, 122), (53, 114, 165)]}
    else:
        if not args.user:
            p.error("a username is required unless --demo is used")
        from .data import fetch
        d = fetch(args.user)

    spec = RavenSpec(
        stage=args.stage if args.stage is not None else stage_for(d["total"]),
        sheen_a=tuple(d["sheen"][0]), sheen_b=tuple(d["sheen"][1]),
        streak=args.streak if args.streak is not None else d["streak"],
        yesterday=args.yesterday if args.yesterday is not None else d["yesterday"],
        sparkles=min(8, d["stars"] // 3),
    )
    render_gif(spec, args.output)
    print(f"stage {spec.stage} raven -> {args.output} "
          f"({d['total']} contributions, {spec.streak}-day streak, "
          f"{', '.join(d['languages']) or 'no languages'})")


if __name__ == "__main__":
    main()
