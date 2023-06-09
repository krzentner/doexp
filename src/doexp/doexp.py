#!/usr/bin/env python3
# See https://github.com/krzentner/doexp
from dataclasses import dataclass, field, replace
from typing import Union, List, Tuple, Optional
import os
import re
import subprocess
import psutil
import time
import shutil
import math
import sys
import argparse
import shlex
import tempfile


@dataclass(frozen=True)
class FileArg:
    filename: str


@dataclass(frozen=True)
class In(FileArg):
    pass


@dataclass(frozen=True)
class Out(FileArg):
    pass


@dataclass(frozen=True)
class Cmd:

    args: tuple
    extra_outputs: tuple
    extra_inputs: tuple
    warmup_time: float = 1.0
    ram_gb: float = 4.0
    priority: Union[int, Tuple[int, ...]] = 10
    gpus: Union[str, None] = None
    cores: Optional[int] = None
    skypilot_template: Optional[str] = None

    def __str__(self):
        args = _cmd_to_args(self, "data", "tmp_data")
        return " ".join(args)

    def to_shell(self, data_dir, tmp_data_dir):
        args = _cmd_to_args(self, data_dir, tmp_data_dir)
        return " ".join(shlex.quote(arg) for arg in args)

    def _cores_as_int(self):
        if self.cores is None:
            return 1
        else:
            return self.cores


_BYTES_PER_GB = (1024) ** 3

def printv(verbose, *args, **kwargs):
    if verbose:
        print(*args, **kwargs)


