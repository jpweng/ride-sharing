"""
The weekly summary of open security alerts (.github/workflows/security-summary.yml,
docs/security-alerts.md): how many Dependabot and code scanning alerts are
open, by severity, as the text of one issue.

The repository is public and so is the issue: it gives counts and links to
the Security tab, never what an alert is about or where it is in the code.

    python3 security_summary.py <code scanning alerts.json> <Dependabot alerts.json>
        --repo owner/name --body body.md [--previous previous.md]

Each file holds what `gh api --paginate --slurp` gives for the open alerts
(a list of pages), or anything that isn't one when the list couldn't be read.
Writes the issue's text to --body and prints, for the workflow's outputs:
`open` (how many alerts), `readable` (false when neither list could be
read) and `changed` (whether the text differs from --previous).
"""
import argparse
import json
import sys
from pathlib import Path

SEVERITIES = ("critical", "high", "medium", "low")
# A code scanning rule without a security severity has a plain one.
PLAIN_SEVERITY = {"error": "high", "warning": "medium", "note": "low", "none": "low"}


def alerts_in(path: Path) -> list[dict] | None:
    """The alerts in a file of `gh api --paginate --slurp` pages (or one
    plain list), None when it holds no such thing: the list wasn't read."""
    try:
        pages = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(pages, list):
        return None
    alerts = []
    for page in pages:
        alerts += page if isinstance(page, list) else [page]
    return alerts if all(isinstance(alert, dict) for alert in alerts) else None


def code_severity(alert: dict) -> str:
    rule = alert.get("rule") or {}
    severity = rule.get("security_severity_level") or PLAIN_SEVERITY.get(rule.get("severity"))
    return severity if severity in SEVERITIES else "low"


def dependency_severity(alert: dict) -> str:
    severity = (alert.get("security_advisory") or {}).get("severity")
    return severity if severity in SEVERITIES else "low"


def counts(alerts: list[dict], severity_of) -> dict[str, int]:
    found = {severity: 0 for severity in SEVERITIES}
    for alert in alerts:
        found[severity_of(alert)] += 1
    return found


def _row(name: str, alerts: list[dict] | None, severity_of) -> str:
    if alerts is None:
        return f"| {name} | could not be read | | | | |"
    by_severity = counts(alerts, severity_of)
    cells = " | ".join(str(by_severity[severity]) for severity in SEVERITIES)
    return f"| {name} | {cells} | {len(alerts)} |"


def body(repo: str, code: list[dict] | None, dependencies: list[dict] | None) -> str:
    """The issue's text. The same alerts give the same text: it is compared
    with last week's to know whether anything changed."""
    return "\n".join([
        "Open security alerts on `main`, by severity.",
        "",
        "| | Critical | High | Medium | Low | Total |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        _row("Dependencies (Dependabot)", dependencies, dependency_severity),
        _row("Code (code scanning)", code, code_severity),
        "",
        f"- Dependencies: https://github.com/{repo}/security/dependabot",
        f"- Code: https://github.com/{repo}/security/code-scanning",
        "",
        "The repository is public, so this issue only counts them: what each alert is about is in the"
        " Security tab, for those who have access to it.",
        "",
        "Updated every Monday by `.github/workflows/security-summary.yml`, with a comment when the"
        " counts change. Closed when no alert is left.",
        "",
    ])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("code", type=Path)
    parser.add_argument("dependencies", type=Path)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)

    code, dependencies = alerts_in(args.code), alerts_in(args.dependencies)
    text = body(args.repo, code, dependencies)
    args.body.write_text(text, encoding="utf-8")
    previous = args.previous.read_text(encoding="utf-8") if args.previous and args.previous.is_file() else ""
    print(f"open={len(code or []) + len(dependencies or [])}")
    print(f"readable={'false' if code is None and dependencies is None else 'true'}")
    print(f"changed={'true' if text.strip() != previous.strip() else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
