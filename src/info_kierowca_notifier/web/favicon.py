"""Shared browser-tab icon for every page the web module serves.

Without this, the browser falls back to its own default placeholder (a bare
globe outline) — there was never a favicon at all. A data: URI keeps this a
single self-contained string with no extra file to bundle/ship (this project
already deliberately treats zero runtime dependencies as a feature; a static
.ico/.svg asset would be the first exception). A monochrome car silhouette,
matching this project's own subject matter and the muted, colourless icon
style already used elsewhere on these pages (TOOLBAR_HTML's stroke icons).
Mid-grey rather than the toolbar's near-white (#eee): a favicon has to read
against both light and dark browser tab bars, unlike the toolbar icons it's
otherwise matching, which only ever sit on this app's own dark background.
"""

FAVICON_LINK = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Crect x='3' y='11' width='18' height='5' rx='2.5' fill='%238a8a8a'/%3E"
    "%3Cpath d='M6 11l2-4h8l2 4' fill='%238a8a8a'/%3E"
    "%3Ccircle cx='7.5' cy='17' r='2' fill='%238a8a8a'/%3E"
    "%3Ccircle cx='16.5' cy='17' r='2' fill='%238a8a8a'/%3E"
    "%3C/svg%3E\">"
)
