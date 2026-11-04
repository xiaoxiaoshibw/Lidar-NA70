"""Wire detection algorithm layered on RayBatch + PacketWindowRing.

The pipeline is staged: proposals (manual ROI or future full-FOV), fitting,
then finite-support validation. Every output references raw rays only via
their global NA70 source identity; no candidate carries only library-local
indices. All thresholds live in a YAML-loaded config passed in explicitly -
this package never touches web backend / solver configuration files itself.
"""

from .constants import (STATUS_MEASURED_CURRENT, STATUS_NO_CURRENT_MEASUREMENT,
                        STATUS_PREDICTED_RECENT, STATUS_UNOBSERVABLE,
                        STATUS_UNKNOWN, STATUS_UNVERIFIED, GEOMETRY_LINE,
                        GEOMETRY_UNKNOWN_LINE, REJECT_CODES, SCHEMA_VERSION)
from .config import (FitConfig, ProposalConfig, ValidationConfig, WireConfig,
                     load_wire_config, validate_wire_config_dict)
from .proposals import Proposal, make_manual_roi_proposal
from .fitting import FitResult, fit_line_ransac, recompute_inliers
from .validation import FiniteSupportReport, evaluate_finite_support
from .pipeline import DetectionOutput, WindowInputs, run_detection_window
from .proposal_full_fov import FullFovConfig, propose_full_fov
from .proposal_hough import HoughConfig, propose_hough
from .proposal_pca import PcaConfig, PcaReport, propose_pca
from .synthetic import SCENARIO_BUILDERS, SyntheticScene, build_scenario
from .tracking import (AssociationGates, TrackEntry, TrackGeometry,
                       associate_candidate, bucket_outputs)

__all__ = ["DetectionOutput", "FiniteSupportReport", "FitConfig",
           "FitResult", "FullFovConfig", "GEOMETRY_LINE", "GEOMETRY_UNKNOWN_LINE",
           "HoughConfig", "PcaConfig", "PcaReport", "Proposal", "ProposalConfig",
           "REJECT_CODES", "SCENARIO_BUILDERS", "SCHEMA_VERSION",
           "STATUS_MEASURED_CURRENT", "STATUS_NO_CURRENT_MEASUREMENT",
           "STATUS_PREDICTED_RECENT", "STATUS_UNKNOWN", "STATUS_UNOBSERVABLE",
           "STATUS_UNVERIFIED", "SyntheticScene", "TrackEntry", "TrackGeometry",
           "AssociationGates", "ValidationConfig", "WireConfig",
           "WindowInputs", "associate_candidate", "bucket_outputs", "build_scenario",
           "evaluate_finite_support", "fit_line_ransac",
           "load_wire_config", "make_manual_roi_proposal", "propose_full_fov",
           "propose_hough", "propose_pca", "recompute_inliers",
           "run_detection_window", "validate_wire_config_dict"]
