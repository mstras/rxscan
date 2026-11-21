# Changelog

## 1.1.1
**Dependency updates.** No new features and no setting changes.
- The image now runs on Python 3.15 (was 3.12).
- Photo reading: Pillow 13 and pillow-heif 1.8 (iPhone HEIC photos).
- Newer minimum versions of httpx, requests and google-auth.
- CI: newer versions of the checkout and Docker build actions.

## 1.1.0
**Everyone gets their own sheet and folder.**
- Sign in with Google. Only emails on the People list can sign in.
- On first sign-in, RxScan creates the person's own sheet (bold headers, frozen top row) and a "RxScan Receipts" Drive folder. They can switch to existing ones with the Google file picker, create new ones, choose the tab, and turn per-year photo folders on or off.
- Uses Google's `drive.file` permission: RxScan only reaches files it created or that the person picked.
- Admin pages: **People** (add, make admin, remove, which also revokes Google access and signs them out), **Reading receipts**, and **Google setup** (a checklist with the exact addresses to paste, plus a credentials test).
- Members can't change admin settings. The last admin can't be removed or demoted.
- The admin password (admin / admin123 by default) stays as a fallback and keeps the 1.0 server-wide Apps Script connection.

**New design**
- Pharmacy-label review cards. Fields the model couldn't read clearly turn the label amber and name the field. Editing a flagged field clears its flag.
- Desktop layout with a sidebar, a pinned scan panel, drag-and-drop photos, and labels in columns.
- Bundled fonts (Public Sans, Barlow Condensed) so the installed app looks right offline. Dark mode.

**Fixes**
- The welcome notice no longer disappears on the very first visit (the service worker reloaded the page when it first took control).

**Upgrade notes:** everyone is signed out once, because the sign-in cookie format changed. New data (`rxscan.db`, `secret.key`) is created in `./data` automatically.

## 1.0.0
First release: photo → vision model → review → Google Sheet via an Apps Script bridge or service account; receipt copies to Drive or the server; installable PWA with an offline queue; sign-in page.
