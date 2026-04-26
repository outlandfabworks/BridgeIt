"""Top-level entry point — allows running `python main.py` from repo root."""
import multiprocessing
multiprocessing.freeze_support()   # must be called before anything else in PyInstaller binaries

from bridgeit.main import main

if __name__ == "__main__":
    main()
