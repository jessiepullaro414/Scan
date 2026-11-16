"""Entry point of the packaged program (``ohs.exe``): the same as the ``ohs`` command, so
``ohs.exe app`` starts OpenHandScan Studio and ``ohs.exe bench``, ``ohs.exe process ...`` work as
they do from Python. See ``build_exe.py``."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # a packaged program starting worker processes runs this file again
    from ohs.app.cli import main

    sys.exit(main())
