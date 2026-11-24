"""
The weekly summary of open security alerts (.github/scripts/security_summary.py,
.github/workflows/security-summary.yml, docs/security-alerts.md): one public
issue that counts the open Dependabot and code scanning alerts by severity,
without saying what they are about.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "security_summary.py"
WORKFLOW = (ROOT / ".github" / "workflows" / "security-summary.yml").read_text()

spec = importlib.util.spec_from_file_location("security_summary", SCRIPT)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)

CODE = [
    {"number": 1, "rule": {"id": "py/path-injection", "severity": "error", "security_severity_level": "high"},
     "most_recent_instance": {"location": {"path": "backend/secret_place.py", "start_line": 20}}},
    {"number": 2, "rule": {"id": "py/unused-import", "severity": "note", "security_severity_level": None}},
    {"number": 3, "rule": {"id": "js/odd", "severity": "warning"}},
]
DEPENDENCIES = [
    {"number": 1, "dependency": {"package": {"name": "leakypackage"}},
     "security_advisory": {"severity": "critical", "summary": "Something specific about leakypackage"}},
    {"number": 2, "dependency": {"package": {"name": "otherpackage"}}, "security_advisory": {"severity": "medium"}},
    {"number": 3, "dependency": {"package": {"name": "otherpackage"}}, "security_advisory": {"severity": "medium"}},
]


def _file(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def _run(tmp_path, capsys, code, dependencies, previous=None):
    args = [str(_file(tmp_path, "code.json", code)), str(_file(tmp_path, "dependencies.json", dependencies)),
            "--repo", "owner/name", "--body", str(tmp_path / "body.md"), "--previous", str(tmp_path / "previous.md")]
    if previous is not None:
        (tmp_path / "previous.md").write_text(previous)
    assert summary.main(args) == 0
    outputs = dict(line.split("=", 1) for line in capsys.readouterr().out.split())
    return (tmp_path / "body.md").read_text(), outputs


def test_alerts_are_counted_by_severity(tmp_path, capsys):
    # `gh api --paginate --slurp` gives a list of pages.
    text, outputs = _run(tmp_path, capsys, [CODE[:2], CODE[2:]], [DEPENDENCIES])
    assert "| Dependencies (Dependabot) | 1 | 0 | 2 | 0 | 3 |" in text
    # A rule without a security severity counts by its plain one: a warning is medium, a note low.
    assert "| Code (code scanning) | 0 | 1 | 1 | 1 | 3 |" in text
    assert outputs == {"open": "6", "readable": "true", "changed": "true"}
    assert "https://github.com/owner/name/security/dependabot" in text
    assert "https://github.com/owner/name/security/code-scanning" in text


def test_the_issue_says_nothing_about_what_an_alert_is(tmp_path, capsys):
    """The repository is public: the issue counts, the Security tab tells."""
    text, _ = _run(tmp_path, capsys, [CODE], [DEPENDENCIES])
    for told in ("leakypackage", "otherpackage", "Something specific", "path-injection", "secret_place", "js/odd"):
        assert told not in text


def test_the_same_alerts_give_the_same_text(tmp_path, capsys):
    text, _ = _run(tmp_path, capsys, [CODE], [DEPENDENCIES])
    # What GitHub gives back as the issue's body may end differently.
    _, outputs = _run(tmp_path, capsys, [CODE], [DEPENDENCIES], previous=text.strip() + "\r\n")
    assert outputs["changed"] == "false"
    _, outputs = _run(tmp_path, capsys, [CODE[:1]], [DEPENDENCIES], previous=text)
    assert outputs == {"open": "4", "readable": "true", "changed": "true"}


def test_a_list_that_could_not_be_read_is_not_an_empty_one(tmp_path, capsys):
    text, outputs = _run(tmp_path, capsys, '"unreadable"', [DEPENDENCIES])
    assert "| Code (code scanning) | could not be read | | | | |" in text
    assert outputs == {"open": "3", "readable": "true", "changed": "true"}
    # An error's JSON object, or no JSON at all, isn't a list of alerts either.
    _, outputs = _run(tmp_path, capsys, '{"message": "Not Found"}', "")
    assert outputs == {"open": "0", "readable": "false", "changed": "true"}


def test_no_alert_left(tmp_path, capsys):
    text, outputs = _run(tmp_path, capsys, [[]], [[]])
    assert "| Dependencies (Dependabot) | 0 | 0 | 0 | 0 | 0 |" in text
    assert outputs["open"] == "0" and outputs["readable"] == "true"


def test_the_workflow_keeps_one_issue_and_only_reads_the_alerts():
    assert 'cron: "23 6 * * 1"' in WORKFLOW and "workflow_dispatch:" in WORKFLOW
    permissions = WORKFLOW[WORKFLOW.index("\npermissions:"):WORKFLOW.index("\nconcurrency:")]
    assert [line.split("#")[0].strip() for line in permissions.strip().splitlines()[1:]] == [
        "contents: read", "issues: write", "security-events: read", "vulnerability-alerts: read",
    ]
    # A pull request that changes it shows what it would write, and writes nothing.
    assert "paths: [.github/workflows/security-summary.yml, .github/scripts/security_summary.py]" in WORKFLOW
    assert "DRY_RUN: ${{ github.event_name == 'pull_request' }}" in WORKFLOW
    keep = WORKFLOW[WORKFLOW.index("- name: Keep the issue"):]
    assert "if: env.DRY_RUN != 'true'" in keep
    # Opened for the owner when there is an alert, a comment when the counts change, closed when none is left.
    assert 'gh issue create --repo "$REPO" --title "Open security alerts" --body-file body.md' in keep
    assert '--label "$LABEL" --assignee "$OWNER"' in keep
    assert 'gh issue comment "$NUMBER" --repo "$REPO" --body-file body.md' in keep
    assert 'gh issue close "$NUMBER" --repo "$REPO"' in keep
    # Nothing from an alert goes through the shell: the script reads the files.
    assert "python3 .github/scripts/security_summary.py code.json dependencies.json" in WORKFLOW
