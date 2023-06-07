#!/usr/bin/env python3
import sys
from subprocess import run
import shlex

def main():
    token_filename = sys.argv[1]
    remaining_args = sys.argv[2:]
    print("Running:", file=sys.stderr)
    print(file=sys.stderr)
    print(" ".join(shlex.quote(arg) for arg in remaining_args), file=sys.stderr)
    print(file=sys.stderr)
    run(remaining_args, check=True)
    with open(token_filename, 'w') as f:
        f.write("SUCCESS")

if __name__ == '__main__':
    main()
