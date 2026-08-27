#!/usr/bin/env python3
"""Generate the initial fixed 24-slot platform from a validated vendor base."""

import importlib.util
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: generate-fixed-platform.py WEBUI_DIR BASE OUTPUT")
    webui_dir, base_path, output_path = map(Path, sys.argv[1:])
    sys.path.insert(0, str(webui_dir))
    spec = importlib.util.spec_from_file_location("switch_manager_app", webui_dir / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requested = {
        group["key"]: {"layout": "bonded", "speed": 100000}
        for group in module.GROUPS
    }
    rendered, parsed = module.generate_platform(base_path.read_text(encoding="utf-8"), requested)
    if not module.uses_fixed_logical_model(parsed):
        raise SystemExit("generated platform does not use the fixed logical-port model")
    output_path.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
