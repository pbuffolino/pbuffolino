import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch
import urllib.error
import xml.etree.ElementTree as ET


SPEC = importlib.util.spec_from_file_location("render_token", Path(__file__).parents[1] / "scripts/render_token.py")
card = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(card)
START = dt.date(2026, 1, 1)


def collection(start=START, end=START, private=2, public=3):
    days = [{"date": (start + dt.timedelta(days=i)).isoformat(),
             "contributionCount": private + public if i == 0 else 0}
            for i in range((end - start).days + 1)]
    return {
        "totalCommitContributions": public,
        "totalIssueContributions": 0,
        "totalPullRequestContributions": 0,
        "totalPullRequestReviewContributions": 0,
        "totalRepositoryContributions": 0,
        "restrictedContributionsCount": private,
        "contributionCalendar": {"totalContributions": private + public,
                                 "weeks": [{"contributionDays": days}]},
        "commitContributionsByRepository": [],
    }


def repo(name="private-sentinel", private=True, fork=False):
    return {"full_name": "pbuffolino/" + name, "private": private, "fork": fork}


class FakeAPI:
    def __init__(self, current=None, repos=None, previous=None):
        self.current = current if current is not None else collection()
        self.repos = repos if repos is not None else [repo()]
        self.previous = previous
        self.paths = []
        self.languages = {"Python": 20, "Shell": 10}

    def collection(self, login, start, end):
        result = self.current if start.year == START.year else self.previous
        return {"login": login, "repositories": {"totalCount": sum(r["private"] and not r["fork"] for r in self.repos)},
                "contributionsCollection": copy.deepcopy(result)}

    def api(self, path):
        self.paths.append(path)
        if path.startswith("user/repos?"):
            page = int(path.rsplit("=", 1)[1])
            return self.repos[(page - 1) * 100:page * 100]
        if isinstance(self.languages, Exception):
            raise self.languages
        return self.languages


