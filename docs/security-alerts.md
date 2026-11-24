# Security alerts

GitHub's own tools watch the repository for security problems. They were switched on in the
repository's settings (Settings, "Advanced Security"), not by a file in the code, except for the
weekly summary below.

## What is on

- **Dependabot alerts**: an alert when a dependency (`pyproject.toml`, `uv.lock`) has a known
  vulnerability.
- **Dependabot security updates**: a pull request with the corrected version when there is one.
  It is a pull request like any other: the Tests check must pass, and merging it deploys. The
  automated review shows as failed on it (a run started by Dependabot gets no Actions secret, and
  the review needs one); it isn't a required check.
- **Code scanning** (CodeQL, default setup): Python, JavaScript and the workflows, on every pull
  request and once a week on `main`. On a pull request its findings show as a check and as notes
  on the lines concerned. It isn't a required check.
- **Secret scanning** with push protection: a push that contains a known kind of secret is
  refused.

The alerts are in the repository's Security tab, for those who have access to it.

## Who is told

- GitHub emails a new Dependabot alert of critical or high severity to those with write access
  (each person's own notification settings; a weekly digest can be chosen there).
- A Dependabot pull request notifies like any pull request.
- The weekly scan of `main` notifies nobody. The summary below covers it, and the Dependabot
  alerts of medium and low severity.

## The weekly summary

[.github/workflows/security-summary.yml](../.github/workflows/security-summary.yml) runs every
Monday morning (and by hand, "Run workflow"). It counts the open Dependabot and code scanning
alerts by severity ([.github/scripts/security_summary.py](../.github/scripts/security_summary.py))
and keeps one issue, "Open security alerts", labelled `security-alerts` and assigned to the
repository's owner:

- an alert is open and there is no such issue: it opens one (GitHub emails the owner);
- the counts changed since last time: it updates the issue's text and adds a comment with the new
  counts (another email);
- nothing changed: it does nothing;
- no alert is left: it closes the issue.

**The issue only counts.** The repository is public and so are its issues: what an alert is about
(the package, the rule, the file) stays in the Security tab. The same goes for commits and pull
requests that fix one: they say what changes, not what could have been done with it.

A list that can't be read shows as "could not be read" in the issue, not as zero; when neither can
be read the run fails. The workflow's token reads the alerts (`security-events: read`,
`vulnerability-alerts: read`) and writes issues (`issues: write`), nothing else.

A pull request that changes the workflow or its script runs it without touching the issue: the
run's summary shows the text it would write.
