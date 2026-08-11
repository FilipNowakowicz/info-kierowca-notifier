"""Shared browser-tab icon for every page the web module serves.

Without this, the browser falls back to its own default placeholder (a bare
globe outline) — there was never a favicon at all. A data: URI keeps this a
single self-contained string with no extra file to bundle/ship (this project
already deliberately treats zero runtime dependencies as a feature; a static
.ico/.svg asset would be the first exception). A monochrome, transparent-
background front-on car silhouette (headlights/grille as negative-space
cutouts via fill-rule="evenodd"), matching a reference image the user
supplied directly — solid dark shape, no colour, no background box.
"""

FAVICON_LINK = (
    "<link rel=\"icon\" href=\"data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Cpath fill-rule='evenodd' fill='%232b2b2b' d='"
    "M12 2.5 Q8.5 2.5 7 6 L5.2 9.3 Q2 9.6 2 11.5 L2.6 12.2 Q2 13 2 14.5 "
    "L2 17.5 Q2 19.5 4 19.5 L20 19.5 Q22 19.5 22 17.5 L22 14.5 Q22 13 21.4 12.2 "
    "L22 11.5 Q22 9.6 18.8 9.3 L17 6 Q15.5 2.5 12 2.5 Z "
    "M3.6 13.4 L7.1 12.7 L7.4 14.7 L3.9 15.4 Z "
    "M20.4 13.4 L16.9 12.7 L16.6 14.7 L20.1 15.4 Z "
    "M9.3 15.3 Q9.3 14.9 9.7 14.9 L14.3 14.9 Q14.7 14.9 14.7 15.3 "
    "L14.7 17.6 Q14.7 18 14.3 18 L9.7 18 Q9.3 18 9.3 17.6 Z"
    "'/%3E%3C/svg%3E\">"
)
