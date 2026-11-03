# Profile repository

Run `bash scripts/check-local.sh` locally before merge. It requires Python 3.12
and actionlint, runs offline tests and validates the publisher workflow. Do not
run the authenticated generator merely to validate a source change.

Code review is performed by independent native Codex reviewers on the exact PR
head. Changes to credentials, publication or workflow trust boundaries require
separate code and security reviews. Record validation and review results in the
PR; rerun affected checks after fixes. The implementer cannot self-approve.

GitHub Actions is retained only for the weekly profile service and its isolated
publisher. Preserve CodeQL security requirements, PR/conversation requirements,
the environment-scoped read token, and the publisher's fixed-asset validation.
See [the automation contract](docs/automation.md). Do not broaden credentials or
change environment protections as part of ordinary source validation.
