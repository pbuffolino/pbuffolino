# Weekly profile refresh

The `Refresh id_token card` workflow runs Sundays at 06:17 UTC, on changes to
its generator/tests/workflow on `main`, and through **Actions → Run workflow**.
PRs run offline tests without private credentials. Only runs on `main` publish.

The card includes the current UTC year's GitHub contribution calendar and a
trailing seven-day average (shortened at the start of January). Languages are
the top three by repository language bytes across visible owned non-fork
repositories, including private repositories. They are not authored-code shares.
Biography, links, interests, and other static claims remain manually maintained.

## One-time credential setup

1. While signed in as `pbuffolino`, create a dedicated
   [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new).
   Choose resource owner `pbuffolino`, **All repositories**, repository
   **Contents: Read-only**, and **Metadata: Read-only**. Do not grant write access.
   Set a finite expiration and a calendar reminder to rotate it before expiry.
2. Save it as the repository Actions secret `PROFILE_TOKEN` under
   [Settings → Secrets and variables → Actions](https://github.com/pbuffolino/pbuffolino/settings/secrets/actions).
   Never put its value in a commit, issue, workflow log, or chat.
3. Enable private contribution counts in the GitHub profile's contribution
   settings if anonymous private activity should be included. The token's
   actual contribution visibility is tested during collection, rather than
   inferred from its configured permissions. The generator accepts verified
   anonymized restricted activity as well as directly visible private commits.
4. Run the workflow manually on `main`. Require a green render job and a
   summary reporting verified private visibility. If permissions do not expose
   the required data, stop and review the failed setup; do not substitute a
   public-only result or silently broaden permissions.
5. Under [GitHub notification settings](https://github.com/settings/notifications),
   enable Actions email or web notifications, optionally failed workflows only.
   Ensure this repository is watched. This account-level preference must be
   confirmed in GitHub; committing the workflow does not enable notifications.

The data token is used only for read requests. The workflow's automatic
`GITHUB_TOKEN`, scoped to this profile repository, performs the generated-card
commit. Private source responses and repository identifiers stay in process
memory and are never saved as artifacts or printed.

## Verification and failure behavior

Each run checks the authenticated owner, owned private repository visibility,
complete UTC calendar dates, daily and aggregate contribution totals, repository
pagination, and language responses. A current year without private activity is
checked against the previous year for private-visibility evidence. If neither
year provides evidence, the run fails. Empty repositories and a current-year
zero total are valid; missing API data is not.

Owned non-fork private repository coverage is checked between GraphQL and REST.
Private forks can be omitted by REST and are excluded from the language totals.
A token scoped to a
subset can hide repositories from both APIs, so the **All repositories** setup
choice matters. Calendar totals reflect what GitHub exposes to that credential
and the user's private contribution settings, not an independent audit of every
commit in every repository or organization.

Transient network errors, rate limits, and HTTP 5xx responses get at most three
attempts per request, with 20-second timeouts and bounded backoff. Other errors
fail immediately. The whole render job is limited to ten minutes. Failed
collection or SVG validation leaves the previous published card in place.
Non-fast-forward pushes fail without force-pushing; dispatch a new run on current main.

Successful fetch time is recorded in the SVG's `metadata` and the workflow
summary. After a successful run, verify the bot commit and profile image match
the new SVG. Confirm the next Sunday run appears in Actions. GitHub schedules
are best effort and may be delayed. Public-repository schedules can be disabled
after 60 days without activity; there is no independent missed-run monitor.

To rotate a credential, replace `PROFILE_TOKEN` and manually run the workflow.
To stop publication, disable the workflow. To undo a bad published card, restore
the last known-good SVG in a normal commit, fix the generator, then rerun. Never
force-push or overwrite a failed fetch with zeroes.

## Local validation

```sh
python3 -m unittest discover -s tests -v
actionlint .github/workflows/refresh.yml
```

For an authenticated local preview, securely supply the dedicated token through
`GH_TOKEN` and run `python3 scripts/render_token.py --output /tmp/profile.svg`.
Do not save credentials in a shell command or repository file.

References:

- [GitHub token permission mappings](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens)
- [GitHub contribution collection semantics](https://docs.github.com/en/graphql/reference/users#contributionscollection)
- [GitHub workflow notification settings](https://docs.github.com/en/subscriptions-and-notifications/how-tos/managing-github-actions-notifications)
- [GitHub scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
