#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from figedit_core import prepare_measurements


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare OCR/CV/style evidence for model-authored Visio reconstruction.")
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--ocr-profile", default="v6_medium", choices=["auto", "v6_medium", "v6_small", "v6_tiny", "v5_mobile"])
    parser.add_argument("--ocr-timeout", type=int, default=120, help="Seconds before OCR is skipped; 0 disables the timeout.")
    args = parser.parse_args()
    result = prepare_measurements(args.image.resolve(), args.out.resolve(), args.lang, args.gpu, args.ocr_profile, args.ocr_timeout)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
