"""runscripts/analysis.py reads the command line (for the Polars thread count) only when run as a script.

bigraph-schema's process discovery imports every module of a package that requires it, vEcoli included, inside
whatever program is running. compose-api's runner, for one, is called with ``-n <end time>`` (a float), which this
module's ``--cpus/-n`` option rejected at import, ending the run before it started.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _python(*args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "POLARS_MAX_THREADS"}
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, env=env, cwd=ROOT
    )


def test_importing_analysis_leaves_the_command_line_alone():
    # the arguments compose-api's runner is called with, plus a --config that does not exist
    code = (
        "import os, sys\n"
        "sys.argv = ['runner.py', 'run', 'x.omex', '-o', 'out', '-n', '60.0', '--config', '/nonexistent.json']\n"
        "import runscripts.analysis\n"
        "print('POLARS_MAX_THREADS', os.environ.get('POLARS_MAX_THREADS'))\n"
    )
    done = _python("-c", code)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "POLARS_MAX_THREADS None"


def test_running_analysis_still_sets_the_polars_thread_count():
    done = _python(str(ROOT / "runscripts" / "analysis.py"), "--cpus", "3", "--help")
    assert done.returncode == 0, done.stderr
    assert "Setting POLARS_MAX_THREADS=3" in done.stdout
