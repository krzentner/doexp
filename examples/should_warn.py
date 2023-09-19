#!/usr/bin/env -S doexp
from doexp import cmd, FileArg, In, Out, GLOBAL_CONTEXT

# Should cause a warning, due to not having any outputs
cmd("python", "-c", "print('Should warn')")
