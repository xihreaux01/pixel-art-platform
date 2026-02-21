/**
 * PixelForge - Pixel Art Platform
 * Single-page application with hash-based routing.
 * Vanilla JS, no frameworks.
 */
(function () {
    'use strict';

    // =========================================================================
    // Configuration
    // =========================================================================
    const API = {
        AUTH:        '/api/v1/auth',
        CREDITS:     '/api/v1/credits',
        GENERATIONS: '/api/v1/generations',
        MARKETPLACE: '/api/v1/marketplace',
        ART:         '/api/v1/art',
    };

    // =========================================================================
    // State
    // =========================================================================
    const state = {
        user: null,          // { email, username } after login
        isLoggedIn: false,
        balance: null,
        phoneVerified: false,
        currentEventSource: null,
    };

    // =========================================================================
    // Utility helpers
    // =========================================================================

    /** Fetch wrapper with credentials and JSON handling. */
    async function api(path, options = {}) {
        const opts = {
            credentials: 'include',
            headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
            ...options,
        };
        if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
            opts.body = JSON.stringify(opts.body);
        }
        const res = await fetch(path, opts);
        if (res.status === 401) {
            // Attempt silent refresh once
            const refreshed = await tryRefresh();
            if (refreshed) {
                const retry = await fetch(path, opts);
                return handleResponse(retry);
            }
            state.isLoggedIn = false;
            state.user = null;
            navigate('/login');
            throw new Error('Session expired');
        }
        return handleResponse(res);
    }

    async function handleResponse(res) {
        let data = null;
        const text = await res.text();
        try { data = JSON.parse(text); } catch { data = text; }
        if (!res.ok) {
            const msg = (data && data.detail) ? data.detail : `Request failed (${res.status})`;
            throw new Error(msg);
        }
        return data;
    }

    async function tryRefresh() {
        try {
            await fetch(API.AUTH + '/refresh', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
            });
            return true;
        } catch {
            return false;
        }
    }

    function navigate(path) {
        window.location.hash = path;
    }

    function escapeHtml(str) {
        const el = document.createElement('span');
        el.textContent = str;
        return el.innerHTML;
    }

    function formatCents(cents) {
        return '$' + (cents / 100).toFixed(2);
    }

    function formatDate(isoStr) {
        if (!isoStr) return '---';
        try {
            const d = new Date(isoStr);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString();
        } catch {
            return isoStr;
        }
    }

    function truncateId(id) {
        if (!id) return '---';
        return id.length > 12 ? id.substring(0, 8) + '...' : id;
    }

    // =========================================================================
    // Toast notifications
    // =========================================================================
    function showToast(message, type = 'info', duration = 4000) {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = 'toast ' + type;
        toast.textContent = message;
        toast.addEventListener('click', function () { toast.remove(); });
        container.appendChild(toast);
        setTimeout(function () { if (toast.parentNode) toast.remove(); }, duration);
    }

    // =========================================================================
    // Navbar management
    // =========================================================================
    function updateNav() {
        const links = document.querySelectorAll('#nav-links > *');
        links.forEach(function (el) {
            const authReq = el.getAttribute('data-auth');
            if (authReq === 'true') {
                el.style.display = state.isLoggedIn ? '' : 'none';
            } else if (authReq === 'false') {
                el.style.display = state.isLoggedIn ? 'none' : '';
            }
        });

        // Update balance display
        const balEl = document.getElementById('nav-balance');
        if (state.balance !== null) {
            balEl.textContent = state.balance + ' credits';
        } else {
            balEl.textContent = '';
        }

        // Highlight active link
        const hash = window.location.hash || '#/login';
        document.querySelectorAll('#nav-links a').forEach(function (a) {
            a.classList.toggle('active', hash.startsWith(a.getAttribute('href')));
        });
    }

    async function fetchBalance() {
        if (!state.isLoggedIn) return;
        try {
            const data = await api(API.CREDITS + '/balance');
            state.balance = data.credit_balance;
            updateNav();
        } catch {
            // silently ignore
        }
    }

    // =========================================================================
    // Router
    // =========================================================================
    const routes = {};

    function registerRoute(path, handler) {
        routes[path] = handler;
    }

    function getRouteAndParams() {
        const hash = (window.location.hash || '#/login').substring(1); // strip #
        // Check exact match first
        if (routes[hash]) return { handler: routes[hash], params: {} };

        // Check parameterized routes
        for (const pattern of Object.keys(routes)) {
            const paramNames = [];
            const regexStr = pattern.replace(/:([^/]+)/g, function (_, name) {
                paramNames.push(name);
                return '([^/]+)';
            });
            const match = hash.match(new RegExp('^' + regexStr + '$'));
            if (match) {
                const params = {};
                paramNames.forEach(function (name, i) {
                    params[name] = match[i + 1];
                });
                return { handler: routes[pattern], params: params };
            }
        }
        return null;
    }

    async function handleRoute() {
        // Close any open SSE connection
        if (state.currentEventSource) {
            state.currentEventSource.close();
            state.currentEventSource = null;
        }

        // Close mobile menu
        document.getElementById('nav-links').classList.remove('open');

        const route = getRouteAndParams();
        const appEl = document.getElementById('app');

        if (!route) {
            appEl.innerHTML = '<div class="empty-state"><h3>Page Not Found</h3><p><a href="#/dashboard">Go to Dashboard</a></p></div>';
            updateNav();
            return;
        }

        // Auth guard
        const hash = window.location.hash || '#/login';
        const authRequired = ['/dashboard', '/generate', '/collection', '/credits', '/provenance'];
        const needsAuth = authRequired.some(function (p) { return hash.substring(1).startsWith(p); });
        if (needsAuth && !state.isLoggedIn) {
            navigate('/login');
            return;
        }

        try {
            await route.handler(appEl, route.params);
        } catch (err) {
            appEl.innerHTML = '<div class="empty-state"><h3>Error</h3><p>' + escapeHtml(err.message) + '</p></div>';
        }
        updateNav();
    }

    // =========================================================================
    // Page: Login / Register
    // =========================================================================
    registerRoute('/login', function (container) {
        if (state.isLoggedIn) { navigate('/dashboard'); return; }

        container.innerHTML = '\
            <div class="auth-container">\
                <h1 class="text-center mb-3">PixelForge</h1>\
                <div class="auth-tabs">\
                    <button class="auth-tab active" data-tab="login">Login</button>\
                    <button class="auth-tab" data-tab="register">Register</button>\
                </div>\
                <div class="card">\
                    <form id="login-form" class="auth-form active">\
                        <div class="form-group">\
                            <label>Email</label>\
                            <input type="email" id="login-email" required autocomplete="email">\
                        </div>\
                        <div class="form-group">\
                            <label>Password</label>\
                            <input type="password" id="login-password" required autocomplete="current-password">\
                        </div>\
                        <button type="submit" class="btn btn-primary btn-block">Login</button>\
                    </form>\
                    <form id="register-form" class="auth-form">\
                        <div class="form-group">\
                            <label>Email</label>\
                            <input type="email" id="reg-email" required autocomplete="email">\
                        </div>\
                        <div class="form-group">\
                            <label>Username</label>\
                            <input type="text" id="reg-username" required autocomplete="username">\
                        </div>\
                        <div class="form-group">\
                            <label>Password</label>\
                            <input type="password" id="reg-password" required autocomplete="new-password" minlength="8">\
                        </div>\
                        <button type="submit" class="btn btn-primary btn-block">Create Account</button>\
                    </form>\
                </div>\
            </div>';

        // Tab toggle
        container.querySelectorAll('.auth-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                container.querySelectorAll('.auth-tab').forEach(function (t) { t.classList.remove('active'); });
                container.querySelectorAll('.auth-form').forEach(function (f) { f.classList.remove('active'); });
                tab.classList.add('active');
                var formId = tab.getAttribute('data-tab') + '-form';
                document.getElementById(formId).classList.add('active');
            });
        });

        // Login submit
        document.getElementById('login-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var btn = this.querySelector('button[type="submit"]');
            btn.disabled = true;
            btn.textContent = 'Logging in...';
            try {
                var data = await api(API.AUTH + '/login', {
                    method: 'POST',
                    body: {
                        email: document.getElementById('login-email').value,
                        password: document.getElementById('login-password').value,
                    },
                });
                state.isLoggedIn = true;
                state.user = { email: document.getElementById('login-email').value };
                showToast('Login successful!', 'success');
                await fetchBalance();
                navigate('/dashboard');
            } catch (err) {
                showToast(err.message, 'error');
                btn.disabled = false;
                btn.textContent = 'Login';
            }
        });

        // Register submit
        document.getElementById('register-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var btn = this.querySelector('button[type="submit"]');
            btn.disabled = true;
            btn.textContent = 'Creating...';
            try {
                await api(API.AUTH + '/register', {
                    method: 'POST',
                    body: {
                        email: document.getElementById('reg-email').value,
                        username: document.getElementById('reg-username').value,
                        password: document.getElementById('reg-password').value,
                    },
                });
                showToast('Account created! Please log in.', 'success');
                // Switch to login tab
                container.querySelectorAll('.auth-tab').forEach(function (t) { t.classList.remove('active'); });
                container.querySelectorAll('.auth-form').forEach(function (f) { f.classList.remove('active'); });
                container.querySelector('[data-tab="login"]').classList.add('active');
                document.getElementById('login-form').classList.add('active');
            } catch (err) {
                showToast(err.message, 'error');
                btn.disabled = false;
                btn.textContent = 'Create Account';
            }
        });
    });

    // =========================================================================
    // Page: Dashboard
    // =========================================================================
    registerRoute('/dashboard', async function (container) {
        container.innerHTML = '<div class="loading-container"><div class="spinner spinner-lg"></div><span>Loading dashboard...</span></div>';
        await fetchBalance();

        var phoneStatus = state.phoneVerified
            ? '<span class="status-badge verified">Phone Verified</span>'
            : '<span class="status-badge unverified" id="verify-phone-btn">Phone Not Verified - Click to Verify</span>';

        container.innerHTML = '\
            <div class="dashboard-header">\
                <div>\
                    <h1>Dashboard</h1>\
                    <div>' + phoneStatus + '</div>\
                </div>\
                <div>\
                    <span class="balance-label">Credit Balance</span>\
                    <div class="balance-display">' + (state.balance !== null ? state.balance : '---') + '</div>\
                </div>\
            </div>\
            <div class="pixel-divider"></div>\
            <h2 class="mb-2">Quick Actions</h2>\
            <div class="quick-actions">\
                <div class="quick-action-card" data-nav="#/generate">\
                    <div class="icon">&#9998;</div>\
                    <h3>Generate Art</h3>\
                    <p>Create pixel art with AI</p>\
                </div>\
                <div class="quick-action-card" data-nav="#/marketplace">\
                    <div class="icon">&#9878;</div>\
                    <h3>Marketplace</h3>\
                    <p>Browse & buy pixel art</p>\
                </div>\
                <div class="quick-action-card" data-nav="#/collection">\
                    <div class="icon">&#9733;</div>\
                    <h3>Collection</h3>\
                    <p>View your art pieces</p>\
                </div>\
                <div class="quick-action-card" data-nav="#/credits">\
                    <div class="icon">&#9733;</div>\
                    <h3>Credit Store</h3>\
                    <p>Buy credits for generation</p>\
                </div>\
            </div>';

        // Quick action navigation
        container.querySelectorAll('.quick-action-card').forEach(function (card) {
            card.addEventListener('click', function () {
                window.location.hash = card.getAttribute('data-nav');
            });
        });

        // Phone verify button
        var verifyBtn = document.getElementById('verify-phone-btn');
        if (verifyBtn) {
            verifyBtn.addEventListener('click', function () {
                openPhoneModal();
            });
        }
    });

    // =========================================================================
    // Phone Verification Modal
    // =========================================================================
    function openPhoneModal() {
        document.getElementById('phone-modal').classList.remove('hidden');
    }

    function closePhoneModal() {
        document.getElementById('phone-modal').classList.add('hidden');
    }

    function initPhoneModal() {
        document.getElementById('phone-modal-close').addEventListener('click', closePhoneModal);
        document.querySelector('#phone-modal .modal-backdrop').addEventListener('click', closePhoneModal);

        document.getElementById('phone-form').addEventListener('submit', async function (e) {
            e.preventDefault();
            var btn = this.querySelector('button[type="submit"]');
            btn.disabled = true;
            btn.textContent = 'Verifying...';
            try {
                await api(API.AUTH + '/verify-phone', {
                    method: 'POST',
                    body: { phone_number: document.getElementById('phone-input').value },
                });
                state.phoneVerified = true;
                showToast('Phone number verified!', 'success');
                closePhoneModal();
                // Refresh dashboard if on it
                if (window.location.hash === '#/dashboard') {
                    handleRoute();
                }
            } catch (err) {
                showToast(err.message, 'error');
            } finally {
                btn.disabled = false;
                btn.textContent = 'Verify';
            }
        });
    }

    // =========================================================================
    // Page: Credit Store
    // =========================================================================
    registerRoute('/credits', async function (container) {
        container.innerHTML = '<div class="loading-container"><div class="spinner spinner-lg"></div><span>Loading credit packs...</span></div>';

        try {
            var packs = await api(API.CREDITS + '/packs');
            await fetchBalance();

            var html = '\
                <h1>Credit Store</h1>\
                <p class="text-muted mb-2">Current balance: <span class="text-yellow">' + (state.balance !== null ? state.balance : '---') + ' credits</span></p>\
                <div class="pixel-divider"></div>\
                <div class="grid-3">';

            packs.forEach(function (pack) {
                html += '\
                    <div class="pack-card">\
                        <div class="pack-name">' + escapeHtml(pack.name) + '</div>\
                        <div class="pack-credits">' + pack.credits + '</div>\
                        <div class="pack-credits-label">credits</div>\
                        <div class="pack-price">' + formatCents(pack.price_cents) + ' ' + (pack.currency || 'USD') + '</div>\
                        <button class="btn btn-primary btn-block buy-pack-btn" data-pack-id="' + escapeHtml(pack.pack_id) + '">Buy Now</button>\
                    </div>';
            });

            html += '</div>';
            container.innerHTML = html;

            // Buy buttons
            container.querySelectorAll('.buy-pack-btn').forEach(function (btn) {
                btn.addEventListener('click', async function () {
                    btn.disabled = true;
                    btn.textContent = 'Processing...';
                    try {
                        var result = await api(API.CREDITS + '/purchase', {
                            method: 'POST',
                            body: { pack_id: btn.getAttribute('data-pack-id') },
                        });
                        if (result.checkout_url) {
                            window.location.href = result.checkout_url;
                        } else {
                            showToast('Purchase initiated', 'success');
                        }
                    } catch (err) {
                        showToast(err.message, 'error');
                        btn.disabled = false;
                        btn.textContent = 'Buy Now';
                    }
                });
            });
        } catch (err) {
            container.innerHTML = '<div class="empty-state"><h3>Error loading packs</h3><p>' + escapeHtml(err.message) + '</p></div>';
        }
    });

    // =========================================================================
    // Page: Generate Art
    // =========================================================================
    registerRoute('/generate', async function (container) {
        container.innerHTML = '\
            <h1>Generate Pixel Art</h1>\
            <div class="pixel-divider"></div>\
            <div class="gen-form">\
                <div class="form-group">\
                    <label>Select Tier</label>\
                    <div class="tier-selector" id="tier-selector">\
                        <div class="tier-option selected" data-tier="free">\
                            <span class="tier-name">Free</span>\
                            <span class="tier-cost">1/day (phone verified)</span>\
                        </div>\
                    </div>\
                </div>\
                <div class="form-group">\
                    <label>Prompt</label>\
                    <textarea id="gen-prompt" placeholder="Describe the pixel art you want to generate..." maxlength="2000"></textarea>\
                </div>\
                <button id="gen-submit" class="btn btn-primary btn-lg">Generate</button>\
            </div>\
            <div id="gen-progress" class="progress-container hidden"></div>\
            <div id="gen-result" class="gen-result hidden"></div>';

        var selectedTier = 'free';

        // Tier selection
        document.querySelectorAll('.tier-option').forEach(function (opt) {
            opt.addEventListener('click', function () {
                document.querySelectorAll('.tier-option').forEach(function (o) { o.classList.remove('selected'); });
                opt.classList.add('selected');
                selectedTier = opt.getAttribute('data-tier');
            });
        });

        // Generate button
        document.getElementById('gen-submit').addEventListener('click', async function () {
            var prompt = document.getElementById('gen-prompt').value.trim();
            if (!prompt) {
                showToast('Please enter a prompt', 'warning');
                return;
            }

            var btn = this;
            btn.disabled = true;
            btn.textContent = 'Submitting...';

            var progressEl = document.getElementById('gen-progress');
            var resultEl = document.getElementById('gen-result');
            progressEl.classList.remove('hidden');
            resultEl.classList.add('hidden');

            progressEl.innerHTML = '\
                <div class="progress-status" id="progress-status">Submitting generation request...</div>\
                <div class="progress-bar-track"><div class="progress-bar-fill" id="progress-fill" style="width: 5%"></div></div>\
                <div class="tool-calls-count" id="tool-calls-count">Tool calls: 0</div>\
                <div class="sse-log" id="sse-log"></div>';

            try {
                var data = await api(API.GENERATIONS, {
                    method: 'POST',
                    body: { tier: selectedTier, prompt: prompt },
                });

                var jobId = data.job_id;
                document.getElementById('progress-status').textContent = 'Job created: ' + truncateId(jobId) + ' - Connecting to stream...';
                document.getElementById('progress-fill').style.width = '15%';

                // Connect SSE for progress
                connectSSE(jobId, progressEl, resultEl, btn);
            } catch (err) {
                showToast(err.message, 'error');
                btn.disabled = false;
                btn.textContent = 'Generate';
                progressEl.classList.add('hidden');
            }
        });
    });

    function connectSSE(jobId, progressEl, resultEl, btn) {
        var evtSource = new EventSource(API.GENERATIONS + '/' + jobId + '/events');
        state.currentEventSource = evtSource;
        var toolCalls = 0;
        var logEl = document.getElementById('sse-log');

        evtSource.onmessage = function (event) {
            var rawData = event.data;
            if (!rawData || rawData.trim() === '') return;

            try {
                var parsed = JSON.parse(rawData);
                var logEntry = document.createElement('div');
                logEntry.className = 'log-entry';
                logEntry.textContent = (parsed.event || 'update') + ': ' + (parsed.message || JSON.stringify(parsed));
                logEl.appendChild(logEntry);
                logEl.scrollTop = logEl.scrollHeight;

                if (parsed.tool_calls_executed !== undefined) {
                    toolCalls = parsed.tool_calls_executed;
                    document.getElementById('tool-calls-count').textContent = 'Tool calls: ' + toolCalls;
                }

                if (parsed.event === 'progress' || parsed.event === 'tool_call') {
                    var pct = Math.min(15 + toolCalls * 10, 90);
                    document.getElementById('progress-fill').style.width = pct + '%';
                    document.getElementById('progress-status').textContent = parsed.message || 'Processing...';
                }

                if (parsed.event === 'complete') {
                    document.getElementById('progress-fill').style.width = '100%';
                    document.getElementById('progress-status').textContent = 'Generation complete!';

                    resultEl.classList.remove('hidden');
                    resultEl.innerHTML = '\
                        <div class="card">\
                            <div class="card-header">Generation Complete</div>\
                            <p>Your pixel art has been generated.</p>\
                            <div class="art-id-display">Art ID: ' + escapeHtml(parsed.art_id || 'N/A') + '</div>\
                            <div class="mt-2">\
                                <a href="#/provenance/' + escapeHtml(parsed.art_id || '') + '" class="btn btn-outline btn-sm">View Provenance</a>\
                            </div>\
                        </div>';

                    evtSource.close();
                    state.currentEventSource = null;
                    btn.disabled = false;
                    btn.textContent = 'Generate';
                    fetchBalance();
                }

                if (parsed.event === 'failed') {
                    document.getElementById('progress-status').textContent = 'Generation failed: ' + (parsed.error_message || 'Unknown error');
                    document.getElementById('progress-status').classList.add('text-danger');
                    evtSource.close();
                    state.currentEventSource = null;
                    btn.disabled = false;
                    btn.textContent = 'Generate';
                }
            } catch {
                // Non-JSON data, possibly keepalive
            }
        };

        evtSource.onerror = function () {
            // SSE connection lost - poll for final status
            evtSource.close();
            state.currentEventSource = null;
            pollJobStatus(jobId, progressEl, resultEl, btn);
        };
    }

    async function pollJobStatus(jobId, progressEl, resultEl, btn) {
        try {
            var data = await api(API.GENERATIONS + '/' + jobId);
            if (data.status === 'completed') {
                document.getElementById('progress-fill').style.width = '100%';
                document.getElementById('progress-status').textContent = 'Generation complete!';
                resultEl.classList.remove('hidden');
                resultEl.innerHTML = '\
                    <div class="card">\
                        <div class="card-header">Generation Complete</div>\
                        <p>Your pixel art has been generated.</p>\
                        <div class="art-id-display">Art ID: ' + escapeHtml(data.art_id || 'N/A') + '</div>\
                        <div class="mt-2">\
                            <a href="#/provenance/' + escapeHtml(data.art_id || '') + '" class="btn btn-outline btn-sm">View Provenance</a>\
                        </div>\
                    </div>';
            } else if (data.status === 'failed') {
                document.getElementById('progress-status').textContent = 'Generation failed: ' + (data.error_message || 'Unknown error');
            } else {
                document.getElementById('progress-status').textContent = 'Status: ' + data.status + ' (stream disconnected, refresh to check)';
            }
        } catch (err) {
            document.getElementById('progress-status').textContent = 'Could not fetch job status';
        }
        btn.disabled = false;
        btn.textContent = 'Generate';
    }

    // =========================================================================
    // Page: My Collection
    // =========================================================================
    registerRoute('/collection', function (container) {
        container.innerHTML = '\
            <h1>My Collection</h1>\
            <div class="pixel-divider"></div>\
            <div class="collection-placeholder">\
                <h2>Your Art Collection</h2>\
                <p>Your generated art pieces will appear here.</p>\
                <p class="text-muted">Art IDs from completed generation jobs will be displayed once a dedicated endpoint is available.</p>\
                <div class="mt-3">\
                    <a href="#/generate" class="btn btn-primary">Generate Your First Art</a>\
                </div>\
            </div>';
    });

    // =========================================================================
    // Page: Marketplace Browse
    // =========================================================================
    registerRoute('/marketplace', async function (container) {
        container.innerHTML = '<div class="loading-container"><div class="spinner spinner-lg"></div><span>Loading marketplace...</span></div>';

        try {
            var data = await api(API.MARKETPLACE + '/', { method: 'GET' });
            renderMarketplace(container, data.listings, data.next_cursor);
        } catch (err) {
            container.innerHTML = '<div class="empty-state"><h3>Error loading marketplace</h3><p>' + escapeHtml(err.message) + '</p></div>';
        }
    });

    function renderMarketplace(container, listings, nextCursor) {
        var html = '\
            <div class="marketplace-header">\
                <h1>Marketplace</h1>\
            </div>\
            <div class="pixel-divider"></div>';

        if (!listings || listings.length === 0) {
            html += '<div class="empty-state"><h3>No Listings Yet</h3><p>The marketplace is empty. Be the first to list pixel art!</p></div>';
        } else {
            html += '<div class="grid-3" id="listings-grid">';
            listings.forEach(function (listing) {
                html += createListingCardHtml(listing);
            });
            html += '</div>';
        }

        if (nextCursor) {
            html += '<div class="load-more-container"><button class="btn btn-outline" id="load-more-btn" data-cursor="' + escapeHtml(nextCursor) + '">Load More</button></div>';
        }

        container.innerHTML = html;

        // Listing card clicks
        container.querySelectorAll('.listing-card').forEach(function (card) {
            card.addEventListener('click', function () {
                navigate('/listing/' + card.getAttribute('data-listing-id'));
            });
        });

        // Load more button
        var loadMoreBtn = document.getElementById('load-more-btn');
        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', async function () {
                loadMoreBtn.disabled = true;
                loadMoreBtn.textContent = 'Loading...';
                try {
                    var cursor = loadMoreBtn.getAttribute('data-cursor');
                    var moreData = await api(API.MARKETPLACE + '/?cursor=' + encodeURIComponent(cursor));
                    var grid = document.getElementById('listings-grid');
                    if (moreData.listings) {
                        moreData.listings.forEach(function (listing) {
                            var temp = document.createElement('div');
                            temp.innerHTML = createListingCardHtml(listing);
                            var card = temp.firstElementChild;
                            card.addEventListener('click', function () {
                                navigate('/listing/' + card.getAttribute('data-listing-id'));
                            });
                            grid.appendChild(card);
                        });
                    }
                    if (moreData.next_cursor) {
                        loadMoreBtn.setAttribute('data-cursor', moreData.next_cursor);
                        loadMoreBtn.disabled = false;
                        loadMoreBtn.textContent = 'Load More';
                    } else {
                        loadMoreBtn.parentElement.remove();
                    }
                } catch (err) {
                    showToast(err.message, 'error');
                    loadMoreBtn.disabled = false;
                    loadMoreBtn.textContent = 'Load More';
                }
            });
        }
    }

    function createListingCardHtml(listing) {
        return '\
            <div class="listing-card" data-listing-id="' + escapeHtml(String(listing.listing_id)) + '">\
                <div class="listing-art-id">Art: ' + escapeHtml(String(listing.art_id)) + '</div>\
                <div class="listing-price">' + formatCents(listing.asking_price_cents) + '</div>\
                <div class="listing-seller">Seller: ' + truncateId(String(listing.seller_user_id)) + '</div>\
                <div class="listing-status">' + escapeHtml(listing.status) + '</div>\
            </div>';
    }

    // =========================================================================
    // Page: Listing Detail
    // =========================================================================
    registerRoute('/listing/:id', async function (container, params) {
        container.innerHTML = '<div class="loading-container"><div class="spinner spinner-lg"></div><span>Loading listing...</span></div>';

        try {
            var listing = await api(API.MARKETPLACE + '/' + params.id);
            var html = '\
                <a href="#/marketplace" class="back-link">&lt; Back to Marketplace</a>\
                <div class="detail-card card">\
                    <div class="card-header">Listing Details</div>\
                    <div class="detail-row">\
                        <span class="detail-label">Listing ID</span>\
                        <span class="detail-value">' + escapeHtml(String(listing.listing_id)) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Art ID</span>\
                        <span class="detail-value">' + escapeHtml(String(listing.art_id)) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Price</span>\
                        <span class="detail-value text-yellow">' + formatCents(listing.asking_price_cents) + ' ' + escapeHtml(listing.currency_code || 'USD') + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Seller</span>\
                        <span class="detail-value">' + escapeHtml(String(listing.seller_user_id)) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Status</span>\
                        <span class="detail-value">' + escapeHtml(listing.status) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Listed At</span>\
                        <span class="detail-value">' + formatDate(listing.listed_at) + '</span>\
                    </div>\
                    <div class="detail-actions">';

            if (listing.status === 'active' && state.isLoggedIn) {
                html += '<button id="buy-listing-btn" class="btn btn-success">Buy This Art</button>';
            }

            html += '<a href="#/provenance/' + escapeHtml(String(listing.art_id)) + '" class="btn btn-outline">View Provenance</a>\
                    </div>\
                </div>';

            container.innerHTML = html;

            // Buy button
            var buyBtn = document.getElementById('buy-listing-btn');
            if (buyBtn) {
                buyBtn.addEventListener('click', async function () {
                    buyBtn.disabled = true;
                    buyBtn.textContent = 'Purchasing...';
                    try {
                        var result = await api(API.MARKETPLACE + '/' + params.id + '/buy', {
                            method: 'POST',
                        });
                        showToast('Purchase successful! Transaction: ' + truncateId(result.transaction_id), 'success');
                        buyBtn.textContent = 'Purchased';
                        fetchBalance();
                    } catch (err) {
                        showToast(err.message, 'error');
                        buyBtn.disabled = false;
                        buyBtn.textContent = 'Buy This Art';
                    }
                });
            }
        } catch (err) {
            container.innerHTML = '<a href="#/marketplace" class="back-link">&lt; Back to Marketplace</a><div class="empty-state"><h3>Listing Not Found</h3><p>' + escapeHtml(err.message) + '</p></div>';
        }
    });

    // =========================================================================
    // Page: Provenance
    // =========================================================================
    registerRoute('/provenance/:artId', async function (container, params) {
        container.innerHTML = '<div class="loading-container"><div class="spinner spinner-lg"></div><span>Loading provenance chain...</span></div>';

        try {
            var data = await api(API.ART + '/' + params.artId + '/provenance');
            var html = '\
                <a href="#/marketplace" class="back-link">&lt; Back</a>\
                <h1>Provenance Chain</h1>\
                <div class="pixel-divider"></div>\
                <div class="provenance-meta card mb-3">\
                    <div class="card-header">Art Piece Details</div>\
                    <div class="detail-row">\
                        <span class="detail-label">Art ID</span>\
                        <span class="detail-value">' + escapeHtml(String(data.art_id)) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Creator</span>\
                        <span class="detail-value">' + escapeHtml(String(data.creator_user_id)) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Generation Tier</span>\
                        <span class="detail-value text-cyan">' + escapeHtml(data.generation_tier) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Created</span>\
                        <span class="detail-value">' + formatDate(data.created_at) + '</span>\
                    </div>\
                    <div class="detail-row">\
                        <span class="detail-label">Seal Signature</span>\
                        <span class="detail-value" style="font-size:0.7rem">' + escapeHtml(data.seal_signature || '---') + '</span>\
                    </div>\
                </div>\
                <h2 class="mb-2">Ownership History</h2>';

            if (!data.provenance_chain || data.provenance_chain.length === 0) {
                html += '<div class="empty-state"><h3>No Transfers Yet</h3><p>This art has not been transferred.</p></div>';
            } else {
                html += '<div class="chain-timeline">';
                data.provenance_chain.forEach(function (entry) {
                    html += '\
                        <div class="chain-entry">\
                            <div class="transfer-type">' + escapeHtml(entry.transfer_type) + '</div>\
                            <div class="transfer-users">' +
                                (entry.from_user_id ? truncateId(String(entry.from_user_id)) : 'System') +
                                ' &rarr; ' + truncateId(String(entry.to_user_id)) +
                            '</div>\
                            <div class="transfer-date">' + formatDate(entry.transferred_at) + '</div>\
                        </div>';
                });
                html += '</div>';
            }

            container.innerHTML = html;
        } catch (err) {
            container.innerHTML = '<a href="#/marketplace" class="back-link">&lt; Back</a><div class="empty-state"><h3>Provenance Not Found</h3><p>' + escapeHtml(err.message) + '</p></div>';
        }
    });

    // =========================================================================
    // Initialization
    // =========================================================================
    function init() {
        // Hamburger menu toggle
        document.getElementById('hamburger').addEventListener('click', function () {
            document.getElementById('nav-links').classList.toggle('open');
        });

        // Logout button
        document.getElementById('btn-logout').addEventListener('click', async function () {
            try {
                await api(API.AUTH + '/logout', { method: 'POST' });
            } catch {
                // Logout even if API fails
            }
            state.isLoggedIn = false;
            state.user = null;
            state.balance = null;
            state.phoneVerified = false;
            showToast('Logged out', 'info');
            navigate('/login');
        });

        // Phone modal
        initPhoneModal();

        // Route handler
        window.addEventListener('hashchange', handleRoute);

        // Initial route
        if (!window.location.hash || window.location.hash === '#' || window.location.hash === '#/') {
            window.location.hash = '#/login';
        }
        handleRoute();
    }

    // Start the application when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
