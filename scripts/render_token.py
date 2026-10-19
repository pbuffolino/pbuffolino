"""Render assets/id_token.svg: profile claims styled as a decoded, unsigned ID token.

The signature strip shows daily GitHub contributions for the current year,
with a 7-day average. Private activity is included only after verification.

Contribution counts come from the public GitHub profile calendar. The authenticated
API (GH_TOKEN in Actions) verifies private access and supplies language aggregates.
Only totals are computed and nothing about individual repos is printed,
because this repo's workflow logs are public.

Usage: python3 scripts/render_token.py [--login pbuffolino]
"""
import argparse
import base64
import datetime as dt
import html
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "id_token.svg")
E = html.escape
SANS = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"
BG, PANEL, INK, MUTE = "#101b33", "#17264a", "#dce4f5", "#8391b5"
CH, CP, CS, CN = "#ff8a7a", "#c3a6ff", "#5fd4c4", "#f3c77b"
NS = "https://seeknay.com/claims"
MAX_RESPONSE_BYTES = 2_000_000
MAX_SVG_BYTES = 80_000
COUNT_TEXT = r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)"
SVG_TAGS = {"svg", "metadata", "rect", "text", "tspan", "path"}
SVG_ATTRS = {"width", "height", "viewBox", "role", "aria-label", "x", "y", "rx",
             "fill", "opacity", "font-family", "font-size", "font-weight", "text-anchor",
             "d", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
             "{http://www.w3.org/XML/1998/namespace}space"}

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


class RefreshError(RuntimeError):
    """A safe, actionable error that may appear in public workflow logs."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the data credential to a redirect destination.
        raise RefreshError("Unexpected GitHub API redirect; retry after checking repository access.")


class ProfileCalendar(HTMLParser):
    """Read only dates, tooltip counts, and the public calendar's total heading."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells, self.tooltips, self.headings = {}, {}, []
        self.active_tip = None
        self.heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs:
            key = attrs.get("id")
            if not key or key in self.cells:
                raise RefreshError("Public contribution calendar contains duplicate or invalid cells.")
            self.cells[key] = dt.date.fromisoformat(attrs["data-date"])
        if tag == "tool-tip" and attrs.get("for", "").startswith("contribution-day-component-"):
            key = attrs["for"]
            if key in self.tooltips:
                raise RefreshError("Public contribution calendar contains duplicate counts.")
            self.active_tip = key
            self.tooltips[key] = ""
        if tag == "h2" and attrs.get("id") == "js-contribution-activity-description":
            self.heading = ""

    def handle_data(self, data):
        if self.active_tip is not None:
            self.tooltips[self.active_tip] += data
        if self.heading is not None:
            self.heading += data

    def handle_endtag(self, tag):
        if tag == "tool-tip":
            self.active_tip = None
        if tag == "h2" and self.heading is not None:
            self.headings.append(" ".join(self.heading.split()))
            self.heading = None


def profile_calendar(document, start, end):
    parser = ProfileCalendar()
    parser.feed(document)
    parser.close()
    expected_heading = rf"({COUNT_TEXT}) contributions? in {start.year}"
    match = re.fullmatch(expected_heading, parser.headings[0]) if len(parser.headings) == 1 else None
    if not match or start.year != end.year or start != dt.date(start.year, 1, 1):
        raise RefreshError("Public contribution calendar heading is missing or changed.")
    total = int(match[1].replace(",", ""))
    days = {}
    for key, date in parser.cells.items():
        tooltip = parser.tooltips.get(key, "").strip()
        count_match = re.fullmatch(
            rf"(No|{COUNT_TEXT}) contributions? on [A-Za-z]+ [0-9]+(?:st|nd|rd|th)\.", tooltip)
        if not count_match or date in days or date.year != start.year:
            raise RefreshError("Public contribution calendar dates or counts are incomplete.")
        days[date] = 0 if count_match[1] == "No" else int(count_match[1].replace(",", ""))
    year_end = dt.date(start.year, 12, 31)
    if (len(days) != (year_end - start).days + 1 or sum(days.values()) != total
            or any(value for date, value in days.items() if date > end)):
        raise RefreshError("Public contribution totals or date coverage are incomplete; no card was published.")
    return {date: value for date, value in days.items() if date <= end}


def fetch_profile_calendar(login, start, end):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
        raise RefreshError("Invalid GitHub login.")
    # Public aggregates only. This opener never receives the private-data token.
    request = urllib.request.Request(
        f"https://github.com/users/{login}/contributions?from={start}&to={end}",
        headers={"Accept": "text/html", "Accept-Language": "en-US", "User-Agent": "weekly-profile-card"})
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in range(3):
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise RefreshError("Public contribution calendar exceeded its safety limit.")
                document = raw.decode("utf-8")
            return profile_calendar(document, start, end)
        except urllib.error.HTTPError as error:
            status = error.code
            retry = status in (429, 500, 502, 503, 504)
            error.close()
            if not retry or attempt == 2:
                raise RefreshError(f"Public contribution calendar failed (HTTP {status}); no card was published.") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if attempt == 2:
                raise RefreshError("Public contribution calendar connection failed; retry later.") from None
        except UnicodeError:
            raise RefreshError("Public contribution calendar encoding is invalid.") from None
        time.sleep(2 ** attempt)


