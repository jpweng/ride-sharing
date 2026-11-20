"use strict";

// ---------------------------------------------------------------------
// Profiles (backend/profiles.py, see docs/profiles.md): the user's own on
// the account page (everything optional: a photo, a few words, five things
// to say or not, and for drivers their car), and "See profile" on the cards
// of the people one may ride with, which opens it in a popup. Loaded after
// app.js, bookings.js and ratings.js, whose lists of extras it adds to.
// ---------------------------------------------------------------------
const PROFILE_TRAITS = ["talkative", "sleeps", "smoker", "pets", "music"];
const PHOTO_SIZE = 512; // the photo is cut to a square of this many pixels
const PHOTO_QUALITY = 0.85;

function traitLabel(trait) {
  if (trait === "talkative") return t("profile.traitTalkative");
  if (trait === "sleeps") return t("profile.traitSleeps");
  if (trait === "smoker") return t("profile.traitSmoker");
  if (trait === "pets") return t("profile.traitPets");
  return t("profile.traitMusic");
}

function photoUrl(profile) {
  return `/api/profiles/${profile.user_id}/photo?v=${encodeURIComponent(profile.photo_version || "")}`;
}

function carText(profile) {
  const car = [profile.car_color, profile.car_make, profile.car_model].filter(Boolean).join(" ");
  return [car, profile.car_year].filter(Boolean).join(", ");
}

// ---------------------------------------------------------------------
// One's own profile, on the account page
// ---------------------------------------------------------------------
let ownProfile = null;

async function loadOwnProfile() {
  const box = document.getElementById("profile-box");
  // Not while viewing the demo as a fictional driver, nor before the name is chosen.
  box.hidden = true;
  if (!signedIn() || currentUser.is_demo) return;
  try {
    ownProfile = await fetchJson("/api/profile");
  } catch (err) {
    showStatus("account-status", err.message, false);
    return;
  }
  box.hidden = false;
  renderOwnProfile();
}

function renderOwnProfile() {
  const p = ownProfile;
  document.getElementById("profile-about").value = p.about || "";
  PROFILE_TRAITS.forEach((trait) => {
    const select = document.getElementById(`profile-${trait}`);
    select.innerHTML = [["", t("profile.optUnsaid")], ["yes", t("profile.optYes")], ["no", t("profile.optNo")]]
      .map(([value, label]) => `<option value="${value}">${esc(label)}</option>`)
      .join("");
    select.value = p[trait] === null ? "" : p[trait] ? "yes" : "no";
  });
  document.getElementById("profile-car-make").value = p.car_make || "";
  document.getElementById("profile-car-model").value = p.car_model || "";
  document.getElementById("profile-car-year").value = p.car_year || "";
  document.getElementById("profile-car-color").value = p.car_color || "";

  // The photo: only when the server can store photos.
  document.getElementById("profile-photo-box").hidden = !p.photos_enabled;
  const img = document.getElementById("profile-photo");
  img.hidden = !p.has_photo;
  if (p.has_photo) img.src = photoUrl(p);
  document.getElementById("profile-photo-remove").hidden = !p.has_photo;
}

document.getElementById("profile-save").addEventListener("click", async () => {
  const text = (id) => document.getElementById(id).value.trim() || null;
  const body = { about: text("profile-about") };
  PROFILE_TRAITS.forEach((trait) => {
    const value = document.getElementById(`profile-${trait}`).value;
    body[trait] = value === "" ? null : value === "yes";
  });
  const year = Number(document.getElementById("profile-car-year").value);
  Object.assign(body, {
    car_make: text("profile-car-make"), car_model: text("profile-car-model"),
    car_year: year || null, car_color: text("profile-car-color"),
  });
  try {
    ownProfile = await postJson("/api/profile", body, "PUT");
    renderOwnProfile();
    showStatus("profile-own-status", t("profile.saved"), true);
  } catch (err) {
    showStatus("profile-own-status", err.message, false);
  }
});

// The chosen image is cut to a centred square and shrunk here, in the
// browser: what is sent is small, and carries none of the file's own data
// (where and with what it was taken).
async function squareJpeg(file) {
  const image = await createImageBitmap(file, { imageOrientation: "from-image" });
  const side = Math.min(image.width, image.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = Math.min(PHOTO_SIZE, side);
  canvas.getContext("2d").drawImage(
    image, (image.width - side) / 2, (image.height - side) / 2, side, side, 0, 0, canvas.width, canvas.height,
  );
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("no image"))), "image/jpeg", PHOTO_QUALITY);
  });
}