class CalendarTests(unittest.TestCase):
    def test_daily_and_aggregate_totals_reconcile(self):
        self.assertEqual(card.calendar(collection(), START, START), {START: 5})

    def test_invalid_counts_dates_and_partial_totals_fail(self):
        for kind in ("missing", "duplicate", "future", "negative", "boolean", "total", "aggregate"):
            with self.subTest(kind=kind):
                data = collection()
                days = data["contributionCalendar"]["weeks"][0]["contributionDays"]
                if kind == "missing":
                    days.clear()
                elif kind == "duplicate":
                    days.append(dict(days[0]))
                elif kind == "future":
                    days[0]["date"] = "2026-01-02"
                elif kind in ("negative", "boolean"):
                    days[0]["contributionCount"] = -1 if kind == "negative" else True
                elif kind == "total":
                    data["contributionCalendar"]["totalContributions"] += 1
                else:
                    data["restrictedContributionsCount"] += 1
                with self.assertRaises(card.RefreshError):
                    card.calendar(data, START, START)

    def test_leap_year_and_year_end(self):
        start, end = dt.date(2024, 1, 1), dt.date(2024, 12, 31)
        self.assertEqual(len(card.calendar(collection(start, end), start, end)), 366)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.profile = patch.object(card, "fetch_profile_calendar", return_value={START: 5}).start()
        self.addCleanup(patch.stopall)

    def test_full_profile_replaces_smaller_api_without_double_counting(self):
        self.profile.return_value = {START: 6555}
        cal, _, _ = card.fetch(FakeAPI(), "pbuffolino", START, START)
        self.assertEqual(sum(cal.values()), 6555)

    def test_public_calendar_below_api_or_unavailable_is_fatal(self):
        self.profile.return_value = {START: 4}
        with self.assertRaisesRegex(card.RefreshError, "behind the API"):
            card.fetch(FakeAPI(), "pbuffolino", START, START)
        self.profile.side_effect = card.RefreshError("Public calendar unavailable")
        with self.assertRaises(card.RefreshError):
            card.fetch(FakeAPI(), "pbuffolino", START, START)

    def test_private_aggregates_and_top_languages(self):
        api = FakeAPI(repos=[repo(), repo("public", private=False), repo("fork", fork=True)])
        cal, languages, n_repos = card.fetch(api, "pbuffolino", START, START)
        self.assertEqual((sum(cal.values()), languages, n_repos), (5, ["python", "shell"], 2))
        self.assertEqual(len([p for p in api.paths if p.endswith("/languages")]), 2)

    def test_repository_pagination(self):
        api = FakeAPI(repos=[repo(str(i)) for i in range(101)])
        self.assertEqual(card.fetch(api, "pbuffolino", START, START)[2], 101)
        self.assertIn("user/repos?affiliation=owner&per_page=100&page=2", api.paths)

    def test_empty_repository_language_response_is_valid(self):
        api = FakeAPI()
        api.languages = {}
        self.assertEqual(card.fetch(api, "pbuffolino", START, START)[1], [])

    def test_language_metadata_cannot_inject_markup_or_workflow_commands(self):
        for name in ('<script>alert(1)</script>', 'Python\n::warning::private-sentinel',
                     'x' * 65, '\u202ePython'):
            api = FakeAPI()
            api.languages = {name: 1}
            with self.subTest(name=name), self.assertRaises(card.RefreshError):
                card.fetch(api, "pbuffolino", START, START)

    def test_zero_current_year_uses_previous_year_private_evidence(self):
        self.profile.return_value = {START: 0}
        api = FakeAPI(current=collection(private=0, public=0),
                      previous=collection(dt.date(2025, 1, 1), dt.date(2025, 12, 31)))
        self.assertEqual(sum(card.fetch(api, "pbuffolino", START, START)[0].values()), 0)

    def test_no_private_evidence_fails(self):
        api = FakeAPI(current=collection(private=0), previous=collection(
            dt.date(2025, 1, 1), dt.date(2025, 12, 31), private=0))
        with self.assertRaisesRegex(card.RefreshError, "visibility could not be verified"):
            card.fetch(api, "pbuffolino", START, START)

    def test_direct_private_commits_verify_visibility(self):
        data = collection(private=0)
        data["commitContributionsByRepository"] = [
            {"repository": {"isPrivate": True}, "contributions": {"totalCount": 3}}]
        self.assertEqual(sum(card.fetch(FakeAPI(current=data), "pbuffolino", START, START)[0].values()), 5)

    def test_public_only_repository_access_fails(self):
        with self.assertRaisesRegex(card.RefreshError, "private repositories"):
            card.fetch(FakeAPI(repos=[repo(private=False)]), "pbuffolino", START, START)

    def test_repository_coverage_mismatch_fails(self):
        api = FakeAPI()
        original = api.collection
        def incomplete(*args):
            result = original(*args)
            result["repositories"]["totalCount"] += 1
            return result
        api.collection = incomplete
        with self.assertRaisesRegex(card.RefreshError, "coverage"):
            card.fetch(api, "pbuffolino", START, START)

    def test_private_forks_do_not_change_language_coverage(self):
        # GraphQL and REST disagree about some private forks; neither should
        # contribute to the non-fork language coverage check.
        api = FakeAPI(repos=[repo(), repo("private-fork", fork=True)])
        self.assertEqual(card.fetch(api, "pbuffolino", START, START)[2], 1)

    def test_duplicate_repository_page_is_rejected(self):
        with self.assertRaisesRegex(card.RefreshError, "coverage"):
            card.fetch(FakeAPI(repos=[repo(), repo()]), "pbuffolino", START, START)

    def test_partial_language_failure_is_fatal(self):
        api = FakeAPI()
        api.languages = card.RefreshError("GitHub request failed (HTTP 403)")
        with self.assertRaises(card.RefreshError):
            card.fetch(api, "pbuffolino", START, START)

    def test_malformed_repository_data_fails(self):
        for entry in (repo("../escape"), repo(private=1), repo(fork="false")):
            with self.subTest(entry=entry), self.assertRaises(card.RefreshError):
                card.fetch(FakeAPI(repos=[entry]), "pbuffolino", START, START)


def profile_document(start=START, value=6555):
    end = dt.date(start.year, 12, 31)
    parts = [f'<h2 id="js-contribution-activity-description">{value:,} contributions in {start.year}</h2>']
    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        key = f"contribution-day-component-{offset}"
        label = f"{value:,} contributions" if offset == 0 and value else "No contributions"
        parts.append(f'<td data-date="{date}" id="{key}"></td>'
                     f'<tool-tip for="{key}">{label} on {date:%B} {date.day}th.</tool-tip>')
    return "".join(parts)


