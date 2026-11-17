"""Command-line client for the SRv6 controller control socket."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .control import ControlClient, ControlError, default_socket_path


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=False))


def _offline_path(args: argparse.Namespace) -> Any:
    from net_topology_yaml import load_topology
    from srctl.topology import TopologyView

    topology = load_topology(Path(args.topology))
    view = TopologyView.from_topology(topology)
    nodes = view.path(args.source, args.destination, args.via, args.avoid)
    segments = view.segments_for(args.destination, args.via)
    return {"path": list(nodes), "segments": list(segments)}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="srctl", description=__doc__)
    parser.add_argument(
        "--socket",
        default=default_socket_path(),
        help="controller control socket (default: per-user /tmp path)",
    )
    parser.add_argument(
        "--topology",
        help="offline mode: compute paths from this YAML file instead of the controller",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show controller status")
    sub.add_parser("topology", help="show the controller topology")
    sub.add_parser("policies", help="list the effective policies")
    sub.add_parser("reconcile", help="force a reconciliation")

    apply_cmd = sub.add_parser(
        "apply", help="apply a YAML/JSON policy document"
    )
    apply_cmd.add_argument("-f", "--file", required=True, help="policy document")
    apply_cmd.add_argument(
        "--prune",
        action="store_true",
        help="make the document the complete file-origin policy set",
    )

    diff_cmd = sub.add_parser(
        "diff", help="validate a policy document and show planned actions"
    )
    diff_cmd.add_argument("-f", "--file", required=True, help="policy document")
    diff_cmd.add_argument(
        "--prune",
        action="store_true",
        help="plan against a complete file-origin policy set",
    )

    path = sub.add_parser("path", help="compute a path and segment list")
    path.add_argument("source")
    path.add_argument("destination")
    path.add_argument("--via", action="append", default=[])
    path.add_argument("--avoid", action="append", default=[])

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "path" and args.topology:
            _print(_offline_path(args))
            return 0

        client = ControlClient(args.socket)
        if args.command == "status":
            _print(client.request("status"))
        elif args.command == "topology":
            _print(client.request("topology"))
        elif args.command == "policies":
            _print(client.request("policies.list"))
        elif args.command == "reconcile":
            _print(client.request("reconcile"))
        elif args.command in ("apply", "diff"):
            content = Path(args.file).read_text(encoding="utf-8")
            method = "policies.apply" if args.command == "apply" else "policies.diff"
            _print(
                client.request(
                    method,
                    {"content": content, "prune": args.prune},
                )
            )
        elif args.command == "path":
            _print(
                client.request(
                    "paths.compute",
                    {
                        "source": args.source,
                        "destination": args.destination,
                        "via": args.via,
                        "avoid": args.avoid,
                    },
                )
            )
        else:  # pragma: no cover - argparse rejects unknown commands
            raise ControlError("invalid", f"unknown command {args.command!r}")
    except (ControlError, OSError) as exc:
        print(f"srctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