class GitHub:
    def __init__(self, token):
        if not token:
            raise RefreshError("PROFILE_TOKEN is missing; configure the Actions secret before refreshing.")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def api(self, path, payload=None):
        """Bound retries; never expose URLs, response bodies, or credentials on failure."""
        if not isinstance(path, str) or not re.fullmatch(
                r"graphql|user/repos\?affiliation=owner&per_page=100&page=[1-9][0-9]{0,3}"
                r"|repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/languages", path):
            raise RefreshError("Unexpected GitHub API endpoint; no request was sent.")
        if (path == "graphql") != (payload is not None):
            raise RefreshError("Unexpected GitHub API request method; no request was sent.")
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            "https://api.github.com/" + path, data=body,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "User-Agent": "weekly-profile-card"},
        )
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=20) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise RefreshError("GitHub response exceeded its safety limit.")
                    result = json.loads(raw)
                if isinstance(result, dict) and result.get("errors"):
                    raise RefreshError("GitHub returned GraphQL errors; no card was published.")
                return result
            except urllib.error.HTTPError as error:
                status = error.code
                retry = status in (429, 500, 502, 503, 504) or (
                    status == 403 and (error.headers.get("X-RateLimit-Remaining") == "0"
                                       or error.headers.get("Retry-After") is not None))
                error.close()
                if not retry or attempt == 2:
                    raise RefreshError(f"GitHub request failed (HTTP {status}); check credential access or API status.") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                if attempt == 2:
                    raise RefreshError("GitHub connection failed after three attempts; retry later.") from None
            except (ValueError, UnicodeError):
                raise RefreshError("GitHub returned invalid JSON; no card was published.") from None
            time.sleep(2 ** attempt)

    def collection(self, login, start, end):
        query = """query($from:DateTime!,$to:DateTime!){viewer{
          login repositories(privacy:PRIVATE,affiliations:OWNER,isFork:false,first:1){totalCount}
          contributionsCollection(from:$from,to:$to){
            totalCommitContributions totalIssueContributions totalPullRequestContributions
            totalPullRequestReviewContributions totalRepositoryContributions restrictedContributionsCount
            contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}
            commitContributionsByRepository(maxRepositories:100){
              repository{isPrivate} contributions{totalCount}}
          }}}"""
        data = self.api("graphql", {"query": query, "variables": {
            "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"}})
        viewer = data["data"]["viewer"]
        if viewer["login"].lower() != login.lower():
            raise RefreshError("PROFILE_TOKEN must belong to the profile owner.")
        return viewer


def count(value):
    if type(value) is not int or value < 0:
        raise RefreshError("GitHub returned an invalid aggregate count.")
    return value


def calendar(collection, start, end):
    cal = {}
    for week in collection["contributionCalendar"]["weeks"]:
        for day in week["contributionDays"]:
            date = dt.date.fromisoformat(day["date"])
            if date in cal or not start <= date <= end:
                raise RefreshError("GitHub returned duplicate or out-of-range contribution dates.")
            cal[date] = count(day["contributionCount"])
    expected = (end - start).days + 1
    total = count(collection["contributionCalendar"]["totalContributions"])
    fields = ("totalCommitContributions", "totalIssueContributions", "totalPullRequestContributions",
              "totalPullRequestReviewContributions", "totalRepositoryContributions", "restrictedContributionsCount")
    if len(cal) != expected or sum(cal.values()) != total or sum(count(collection[f]) for f in fields) != total:
        raise RefreshError("GitHub contribution totals or date coverage are incomplete; no card was published.")
    return cal


def private_evidence(collection):
    # Restricted counts are anonymized private activity, already included in the calendar.
    return count(collection["restrictedContributionsCount"]) + sum(
        count(item["contributions"]["totalCount"])
        for item in collection["commitContributionsByRepository"] if item["repository"]["isPrivate"])


