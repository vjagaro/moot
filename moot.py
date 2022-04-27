#!/usr/bin/env python3

__version__ = "0.1.0"

import argparse
import itertools
import os
import signal
import subprocess
import sys
import time
import threading
import types

READ_SIZE = 4096
WRITE_SIZE = 4096
MAX_LINE_LENGTH = 77
MAX_SHELL_COMMAND_LINES = 4
PROCESS_WAIT_LOOP_POLL = 0.4
STDOUT = 1
STDERR = 2

received_sigterm = False


def get_parser():
    parser = argparse.ArgumentParser(
        usage="%(prog)s [OPTIONS ...] SUMMARY [COMMAND ...]",
        description="""
            Run COMMAND with its output suppressed and SUMMARY shown instead.
            If COMMAND errors, then its output will be shown.
        """,
    )
    parser.add_argument(
        "-l",
        "--log",
        nargs=1,
        help="additionally write output to FILE",
        metavar="FILE",
    )
    parser.add_argument(
        "-a",
        "--always-output",
        action="store_true",
        help="show output regardless of error state",
    )
    parser.add_argument(
        "--no-color",
        dest="color",
        action="store_false",
        help="suppress color",
    )
    parser.add_argument(
        "--no-info",
        dest="info",
        action="store_false",
        help="suppress info (command, exit code, duration)",
    )
    parser.add_argument(
        "--no-timestamps",
        dest="timestamps",
        action="store_false",
        help="suppress timestamps",
    )
    parser.add_argument("summary", metavar="SUMMARY", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="*", metavar="COMMAND", help=argparse.SUPPRESS)
    return parser


def get_config(argv, environ):
    args = []
    end_of_options = False
    for arg in argv[1:]:
        if not end_of_options:
            if arg == "--":
                end_of_options = True
            elif not arg.startswith("-") and not (
                args and (args[-1] == "-l" or args[-1] == "--log")
            ):
                args.append("--")
                end_of_options = True
        args.append(arg)

    config = get_parser().parse_args(args)

    # argparse confusingly uses a list for nargs=1
    for key in ("log", "summary"):
        value = getattr(config, key)
        if type(value) is list:
            setattr(config, key, value[0])

    if config.command:
        config.shell = False
    else:
        config.command = [environ.get("MOOT_SHELL", environ.get("SHELL", "/bin/bash"))]
        config.shell = True

    config.shell_env = environ.get("MOOT_SHELL_ENV")

    return config


class Printer(object):
    def __init__(self, file=None, color=False):
        self.file = file
        self.color = color

    def print(self, *args, **kwargs):
        print(*args, file=self.file, **kwargs)

    def print_color(self, code):
        if self.color:
            self.print("\x1b[%dm" % code, end="")

    def ok_color(self):
        self.print_color(33)  # Brown

    def error_color(self):
        self.print_color(31)  # Red

    def system_color(self):
        self.print_color(36)  # Cyan

    def reset_color(self):
        self.print_color(0)  # Reset

    def summary(self, summary):
        self.print(summary, end="", flush=True)

    def passed(self):
        self.print(" ✓")

    def failed(self):
        self.error_color()
        self.print(" ✗")
        self.reset_color()

    def report_header(self, process):
        if process.errored:
            self.error_color()
        else:
            self.system_color()
        self.print("$ " + " ".join(process.command))
        if process.shell_commands:
            lines = process.shell_commands.decode("utf-8").rstrip().split("\n")
            if len(lines) > MAX_SHELL_COMMAND_LINES:
                lines = lines[:MAX_SHELL_COMMAND_LINES]
                lines[-1] += " ..."
            for line in lines:
                self.print("> " + line)
        self.reset_color()

    def report_main(self, process, timestamps=True):
        duration = str(round(process.ended - process.started, 1))
        for kind, ts, data in process.output:
            self.ok_color()
            if timestamps:
                if kind == STDERR:
                    l, r = "{", "}"
                else:
                    l, r = "[", "]"
                elapsed = str(round(ts - process.started, 1)).rjust(len(duration))
                self.print("%s%s%s " % (l, elapsed, r), end="")
            self.print(data.decode("utf-8"))
            self.reset_color()

    def report_footer(self, process):
        if process.errored:
            self.error_color()
        else:
            self.system_color()
        self.print("> exit: %d" % process.returncode)
        duration = str(round(process.ended - process.started, 2))
        self.print("> duration: %ss" % duration)
        self.reset_color()

    def report(self, process, info=True, timestamps=True):
        if info:
            self.report_header(process)
        self.report_main(process, timestamps=timestamps)
        if info:
            self.report_footer(process)


class NullPrinter(object):
    def __getattr__(self, name):
        def method(*args, **kwargs):
            pass

        return method


def start_thread(name, target, *args):
    thread = threading.Thread(name=name, target=target, args=args)
    thread.daemon = True
    thread.start()
    return thread


