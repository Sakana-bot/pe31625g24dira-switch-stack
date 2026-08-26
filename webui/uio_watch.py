#!/usr/bin/env python3
"""Continuously sample one FM10000 logical port without invoking the SDK."""

import argparse
import json
import mmap
import os
import struct
import time

from app import (
    DEFAULT_CONFIG,
    FM10000_EPL_BASE,
    FM10000_EPL_STRIDE,
    FM10000_PORT_STRIDE,
    GROUP_BY_KEY,
    decode_port_status,
    parse_platform,
    read_json,
)


def main():
    parser = argparse.ArgumentParser(
        description="Watch a logical port's FM10000 PORT_STATUS register"
    )
    parser.add_argument("logical", type=int)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    if args.duration <= 0 or args.duration > 300 or args.interval < 0.01:
        raise SystemExit("duration must be 0..300 seconds and interval at least 0.01 second")

    config = read_json(args.config)
    _, parsed = parse_platform(config["platform_persistent"])
    matches = [item for item in parsed["ports"] if item["logical"] == args.logical]
    if len(matches) != 1:
        raise SystemExit(f"logical port {args.logical} is not an active external port")
    port = matches[0]
    physical = GROUP_BY_KEY[port["group"]]
    lane = port["lane"] if port["lane"] is not None else 0
    register = FM10000_EPL_BASE + FM10000_EPL_STRIDE * physical["epl"] + FM10000_PORT_STRIDE * lane
    offset = register * 4
    page = getattr(mmap, "PAGESIZE", 4096)
    length = ((offset + 4 + page - 1) // page) * page

    fd = os.open(config.get("uio_device", "/dev/uio0"), os.O_RDWR)
    mapped = mmap.mmap(fd, length, flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=0)
    started = time.time()
    previous = None
    samples = 0
    changes = 0
    counts = {}
    try:
        while time.time() - started < args.duration:
            value = struct.unpack("<I", mapped[offset : offset + 4])[0]
            status = decode_port_status(value)
            state = (
                status["oper"],
                status["fault"],
                status["rx_link_up"],
                status["high_ber"],
                status["pcs"],
            )
            counts[state] = counts.get(state, 0) + 1
            samples += 1
            if state != previous:
                if previous is not None:
                    changes += 1
                print(
                    "{:8.3f}s oper={} fault={} rx={} hiber={} pcs={} raw={}".format(
                        time.time() - started,
                        status["oper"],
                        status["fault"],
                        int(status["rx_link_up"]),
                        int(status["high_ber"]),
                        status["pcs"],
                        status["raw"],
                    )
                )
                previous = state
            time.sleep(args.interval)
    finally:
        mapped.close()
        os.close(fd)

    states = []
    for state, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        states.append(
            {
                "oper": state[0],
                "fault": state[1],
                "rx_link_up": state[2],
                "high_ber": state[3],
                "pcs": state[4],
                "samples": count,
            }
        )
    print(
        json.dumps(
            {
                "logical": args.logical,
                "epl": physical["epl"],
                "lane": lane,
                "duration": round(time.time() - started, 3),
                "interval": args.interval,
                "samples": samples,
                "state_changes": changes,
                "states": states,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
