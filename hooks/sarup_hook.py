"""Compatibility entry point: the hook now lives in the package as `sarup.hook`.

Existing installs point their PostToolUse hook at this file; new installs run
the `sarup-hook` command instead.
"""

from sarup.hook import main

if __name__ == "__main__":
    main()
