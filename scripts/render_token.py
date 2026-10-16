"""Render assets/id_token.svg: profile claims styled as a decoded, unsigned ID token.

The signature strip shows daily GitHub contributions for the current year,
split into personal-repo commits and everything else, with a 7-day average.

Data comes from the GitHub API through the `gh` CLI (GH_TOKEN in Actions).
Only totals are computed and nothing about individual repos is printed,
because this repo's workflow logs are public.

Usage: python3 scripts/render_token.py [--login pbuffolino]
"""
import argparse, base64, datetime as dt, html, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "id_token.svg")
E = html.escape
SANS = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"
BG, PANEL, INK, MUTE = "#101b33", "#17264a", "#dce4f5", "#8391b5"
CH, CP, CS, CN = "#ff8a7a", "#c3a6ff", "#5fd4c4", "#f3c77b"
NS = "https://seeknay.com/claims"

STATIC_CLAIMS = {
    "title": "Workforce Identity Architect",
    "writes_about": ["identity", "security", "powershell"],
    "builds_with": ["claude-code", "codex", "cursor"],
    "favorite_ade": "orca",
}
OFF_DUTY = {
    "brews": ["espresso", "pour-over"],
    "fuel": "pizza",
    "trek": ["tng", "voy", "ds9", "pic", "tos", "dis"],
    "playing": "diablo iv",
    "legacy": ["tfc", "cs", "d2", "l2", "wow", "ow"],
}


def gh(*args):
    """Run gh and return parsed JSON; stderr is suppressed so repo names never reach public logs."""
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed (exit {r.returncode})")
    return json.loads(r.stdout) if r.stdout.strip() else None


