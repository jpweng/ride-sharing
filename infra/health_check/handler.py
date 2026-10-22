"""
Uptime check, run every 5 minutes by EventBridge (see infra/monitoring.tf).

Raises unless HEALTH_URL answers 200 with {"status": "ok"}; the CloudWatch
alarm on this function's Errors metric then emails the alert.
"""
import json
import os
import urllib.request

HEALTH_URL = os.environ["HEALTH_URL"]


def handler(event, context):
    request = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "ride-sharing-health-check"})
    # Non-2xx answers raise HTTPError; no answer raises URLError or TimeoutError.
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    if body.get("status") != "ok":
        raise RuntimeError(f"{HEALTH_URL} answered {body!r}")
    return "ok"
