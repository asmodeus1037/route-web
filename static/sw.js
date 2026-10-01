var CACHE = 'route-master-v2';

self.addEventListener('install', function() {
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function(event) {
    var request = event.request;
    if (request.method !== 'GET') return;
    var url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.indexOf('/api/') === 0) return;

    var canCache = url.pathname.indexOf('/master') === 0
        || url.pathname.indexOf('/static/') === 0
        || url.pathname === '/login'
        || url.pathname === '/manifest.json'
        || url.pathname.indexOf('/data/') === 0;

    event.respondWith(
        fetch(request).then(function(response) {
            if (response && response.ok && canCache) {
                var copy = response.clone();
                caches.open(CACHE).then(function(cache) {
                    cache.put(request, copy);
                });
            }
            return response;
        }).catch(function() {
            return caches.match(request);
        })
    );
});