document.getElementById("profile-photo-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = ""; // the same file can be chosen again
  if (!file) return;
  let photo;
  try {
    photo = await squareJpeg(file);
  } catch {
    return showStatus("profile-own-status", t("profile.photoFailed"), false);
  }
  try {
    ownProfile = await fetchJson("/api/profile/photo", {
      method: "PUT", headers: { "Content-Type": "image/jpeg" }, body: photo,
    });
    hideStatus("profile-own-status");
    renderOwnProfilePhoto();
  } catch (err) {
    showStatus("profile-own-status", err.message, false);
  }
});

document.getElementById("profile-photo-remove").addEventListener("click", async () => {
  try {
    ownProfile = await fetchJson("/api/profile/photo", { method: "DELETE" });
    renderOwnProfilePhoto();
  } catch (err) {
    showStatus("profile-own-status", err.message, false);
  }
});

// After the photo changed: only the photo, so what is being typed stays.
function renderOwnProfilePhoto() {
  const img = document.getElementById("profile-photo");
  img.hidden = !ownProfile.has_photo;
  if (ownProfile.has_photo) img.src = photoUrl(ownProfile);
  document.getElementById("profile-photo-remove").hidden = !ownProfile.has_photo;
}

// ---------------------------------------------------------------------
// Someone else's profile, in a popup
// ---------------------------------------------------------------------
function profileDialog() {
  let dialog = document.getElementById("profile-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "profile-dialog";
    dialog.className = "notice profile-dialog";
    dialog.innerHTML = '<div id="profile-dialog-body"></div><button type="button" class="submit-btn" id="profile-dialog-ok"></button>';
    document.body.appendChild(dialog);
    dialog.querySelector("#profile-dialog-ok").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close(); // the backdrop
    });
  }
  return dialog;
}

async function openProfile(userId) {
  let p;
  try {
    p = await fetchJson(`/api/profiles/${userId}`);
  } catch (err) {
    return showNotice(t("profile.see"), err.message);
  }
  const said = PROFILE_TRAITS.filter((trait) => p[trait] !== null)
    .map((trait) => `<li>${esc(traitLabel(trait))}: ${esc(p[trait] ? t("profile.optYes") : t("profile.optNo"))}</li>`);
  const car = carText(p);
  if (car) said.push(`<li>${esc(t("profile.car", { car }))}</li>`);
  const empty = !p.about && !said.length && !p.has_photo;
  const dialog = profileDialog();
  dialog.querySelector("#profile-dialog-body").innerHTML = `
    <div class="profile-head">
      ${p.has_photo ? `<img class="profile-photo" src="${esc(photoUrl(p))}" alt="" />` : ""}
      <div class="notice-title">${esc(p.name)}</div>
    </div>
    ${p.about ? `<p class="profile-about">${esc(p.about)}</p>` : ""}
    ${said.length ? `<ul>${said.join("")}</ul>` : ""}
    ${empty ? `<p>${esc(t("profile.empty", { name: p.name }))}</p>` : ""}
  `;
  dialog.querySelector("#profile-dialog-ok").textContent = t("common.ok");
  if (!dialog.open) dialog.showModal();
}

// "See profile", where the server lets one see it (profiles.may_see): on a
// search result (a candidate driver, not a demo one), on a booking (the
// other side), and on a ride request the driver was told about.
function profileLink(userId) {
  const box = document.createElement("div");
  box.className = "profile-link-box";
  box.addEventListener("click", (e) => e.stopPropagation()); // not a click on the card around it
  box.appendChild(actionButton("profile.see", "link-btn", () => openProfile(userId)));
  return box;
}

CARD_EXTRAS.push((kind, item, card) => {
  if (kind !== "match" || item.is_demo || !signedIn() || currentUser.is_demo) return;
  // The driver's photo, when they have one (the match's driver_photo), over
  // the initial in the card's circle; the initial stays if it can't be loaded.
  const avatar = card.querySelector(".avatar");
  if (avatar && item.driver_photo) {
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.addEventListener("error", () => img.remove());
    img.src = photoUrl({ user_id: item.driver_id, photo_version: item.driver_photo });
    avatar.appendChild(img);
  }
  if (item.driver_name === currentUser.name) return;
  card.appendChild(profileLink(item.driver_id));
});

BOOKING_EXTRAS.push((booking, element, otherRole) => {
  element.appendChild(profileLink(otherRole === "driver" ? booking.drive.driver_id : booking.passenger_id));
});

REQUEST_EXTRAS.push((request, actions, context) => {
  // In the inbox only: the list of requests in one's areas shows names alone.
  if (!context.notification || !request.passenger_id) return;
  actions.after(profileLink(request.passenger_id));
});

// ---------------------------------------------------------------------
// Hooking into app.js's account page
// ---------------------------------------------------------------------
{
  const showAccountPage = PAGES.account.onShow;
  PAGES.account.onShow = (...args) => {
    showAccountPage(...args);
    hideStatus("profile-own-status");
    loadOwnProfile();
  };
}
if (document.getElementById("page-account").classList.contains("active")) loadOwnProfile();
