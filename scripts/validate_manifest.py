#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from figedit_core import read_json, validate_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the FigEdit-compatible Visio manifest.")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    path = args.manifest.resolve()
    result = validate_manifest(read_json(path), path)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
