/**
 * WCComps shared Alpine.js mixins and utility functions.
 *
 * Loaded globally in base_site.html before Alpine.js initializes.
 * Mixins are spread into Alpine.data() blocks: { ...toastMixin(), ... }
 */

/* ── Helpers ─────────────────────────────────────────────────────── */

function getCSRFToken() {
    const c = document.cookie.split('; ').find(c => c.startsWith('csrftoken='));
    if (c) return c.split('=')[1];
    // Fallback: base_site.html renders the token (cookie may be missing in a fresh session)
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

function _toFormData(data) {
    if (data instanceof FormData) return data;
    const fd = new FormData();
    for (const [k, v] of Object.entries(data)) fd.append(k, v);
    return fd;
}

/** POST with CSRF token, return parsed JSON. */
async function wcPost(url, data = {}) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        body: _toFormData(data),
    });
    return response.json();
}

/** POST and read NDJSON stream. Calls onProgress(msg) per line, onDone(msg) for {done:true}. */
async function wcStream(url, data, onProgress, onDone) {
    const response = await fetch(url, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        body: _toFormData(data),
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (!line.trim()) continue;
            const msg = JSON.parse(line);
            if (msg.done) onDone(msg);
            else onProgress(msg);
        }
    }
}

/* ── Alpine Mixins ───────────────────────────────────────────────── */

/** Toast notification state: message, messageType, alertClass. */
function toastMixin() {
    return {
        message: '',
        messageType: 'success',
        get alertClass() {
            return this.messageType === 'success' ? 'alert--success' : 'alert--error';
        },
    };
}

/** Progress bar state and computed properties. */
function progressMixin() {
    return {
        progressStep: '',
        progressCurrent: 0,
        progressTotal: 0,
        progressFailed: 0,
        get hasProgress() { return this.progressTotal > 0; },
        get progressText() { return this.progressCurrent + '/' + this.progressTotal; },
        get progressBarClass() { return this.progressFailed > 0 ? 'progress-bar__fill--warn' : ''; },
        get progressStyle() { return 'width:' + Math.round(this.progressCurrent / this.progressTotal * 100) + '%'; },
        get hasFailures() { return this.progressFailed > 0; },
        get failedText() { return this.progressFailed + ' failed'; },
    };
}

/**
 * Streaming action mixin: toast + progress + doStreamAction().
 * @param {string} url — POST endpoint (rendered by Django template tag).
 */
function streamMixin(url) {
    return {
        ...toastMixin(),
        ...progressMixin(),
        loading: false,
        get notLoading() { return !this.loading; },
        get disabledClass() { return this.loading ? 'disabled' : ''; },

        async doStreamAction(action, extraData = {}) {
            this.loading = true;
            this.message = '';
            this.progressStep = 'Starting...';
            this.progressCurrent = 0;
            this.progressTotal = 1;
            this.progressFailed = 0;

            try {
                await wcStream(
                    url,
                    { action, ...extraData },
                    (msg) => {
                        this.progressStep = msg.step;
                        this.progressCurrent = msg.current;
                        this.progressTotal = msg.total;
                        if (!msg.ok) this.progressFailed++;
                    },
                    (msg) => {
                        this.message = msg.message;
                        this.messageType = msg.success ? 'success' : 'error';
                        if (msg.success) setTimeout(() => location.reload(), 2000);
                    },
                );
            } catch (e) {
                this.message = 'Request failed: ' + e.message;
                this.messageType = 'error';
            }

            this.loading = false;
            if (!this.message) this.progressTotal = 0;
            setTimeout(() => { this.message = ''; }, 5000);
        },
    };
}

/**
 * Bulk selection mixin for review/list pages.
 * @param {string} dataAttr — HTML data attribute suffix (e.g. 'incident-id').
 * Call this.initBulkSelect() from your init() method.
 */
function bulkSelectMixin(dataAttr) {
    const camel = dataAttr.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    return {
        selected: [],
        selectableIds: [],
        submitting: false,
        initBulkSelect() {
            this.selectableIds = Array.from(
                this.$el.querySelectorAll('[data-' + dataAttr + ']'),
            ).map(el => el.dataset[camel]);
        },
        get allSelected() {
            return this.selected.length === this.selectableIds.length && this.selectableIds.length > 0;
        },
        get someSelected() {
            return this.selected.length > 0 && this.selected.length < this.selectableIds.length;
        },
        toggleAll() {
            this.selected = this.allSelected ? [] : [...this.selectableIds];
            this.syncCheckboxes();
        },
        toggleItem(e) {
            const val = e.target.value;
            const idx = this.selected.indexOf(val);
            if (e.target.checked && idx === -1) this.selected.push(val);
            else if (!e.target.checked && idx > -1) this.selected.splice(idx, 1);
        },
        syncCheckboxes() {
            this.$el.querySelectorAll('input[type="checkbox"][value]').forEach(cb => {
                cb.checked = this.selected.includes(cb.value);
            });
        },
    };
}