def handle_input(pipe, data):
    pos = 0
    while not received_signal:
        chunk = data[pos : (pos + WRITE_SIZE)]
        if not chunk:
            break
        os.write(pipe.fileno(), chunk)
        pos += WRITE_SIZE
    pipe.close()


def handle_output(kind, pipe, output, lock):
    prev_data = b""
    prev_time = None
    while not received_signal:
        data = os.read(pipe.fileno(), READ_SIZE)
        with lock:
            if data == b"":
                if prev_data:
                    output.append((kind, prev_time, prev_data))
                break
            lines = data.split(b"\n")
            if len(lines) == 1:
                prev_data += lines[0]
                if not prev_time:
                    prev_time = time.time()
            else:
                if prev_data:
                    output.append((kind, prev_time, prev_data + lines[0]))
                    prev_data = b""
                    prev_time = None
                    lines.pop(0)
                if lines[-1]:
                    prev_data += lines[-1]
                    prev_time = time.time()
                lines.pop()
                for line in lines:
                    output.append((kind, time.time(), line))
    pipe.close()


def setup_signal_handler():
    def setup_signal(name):
        def handler(num, frame):
            global received_signal
            received_signal = signalnum

        signalnum = getattr(signal, name)
        signal.signal(signalnum, handler)

    [setup_signal(name) for name in ["SIGTERM", "SIGINT"]]

    global received_signal
    received_signal = None


def wait_for_process(process, spin_out=None):
    spinner = itertools.cycle(["-", "\\", "|", "/"])
    first = True
    while True:
        try:
            process.wait(PROCESS_WAIT_LOOP_POLL)
            if spin_out and not first:
                spin_out.write("\b\b")
            break
        except subprocess.TimeoutExpired:
            if spin_out:
                if first:
                    first = False
                    spin_out.write(" ")
                else:
                    spin_out.write("\b")
                spin_out.write(next(spinner))
                spin_out.flush()
            if received_signal:
                break


def slurp(file):
    buffer = []
    while not received_signal:
        data = os.read(file.fileno(), READ_SIZE)
        if not data:
            break
        buffer.append(data)
    return b"".join(buffer)


def run_command(command, shell=False, shell_env=None, spin_out=None, stdin=None):
    if shell and stdin:
        shell_commands = slurp(stdin)
    else:
        shell_commands = None

    result = types.SimpleNamespace(
        command=command,
        shell_commands=shell_commands,
        output=[],
        started=time.time(),
        ended=None,
        errored=True,
    )

    process = None
    try:
        process = subprocess.Popen(
            result.command,
            stdin=(subprocess.PIPE if shell_commands else stdin),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        result.output.append(
            (
                STDERR,
                time.time(),
                b"Command not found: " + command[0].encode("utf-8"),
            )
        )
        result.ended = time.time()
        result.returncode = 1
        return result

    lock = threading.Lock()
    if process.stdin and shell_commands:
        stdin_data = (shell_env or "").encode("utf-8") + b"\n" + shell_commands
        stdin_thread = start_thread("stdin", handle_input, process.stdin, stdin_data)
    else:
        stdin_thread = None
    stdout_thread = start_thread(
        "stdout", handle_output, STDOUT, process.stdout, result.output, lock
    )
    stderr_thread = start_thread(
        "stderr", handle_output, STDERR, process.stderr, result.output, lock
    )

    try:
        wait_for_process(process, spin_out=spin_out)
        if received_signal:
            process.terminate()
            data = ("Received: " + str(received_signal)).encode("utf-8")
            with lock:
                result.output.append((STDERR, time.time(), data))
            result.returncode = -int(received_signal)
        else:
            result.returncode = process.returncode
    except BaseException as e:
        data = ("Exception: " + repr(e)).encode("utf-8")
        with lock:
            result.output.append((STDERR, time.time(), data))
        process.terminate()
        result.returncode = -2
    finally:
        if stdin_thread:
            stdin_thread.join()
        stdout_thread.join()
        stderr_thread.join()

    result.ended = time.time()
    result.errored = result.returncode != 0
    return result


def main():
    setup_signal_handler()

    config = get_config(sys.argv, os.environ)
    stdin = sys.stdin
    stdout = sys.stdout
    color = config.color and stdout.isatty()
    spin_out = stdout if config.summary and stdout.isatty() else None

    out = Printer(file=stdout, color=color)
    if config.log:
        logfile = open(config.log, "a", encoding="utf-8")
        log = Printer(file=logfile, color=False)
    else:
        log = NullPrinter()

    if config.summary:
        out.summary(config.summary)
        log.summary(config.summary)

    process = run_command(
        config.command,
        shell=config.shell,
        shell_env=config.shell_env,
        spin_out=spin_out,
        stdin=stdin,
    )

    if config.summary:
        if process.errored:
            out.failed()
            log.failed()
        else:
            out.passed()
            log.passed()

    if config.always_output or process.errored:
        out.report(process, info=config.info, timestamps=config.timestamps)
    log.report(process, info=config.info, timestamps=config.timestamps)

    sys.exit(process.returncode)


if __name__ == "__main__":
    main()
