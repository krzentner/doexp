#!/usr/bin/env python3
import argparse
import re
from subprocess import run
import sys
import os


def parse_args():
    parser = argparse.ArgumentParser("doexp skypilot wrapper")
    parser.add_argument("--task-file", required=True)
    parser.add_argument("-o", "--out-file", action="append")
    return parser.parse_args()


def main():
    args = parse_args()
    _, file_path = os.path.split(args.task_file)
    file_path = file_path.split(".")[0].replace("_", "")
    cluster_name = f"sky-doexp-{file_path}"
    # Autostop after four hours, manually stop immediately once downloads are done
    launch_proc = run(
        [
            "sky",
            "launch",
            "--yes",
            "--idle-minutes-to-autostop=240",
            "--down",
            "--cluster",
            cluster_name,
            args.task_file,
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    print(launch_proc.stdout)
    # print(launch_proc.stdout, file=sys.stderr)
    print(launch_proc.stderr, file=sys.stderr)
    output = launch_proc.stdout
    job_return_codes_crashed = None
    copy_output_failed = True
    try:
        job_id = re.findall(r"Job submitted with Job ID: ([0-9]+)", output)
        job_return_codes_crashed = re.findall(
            r"Job [0-9]+ failed with return code list:.* \[([^0-9]+)", output
        )
        job_succeeded = "Job finished (status: SUCCEEDED)" in output
        print("skypilot_wrapper: job_id:", job_id)
        print("skypilot_wrapper: cluster_name:", cluster_name)
        print("skypilot_wrapper: job_return_codes_crashed:", job_return_codes_crashed)
        print("skypilot_wrapper: job_succeeded:", job_succeeded)
        if job_succeeded:
            copy_output_failed = False
            for out_file in args.out_file:
                rsync_proc = run(
                    [
                        "rsync",
                        "-Pavz",
                        f"{cluster_name}:/home/gcpuser/sky_workdir/{out_file}",
                        out_file,
                    ]
                )
                if rsync_proc.returncode != 0:
                    print(
                        "skypilot_wrapper: Could not copy out-file",
                        out_file,
                        file=sys.stderr,
                    )
                    copy_output_failed = True
            run(
                [
                    "rsync",
                    "-Prz",
                    f"{cluster_name}:/home/gcpuser/data/",
                    os.path.expanduser("~/data"),
                ],
                check=False,
            )
    finally:
        if cluster_name:
            run(["sky", "down", "--yes", cluster_name])
    if not job_succeeded:
        if job_return_codes_crashed:
            sys.exit(int(job_return_codes_crashed[0]))
        else:
            sys.exit(1)
    if copy_output_failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
