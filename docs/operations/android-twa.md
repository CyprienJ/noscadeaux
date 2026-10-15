# Android TWA

The Android application opens the existing Django website. Models, sessions,
CSRF protection and templates continue to work as on the web. No separate API is
required. This repository supplies the website resources; it does not yet contain
an Android project or a Play Store release.

## Website resources

- `/manifest.webmanifest`: stable application ID, root start URL and scope,
  standalone display, theme colors and icon URLs. The root start URL keeps
  Django's existing language negotiation and signed-in redirect behavior.
- `/service-worker.js`: registered by the shared base template in a secure
  browser context. Served from the domain root with revalidation enabled.
- `/offline/`: self-contained French/English fallback, cached without personal
  information. No CDN or authenticated resource is needed to render it.
- `/.well-known/assetlinks.json`: public Android association, outside
  `i18n_patterns`, with no redirect or authentication requirement.
- `/fr/account-deletion/` and `/en/account-deletion/`: public account deletion
  information, email request option and authenticated deletion confirmation.
  These remain accessible during onboarding. Deletion still requires POST and CSRF.

The worker caches only the offline document. Personal pages, API responses,
uploads and reservations are not cached or queued for replay. Offline navigation
falls back to this document without changing the requested URL; Retry reloads
that URL. A first successful visit is needed to install the offline fallback.
HTTP errors remain visible. Bump `noscadeaux-offline-v1` in the worker when the
fallback changes; a worker update becomes active after existing clients close.

## Icons to finalize

The manifest automatically picks up PNG files at `gifts/static/gifts/pwa/icon-192.png` and
`gifts/static/gifts/pwa/icon-512.png`. Supply the chosen square artwork at these
sizes before packaging. Until supplied, the manifest references the existing
favicon with its actual sizes; this fallback does not provide the large icons
needed for packaging. The existing favicon is small; a larger original is
preferable to enlarging it. These icons are declared as `any`, not `maskable`.
If adding maskable artwork, respect its safe zone and add a separate manifest
entry. Run `collectstatic` in the normal deployment flow.

## Android association and deployment

1. Generate an Android TWA project with Bubblewrap using the production manifest.
   Use `com.cyprienj.noscadeaux` as the Android application ID. This public package
   name is defined directly by `TWA_PACKAGE_NAME` in `config/settings.py`;
   no environment or GitHub variable is needed for it.
2. Set `TWA_SHA256_FINGERPRINTS` in the deployment `.env`.
   Docker Compose passes it to Django. Fingerprints are comma-separated.
   For the GitHub Actions deployment, set a repository variable of the same name;
   the workflow writes it to the server's `.env`.
3. Use the **Play App Signing certificate** fingerprint from Play Console for
   Store installations, rather than only the upload key. You can include an
   additional signing fingerprint for local release testing.
4. Redeploy/recreate the web service so it receives the settings. Without a
   fingerprint, the endpoint returns `[]`: association is not configured yet.
5. Verify the exact launch domain serves the JSON with status 200 and
   `Content-Type: application/json`, without a redirect or a proxy/CDN challenge.
   If the TWA uses both `www.noscadeaux.fr` and `noscadeaux.fr`, configure and
   validate both origins in Android and serve the association on both hosts.
6. Build the signed Android App Bundle and test via a Play testing track before
   submitting it. In-app links on the verified origin should stay full screen;
   external sites can display browser UI.

The web deployment continues to provide application changes. Updates to the
Android package, signing, permissions or Android SDK settings require a new
Store release.

## Validation before publication

- Check manifest icons return real PNGs with the declared dimensions.
- Confirm association works for the build installed **from Play** and that no
  browser address bar appears on the launch origin.
- Test French/English navigation, registration including Turnstile, login,
  logout, password reset, account deletion, gift reservations, photo upload,
  CSV download, external shop links and Android Back.
- Visit online, close/reopen as needed for worker activation, then disable the
  network and navigate/relaunch. Check Retry succeeds when the network returns.
- Test account switching and ensure no previous user's pages appear offline.
- Complete the Play Console listing, privacy policy URL, data safety declaration,
  account deletion URL and testing/review requirements applicable to the account.

References: [Android TWA quick start](https://developer.android.com/develop/ui/views/layout/webapps/guide-trusted-web-activities-version2),
[asset links](https://developer.android.com/training/app-links/configure-assetlinks),
[account deletion requirements](https://support.google.com/googleplay/android-developer/answer/13327111).
