"use strict";

// ---------------------------------------------------------------------
// The code of the sign-in email (backend/auth.py: verify_code, see
// docs/sign-in.md): once a link was asked for, the sign-in page offers to
// type the email's code instead of opening its link. That is the way in
// where the link can't be opened: the app installed on an iPhone keeps its
// own cookies, apart from the browser the link opens in, and so does a
// computer when the email is read on a phone. Loaded after app.js, whose
// sign-in form says when a link was asked for ("signin:sent").
// ---------------------------------------------------------------------
{
  const form = document.getElementById("signin-code-form");
  const input = document.getElementById("signin-code");
  const button = document.getElementById("signin-code-submit");
  let askedFor = ""; // the address the last link was asked for

  function reset(shown) {
    form.hidden = !shown;
    input.value = "";
    hideStatus("signin-code-status");
  }

  window.addEventListener("signin:sent", (e) => {
    askedFor = e.detail.email;
    reset(true);
  });

  // Signed in another way meanwhile (the link, in another tab), or signed out again.
  window.addEventListener("user:changed", () => reset(false));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = input.value.replace(/\s/g, "");
    if (!/^[0-9]{6}$/.test(code)) return showStatus("signin-code-status", t("signin.needCode"), false);
    button.disabled = true;
    try {
      const data = await postJson("/api/auth/code", { email: askedFor, code });
      hideStatus("signin-status");
      setUser(data.user); // hides this form ("user:changed")
      route(); // the page the user came from, or the profile page the first time
    } catch (err) {
      showStatus("signin-code-status", err.message, false);
    } finally {
      button.disabled = false;
    }
  });
}
