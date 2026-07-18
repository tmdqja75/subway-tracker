# Known risk: Next.js bundled PostCSS advisory

- **Status:** Open — recheck required before release.
- **Audit evidence:** `npm audit --omit=dev` reports two moderate findings: `next@16.2.10` depends on `postcss` `<8.5.10` (`GHSA-qx2v-qp2m-jg93`, XSS through an unescaped `</style>` in CSS stringify output).
- **Affected dependency:** the required, pinned direct dependency is `next@16.2.10`; its nested `node_modules/next/node_modules/postcss` is affected.
- **No safe ordinary upgrade:** npm currently marks the affected Next range as `9.3.4-canary.0 - 16.3.0-canary.5` and offers only `next@9.3.3` as a semver-major fix (a downgrade), which is not a compatible remediation for this Next 16 application.
- **Practical exposure:** this static-export app currently has no user-supplied CSS and serves prebuilt CSS. Risk remains if build-time or server-side CSS stringification ever includes untrusted content; this is not suppressed.
- **Release gate:** before every release, rerun `npm audit --omit=dev`. Re-evaluate immediately if a supported Next upgrade resolves the advisory or if untrusted content can reach CSS generation; obtain an explicit risk decision if the advisory remains.
