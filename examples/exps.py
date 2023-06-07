#!/usr/bin/env -S doexp
from doexp import cmd, FileArg, In, Out, GLOBAL_CONTEXT
import glob
from socket import gethostname
import os

HOSTNAME = gethostname()

# Not needed by doexp, but sometimes useful for experiments.
os.environ["DOEXP_PROJECT_ROOT"] = os.getcwd()

# Set to the number of cpus by default
# Likely should be set based on HOSTNAME
# This flag affects slurm and skypilot as well.
# When using those, you will probably want to override the default.
GLOBAL_CONTEXT.max_concurrent_jobs = 1

# Example of starting 10 expeirments
for i in range(10):
    cmd(
        "python",
        "examples/example_experiment.py",
        "--content",
        f"Hello {HOSTNAME}!",
        "--out-file",
        Out(f"example-file-{i}.txt"),
        ram_gb=0.1,
        priority=(10, -i),
    )

    # If a command doesn't produce an output file, use the token_wrapper to
    # record that it ran.
    cmd(
        "python",
        "-m",
        "doexp.token_wrapper",
        Out(f"experiment-{i}-done.token"),
        "cat",
        In(f"example-file-{i}.txt"),
        ram_gb=0.1,
        priority=11, # Run as soon as input is produced above
    )


# Convenient way of dividing experiments by workstation
if os.path.exists(f"{HOSTNAME}_exps.py"):
    with open(f"{HOSTNAME}_exps.py") as f:
        exec(f.read())
else:
    # Run all of them if there's no file for this machine
    for path in glob.glob("*_exps.py"):
        with open(path) as f:
            exec(f.read())