def gh_lines(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def fetch(login, jan1, today):
    q = """query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){
      contributionsCollection(from:$from,to:$to){contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""
    data = gh("api", "graphql", "-f", f"query={q}", "-F", f"login={login}",
              "-F", f"from={jan1}T00:00:00Z", "-F", f"to={today}T23:59:59Z")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    cal = {dt.date.fromisoformat(d["date"]): d["contributionCount"] for w in weeks for d in w["contributionDays"]}

    repos = gh_lines("api", "user/repos?affiliation=owner&per_page=100", "--paginate",
                     "--jq", ".[] | select(.fork | not) | .full_name")
    if not repos:
        raise RuntimeError("no personal repos visible; the token needs read access to your repositories")
    personal, lang_bytes = {}, {}
    for r in repos:
        for d in gh_lines("api", f"repos/{r}/commits?author={login}&since={jan1}T00:00:00Z&per_page=100",
                          "--paginate", "--jq", ".[].commit.author.date[:10]"):
            k = dt.date.fromisoformat(d)
            personal[k] = personal.get(k, 0) + 1
        try:
            for lang, b in (gh("api", f"repos/{r}/languages") or {}).items():
                lang_bytes[lang] = lang_bytes.get(lang, 0) + b
        except RuntimeError:
            pass
    langs = [l.lower() for l, _ in sorted(lang_bytes.items(), key=lambda kv: -kv[1])[:3]]
    return cal, personal, langs, len(repos)


def T(x, y, s, size=13, fill=INK, w=400, font=SANS, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{w}" fill="{fill}" '
            f'text-anchor="{anchor}">{E(str(s))}</text>')


def render(login, now, cal, personal, langs):
    jan1, today = dt.date(now.year, 1, 1), now.date()
    days = [jan1 + dt.timedelta(n) for n in range((today - jan1).days + 1)]
    daily = [cal.get(d, 0) for d in days]
    iat = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
    exp = int(dt.datetime(now.year + 1, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    header = {"alg": "none", "typ": "JWT"}
    payload = {"iss": f"https://github.com/{login}", "sub": login, "aud": "github-profile-readme",
               "iat": iat, "exp": exp, "amr": ["mfa", "hwk"], "name": "Pat Buffolino",
               NS: {**STATIC_CLAIMS, "repo_languages": langs, "off_duty": OFF_DUTY}}
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).decode().rstrip("=")
    h, p = b64(header), b64(payload)  # unsecured JWT: empty signature, trailing dot (RFC 7519 §6.1)

    W, o = 840, []
    o += [T(32, 44, "id_token", 16, INK, 600), T(W - 32, 44, login, 12, MUTE, 400, MONO, "end")]
    y = 66
    flat = [(c, col) for t, col in ((h, CH), (".", INK), (p, CP), (".", INK)) for c in t]
    lines = [flat[j:j + 104] for j in range(0, len(flat), 104)]
    o.append(f'<rect x="24" y="{y-8}" width="{W-48}" height="{20 + 16*len(lines)}" rx="8" fill="{PANEL}"/>')
    for i, ln in enumerate(lines):
        spans, run, rc = [], "", None
        for ch, c in ln:
            if c != rc and run:
                spans.append((run, rc)); run = ""
            run += ch; rc = c
        spans.append((run, rc))
        o.append(f'<text x="40" y="{y+14+i*16}" font-family="{MONO}" font-size="11.5" xml:space="preserve">'
                 + "".join(f'<tspan fill="{c}">{E(r)}</tspan>' for r, c in spans) + "</text>")
    y += 20 + 16 * len(lines) + 30

    def jl(obj, kc, ind=1):
        out, it = [], list(obj.items())
        for i, (k, v) in enumerate(it):
            c, pad = ("," if i < len(it) - 1 else ""), "  " * ind
            if isinstance(v, dict):
                out.append([(pad, INK), (f'"{k}"', kc), (": {", INK)])
                out += jl(v, kc, ind + 1)
                out.append([(pad + "}", INK), (c, INK)])
                continue
            if isinstance(v, list): val, vc = "[" + ", ".join(f'"{x}"' for x in v) + "]", INK
            elif isinstance(v, int): val, vc = str(v), CN
            else: val, vc = f'"{v}"', INK
            out.append([(pad, INK), (f'"{k}"', kc), (": ", INK), (val, vc), (c, INK)])
        return out

    def draw(x, y0, ls):
        for i, ln in enumerate(ls):
            o.append(f'<text x="{x}" y="{y0+i*17}" font-family="{MONO}" font-size="12" xml:space="preserve">'
                     + "".join(f'<tspan fill="{c}">{E(r)}</tspan>' for r, c in ln) + "</text>")

    o.append(T(32, y, "Header", 12.5, CH))
    draw(32, y + 22, [[("{", INK)]] + jl(header, CH) + [[("}", INK)]])
    o.append(T(400, y, "What this is", 12.5, MUTE))
    for i, ln in enumerate(["Styled like an ID token: the note a sign-in system",
                            "hands an app to say who you are. Unsigned on",
                            "purpose. It’s a README, not a credential."]):
        o.append(T(400, y + 22 + i * 18, ln, 13, INK))
    y += 22 + 4 * 17 + 12
    o.append(T(32, y, "Payload", 12.5, CP))
    pl = [[("{", INK)]] + jl(payload, CP) + [[("}", INK)]]
    cut = next(i for i, ln in enumerate(pl) if any(r == f'"{NS}"' for r, _ in ln))
    draw(32, y + 22, pl[:cut]); draw(400, y + 22 + 17, pl[cut:])
    y += 22 + max(cut, len(pl) - cut + 1) * 17 + 14

    o.append(T(32, y, "Signature", 12.5, CS))
    o.append(T(110, y, "empty, as alg: none requires", 12, MUTE))
    o.append(T(W - 32, y, f"{sum(daily):,} GitHub contributions, all repos, {now.year}", 12, MUTE, anchor="end"))
    sh = 64
    base, mx, bw = y + 22 + sh, max(max(daily), 1), (W - 64) / len(daily)
    pers = [min(personal.get(d, 0), v) for d, v in zip(days, daily)]
    for i, v in enumerate(daily):
        if not v:
            continue
        hh = max(2, sh * v / mx)
        ph = hh * pers[i] / v
        x, wb = 32 + i * bw, max(bw * 0.72, 1)
        if hh - ph > 0:
            o.append(f'<rect x="{x:.1f}" y="{base-hh+ph:.1f}" width="{wb:.2f}" height="{hh-ph:.1f}" fill="{CS}" opacity=".75"/>')
        if ph > 0:
            o.append(f'<rect x="{x:.1f}" y="{base-hh:.1f}" width="{wb:.2f}" height="{max(ph, 1.5):.1f}" fill="{CP}"/>')
    avg = [sum(daily[max(0, i - 6):i + 1]) / len(daily[max(0, i - 6):i + 1]) for i in range(len(daily))]
    pts = [(32 + i * bw + bw * 0.36, base - sh * a / mx) for i, a in enumerate(avg)]
    d_ = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for (x0, y0_), (x1, y1_) in zip(pts, pts[1:]):
        d_ += f" Q{x0:.1f},{y0_:.1f} {(x0 + x1) / 2:.1f},{(y0_ + y1_) / 2:.1f}"
    o.append(f'<path d="{d_}" fill="none" stroke="{INK}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>')
    o.append(f'<rect x="32" y="{base}" width="{W-64}" height="1" fill="{MUTE}" opacity=".35"/>')
    for m in range(1, today.month + 1):
        x = 32 + (dt.date(now.year, m, 1) - jan1).days * bw
        o.append(f'<rect x="{x:.1f}" y="{base}" width="1" height="4" fill="{MUTE}" opacity=".6"/>')
        o.append(T(x + 3, base + 15, dt.date(now.year, m, 1).strftime("%b"), 10.5, MUTE))
    if max(daily):
        pk = max(range(len(daily)), key=daily.__getitem__)
        px = 32 + pk * bw
        o.append(f'<rect x="{px:.1f}" y="{base-sh-2}" width="1" height="2" fill="{INK}"/>')
        o.append(T(px - 6, base - sh + 2, f"peak: {daily[pk]} on {days[pk]:%b} {days[pk].day}", 11, INK, 500, SANS, "end"))
    lg = base + 30
    for lx, col, op, lab in ((32, CS, ".75", "work & other"), (132, CP, "1", "personal projects")):
        o.append(f'<rect x="{lx}" y="{lg-8}" width="9" height="9" rx="2" fill="{col}" opacity="{op}"/>')
        o.append(T(lx + 14, lg, lab, 11, MUTE))
    o.append(f'<path d="M262,{lg-4} q8,-6 16,0 t16,0" fill="none" stroke="{INK}" stroke-width="1.6" stroke-linecap="round"/>')
    o.append(T(300, lg, "7-day average", 11, MUTE))
    H = lg + 22
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="Profile summary styled as a decoded ID token">'
            f'<rect width="{W}" height="{H}" rx="14" fill="{BG}"/>' + "".join(o) + "</svg>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default="pbuffolino")
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    jan1, today = dt.date(now.year, 1, 1), now.date()
    cal, personal, langs, n_repos = fetch(a.login, jan1, today)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(render(a.login, now, cal, personal, langs))
    print(f"rendered: {sum(cal.values())} contributions, {sum(personal.values())} personal commits, "
          f"{n_repos} personal repos, top languages {langs}")


if __name__ == "__main__":
    sys.exit(main())
