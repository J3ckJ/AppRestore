"""Entry point of the standalone AppRestore CLI (PyInstaller, console)."""

from __future__ import annotations

import sys


def main() -> int:
    from apprestore_core.frozen import ensure_std_streams, maybe_dispatch

    ensure_std_streams()
    # The frozen CLI is also the "interpreter" for pymobiledevice3 children.
    dispatched = maybe_dispatch(sys.argv[1:])
    if dispatched is not None:
        return dispatched

    from apprestore_core.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
