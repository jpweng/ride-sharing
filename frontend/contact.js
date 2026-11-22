"use strict";

// ---------------------------------------------------------------------
// The contact page (contact.html, docs/contact.md): a form whose message
// the server emails to the team (POST /api/contact). The page stands on its
// own, without app.js: only i18n.js is loaded before this file.
//
// The form is switched off until the server knows where to send messages
// (GET /api/contact): the page then says so instead of showing a form that
// can't work.
// ---------------------------------------------------------------------
(() => {
  const form = document.getElementById("contact-form");
  const statusEl = document.getElementById("contact-status");
  const sendBtn = document.getElementById("contact-send");
  const field = (name) => document.getElementById(`contact-${name}`);

  // What the status line says, as a function, so that it follows a change
  // of language.
  let status = null;

  function renderStatus() {
    statusEl.className = status ? `status-msg show ${status.kind}` : "status-msg";
    statusEl.textContent = status ? status.text() : "";
  }

  function setStatus(kind, text) {
    status = kind ? { kind, text } : null;
    renderStatus();
  }

  // Like app.js's fetchJson: the answer's JSON, or an error whose text() is
  // the translation of the API's error code.
  async function api(url, options) {
    const resp = await fetch(url, options);
    let data = {};
    try {
      data = await resp.json();
    } catch {
      // e.g. the proxy's 429, which has no JSON body
    }
    if (!resp.ok) {
      const code = data.detail?.code || (resp.status === 429 ? "too_many_requests" : null);
      const error = new Error(code || String(resp.status));
      error.text = () => (code ? I18N.t(`errors.${code}`) : t("errors.request_failed", { status: resp.status }));
      throw error;
    }
    return data;
  }

  function failed(error) {
    // A network failure has no text() of its own.
    setStatus("err", error.text || (() => t("errors.offline")));
  }

  async function start() {
    try {
      const { enabled } = await api("/api/contact");
      if (!enabled) {
        form.hidden = true;
        setStatus("err", () => t("errors.contact_unavailable"));
        return;
      }
    } catch {
      // Unknown: leave the form, sending will say what is wrong.
    }
    // Signed in: the name and the address are known already.
    try {
      const { user } = await api("/api/auth/me");
      if (user && !user.is_demo) {
        if (!field("name").value) field("name").value = [user.first_name, user.last_name].filter(Boolean).join(" ");
        if (!field("email").value) field("email").value = user.email || "";
      }
    } catch {
      // Not signed in, or offline: the visitor types them.
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = {
      name: field("name").value.trim(),
      email: field("email").value.trim(),
      message: field("message").value.trim(),
      website: field("website").value,
      language: I18N.lang(),
    };
    if (!message.name || !message.email || !message.message) {
      setStatus("err", () => t("errors.contact_incomplete"));
      return;
    }
    sendBtn.disabled = true;
    sendBtn.textContent = t("contact.sending");
    setStatus(null);
    try {
      await api("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(message),
      });
      form.hidden = true;
      form.reset();
      setStatus("ok", () => t("contact.sent", { email: message.email }));
    } catch (error) {
      failed(error);
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = t("contact.send");
    }
  });

  window.addEventListener("i18n:change", renderStatus);
  I18N.ready.then(start);
})();
