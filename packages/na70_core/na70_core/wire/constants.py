"""Versioned schema, output taxonomy and reject codes for wire detection."""

SCHEMA_VERSION = "na70-wire-v1"

# Geometry classification; identity is the truth-provider's job, not ours.
GEOMETRY_LINE = "LINE_STRUCTURE"
GEOMETRY_UNKNOWN_LINE = "UNKNOWN_LINE"

# Evidence bucket (never mix)
STATUS_MEASURED_CURRENT = "MEASURED_CURRENT"
STATUS_PREDICTED_RECENT = "PREDICTED_RECENT"
STATUS_NO_CURRENT_MEASUREMENT = "NO_CURRENT_MEASUREMENT"
STATUS_UNOBSERVABLE = "UNOBSERVABLE"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_UNVERIFIED = "UNVERIFIED"

# Reject reasons - wire-debug-acceptance-v1-draft §3 registry. Adapter layer
# is source of truth for what counts as a real "stage" for trace reporting.
# Registered extensions (2026-10-03, rationale locked by the synthetic-matrix
# findings; remain part of this module's compatible enum):
#   LATERAL_SPREAD - finite_support rejects dense blobs masquerading as lines
#   EXCESSIVE_SAG  - finite_support rejects sagging segments beyond max_sag_m
#   SPARSE_SUPPORT - finite_support rejects too few returns per span-metre
REJECT_CODES = frozenset({
    "INVALID_PACKET", "INVALID_TIME", "TIME_GAP", "UNKNOWN_DIRECTION",
    "NO_VALID_RETURN", "PARTIAL_WINDOW", "RESOURCE_LIMIT",
    "MOTION_UNCOMPENSATED", "NO_PROPOSAL", "INSUFFICIENT_SUPPORT",
    "DEGENERATE_SAMPLE", "FIT_REJECTED", "LOW_COVERAGE", "LARGE_GAP",
    "BACKGROUND_AMBIGUOUS", "STALE_MEASUREMENT", "MISSING_PROVENANCE",
    "AMBIGUOUS_LINE", "LATERAL_SPREAD", "EXCESSIVE_SAG", "SPARSE_SUPPORT",
})
