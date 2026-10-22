import sys

from ezwipe.diag.cli import main

code = main()
if getattr(sys, "frozen", False):
    input("\nPress Enter to close...")
raise SystemExit(code)
