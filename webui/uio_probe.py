#!/usr/bin/env python3
"""Read-only FM10000 link-register diagnostic for deployment checks."""

import json
import sys

from app import DEFAULT_CONFIG, direct_port_payload, read_json

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    print(json.dumps(direct_port_payload(read_json(path)), indent=2, sort_keys=True))
