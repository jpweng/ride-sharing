"use strict";

// ---------------------------------------------------------------------
// Sign-in with Google or Apple (backend/social.py, see
// docs/social-login.md), on the sign-in page under the email form. The
// buttons are plain links to /auth/<provider>/start: the server sends the
// browser to the provider and back. Shown only for the providers the
// server has keys for (GET /api/auth/providers). Loaded after app.js,
// whose pages and helpers it uses.
// ---------------------------------------------------------------------

async function showSocialSignIn() {
  const box = document.getElementById("signin-social");
  let providers = [];
  try {
    providers = (await fetchJson("/api/auth/providers")).providers;
  } catch {
    // the email link still works
  }
  box.querySelectorAll("[data-provider]").forEach((link) => {
    link.hidden = !providers.includes(link.dataset.provider);
    // The provider's page, and the emails afterwards, in the page's language.
    link.href = `/auth/${link.dataset.provider}/start?lang=${I18N.lang()}`;
  });
  box.hidden = !providers.length;
}

// The server sent the browser back to /?signin_error=<code>: say why on the
// sign-in page, and take it out of the address.
function showSocialSignInError() {
  const params = new URLSearchParams(location.search);
  const code = params.get("signin_error");
  if (!code) return;
  params.delete("signin_error");
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
  // The code comes from the address: only the ones the server sends have a
  // message of their own, anything else is "it didn't work".
  const message = code === "social_cancelled" ? t("errors.social_cancelled")
    : code === "social_email_unverified" ? t("errors.social_email_unverified")
    : code === "account_banned" ? t("errors.account_banned")
    : t("errors.social_failed");
  showStatus("signin-notice", message, false);
}

// First sign-in with a provider: the name it gave is filled in on the
// profile page, for the user to check (it can't be changed afterwards).
function suggestProfileName() {
  const suggested = currentUser?.suggested_name;
  if (!suggested) return;
  [["profile-first-name", suggested.first_name], ["profile-last-name", suggested.last_name]].forEach(([id, value]) => {
    const input = document.getElementById(id);
    if (value && !input.value) input.value = value;
  });
}

{
  const showSignIn = PAGES.signin.onShow;
  PAGES.signin.onShow = (...args) => {
    showSignIn(...args);
    showSocialSignIn();
  };
  const showProfilePage = PAGES.profile.onShow;
  PAGES.profile.onShow = (...args) => {
    showProfilePage(...args);
    suggestProfileName();
  };
}

// app.js may have shown a page before this script ran (it routes as soon
// as it knows who is signed in).
I18N.ready.then(() => {
  showSocialSignInError();
  if (document.getElementById("page-signin").classList.contains("active")) showSocialSignIn();
  if (document.getElementById("page-profile").classList.contains("active")) suggestProfileName();
});
