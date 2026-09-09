# Firefox extension and API

[Architecture](README.md)

The Firefox Desktop extension extracts product details in the browser, lets the
user review them in a sidebar, and sends the selected values to Django. The backend
stores submitted product/image URLs; quick-add does not fetch the shop page itself.

## Browser components

| Source | Responsibility |
| --- | --- |
| [manifest.json](../../firefox-extension/manifest.json) | Permissions, entry points, compatibility |
| [background.js](../../firefox-extension/background.js) | Tab capture, permissions, authorization coordination |
| [extractor.js](../../firefox-extension/extractor.js) | Product detection and field extraction |
| [sidebar/](../../firefox-extension/sidebar/) | Review/edit captured product and submit |
| [options/](../../firefox-extension/options/) | Server configuration |

Extraction prioritizes structured Product data and uses page signals as fallbacks.
Shop-origin permission enables refreshes after product options change. The project
targets Firefox Desktop 140+; the sidebar UI does not support Android.
Use the [extension development guide](../../firefox-extension/README.md) for loading,
permissions, tests, and packaging.

## Authorization flow

1. The extension generates state and a PKCE verifier/challenge.
2. `/extension/authorize/` requires browser login and shows a consent page.
3. The CSRF-protected consent POST issues a one-time code and redirects to the
   validated Firefox HTTPS identity callback with code and state.
4. The extension exchanges code, verifier, and redirect URI at the token endpoint.
5. Subsequent API calls use `Authorization: Bearer <token>`.
6. Disconnect revokes that token through the revoke endpoint.

Codes expire after five minutes. Only hashes of codes and bearer tokens are
stored. Code exchange locks the authorization row, checks expiry, reuse,
redirect URI and PKCE, then marks it used. Callback validation restricts the host
to a subdomain of `extensions.allizom.org` and rejects credentials, ports, query
strings, and fragments.

Access tokens use a lookup prefix plus a secret. Authentication requires an
unrevoked token and an active, verified user, compares hashes, and updates the
last-use timestamp. There is no access-token expiry field in the current model.

## Endpoints

These routes have no language prefix.

| Method and path | Input / result |
| --- | --- |
| GET/POST `/extension/authorize/` | `redirect_uri`, `state`, `code_challenge`; consent and redirect |
| GET `/extension/privacy/` | Privacy information |
| POST `/api/extension/token/` | JSON code/verifier/redirect URI; returns access token |
| GET `/api/extension/me/` | Current user ID/nickname and group IDs/names |
| POST `/api/extension/quick-add/` | Reviewed product; returns created gift and list URL |
| POST `/api/extension/revoke/` | Revokes current token; returns 204 |

JSON bodies must be objects and are limited to 64 KiB. Token, quick-add, and revoke
POSTs are CSRF-exempt because they do not use session cookies for authentication.

## Quick-add payload

```json
{
  "title": "Example book",
  "url": "https://shop.example/books/example",
  "image_url": "https://shop.example/images/example.jpg",
  "price": "19.90",
  "currency": "EUR",
  "visible_in": [12]
}
```

Title is required and limited to 200 characters. Product URL is required; URLs
must be HTTP/HTTPS and at most 1,000 characters. Image URL and price are optional.
Price must be finite, nonnegative, and at most 99,999.99. Currency defaults to EUR
and must be three letters; it is normalized to uppercase.

`visible_in` is an optional list of integer group IDs, all belonging to the user.
An empty list leaves the gift's group selection empty. Creation is atomic; invalid
group selection rolls it back. Existing non-offered, non-shared gifts for the same
owner and exact product URL cause a 409 response.

Success is 201 with `gift.id`, `gift.title`, and `gift.list_url`. Typical failures
are 400 for invalid input, 401 for authentication/code failure, 403 for invalid
groups, and 409 for duplicate products. Errors use an `error` string.

## Sources and tests

- [extension_api.py](../../gifts/extension_api.py),
  [extension_urls.py](../../gifts/extension_urls.py).
- [test_extension_api.py](../../gifts/test_extension_api.py).
- [Extension tests](../../firefox-extension/tests/).

The repository's [api.yaml](../../api.yaml) is an additional API description.
For current routing and payload behavior, use the URL modules, handlers, and tests.
