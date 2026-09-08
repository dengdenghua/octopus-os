from __future__ import annotations

import sys


def main() -> int:
    """Dispatch the fixed extraction mode before importing the application CLI."""
    worker_flag = "--echo-document-worker"
    if worker_flag in sys.argv[1:]:
        # Worker launch is an internal, argument-free protocol. Malformed calls
        # must not fall through to the CLI and accidentally start another server.
        if sys.argv[1:] != [worker_flag]:
            return 2
        sys.argv = sys.argv[:1]
        from runtime.execution.misc.document_worker import main as worker_main

        return worker_main()

    from runtime.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
