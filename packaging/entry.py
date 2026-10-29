"""PyInstaller entry point for the Curvis app."""

import multiprocessing
import sys

from curvis.app import main

if __name__ == "__main__":
    # Worker processes of the job scheduler start through this executable too.
    multiprocessing.freeze_support()
    sys.exit(main())
