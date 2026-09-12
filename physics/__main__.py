"""CLI for the physics validation layer.

    python3 -m physics validate scene.json [--placements placements.json] [--pretty]
    python3 -m physics validate-packer3d result.json [--strategy naive|optimized]
                                         [--items scenario.json] [--pretty]
    python3 -m physics example
    python3 -m physics scan-to-object item.json

See examples/README.md for sample input files and the exact shapes involved,
and docs/SOLVER_INTEGRATION.md for the packer3d frame mapping.
"""
from __future__ import annotations

import argparse
import json
import sys

from physics.io import (
    object_from_box_fit,
    object_from_scanned_item,
    object_to_dict,
    result_to_json,
    scene_from_dict,
    validate,
)

# Errors expected from bad/missing/malformed *input files* -- these map to
# exit code 2 ("unreadable input"), as opposed to a successful validation
# that merely found violations (exit code 1).
_INPUT_ERRORS = (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError)


def _load_json(path: str):
    with open(path) as f:
        return json.load(f)


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        scene = scene_from_dict(_load_json(args.scene))
        placements = None
        if args.placements:
            raw = _load_json(args.placements)
            # Accept either a bare list of placements or the solver's
            # documented `{"placements": [...]}` wrapper.
            placements = raw.get("placements", raw) if isinstance(raw, dict) else raw
    except _INPUT_ERRORS as e:
        print(f"error reading input: {e}", file=sys.stderr)
        return 2

    result = validate(scene, placements)
    text = result_to_json(result)
    print(json.dumps(json.loads(text), indent=2) if args.pretty else text)
    return 0 if result["valid"] else 1


def _cmd_validate_packer3d(args: argparse.Namespace) -> int:
    from physics.packer3d_adapter import validate_packer3d

    try:
        result = _load_json(args.result)
        items = _load_json(args.items) if args.items else None
        out = validate_packer3d(result, items=items, strategy=args.strategy)
    except _INPUT_ERRORS as e:
        print(f"error reading input: {e}", file=sys.stderr)
        return 2

    text = result_to_json(out)
    print(json.dumps(json.loads(text), indent=2) if args.pretty else text)
    return 0 if out["valid"] else 1


def _cmd_example(_args: argparse.Namespace) -> int:
    from physics.io import scene_to_dict
    from tests.fixtures import scene_scanned_hulls

    print(result_to_json(scene_to_dict(scene_scanned_hulls())))
    return 0


def _cmd_scan_to_object(args: argparse.Namespace) -> int:
    try:
        item = _load_json(args.item)
        if args.footprint_from_hull:
            hull = _load_json(args.footprint_from_hull)
            obj = object_from_box_fit(
                item.get("id", hull.get("id")),
                hull["width"],
                hull["depth"],
                hull["height"],
                hull["center"],
                hull["axis"],
                hull_xz_world=hull["hull"],
            )
        else:
            obj = object_from_scanned_item(item)
    except _INPUT_ERRORS as e:
        print(f"error reading input: {e}", file=sys.stderr)
        return 2

    print(result_to_json(object_to_dict(obj)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m physics")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a scene (optionally with placements)")
    p_validate.add_argument("scene", help="path to a scene JSON file (see scene_to_dict/scene_from_dict)")
    p_validate.add_argument("--placements", help="path to a placements JSON file")
    p_validate.add_argument("--pretty", action="store_true", help="pretty-print the result JSON")
    p_validate.set_defaults(func=_cmd_validate)

    p_p3d = sub.add_parser("validate-packer3d", help="validate a packer3d solver result JSON")
    p_p3d.add_argument("result", help="path to a packer3d result JSON (single strategy or --compare)")
    p_p3d.add_argument("--strategy", help="which strategy of a --compare result to validate")
    p_p3d.add_argument("--items", help="path to the scenario JSON (supplies keep_upright / priority)")
    p_p3d.add_argument("--pretty", action="store_true", help="pretty-print the result JSON")
    p_p3d.set_defaults(func=_cmd_validate_packer3d)

    p_example = sub.add_parser("example", help="print an example scene JSON")
    p_example.set_defaults(func=_cmd_example)

    p_scan = sub.add_parser("scan-to-object", help="convert a ScannedItem JSON (cm) into an Object dict (m)")
    p_scan.add_argument("item", help="path to a ScannedItem JSON file (width/depth/height in cm)")
    p_scan.add_argument(
        "--footprint-from-hull",
        help="path to a world-space hull JSON (a BoxFit dump: width/depth/height in m, "
        "center, axis, and a hull [[x,z],...] in m) -- derives a posed Object with a "
        "footprint instead of converting the pose-less ScannedItem",
    )
    p_scan.set_defaults(func=_cmd_scan_to_object)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