class ProfileCalendarTests(unittest.TestCase):
    def test_full_year_coverage_daily_sum_and_zero_future_days(self):
        cal = card.profile_calendar(profile_document(), START, START)
        self.assertEqual(cal, {START: 6555})
        start, end = dt.date(2024, 1, 1), dt.date(2024, 12, 31)
        cal = card.profile_calendar(profile_document(start, value=0), start, end)
        self.assertEqual((len(cal), sum(cal.values())), (366, 0))

    def test_markup_changes_partial_dates_bad_counts_and_mismatched_sum_fail(self):
        original = profile_document()
        for document in (
            "<html>Sign in or retry</html>",
            original.replace('id="js-contribution-activity-description"', 'id="changed"'),
            original.replace("6,555 contributions in", "6,554 contributions in"),
            original.replace('data-date="2026-01-01"', 'data-date="2025-12-31"'),
            original.replace('data-date="2026-01-02"', 'data-date="2026-01-01"'),
            original.replace('data-date="2026-01-02"', 'other="2026-01-02"'),
            original.replace("6,555 contributions on", "private-sentinel contributions on"),
            original.replace("6,555", "6,,555"),
            original.replace("No contributions on January 2th.", "1 contribution on January 2th."),
            original + '<td id="contribution-day-component-0" data-date="2026-01-01"></td>',
        ):
            with self.subTest(document=document[:80]), self.assertRaises(card.RefreshError):
                card.profile_calendar(document, START, START)

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_public_request_has_no_authorization_or_cookie(self, urlopen):
        urlopen.return_value = io.BytesIO(profile_document().encode())
        with patch.dict(os.environ, {"GH_TOKEN": "token-sentinel"}):
            self.assertEqual(card.fetch_profile_calendar("pbuffolino", START, START), {START: 6555})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://github.com/users/pbuffolino/contributions?from=2026-01-01&to=2026-01-01")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))

    @patch.object(card.time, "sleep")
    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_public_errors_retry_with_bounds_and_never_echo_response(self, urlopen, sleep):
        urlopen.side_effect = urllib.error.HTTPError("private-sentinel", 503, "token-sentinel", {}, None)
        with self.assertRaisesRegex(card.RefreshError, "HTTP 503") as error:
            card.fetch_profile_calendar("pbuffolino", START, START)
        self.assertNotIn("sentinel", str(error.exception))
        self.assertEqual((urlopen.call_count, sleep.call_count), (3, 2))

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_public_request_invalid_login_and_oversize_are_rejected(self, urlopen):
        with self.assertRaises(card.RefreshError):
            card.fetch_profile_calendar("../escape", START, START)
        urlopen.assert_not_called()
        urlopen.return_value = io.BytesIO(b" " * (card.MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(card.RefreshError, "safety limit"):
            card.fetch_profile_calendar("pbuffolino", START, START)


class RequestTests(unittest.TestCase):
    def response(self, body):
        return io.BytesIO(json.dumps(body).encode())

    def test_missing_token(self):
        with self.assertRaisesRegex(card.RefreshError, "missing"):
            card.GitHub("")

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_unexpected_api_destinations_never_receive_credentials(self, urlopen):
        for path in ("https://other.example/", "//other.example/", "../user",
                     "repos/pbuffolino/private/contents/passwords", "graphql#fragment",
                     "user/repos?affiliation=owner&per_page=100&page=1&extra=true"):
            with self.subTest(path=path), self.assertRaises(card.RefreshError):
                card.GitHub("token-sentinel").api(path)
        urlopen.assert_not_called()

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_oversized_response_is_rejected(self, urlopen):
        urlopen.return_value = io.BytesIO(b" " * (card.MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(card.RefreshError, "safety limit"):
            card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"})

    def test_redirects_never_forward_credentials(self):
        with self.assertRaisesRegex(card.RefreshError, "redirect"):
            card.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example/")

    @patch.object(card.time, "sleep")
    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_retry_transient_error_then_success(self, urlopen, sleep):
        error = urllib.error.HTTPError("private-sentinel", 503, "private-sentinel", {}, None)
        urlopen.side_effect = [error, self.response({"ok": True})]
        self.assertEqual(card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"}), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch.object(card.time, "sleep")
    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_rate_limit_retries_are_bounded(self, urlopen, sleep):
        urlopen.side_effect = [urllib.error.HTTPError("private-sentinel", 403, "secret", {
            "X-RateLimit-Remaining": "0"}, None) for _ in range(3)]
        with self.assertRaisesRegex(card.RefreshError, "HTTP 403") as raised:
            card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"})
        self.assertNotIn("private-sentinel", str(raised.exception))
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_expired_or_underprivileged_token_is_not_retried(self, urlopen):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                urlopen.reset_mock()
                urlopen.side_effect = urllib.error.HTTPError("private-sentinel", status, "secret", {}, None)
                with self.assertRaises(card.RefreshError) as raised:
                    card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"})
                self.assertNotIn("sentinel", str(raised.exception))
                self.assertEqual(urlopen.call_count, 1)

    @patch.object(card.time, "sleep")
    @patch.object(card.urllib.request.OpenerDirector, "open", side_effect=TimeoutError("private-sentinel"))
    def test_timeouts_are_bounded_and_sanitized(self, urlopen, sleep):
        with self.assertRaisesRegex(card.RefreshError, "three attempts"):
            card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"})
        self.assertEqual(urlopen.call_count, 3)

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_graphql_partial_data_errors_are_rejected(self, urlopen):
        urlopen.return_value = self.response({"data": {"viewer": None}, "errors": [{"message": "private-sentinel"}]})
        with self.assertRaises(card.RefreshError) as raised:
            card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"})
        self.assertNotIn("private-sentinel", str(raised.exception))

    @patch.object(card.urllib.request.OpenerDirector, "open")
    def test_invalid_json_is_rejected_without_response_details(self, urlopen):
        urlopen.return_value = io.BytesIO(b'private-sentinel')
        with self.assertRaisesRegex(card.RefreshError, "invalid JSON") as raised:
            card.GitHub("token-sentinel").api("graphql", {"query": "query{viewer{login}}"})
        self.assertNotIn("sentinel", str(raised.exception))

    @patch.object(card.GitHub, "api", return_value={"data": {"viewer": {"login": "someone-else"}}})
    def test_wrong_account_is_rejected(self, api):
        with self.assertRaisesRegex(card.RefreshError, "profile owner"):
            card.GitHub("token-sentinel").collection("pbuffolino", START, START)


class PublicationTests(unittest.TestCase):
    def test_active_svg_variants_preserve_previous_card(self):
        for content in unsafe_svgs():
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "card.svg"
                path.write_text("previous")
                with self.assertRaises((card.RefreshError, ET.ParseError)):
                    card.write_card(path, content)
                self.assertEqual(path.read_text(), "previous")

    def test_single_total_chart_metadata_escaping_and_zero_year(self):
        for now in (dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                    dt.datetime(2024, 12, 31, tzinfo=dt.timezone.utc)):
            svg = card.render("pbuffolino", now, {}, ['C<&"'])
            root = ET.fromstring(svg)
            self.assertNotIn("personal projects", svg)
            self.assertNotIn("work &amp; other", svg)
            metadata = json.loads(root.find("{http://www.w3.org/2000/svg}metadata").text)
            self.assertEqual(metadata["fetched_at"], now.isoformat())
            self.assertTrue(metadata["private_contributions_verified"])
            self.assertEqual(metadata["contribution_source"], "github-public-profile")
            self.assertEqual(metadata["contribution_total"], 0)
            self.assertNotIn("<script", svg)
            self.assertIn("&lt;", svg)

    def test_invalid_svg_and_failed_atomic_replace_preserve_previous_card(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "card.svg"
            path.write_text("previous")
            for svg in ("broken xml", '<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'):
                with self.assertRaises((card.RefreshError, ET.ParseError)):
                    card.write_card(path, svg)
                self.assertEqual(path.read_text(), "previous")
            with patch.object(card.os, "replace", side_effect=OSError("private-sentinel")):
                with self.assertRaises(OSError):
                    card.write_card(path, '<svg xmlns="http://www.w3.org/2000/svg"/>')
            self.assertEqual(path.read_text(), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_fetch_preserves_file_and_sanitizes_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "card.svg"
            path.write_text("previous")
            log = io.StringIO()
            with patch.dict(os.environ, {"GH_TOKEN": "token-sentinel"}), \
                    patch.object(card.sys, "argv", ["render", "--output", str(path)]), \
                    patch.object(card, "fetch", side_effect=KeyError("private-sentinel")), \
                    contextlib.redirect_stderr(log):
                self.assertEqual(card.main(), 1)
            self.assertEqual(path.read_text(), "previous")
            self.assertNotIn("sentinel", log.getvalue())

    def test_successful_main_publishes_only_card_and_aggregate_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path, summary = Path(directory) / "card.svg", Path(directory) / "summary.md"
            today = dt.datetime.now(dt.timezone.utc).date()
            log = io.StringIO()
            with patch.dict(os.environ, {"GH_TOKEN": "token-sentinel", "GITHUB_STEP_SUMMARY": str(summary)}), \
                    patch.object(card.sys, "argv", ["render", "--output", str(path)]), \
                    patch.object(card, "fetch", return_value=({today: 5}, ["python"], 3)), \
                    contextlib.redirect_stdout(log):
                self.assertEqual(card.main(), 0)
            ET.fromstring(path.read_text())
            self.assertIn("GitHub contributions: 5", summary.read_text())
            self.assertNotIn("sentinel", log.getvalue() + summary.read_text() + path.read_text())


def unsafe_svgs():
    prefix = '<svg xmlns="http://www.w3.org/2000/svg"'
    return [
        prefix + ' onload="alert(1)"/>',
        prefix + '><a href="https://other.example/">link</a></svg>',
        prefix + '><animate attributeName="href"/></svg>',
        prefix + '><style>text{fill:red}</style></svg>',
        prefix + '><foreignObject/></svg>',
        prefix + '><image href="https://other.example/"/></svg>',
        prefix + '><rect fill="url(https://other.example/)"/></svg>',
        prefix + r'><rect fill="\75rl(https://other.example/)"/></svg>',
        prefix + '><rect style="fill:red"/></svg>',
        prefix + '><script xmlns="https://other.example/"/></svg>',
        '<?xml-stylesheet href="https://other.example/"?>' + prefix + '/>',
        '<!DOCTYPE svg [<!ENTITY secret "private-sentinel">]>' + prefix + '>&secret;</svg>',
    ]


class PublisherBoundaryTests(unittest.TestCase):
    """Execute the actual inline publisher using only synthetic inputs and HTTP mocks."""
    source = "a" * 40
    parent = "f" * 40
    commit = "e" * 40

    @classmethod
    def setUpClass(cls):
        workflow = (Path(__file__).parents[1] / ".github/workflows/refresh.yml").read_text()
        block = workflow.rsplit("          python3 -I - <<'PY'\n", 1)[1].split("          PY", 1)[0]
        cls.program = compile(textwrap.dedent(block), "inline-publisher", "exec")

    def run_publisher(self, svg=None, responses=None, encoded=None):
        svg = svg if svg is not None else card.render(
            "pbuffolino", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), {}, ["python"])
        encoded = encoded if encoded is not None else card.base64.b64encode(svg.encode()).decode()
        opener = Mock()
        replies = responses if responses is not None else [
            {"object": {"sha": self.source}}, {"object": {"sha": self.parent}}, {"tree": {"sha": "b" * 40}},
            {"sha": "c" * 40}, {"sha": "d" * 40}, {"sha": self.commit},
            {"object": {"sha": self.commit}}, {"object": {"sha": self.commit}}]
        opener.open.side_effect = [r if isinstance(r, Exception) else io.BytesIO(json.dumps(r).encode())
                                    for r in replies]
        log = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with patch.dict(os.environ, {"GH_TOKEN": "token-sentinel", "CARD_B64": encoded,
                                        "SOURCE_SHA": self.source, "GITHUB_STEP_SUMMARY": str(summary)}, clear=True), \
                    patch.object(card.urllib.request, "build_opener", return_value=opener), \
                    contextlib.redirect_stderr(log):
                try:
                    exec(self.program, {})
                    result = 0
                except SystemExit as error:
                    result = error.code
            content = summary.read_text() if summary.exists() else ""
        return result, opener.open.call_args_list, log.getvalue(), content

    def test_writer_updates_only_fixed_asset_with_non_force_reference_update(self):
        result, calls, log, summary = self.run_publisher()
        self.assertEqual(result, 0)
        requests = [call.args[0] for call in calls]
        self.assertEqual(len(requests), 8)
        self.assertTrue(all(r.full_url.startswith(
            "https://api.github.com/repos/pbuffolino/pbuffolino/git/") for r in requests))
        tree = json.loads(requests[4].data)
        self.assertEqual(tree["tree"], [{"path": "assets/id_token.svg", "mode": "100644",
                                         "type": "blob", "sha": "c" * 40}])
        self.assertEqual(json.loads(requests[5].data)["parents"], [self.parent])
        self.assertEqual(requests[6].method, "PATCH")
        self.assertTrue(requests[6].full_url.endswith('/git/refs/heads/profile-card'))
        self.assertEqual(json.loads(requests[6].data), {"sha": self.commit, "force": False})
        self.assertFalse(any(r.get_method() != 'GET' and r.full_url.endswith('/main') for r in requests))
        self.assertIn(self.commit, summary)
        self.assertNotIn("sentinel", log + summary)

    def test_untrusted_output_is_rejected_before_any_network_access(self):
        for svg in unsafe_svgs() + ["<svg/>", "x" * 80_001]:
            with self.subTest(svg=svg[:100]):
                result, calls, log, summary = self.run_publisher(svg=svg)
                self.assertEqual(result, 1)
                self.assertEqual(calls, [])
                self.assertEqual(summary, "")
                self.assertNotIn("sentinel", log)
        result, calls, _, _ = self.run_publisher(encoded="invalid-base64")
        self.assertEqual((result, calls), (1, []))

    def test_stale_source_causes_no_writes(self):
        result, calls, _, summary = self.run_publisher(responses=[{"object": {"sha": "f" * 40}}])
        self.assertEqual(result, 1)
        self.assertEqual([c.args[0].get_method() for c in calls], ["GET"])
        self.assertEqual(summary, "")

    def test_failed_update_never_reports_success_or_exposes_raw_error(self):
        responses = [{"object": {"sha": self.source}}, {"object": {"sha": self.parent}}, {"tree": {"sha": "b" * 40}},
                     {"sha": "c" * 40}, {"sha": "d" * 40}, {"sha": self.commit},
                     urllib.error.HTTPError("private-sentinel", 422, "token-sentinel", {}, None)]
        result, calls, log, summary = self.run_publisher(responses=responses)
        self.assertEqual(result, 1)
        self.assertEqual(len(calls), 7)
        self.assertEqual(summary, "")
        self.assertNotIn("sentinel", log)

    def test_failed_readback_is_not_reported_as_verified(self):
        responses = [{"object": {"sha": self.source}}, {"object": {"sha": self.parent}}, {"tree": {"sha": "b" * 40}},
                     {"sha": "c" * 40}, {"sha": "d" * 40}, {"sha": self.commit},
                     {"object": {"sha": self.commit}}, {"object": {"sha": "f" * 40}}]
        result, _, _, summary = self.run_publisher(responses=responses)
        self.assertEqual(result, 1)
        self.assertEqual(summary, "")

    def test_workflow_keeps_data_and_write_credentials_in_separate_jobs(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/refresh.yml").read_text()
        render, publisher = workflow.split("  render:\n", 1)[1].split("  publish:\n", 1)
        self.assertIn("      contents: read\n", render)
        self.assertIn("          persist-credentials: false\n", render)
        self.assertIn("    environment: profile-data\n", render)
        self.assertIn("      contents: write\n", publisher)
        self.assertNotIn("secrets.", publisher)
        self.assertNotIn("uses:", publisher)
        self.assertNotIn("checkout", publisher.split("    steps:\n", 1)[0])
        self.assertNotIn("${{", textwrap.dedent(workflow.rsplit("          python3 -I - <<'PY'\n", 1)[1]))
        for trigger in ("pull_request_target:", "workflow_run:", "issue_comment:"):
            self.assertNotIn(trigger, workflow)


if __name__ == "__main__":
    unittest.main()
