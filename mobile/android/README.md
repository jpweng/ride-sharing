# Android app (Trusted Web Activity)

The Android app is the website itself, <https://rideandrail.ch>, opened full screen by Chrome in a
[Trusted Web Activity](https://developer.chrome.com/docs/android/trusted-web-activity) (TWA). There
is no app code to maintain: every deploy of the site updates the app. A new app version on Google
Play is only needed when this configuration changes (name, icon, colours, Android target version).

This folder holds the configuration, not the app binary:

- [twa-manifest.json](twa-manifest.json): the input of [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap),
  Google's tool that generates and builds the Android project. Package name `ch.rideandrail.app`,
  host `rideandrail.ch`, colours and icons from the site's
  [manifest.webmanifest](../../frontend/manifest.webmanifest), notifications enabled.
- The Android project Bubblewrap generates here (Gradle files, `app/`) contains no secrets and may be
  committed. Build outputs (`*.apk`, `*.aab`, `build/`) are ignored by git.

**Never commit the signing key.** `android.keystore` (and any `*.keystore`, `*.jks`) is ignored by
git; keep it and its passwords in a password manager. With Play App Signing (below) it is only the
*upload* key: if it's lost, Google can reset it, but it's still a nuisance.

## How the site and the app trust each other

Chrome shows the site without a browser bar only if the site lists the app's signing certificate in
`https://rideandrail.ch/.well-known/assetlinks.json` ([Digital Asset Links](https://developers.google.com/digital-asset-links)).
The backend generates that file from the `ANDROID_CERT_SHA256` environment variable: the SHA-256
fingerprints of the certificates, comma-separated (`AB:CD:…` format). Unset, it serves an empty list
`[]`, and the app shows a small browser bar at the top (it still works).

The variable comes from the GitHub repo **variable** `ANDROID_CERT_SHA256` (Settings → Secrets and
variables → Actions → Variables; a fingerprint is public, not a secret). The deploy workflow writes it
into the server's `.env`, and [deploy/compose.yaml](../../deploy/compose.yaml) passes it to the app.
After changing it, re-run the *Test and deploy* workflow. Check the result with Google's tester:
<https://developers.google.com/digital-asset-links/tools/generator>, or simply open
<https://rideandrail.ch/.well-known/assetlinks.json>.

## Steps (for a human)

### 1. Install the tools

- [Node.js](https://nodejs.org) (LTS), then Bubblewrap:
  ```bash
  npm install -g @bubblewrap/cli
  ```
- On first run, Bubblewrap offers to download a JDK and the Android SDK command-line tools itself;
  accept. (`bubblewrap doctor` checks the setup.)

### 2. Generate the Android project

From this folder, either regenerate the project from the committed configuration:

```bash
cd mobile/android
bubblewrap update --skipVersionUpgrade
```

or start from the live site's manifest and answer the questions with the values in
`twa-manifest.json` (domain `rideandrail.ch`, package `ch.rideandrail.app`, notifications: yes):

```bash
bubblewrap init --manifest https://rideandrail.ch/manifest.webmanifest
```

`init` is also where Bubblewrap offers to **create the signing key**: accept, keep the path
`./android.keystore` and alias `android`, and store both passwords in a password manager. (To create
it by hand instead: `keytool -genkeypair -v -keystore android.keystore -alias android -keyalg RSA
-keysize 2048 -validity 10000`.) If you used `init`, commit the resulting `twa-manifest.json` if it
differs from this one.

### 3. Build

```bash
bubblewrap build
```

This asks for the keystore passwords and produces `app-release-bundle.aab` (for Google Play) and
`app-release-signed.apk` (to install directly on a phone for testing).

### 4. Create the app in Google Play Console

1. In a Google Play developer account, *Create app*: name "Ride and Rail", app (not game), free.
2. Fill in the required *App content*: privacy policy URL <https://rideandrail.ch/privacy.html>,
   data safety form, content rating, target audience, ads (none). A store listing needs the 512×512
   icon (Bubblewrap writes `store_icon.png`), a 1024×500 feature graphic and phone screenshots.
3. Use **Play App Signing** (the default for new apps): Google holds the key that signs the app on
   users' phones; your `android.keystore` is the upload key.

### 5. Set the fingerprints, so the app opens without a browser bar

1. Play Console → the app → *Test and release* → *Setup* → *App signing*: copy the SHA-256
   fingerprint of the **app signing key certificate** (Google's key, which signs what users install).
2. Optionally also the **upload key certificate** fingerprint, so that the APK you build and install
   yourself (step 3) also opens without a browser bar. You can also read it locally:
   `keytool -list -v -keystore android.keystore -alias android`.
3. Set the GitHub repo variable, comma-separated if both:
   ```bash
   gh variable set ANDROID_CERT_SHA256 --body "AA:BB:…:FF,11:22:…:99"
   ```
4. Re-run the *Test and deploy* workflow, then check `https://rideandrail.ch/.well-known/assetlinks.json`.

### 6. Closed test, then production

A new personal developer account must run a closed test before it may publish to production:

1. *Test and release* → *Testing* → *Closed testing*: create a track, upload the `.aab`, add testers
   (an email list or a Google Group) and send them the opt-in link. Testers install the app from
   Google Play through that link.
2. At least **12 testers** must stay opted in for **14 days in a row**. Invite more than 12 in case
   some drop out, and ask them to actually use the app during those days; Google asks about the
   test when you apply.
3. After the 14 days, on the Play Console dashboard: *Apply for production*, answer the questions
   about the test, and wait for Google's review (usually a few days). Then promote the release to
   *Production*.

### Later releases

Increase `appVersionCode` (and `appVersionName`) in `twa-manifest.json`, or let
`bubblewrap update` do it (without `--skipVersionUpgrade`), then `bubblewrap build` and upload the
new `.aab`. Not needed for changes to the site itself.

## Alternative: PWABuilder

[PWABuilder](https://www.pwabuilder.com) builds the same kind of app in a browser, without
installing anything: enter `https://rideandrail.ch`, choose *Android* → *Google Play*, use package
ID `ch.rideandrail.app`, and download a zip with the `.aab`, the signing key and its passwords
(`signing-key-info.txt`). Keep the key and the passwords out of git as above. The rest (Play
Console, fingerprints, closed test) is the same.
