const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../templates/pwa/service-worker.js'), 'utf8');

function worker(fetch) {
    const handlers = {};
    const fallback = new Response('offline');
    const cached = [];
    const deleted = [];
    const cache = {
        add: async request => cached.push(request.url),
        match: async url => url === '/offline/' ? fallback : undefined,
    };
    // Browser Request accepts relative URLs; Node Request requires an absolute URL.
    class BrowserRequest extends Request {
        constructor(url, options) { super(new URL(url, 'https://noscadeaux.fr'), options); }
    }
    vm.runInNewContext(source, {
        self: {
            addEventListener: (type, handler) => { handlers[type] = handler; },
            location: {origin: 'https://noscadeaux.fr'},
            clients: {claim: async () => {}},
        },
        caches: {
            open: async () => cache,
            keys: async () => ['noscadeaux-offline-v0', 'noscadeaux-offline-v1', 'other-app'],
            delete: async key => deleted.push(key),
        },
        fetch, Request: BrowserRequest, Response, URL,
    });
    return {handlers, fallback, cached, deleted};
}

test('only the public fallback is cached, and only obsolete app caches are removed', async () => {
    const {handlers, cached, deleted} = worker();
    let pending;
    handlers.install({waitUntil: promise => { pending = promise; }});
    await pending;
    assert.deepEqual(cached, ['https://noscadeaux.fr/offline/']);
    handlers.activate({waitUntil: promise => { pending = promise; }});
    await pending;
    assert.deepEqual(deleted, ['noscadeaux-offline-v0']);
});

test('offline navigation shows the fallback while keeping the original page URL', async () => {
    const {handlers, fallback} = worker(async () => { throw new TypeError('offline'); });
    let pending;
    handlers.fetch({
        request: {method: 'GET', mode: 'navigate', url: 'https://noscadeaux.fr/fr/dashboard/'},
        respondWith: promise => { pending = promise; },
    });
    assert.equal(await pending, fallback);
});

test('HTTP errors are preserved instead of being hidden as offline failures', async () => {
    const forbidden = new Response('forbidden', {status: 403});
    const {handlers} = worker(async () => forbidden);
    let pending;
    handlers.fetch({
        request: {method: 'GET', mode: 'navigate', url: 'https://noscadeaux.fr/fr/dashboard/'},
        respondWith: promise => { pending = promise; },
    });
    assert.equal(await pending, forbidden);
});

test('mutations, API requests and external origins are left to the browser', () => {
    const {handlers} = worker(() => assert.fail('unexpected fetch'));
    for (const request of [
        {method: 'POST', mode: 'navigate', url: 'https://noscadeaux.fr/fr/account/delete/'},
        {method: 'GET', mode: 'cors', url: 'https://noscadeaux.fr/api/'},
        {method: 'GET', mode: 'navigate', url: 'https://example.com/'},
    ]) {
        handlers.fetch({request, respondWith: () => assert.fail('unexpected interception')});
    }
});
