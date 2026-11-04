"""Wire detection configuration. Default file is `na70_core/wire/wire_default.yaml`.

Accepts JSON-that-is-YAML or standard YAML. Every detection/validation
threshold lives here; the code never hardcodes a numeric threshold outside
of the schema defaults.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json


_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "wire_default.yaml"
SCHEMA_VERSION_CONFIG = "na70-wire-config-v1"


@dataclass(frozen=True)
class FitConfig:
    thresh_m: float = 0.6
    max_iteration: int = 400
    min_points: int = 6
    fixed_seed: int = 7


@dataclass(frozen=True)
class ProposalConfig:
    # Manual ROI 默认由调用方显式给出完整 AABB；这里没有默认坐标。
    use_manual_roi: bool = True


@dataclass(frozen=True)
class ValidationConfig:
    # Finite-support gates (>= style semantics). Distances in metres.
    min_unique_packets: int = 4
    min_span_m: float = 1.0
    min_coverage_fraction: float = 0.25
    max_internal_gap_m: float = 50.0
    # Lateral spread of support points within a span bin. A wire's support is
    # a thin 1-D manifold; canopy or facade blobs show wide per-bin spread.
    max_bin_lateral_spread_m: float = 1.0
    # Sag amplitude above which the straight-line model must be rejected so a
    # curve model (P4 extension) is required instead of a silent acceptance.
    max_sag_m: float = 1.0
    # Sparse-support gate. A wire re-returns from many distinct positions per
    # accumulation; a handful of loosely-aligned points spread over tens of
    # metres (canopy tops, facade seams) fails this density test.
    min_span_for_density_check_m: float = 25.0
    min_support_density_per_m: float = 1.0
    max_rms_orthogonal_m: float = 0.5
    max_p95_orthogonal_m: float = 1.0
    max_window_age_s: float = 5.0
    span_bin_m: float = 1.0
    require_direction_known: bool = True


@dataclass(frozen=True)
class WireConfig:
    schema_version: str = SCHEMA_VERSION_CONFIG
    fit: FitConfig = field(default_factory=FitConfig)
    proposal: ProposalConfig = field(default_factory=ProposalConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")


def _validate_positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} must be a positive number")


def _validate_int(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _merge_fit(base: FitConfig, override: dict | None) -> FitConfig:
    if override is None:
        return base
    return FitConfig(
        thresh_m=override.get("thresh_m", base.thresh_m),
        max_iteration=override.get("max_iteration", base.max_iteration),
        min_points=override.get("min_points", base.min_points),
        fixed_seed=override.get("fixed_seed", base.fixed_seed),
    )


def _merge_proposal(base: ProposalConfig, override: dict | None) -> ProposalConfig:
    if override is None:
        return base
    return ProposalConfig(use_manual_roi=override.get("use_manual_roi", base.use_manual_roi))


def _merge_validation(base: ValidationConfig, override: dict | None) -> ValidationConfig:
    if override is None:
        return base
    return ValidationConfig(**{name: override.get(name, getattr(base, name)) for name in ValidationConfig.__dataclass_fields__})


def validate_wire_config_dict(payload: dict) -> WireConfig:
    if not isinstance(payload, dict):
        raise ValueError("wire config must be a mapping")
    schema = payload.get("schema_version", SCHEMA_VERSION_CONFIG)
    if schema != SCHEMA_VERSION_CONFIG:
        raise ValueError(f"unsupported wire config schema {schema!r}")
    fit = _merge_fit(FitConfig(), payload.get("fit"))
    proposal = _merge_proposal(ProposalConfig(), payload.get("proposal"))
    validation = _merge_validation(ValidationConfig(), payload.get("validation"))
    _validate_positive(fit.thresh_m, "fit.thresh_m")
    _validate_int(fit.max_iteration, "fit.max_iteration", 1)
    _validate_int(fit.min_points, "fit.min_points", 2)
    _validate_int(fit.fixed_seed, "fit.fixed_seed", 0)
    _validate_int(validation.min_unique_packets, "validation.min_unique_packets", 1)
    _validate_positive(validation.min_span_m, "validation.min_span_m")
    if not 0 <= validation.min_coverage_fraction <= 1:
        raise ValueError("validation.min_coverage_fraction must be within [0,1]")
    _validate_positive(validation.max_internal_gap_m, "validation.max_internal_gap_m")
    _validate_positive(validation.max_bin_lateral_spread_m, "validation.max_bin_lateral_spread_m")
    _validate_positive(validation.max_sag_m, "validation.max_sag_m")
    _validate_positive(validation.min_span_for_density_check_m, "validation.min_span_for_density_check_m")
    _validate_positive(validation.min_support_density_per_m, "validation.min_support_density_per_m")
    _validate_positive(validation.max_rms_orthogonal_m, "validation.max_rms_orthogonal_m")
    _validate_positive(validation.max_p95_orthogonal_m, "validation.max_p95_orthogonal_m")
    _validate_positive(validation.max_window_age_s, "validation.max_window_age_s")
    _validate_positive(validation.span_bin_m, "validation.span_bin_m")
    if not isinstance(validation.require_direction_known, bool):
        raise ValueError("validation.require_direction_known must be boolean")
    return WireConfig(schema_version=schema, fit=fit, proposal=proposal, validation=validation)


def load_wire_config(path: Path | None = None) -> WireConfig:
    if path is None:
        return validate_wire_config_dict(json.loads(_DEFAULT_PATH.read_text("utf-8")))
    raw = Path(path).read_text("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Allow strict JSON only; YAML superset would silently change types.
        raise ValueError(f"wire config at {path} is not valid JSON-like YAML")
    return validate_wire_config_dict(payload)


DEFAULT_WIRE_CONFIG_PATH = _DEFAULT_PATH
