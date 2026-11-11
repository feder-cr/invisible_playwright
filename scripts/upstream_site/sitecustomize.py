"""Put the upstream suite's redirect into EVERY interpreter of the job.

`upstream_baseline.py` puts this folder first on PYTHONPATH, so Python imports
this file at startup, in pytest and in any child a test starts. A child made
with `multiprocessing` on Windows is a fresh interpreter (spawn): without this
it imports the stock `playwright` and looks for Playwright's own Firefox. The
variable is set only by that script, so no other interpreter is touched.
"""
import os

if os.environ.get("INVPW_BINARY_PATH"):
    import scripts._upstream_alias  # noqa: F401  (importing it installs it)
