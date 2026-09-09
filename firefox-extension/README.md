# nosCadeaux Firefox Quick Add

[Project documentation](../README.md) · [Architecture and API protocol](../docs/architecture/extension.md)

This release targets Firefox Desktop 140 and later. Firefox for Android is not supported because the extension uses
the desktop sidebar API.

## Local development

1. Run Django with `DEV=True` on `http://127.0.0.1:8000`.
2. Open `about:debugging#/runtime/this-firefox` in Firefox.
3. Choose **Load Temporary Add-on** and select `manifest.json`.
4. Open the extension settings and set the server to `http://127.0.0.1:8000`.
5. Visit a product page and click the extension toolbar button.
6. Connect the extension, review the extracted fields, and add the product.

Use **Refresh** in the sidebar to scan the active tab again after product options or dynamically loaded prices change.

Product classification prioritizes Schema.org `Product` data and falls back to product URLs, identifiers, prices,
images, and purchase controls. French actions such as “Ajouter au panier” and “Précommander” are supported.

On the first scan of a shop, Firefox asks for access to that shop's origin. This permission makes subsequent
sidebar refreshes reliable and can be revoked from Firefox's add-on permissions screen.

Firefox match patterns do not support port numbers. Local permissions therefore use
`http://127.0.0.1/*` and `http://localhost/*`; the server URL in settings may still include `:8000`.
After changing `manifest.json`, reload the temporary add-on from `about:debugging`.

The extension requests temporary `activeTab` access only after a toolbar click. Its bearer token can only call the extension API and can be revoked with **Disconnect**.

## Tests

Run the extension unit tests from the repository root:

```bash
node --test firefox-extension/tests/*.test.js
```

The suite covers product extraction as well as background-worker permissions, successful captures, and error handling.

## Packaging

Run `web-ext lint` and `web-ext build` from this directory before submitting the generated archive to addons.mozilla.org.
The Mozilla linter may report Android compatibility warnings for `permissions.request`; select Firefox Desktop only
when submitting to AMO. Do not add `gecko_android` compatibility metadata until an Android-specific interface exists.
