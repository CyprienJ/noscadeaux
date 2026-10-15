// Cache only a public, self-contained fallback. Personal pages and mutations
// always go to the network; reservations must never be replayed offline.
const CACHE_NAME = 'noscadeaux-offline-v1';
const OFFLINE_URL = '/offline/';

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.add(new Request(OFFLINE_URL, {cache: 'reload'})))
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(key => key.startsWith('noscadeaux-offline-') && key !== CACHE_NAME)
                .map(key => caches.delete(key))
        )).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET' || event.request.mode !== 'navigate') return;
    if (new URL(event.request.url).origin !== self.location.origin) return;
    event.respondWith(
        fetch(event.request).catch(async () => {
            const cache = await caches.open(CACHE_NAME);
            return (await cache.match(OFFLINE_URL)) || Response.error();
        })
    );
});
