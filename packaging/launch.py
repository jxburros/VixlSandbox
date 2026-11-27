"""PyInstaller entry point: the packaged app runs vixl_sandbox's main()."""

import sys

from vixl_sandbox.__main__ import main

sys.exit(main())
