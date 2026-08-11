"""Shared browser-tab icon for every page the web module serves.

Without this, the browser falls back to its own default placeholder (a bare
globe outline) — there was never a favicon at all. A data: URI keeps this a
single self-contained string with no extra file to bundle/ship (this project
already deliberately treats zero runtime dependencies as a feature; a static
.ico/.svg asset would be the first exception).

A steering wheel — ring, hub, and three spokes, all stroke rather than fill —
replacing an earlier solid car silhouette that read as a single dark blob at
16px (thin negative-space cutouts collapse under anti-aliasing at favicon
scale; a mostly-open outline can't). The stroke colour is not hard-coded: the
SVG carries its own embedded `<style>` with a `prefers-color-scheme` rule, so
the icon renders dark-on-light or light-on-dark to match the browser's own
theme rather than one fixed colour going invisible against a dark tab strip
(confirmed live: the old fixed `#2b2b2b` fill nearly disappeared on a dark
tab). There is no `data-theme` concept for a browser tab icon the way there
is for the dashboard/wizard pages — the media query alone is what a favicon
gets to react to.
"""

FAVICON_LINK = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Cstyle%3E"
    ".s{stroke:%231f2023;fill:none}.h{fill:%231f2023}"
    "@media (prefers-color-scheme:dark){.s{stroke:%23f2f0ea}.h{fill:%23f2f0ea}}"
    "%3C/style%3E"
    "%3Ccircle class='s' cx='12' cy='12' r='8.4' stroke-width='1.7' stroke-linecap='round'/%3E"
    "%3Ccircle class='h' cx='12' cy='12' r='2.1'/%3E"
    "%3Cline class='s' x1='12' y1='14.1' x2='12' y2='19.6' stroke-width='1.7' stroke-linecap='round'/%3E"
    "%3Cline class='s' x1='10.18' y1='10.95' x2='5.3' y2='8.2' stroke-width='1.7' stroke-linecap='round'/%3E"
    "%3Cline class='s' x1='13.82' y1='10.95' x2='18.7' y2='8.2' stroke-width='1.7' stroke-linecap='round'/%3E"
    "%3C/svg%3E\">"
)
