# `doexp`

The simplest experiment runner KR could write.

# Warning: `doexp` is alpha quality software, and has several quirks:

  - If you enable skypilot with the `--use-skypilot` flag, `doexp` will happily provision hundreds of machines without further confirmation.
  - If the `doexp` process is killed (or crashes), all in progress experiment results will be discarded.
  - doexp will keep running crashing experiments in a loop until stopped manually.

# If you turn on skypilot, watch `doexp` carefully to avoid surprise bills. You have been warned!

## Installation instructions:

Install with:

```
pip install git+https://github.com/krzentner/doexp.git@v0.1.1
```

Copy the `example/exps.py` file into your project, and add experiments to it.

Run

```
doexp <path-to-exps.py>
```


The only required dependency is `psutil`.
SLURM will be used if the `--use-slurm` flag is passed and `srun` is available.
`skypilot` uses a wrapper script (see `src/doexp/skypilot_wrapper.py`).


## What is `doexp` for?

`doexp` is a build tool for papers.
It reads a description of a set of experiments to run, and runs each of those experiments while respecting RAM and core limits.
It runs commands in parallel, while ensuring that the machine does not run out of memory in the process.
It can also spread those experiments out across multiple GPUs, or across multiple machines using `slurm` or `skypilot`.
With enough compute resources available, `doexp` can completely re-run all experiments for a paper.
This allows a single researcher to rapidly iterate across many experiments, and multiple researchers to re-run each other's work more easily.


## How does `doexp` work?

While running, every second `doexp` re-evaluates an "experiment file" (`exps.py` by default), that declares a set of commands.
`doexp` finds the highest priority command for which an output file is missing and sufficient resources are available, and starts running it.
`doexp` will keep starting commands in parallel until there aren't enough resources (usually a RAM or core limit) to run more commands.

Running commands have their output arguments redirected to a temporary directory, and their pipes redirected to files.
When a command exists successfully, its output is copied to a result directory (named `data`, by default).

### Command declarations

Commands are typically declared using the `cmd` function.
The positional arguments to the `cmd` function will become command line arguments.
Besides strings and numbers, `In` and `Out` files can be passed as positional arguments.
A command will only be run once every `In` file in its arguments exists in the `data` directory, and at least one of the `Out` files in its arguments doesn't exist.
Typically, `Out` files will be log / checkpoint directories.

Besides positional arguments, several keyword arguments can also be passed to the `cmd` function.

They are as follows:

  - `priority`: an int (or tuple of ints) that defines the priority of the command (higher priority runs first). Typically used to ensure an even spread across experiments by using `-seed` as a priority.
  - `ram_gb`: a float (or int) of the expected GiB of RAM the command will use. This is softly enforced when run locally, and strongly enforced when using `slurm`. Note that a maximum RAM usage percentile (90% by default) is strictly enforced even when running locally to avoid thrashing.
  - `cores`: a number of cores that the command needs. Strongly enforced when using `slurm`.
  - `warmup_time`: a number of seconds to wait after running a command before starting another command. Useful to avoid hitting rate limits or overloading systems by starting too many processes at once.
  - `extra_outputs`: a tuple of `Out` files that will be created by the command, but which are not present in the arguments.
  - `extra_inputs`: a tuple of `In` files required by the command, but which are not present in the arguments. Often used to emulate globbing.
  - `gpus`: an optional string declaring which gpus the command should have access to. If not passed, `CUDA_VISIBLE_DEVICES` will be used to assign GPUs in a round-robin fashion.
  - `skypilot_template`: a path to a skypilot yaml file that contains a replacement sequence `{command}` in it. A command must specify a `skypilot_template` to use skypilot, and one skypilot cluster will be created using the template per command. See `examples/skypilot_template.yaml` for an example.

## FAQ:

  - My experiments aren't running?
    Try passing the `--verbose` flag, or setting `GLOBAL_CONTEXT.verbose = True`.
    Check to make sure you had enough RAM available when you started doexp.
    doexp assumes all ram in use when it was started can't be used.

## Citing `doexp`:

If doexp was useful in your research, I'd always appreciate a citation:

```
@misc{doexp,
 author = {K.R. Zentner and the doexp contributors},
 title = {doexp: A simple experiment runner},
 year = {2023},
 publisher = {GitHub},
 journal = {GitHub repository},
 howpublished = {\url{https://github.com/krzentner/doexp}},
}
```
