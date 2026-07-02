#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from figedit_core import prepare_render_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and prepare a semantic manifest for Visio COM rendering.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_render_manifest(args.manifest.resolve(), args.out.resolve())
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