def get_cuda_gpus():
    visible_devices = os.environ.get("CUDA_VISIBLE_DEVICES", None)
    if visible_devices is None:
        try:
            _nvidia_smi_proc = subprocess.run(
                ["nvidia-smi", "--list-gpus"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            output = _nvidia_smi_proc.stdout.decode("utf-8")
            return tuple(re.findall(r"GPU ([0-9]*):", output))
        except FileNotFoundError:
            return ()
    else:
        if visible_devices == "-1":
            return ()
        try:
            return tuple([str(int(d)) for d in visible_devices.split(",")])
        except ValueError:
            return ()


def _ram_in_use_gb():
    vm = psutil.virtual_memory()
    return (vm.total - vm.available) / _BYTES_PER_GB


def _filter_cmds_remaining(commands, data_dir):
    out_commands = set()
    for cmd in commands:
        for arg in cmd.args + cmd.extra_outputs:
            if isinstance(arg, Out) and not os.path.exists(
                os.path.join(data_dir, arg.filename)
            ):
                out_commands.add(cmd)
    return out_commands


def _filter_cmds_ready(commands, data_dir, verbose):
    out_commands = set()
    for cmd in commands:
        ready = True
        for arg in cmd.args + cmd.extra_inputs:
            if isinstance(arg, In) and not os.path.exists(
                os.path.join(data_dir, arg.filename)
            ):
                ready = False
                printv(verbose, "Waiting on input:", arg.filename)
                break
        if ready:
            out_commands.add(cmd)
    return out_commands


def _filter_cmds_ram(commands, *, reserved_ram_gb, ram_gb_cap, use_skypilot, verbose):
    out_commands = set()
    for cmd in commands:
        if use_skypilot and cmd.skypilot_template:
            out_commands.add(cmd)
        elif max(reserved_ram_gb, _ram_in_use_gb()) + cmd.ram_gb <= ram_gb_cap:
            out_commands.add(cmd)
        else:
            printv(verbose, "Not enough ram free to run:", cmd)
    return out_commands


def _filter_cmds_cores(commands, *, reserved_cores, max_core_alloc, use_skypilot, verbose):
    out_commands = set()
    for cmd in commands:
        cores = cmd._cores_as_int()
        if use_skypilot and cmd.skypilot_template:
            out_commands.add(cmd)
        elif reserved_cores + cores <= max_core_alloc:
            out_commands.add(cmd)
        else:
            printv(verbose, "Not enough cores free to run:", cmd)
    return out_commands


def _sort_cmds(commands):
    def key(cmd):
        priority_as_list = cmd.priority
        if not isinstance(priority_as_list, (list, tuple)):
            priority_as_list = [priority_as_list]

        return ([-prio for prio in priority_as_list], cmd.warmup_time, cmd.ram_gb)

    return sorted(list(commands), key=key)


def _cmd_to_args(cmd, data_dir, tmp_data_dir):
    args = []
    for arg in cmd.args:
        if isinstance(arg, In):
            assert not arg.filename.startswith("/")
            arg = os.path.join(data_dir, arg.filename)
            os.makedirs(os.path.split(arg)[0], exist_ok=True)
        elif isinstance(arg, (Out, FileArg)):
            assert not arg.filename.startswith("/")
            # Use temporary directory here
            arg = os.path.join(tmp_data_dir, arg.filename)
            os.makedirs(os.path.split(arg)[0], exist_ok=True)
        args.append(str(arg))
    return args


def _filter_completed(running):
    for p in running:
        p[1].poll()
    completed = [p for p in running if p[1].returncode is not None]
    now_running = [p for p in running if p not in completed]
    assert len(completed) + len(now_running) == len(running)
    return now_running, completed


def _cmd_name(cmd):
    args = []
    for arg in cmd.args:
        if isinstance(arg, (In, Out)):
            args.append(arg.filename)
        else:
            args.append(str(arg))
    name = " ".join(args).replace("/", ":")
    if len(name) > 200:
        name = name[:200]
    return name


@dataclass
class Context:

    commands: set = field(default_factory=set)
    running: list = field(default_factory=list)
    data_dir: str = f"{os.getcwd()}/data"
    temporary_data_dir: Optional[str] = None
    verbose: bool = False
    verbose_now: bool = False
    _vm_percent_cap: float = 90.0

    # Affects slurm and skypilot as well
    max_concurrent_jobs: int or None = psutil.cpu_count()

    # Affects slurm and skypilot as well
    max_core_alloc: int or None = psutil.cpu_count()

    # These fields record allocations, etc.
    reserved_cores: int = 0
    warmup_deadline: float = time.monotonic()
    reserved_ram_gb: float = _ram_in_use_gb()
    last_commands_remaining: int = -1
    next_cuda_device: int = 0
    _cuda_devices: List[str] = get_cuda_gpus()

    # External runner fields
    srun_availabe: bool = bool(shutil.which("srun"))
    use_slurm: Optional[bool] = None
    use_skypilot: bool = False

    @property
    def _tmp_data_dir(self):
        if self.temporary_data_dir is not None:
            return self.temporary_data_dir
        else:
            return f"{self.data_dir}_tmp"

    @property
    def ram_gb_cap(self):
        return (
            self._vm_percent_cap
            * psutil.virtual_memory().total
            / (100.0 * _BYTES_PER_GB)
        )

    @property
    def vm_percent_cap(self):
        return self._vm_percent_cap

    @vm_percent_cap.setter
    def vm_percent_cap(self, value):
        self._vm_percent_cap = value

    def cmd(self, command):
        self.commands.add(command)

    def run_all(self, args):
        """Runs all commands listed in the file."""
        self.verbose = args.verbose
        self.data_dir = args.data_dir
        self.temporary_data_dir = args.tmp_dir
        self.use_slurm = args.use_slurm
        self.use_skypilot = args.use_skypilot
        if self.use_skypilot:
            print("WARNING: Using skypilot. Watch usage to avoid excessive bills.")
        if self.srun_availabe and self.use_slurm is None:
            print("srun is available. Either pass --use-slurm or --no-use-slurm")
            return
        elif self.use_slurm and not self.srun_availabe:
            print("srun is not available, cannot use slurm")
            return
        elif not self.use_slurm:
            print(f"Using GPUS: {self._cuda_devices}")
        done = False
        while not done:
            ready_cmds, done = self._refresh_commands(args.expfile, args.dry_run)
            if self._ready() and ready_cmds:
                self.run_cmd(ready_cmds[0])
                done = False
            if not self.use_slurm:
                self._terminate_if_oom()
            self.running, completed = _filter_completed(self.running)
            if self.running:
                done = False
            if not completed:
                # Don't hard-loop
                time.sleep(0.2)
            self._process_completed(completed)

    def _ready(self):
        """Checks global readiness conditions."""
        if (
            self.max_concurrent_jobs is not None
            and len(self.running) >= self.max_concurrent_jobs
        ):
            return False
        if time.monotonic() < self.warmup_deadline:
            return False
        return True

    def _refresh_commands(self, filename, dry_run):
        """Reloads, filters, and sorts the commands"""
        old_commands = self.commands
        self.commands = set()
        try:
            with open(filename) as f:
                content = f.read()
                exec(content, {})
        except Exception as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise exc
            else:
                try:
                    line_num = sys.exc_info()[2].tb_next.tb_lineno
                    print(f"Error in exps.py (line {line_num}):")
                    print(exc)
                    print(">>", content.split("\n")[line_num - 1])
                except AttributeError:
                    print(exc)
                self.commands = old_commands
        if dry_run:
            for cmd in _sort_cmds(self.commands):
                print(cmd.to_shell(self.data_dir, self._tmp_data_dir))
            return set(), True
        ready, done, remaining = self._filter_commands(self.commands)
        if len(remaining) != self.last_commands_remaining:
            self.last_commands_remaining = len(remaining)
            print("Number of commands:", len(self.commands))
            print("Commands remaining:", len(remaining))
            self.verbose_now = self.verbose
        else:
            self.verbose_now = False
        return _sort_cmds(ready), done

    def _filter_commands(self, commands):
        """Filters the commands to find only those that are ready to run"""
        needs_output = _filter_cmds_remaining(commands, self.data_dir)
        has_inputs = _filter_cmds_ready(needs_output, self.data_dir, self.verbose_now)
        if needs_output and not has_inputs:
            print("Commands exist without any way to acquire inputs:")
            for cmd in needs_output:
                print(str(cmd))
        if self.use_slurm:
            fits_in_ram = has_inputs
        else:
            fits_in_ram = _filter_cmds_ram(
                has_inputs,
                reserved_ram_gb=self.reserved_ram_gb,
                ram_gb_cap=self.ram_gb_cap,
                use_skypilot=self.use_skypilot,
                verbose=self.verbose_now,
            )
        fits_in_core_alloc = _filter_cmds_cores(
            fits_in_ram,
            reserved_cores=self.reserved_cores,
            max_core_alloc=self.max_core_alloc,
            use_skypilot=self.use_skypilot,
            verbose=self.verbose_now,
        )
        not_running = self._filter_cmds_running(fits_in_core_alloc)
        return not_running, not bool(needs_output), needs_output

    def _filter_cmds_running(self, commands):
        """Filters out running commands"""
        out_commands = set()
        for cmd in commands:
            running = False
            for (running_cmd, proc) in self.running:
                if cmd == running_cmd:
                    running = True
                    break
            if not running:
                out_commands.add(cmd)
        return out_commands

    def run_cmd(self, cmd):
        """Sets temp files and starts a process for cmd"""
        self.reserved_ram_gb += cmd.ram_gb
        self.reserved_cores += cmd._cores_as_int()
        cmd_dir = os.path.join(self._tmp_data_dir, "pipes", _cmd_name(cmd))
        os.makedirs(cmd_dir, exist_ok=True)
        stdout = open(os.path.join(cmd_dir, "stdout.txt"), "w")
        stderr = open(os.path.join(cmd_dir, "stderr.txt"), "w")
        # print(cmd.to_shell(self.data_dir))
        proc = self._run_process(
            cmd,
            stdout=stdout,
            stderr=stderr,
        )
        print(proc.pid)
        self.warmup_deadline = time.monotonic() + cmd.warmup_time
        self.running.append((cmd, proc))
        return proc

    def _run_process(self, cmd, *, stdout, stderr):
        args = _cmd_to_args(cmd, self.data_dir, self._tmp_data_dir)
        env = os.environ.copy()
        if self.use_skypilot and cmd.skypilot_template:
            tmp_dir_rel_path = os.path.relpath(self._tmp_data_dir, os.getcwd())
            data_dir_rel_path = os.path.relpath(self.data_dir, os.getcwd())
            args_rel = _cmd_to_args(cmd, data_dir_rel_path, tmp_dir_rel_path)
            command = " ".join([shlex.quote(arg) for arg in args_rel])
            with open(cmd.skypilot_template) as f:
                template_content = f.read()
            skypilot_yaml = template_content.format(command=command)
            skypilot_file = tempfile.NamedTemporaryFile(
                "w", suffix=".yaml", delete=False
            )
            skypilot_file.write(skypilot_yaml)
            skypilot_file.close()
            args = [
                "python",
                "-m",
                "doexp.skypilot_wrapper",
                "--task-file",
                skypilot_file.name,
            ]
            for arg in cmd.args:
                if isinstance(arg, (Out, FileArg)) and not isinstance(arg, In):
                    args.append("--out-file")
                    f_path = os.path.join(tmp_dir_rel_path, arg.filename)
                    args.append(f_path)
        elif self.use_slurm:
            ram_mb = int(math.ceil(1024 * cmd.ram_gb))
            if not cmd.cores:
                core_args = ()
                mb_per_core = int(1024 * cmd.ram_gb)
            else:
                core_args = (f"--cpus-per-task={cmd.cores}",)
                mb_per_core = int(math.ceil(1024 * cmd.ram_gb / cmd.cores))
            args = [
                "srun",
                *core_args,
                f"--mem-per-cpu={mb_per_core}M",
                "--",
            ] + args
        elif cmd.gpus is not None:
            env["CUDA_VISIBLE_DEVICES"] = cmd.gpus
        elif len(self._cuda_devices) > 1:
            env["CUDA_VISIBLE_DEVICES"] = str(self._cuda_devices[self.next_cuda_device])
            self.next_cuda_device = (self.next_cuda_device + 1) % len(
                self._cuda_devices
            )
        print(" ".join(args))
        return subprocess.Popen(args, stdout=stdout, stderr=stderr, env=env)

    def _terminate_if_oom(self):
        """Terminates processes if over ram cap"""
        gb_free = self.ram_gb_cap - _ram_in_use_gb()

        def total_time(cmd_proc):
            cmd, proc = cmd_proc
            times = psutil.Process(proc.pid).cpu_times()
            return (
                times.user + times.system + times.children_user + times.children_system
            )

        by_total_time = sorted(self.running, key=total_time)
        for (running_cmd, proc) in by_total_time:
            try:
                mem = psutil.Process(proc.pid).memory_full_info()
            except psutil.ZombieProcess:
                continue
            ram_gb = (mem.pss + mem.uss) / _BYTES_PER_GB
            if ram_gb > running_cmd.ram_gb:
                print(
                    f"Command exceeded memory limit "
                    f"({ram_gb} > {running_cmd.ram_gb}): "
                    f"{_cmd_name(running_cmd)}"
                )
                self.reserved_ram_gb -= running_cmd.ram_gb
                # Can't assign fields on Cmd's, since they're frozen
                self.running.remove((running_cmd, proc))
                self.running.append((replace(running_cmd, ram_gb=ram_gb), proc))
                self.reserved_ram_gb += running_cmd.ram_gb
            if gb_free < 0:
                print(f"Terminating process: {_cmd_name(running_cmd)}")
                proc.terminate()
                gb_free += ram_gb

    def _process_completed(self, completed):
        """Copy outputs from the tmp dir if the process exited successfully"""
        for (cmd, proc) in completed:
            self.reserved_ram_gb -= cmd.ram_gb
            self.reserved_cores -= cmd._cores_as_int()
            if proc.returncode != 0:
                print(f"Error running {str(cmd)}")
                cmd_dir = os.path.join(self._tmp_data_dir, "pipes", _cmd_name(cmd))
                with open(os.path.join(cmd_dir, "stderr.txt")) as f:
                    print(f.read())
            else:
                print(f"Command complete: {str(cmd)}")
                for arg in cmd.args + cmd.extra_outputs:
                    if isinstance(arg, Out):
                        tmp = os.path.join(self._tmp_data_dir, arg.filename)
                        final = os.path.join(self.data_dir, arg.filename)
                        os.makedirs(os.path.split(final)[0], exist_ok=True)
                        try:
                            if os.path.isdir(tmp):
                                shutil.copytree(tmp, final, dirs_exist_ok=True)
                            else:
                                shutil.copy2(tmp, final)
                        except Exception as exc:
                            if isinstance(exc, KeyboardInterrupt):
                                raise exc
                            print(f"Could not copy output {tmp} for command {cmd}")


GLOBAL_CONTEXT = Context()


def cmd(
    *args,
    priority: Union[int, List[int], Tuple[int, ...]] = 10,
    ram_gb: float = 4,
    cores: Optional[int] = None,
    warmup_time: float = 1.0,
    extra_outputs=tuple(),
    extra_inputs=tuple(),
    gpus: Union[str, None] = None,
    skypilot_template: Optional[str] = None,
) -> None:
    """Add a command to be run by the GLOBAL_CONTEXT.

    Args:
        - `priority`: an int (or tuple of ints) that defines the priority of the command (higher priority runs first). Typically used to ensure an even spread across experiments by using `-seed` as a priority.
        - `ram_gb`: a float (or int) of the expected GiB of RAM the command will use. This is softly enforced when run locally, and strongly enforced when using `slurm`. Note that a maximum RAM usage percentile (90% by default) is strictly enforced even when running locally to avoid thrashing.
        - `cores`: a number of cores that the command needs. Strongly enforced when using `slurm`.
        - `warmup_time`: a number of seconds to wait after running a command before starting another command. Useful to avoid hitting rate limits or overloading systems by starting too many processes at once.
        - `extra_outputs`: a tuple of `Out` files that will be created by the command, but which are not present in the arguments.
        - `extra_inputs`: a tuple of `In` files required by the command, but which are not present in the arguments. Often used to emulate globbing.
        - `gpus`: an optional string declaring which gpus the command should have access to. If not passed, `CUDA_VISIBLE_DEVICES` will be used to assign GPUs in a round-robin fashion.
        - `skypilot_template`: a path to a skypilot yaml file that contains a replacement sequence `{command}` in it. A command must specify a `skypilot_template` to use skypilot, and one skypilot cluster will be created using the template per command. See `examples/skypilot_template.yaml` for an example.

    """
    if isinstance(priority, list):
        priority = tuple(priority)
    GLOBAL_CONTEXT.cmd(
        Cmd(
            args=tuple(args),
            extra_outputs=tuple(extra_outputs),
            extra_inputs=tuple(extra_inputs),
            warmup_time=warmup_time,
            ram_gb=ram_gb,
            priority=priority,
            gpus=gpus,
            cores=cores,
            skypilot_template=skypilot_template,
        )
    )


def parse_args():
    parser = argparse.ArgumentParser("doexp.py")
    parser.add_argument("expfile", nargs="?", default="exps.py")
    # data_dir: str = os.path.expanduser("~/exp_data")
    parser.add_argument("-d", "--data-dir", default=f"{os.getcwd()}/data")
    parser.add_argument("-t", "--tmp-dir", default=f"{os.getcwd()}/data_tmp")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-skypilot", action="store_true")
    parser.add_argument("--use-slurm", action="store_true")
    parser.add_argument("--no-use-slurm", dest="use_slurm", action="store_true")
    parser.set_defaults(use_slurm=None)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main():
    if __name__ == "__main__":
        sys.modules["doexp"] = sys.modules["__main__"]
    import doexp

    assert doexp.GLOBAL_CONTEXT is GLOBAL_CONTEXT
    doexp.GLOBAL_CONTEXT.run_all(parse_args())


if __name__ == "__main__":
    main()
