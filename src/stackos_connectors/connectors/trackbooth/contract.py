"""Trackbooth protocol constants; no application inventory or endpoint policy."""

import re

JsonObject = dict[str, object]
PATH_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\}")
READ_METHODS = {"GET"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
