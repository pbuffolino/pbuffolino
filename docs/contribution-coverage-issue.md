# Contribution total regression, local issue draft

Status: tracked in [PR 6](https://github.com/pbuffolino/pbuffolino/pull/6), whose
description carries the current CI, merge, and runtime acceptance receipts.
GitHub issue tracking is unavailable because this repository has Issues disabled.
Checked open/closed issue records and PRs 1 through 5 on
2026-10-19 UTC; no existing tracking record covers this regression. Do not enable
Issues or change repository interaction controls to file this draft.

## Observed and expected behavior

The original committed card (`9b74e5d:assets/id_token.svg`) reported 6,252 GitHub
contributions for 2026. Scheduled run
[39122308956](https://github.com/pbuffolino/pbuffolino/actions/runs/39122308956)
published 1,687 at 2026-10-18T11:12:53Z. The owner-authenticated local API later
returned 1,764, including 1,487 restricted contributions. Expected: preserve the
intended complete profile calendar scope and avoid publishing a substantially
reduced historical total without an explicit explanation or approval.

Impact: public profile substantially understates activity relative to the original
card. Severity: medium, presentation/data quality. No private identifiers or
credentials are needed to reproduce the discrepancy.

## Evidence and hypotheses

- Original and current renderers both use GitHub's contribution calendar for the
  displayed total. The original renderer's separate raw commit enumeration only
  colored the personal-project segment; it did not increase the calendar total.
- PR 1 removed the unsupported personal/work split and changed collection from
  `user(login:...)` to authenticated `viewer`. Current live queries of both return
  the same total. That query change does not explain the discrepancy.
- The current collection verifies some private activity and reconciles API totals,
  but those checks cannot establish full contribution visibility or explain a
  lower historical count. The source of the original 6,252 remains unverified.
- Independent readback at 2026-10-19T02:00Z established 6,555 contributions in
  GitHub's public calendar, confirmed by its heading and daily sum. Both the
  browser and a separate unauthenticated HTTP request show that total. The
  API-only collector is incomplete relative to the intended profile source.
  The exact upstream reason is unverified; no token scopes were broadened.

## Detection and affected owners

Read the SVG text/metadata from the `profile-card` branch, compare with the original
asset in Git history, then compare owner-authenticated GitHub GraphQL
`contributionsCollection` for the same year and fixed end date. Inspect
`scripts/render_token.py`, `tests/test_render_token.py`,
`.github/workflows/refresh.yml`, and `docs/automation.md`.

## Acceptance criteria

- Establish the independent current profile calendar and explain any remaining
  difference from the original asset without inventing or adding overlapping totals.
- Collect the public profile calendar and reject an incomplete calendar, a
  mismatched daily sum, or a total below the authenticated API. Never fall back
  to API-only counts or combine these overlapping totals.
- Exercise regression, legitimate growth, date rollover, and failure preservation.
- Keep publication credentials isolated, logs aggregate-only, and existing branch
  protections intact. Do not broaden token permissions automatically.
- Distinguish local tests, merged code, workflow publication, and public image
  readback. Runtime recovery stays open until applicable live checks pass.

## Implementation and validation

The local patch reads the public GitHub calendar without authentication or cookies,
validates the full year's dates and displayed total, rejects nonzero future days,
and uses that series for the single total and seven-day average. The authenticated
API retains owner/private-access checks and language aggregation. Metadata records
the public calendar source and aggregate count. There is no new credential or
permission grant. The existing isolated publisher is unchanged.

45 offline tests pass, including larger-public-calendar recovery, lower-bound and
unavailable-source rejection, malformed/partial HTML, leap-year coverage, zero
years, credential isolation, and existing failure-preservation boundaries.
Live owner-authenticated collection and SVG validation passed, returning 6,555
contributions across 292 elapsed UTC dates and 26 owned non-fork repositories.
Workflow lint and whitespace checks passed. This is the pre-merge validation
snapshot; see PR 6 for subsequent CI, merge, and publication readback receipts.

Exclusions: upstream attribution/organization details, raw commits on all branches,
future scheduler delivery, and every GitHub profile cache variant are not audited.