def fetch(api, login, jan1, today):
    viewer = api.collection(login, jan1, today)
    collection = viewer["contributionsCollection"]
    api_cal = calendar(collection, jan1, today)
    private_count = count(viewer["repositories"]["totalCount"])
    if not private_count:
        raise RefreshError("No owned private repositories are visible; check PROFILE_TOKEN access.")
    if not private_evidence(collection):
        # A new year may have zero contributions. Verify visibility using last year.
        start, end = dt.date(jan1.year - 1, 1, 1), jan1 - dt.timedelta(days=1)
        previous = api.collection(login, start, end)["contributionsCollection"]
        calendar(previous, start, end)
        if not private_evidence(previous):
            raise RefreshError("Private contribution visibility could not be verified; no card was published.")

    cal = fetch_profile_calendar(login, jan1, today)
    # Some private activity in GraphQL does not establish full profile coverage.
    # Never add these overlapping totals or substitute the smaller API calendar.
    if sum(cal.values()) < sum(api_cal.values()):
        raise RefreshError("Public contribution calendar is behind the API; retry later. No card was published.")

    repos = []
    for page in range(1, 1001):
        batch = api.api(f"user/repos?affiliation=owner&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise RefreshError("GitHub returned invalid repository data.")
        repos.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise RefreshError("Repository pagination exceeded its safety limit.")
    names = [repo["full_name"] for repo in repos]
    if len(names) != len(set(names)) or sum(repo["private"] and not repo["fork"] for repo in repos) != private_count:
        raise RefreshError("Private repository coverage is incomplete; no card was published.")
    lang_bytes = {}
    for repo in repos:
        name = repo["full_name"]
        if (not isinstance(repo["private"], bool) or not isinstance(repo["fork"], bool)
                or not isinstance(name, str) or name.split("/")[0].lower() != login.lower()
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)):
            raise RefreshError("GitHub returned invalid repository metadata.")
        if repo["fork"]:
            continue
        languages = api.api(f"repos/{name}/languages")
        if not isinstance(languages, dict):
            raise RefreshError("GitHub returned invalid language data.")
        for language, size in languages.items():
            if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 +#._-]{0,63}", language):
                raise RefreshError("GitHub returned an invalid language name.")
            lang_bytes[language] = lang_bytes.get(language, 0) + count(size)
    langs = [language.lower() for language, _ in sorted(lang_bytes.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return cal, langs, sum(not repo["fork"] for repo in repos)


def T(x, y, s, size=13, fill=INK, w=400, font=SANS, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{w}" fill="{fill}" '
            f'text-anchor="{anchor}">{E(str(s))}</text>')


def render(login, now, cal, langs):
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
    for i, v in enumerate(daily):
        if not v:
            continue
        hh = max(2, sh * v / mx)
        x, wb = 32 + i * bw, max(bw * 0.72, 1)
        o.append(f'<rect x="{x:.1f}" y="{base-hh:.1f}" width="{wb:.2f}" height="{hh:.1f}" fill="{CS}" opacity=".75"/>')
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
    for lx, col, op, lab in ((32, CS, ".75", "GitHub contributions"),):
        o.append(f'<rect x="{lx}" y="{lg-8}" width="9" height="9" rx="2" fill="{col}" opacity="{op}"/>')
        o.append(T(lx + 14, lg, lab, 11, MUTE))
    o.append(f'<path d="M262,{lg-4} q8,-6 16,0 t16,0" fill="none" stroke="{INK}" stroke-width="1.6" stroke-linecap="round"/>')
    o.append(T(300, lg, "7-day average", 11, MUTE))
    H = lg + 22
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="Profile summary styled as a decoded ID token">'
            f'<metadata>{E(json.dumps({"fetched_at": now.isoformat(), "private_contributions_verified": True, "contribution_source": "github-public-profile", "contribution_total": sum(daily)}))}</metadata>'
            f'<rect width="{W}" height="{H}" rx="14" fill="{BG}"/>' + "".join(o) + "</svg>")


def validate_svg(svg):
    if len(svg.encode("utf-8")) > MAX_SVG_BYTES or "<!" in svg or "<?" in svg:
        raise RefreshError("SVG exceeds its safety limit or contains XML declarations.")
    root = ET.fromstring(svg)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise RefreshError("Renderer did not produce a valid SVG.")
    for node in root.iter():
        if node.tag not in {"{http://www.w3.org/2000/svg}" + tag for tag in SVG_TAGS}:
            raise RefreshError("SVG contains an unexpected element.")
        for name, value in node.attrib.items():
            if name not in SVG_ATTRS or re.search(r"[\\()\x00-\x1f\x7f]", value):
                raise RefreshError("SVG contains an unexpected attribute or resource reference.")


def write_card(path, svg):
    validate_svg(svg)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            output.write(svg)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default="pbuffolino")
    ap.add_argument("--output", default=OUT)
    a = ap.parse_args()
    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", a.login):
            raise RefreshError("Invalid GitHub login.")
        api = GitHub(os.environ.get("GH_TOKEN"))
        started = dt.datetime.now(dt.timezone.utc)
        cal, langs, n_repos = fetch(api, a.login, dt.date(started.year, 1, 1), started.date())
        fetched = dt.datetime.now(dt.timezone.utc)
        if fetched.date() != started.date():
            raise RefreshError("UTC date changed during collection; retry the refresh.")
        write_card(a.output, render(a.login, fetched, cal, langs))
        summary = (f"Fetched at: {fetched.isoformat()}\n"
                   f"GitHub contributions: {sum(cal.values())}\n"
                   f"Owned non-fork repositories: {n_repos}\n"
                   "Contribution source: public GitHub profile calendar, daily sum verified\n"
                   "Private contribution visibility: verified\n")
        print(summary, end="")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
                output.write(summary.replace("\n", "  \n"))
        return 0
    except RefreshError as error:
        print(f"Refresh failed: {error}", file=sys.stderr)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OSError, ET.ParseError):
        # Raw exceptions can contain private names, API responses, or local paths.
        print("Refresh failed: unexpected data or I/O error; no refresh was published.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
