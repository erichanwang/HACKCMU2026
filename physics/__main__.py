"""CLI for the physics validation layer.

    python3 -m physics validate scene.json [--placements placements.json] [--pretty]
    python3 -m physics example
    python3 -m physics scan-to-object item.json

See examples/README.md for sample input files and the exact shapes involved.
"""
from __future__ import annotations

import argparse
import json
import sys

from physics.io import (
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


def _cmd_example(_args: argparse.Namespace) -> int:
    from physics.io import scene_to_dict
    from tests.fixtures import valid_packed_scene

    print(result_to_json(scene_to_dict(valid_packed_scene())))
    return 0


def _cmd_scan_to_object(args: argparse.Namespace) -> int:
    try:
        item = _load_json(args.item)
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

    p_example = sub.add_parser("example", help="print an example scene JSON")
    p_example.set_defaults(func=_cmd_example)

    p_scan = sub.add_parser("scan-to-object", help="convert a ScannedItem JSON (cm) into an Object dict (m)")
    p_scan.add_argument("item", help="path to a ScannedItem JSON file (width/depth/height in cm)")
    p_scan.set_defaults(func=_cmd_scan_to_object)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
