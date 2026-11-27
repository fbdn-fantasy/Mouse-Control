"""Desktop entry point and a read-only terminal check."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="mouse-control")
    parser.add_argument("--list-models", action="store_true", help="list the retail names in the mouse catalog")
    parser.add_argument("--status", action="store_true",
                        help="print the current DPI of connected supported mice and exit")
    args = parser.parse_args()
    if args.list_models:
        from .catalog import CATALOG
        for group, models in CATALOG.items():
            print(group)
            for name in models:
                print(f"  {name}")
        return 0
    if not args.status:
        from .app import MouseApplication
        return MouseApplication().run([sys.argv[0]])

    from .hardware import HardwareError, MouseBackend
    backend = MouseBackend()
    try:
        mice = backend.discover()
        if not mice:
            print("No compatible Logitech mouse detected.", file=sys.stderr)
            return 1
        failed = False
        for mouse in mice:
            try:
                state = backend.read(mouse.id)
                reading = f"{state.dpi} DPI" if state.dpi else (state.detail or "No sensor reading")
                if state.onboard and state.active_stage is not None:
                    stage_dpi = state.stages[state.active_stage]
                    if stage_dpi != state.dpi:
                        reading = f"Active onboard step {state.active_stage + 1} · {stage_dpi} DPI (sensor reports {reading})"
                if state.dpi is None and state.active_stage is None:
                    failed = True
                print(f"{mouse.name}: {reading}")
            except (OSError, HardwareError) as error:
                print(f"{mouse.name}: {error}", file=sys.stderr)
                failed = True
        return int(failed)
    except (OSError, HardwareError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        backend.close()


if __name__ == "__main__":
    sys.exit(main())
