# Vendored front-end libraries

Served from our own origin (WhiteNoise) instead of unpkg.com, so a compromised CDN or npm package
can't inject script into admin pages, and the CSP no longer needs to allow a third-party origin.

Each file was extracted from its npm package tarball after checking the tarball against the
registry's published `dist.integrity` (sha512). `base_site.html` pins the sha384 below as SRI.

| File | Package | sha384 |
|---|---|---|
| alpinejs-csp.min.js | @alpinejs/csp@3.14.8 `dist/cdn.min.js` | ToFwPnlRgZIoDDSatKvWLkecUR4Py5dma633TT4TVBQfs4nHKXXexI3v72xi5LnC |
| alpinejs-collapse.min.js | @alpinejs/collapse@3.14.8 `dist/cdn.min.js` | NArNwzWsUSF+kY2lgW4YriEkjLqi+J+za6HrENUn/3nZqkBnWbxV22kCJEK5Uu6n |
| htmx.min.js | htmx.org@2.0.4 `dist/htmx.min.js` | HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+ |

To upgrade: download the new package tarball from registry.npmjs.org, verify its integrity,
replace the file, and update the sha384 here and in `templates/admin/base_site.html`.
