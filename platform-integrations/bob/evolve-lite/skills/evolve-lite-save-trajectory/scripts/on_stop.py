#!/usr/bin/env python3
"""Stop hook — disabled. evolve-lite:save-trajectory runs only when explicitly invoked."""

import sys


def main():
    # Hook is intentionally a no-op. The save-trajectory skill is run on-demand only.
    sys.exit(0)


if __name__ == "__main__":
    main()
