#!/usr/bin/env bash
# Creates the Android app's upload key, once: the key every build uploaded to Google Play is
# signed with (see README.md, "The upload key"). Needs only openssl; with --github it also
# stores the key in the repository's secrets, for the "Android app" workflow.
#
#   mobile/android/make-upload-key.sh [--github] [folder]
#
# The folder (default: ~/rideandrail-android-upload-key) gets android.keystore and
# password.txt. Keep both in a password manager; never commit them.
set -euo pipefail

github=no
if [ "${1:-}" = "--github" ]; then github=yes; shift; fi
folder="${1:-$HOME/rideandrail-android-upload-key}"

if [ -e "$folder/android.keystore" ]; then
  echo "There is already a key in $folder: not replaced. A new upload key must be registered with Google Play." >&2
  exit 1
fi
umask 077
mkdir -p "$folder"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

password=$(openssl rand -hex 24)
openssl req -x509 -newkey rsa:2048 -sha256 -days 10000 -nodes \
  -subj "/CN=Ride and Rail upload key" -keyout "$work/key.pem" -out "$work/cert.pem" 2>/dev/null
# A PKCS #12 keystore, as Java's keytool makes them, with the alias of twa-manifest.json.
openssl pkcs12 -export -name android -inkey "$work/key.pem" -in "$work/cert.pem" \
  -out "$folder/android.keystore" -passout "pass:$password"
printf '%s\n' "$password" > "$folder/password.txt"

echo "Upload key written to $folder (android.keystore, password.txt)."
echo "Its certificate's SHA-256 fingerprint (public; for ANDROID_CERT_SHA256 if you install your own builds):"
openssl x509 -in "$work/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2

if [ "$github" = yes ]; then
  base64 -w0 "$folder/android.keystore" | gh secret set ANDROID_KEYSTORE
  gh secret set ANDROID_KEYSTORE_PASSWORD < "$folder/password.txt"
  echo "Stored in the repository's secrets ANDROID_KEYSTORE and ANDROID_KEYSTORE_PASSWORD."
fi
