"""cc-companion.exe: the portable build's windowed program, the pet itself."""

import os
import sys

# A windowed program has no console: give print-style output somewhere harmless.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from companion.__main__ import main  # noqa: E402

sys.exit(main())
