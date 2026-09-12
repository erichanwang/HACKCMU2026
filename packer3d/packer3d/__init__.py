"""packer3d - 3D packing of boxes & cylinders with centre-of-mass optimisation."""
from .balance import balance_masses
from .bounds import exhaustive_small, gap_report, lower_bounds
from .decoder import DecoderParams, PackState, decode
from .geometry import EPS
from .models import Container, Item, Obstacle, Orientation, PackResult, Placement
from .objective import ObjectiveWeights, compute_metrics
from .scenario import load_scenario
from .search import OptimizerConfig, pack_naive, pack_optimized
from .verify import verify

__all__ = [
    "Container", "Item", "Obstacle", "Orientation", "PackResult", "Placement",
    "OptimizerConfig", "ObjectiveWeights", "DecoderParams", "PackState",
    "pack_optimized", "pack_naive", "verify", "decode", "balance_masses",
    "gap_report", "lower_bounds", "exhaustive_small", "compute_metrics", "load_scenario", "EPS",
]
__version__ = "0.1.0"
