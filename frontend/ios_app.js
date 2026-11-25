"use strict";

// ---------------------------------------------------------------------
// The site as the iOS app shows it (mobile/ios/, which names itself in the
// User-Agent): without the demo. The server leaves the fictional drives and
// the "view as a demo driver" names out of what it answers the app
// (backend/ios_app.py), and the pages hide what they say about the demo:
// everything marked class="demo-only" (style.css). Loaded in the <head> of
// every page, before anything is drawn. See docs/ios-app-without-demo.md.
// ---------------------------------------------------------------------
const WITHOUT_DEMO = /\bRideAndRailApp\//.test(navigator.userAgent);
if (WITHOUT_DEMO) document.documentElement.classList.add("without-demo");
