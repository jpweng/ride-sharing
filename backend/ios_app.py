"""
The site as the iOS app shows it (mobile/ios/): without the demo. See
docs/ios-app-without-demo.md.

The app is the site in a web view, which names itself at the end of its
User-Agent ("RideAndRailApp/<version>"). To a request from it, the server
answers as if demo drives were switched off: no fictional drive in a
search, a combined trip, the list of drives or the inbox, and no "view as
a demo driver" (demo_drives.shown). Nothing is deleted, and nothing
changes for browsers or the Android app. The pages hide what they say
about the demo themselves (frontend/ios_app.js).

A User-Agent proves nothing: anyone can send this one, and gets the site
without its demo, which shows them less, never more.
"""
from fastapi import Request

USER_AGENT_MARK = "RideAndRailApp/"


def is_app(request: Request) -> bool:
    return USER_AGENT_MARK in request.headers.get("user-agent", "")
