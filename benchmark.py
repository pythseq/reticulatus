__author__ = "Manuel Holtgrewe"
__copyright__ = "Copyright 2017, Manuel Holtgrewe"
__email__ = "manuel.holtgrewe@bihealth.de"
__license__ = "MIT"

import contextlib
import datetime
from itertools import chain
import os
import sys
import time
import threading

import psutil
import GPUtil

from snakemake.exceptions import WorkflowError


#: Interval (in seconds) between measuring resource usage
BENCHMARK_INTERVAL = 30
#: Interval (in seconds) between measuring resource usage before
#: BENCHMARK_INTERVAL
BENCHMARK_INTERVAL_SHORT = 0.5


class BenchmarkRecord:
    """Record type for benchmark times"""

    @classmethod
    def get_header(klass):
        return "\t".join(
            (
                "s",
                "h:m:s",
                "max_rss",
                "max_vms",
                "max_uss",
                "max_pss",
                "io_in",
                "io_out",
                "mean_load",
                "max_gpu_load",
                "max_gpu_mem",
            )
        )

    def __init__(
        self,
        running_time=None,
        max_rss=None,
        max_vms=None,
        max_uss=None,
        max_pss=None,
        io_in=None,
        io_out=None,
        cpu_seconds=None,
        max_gpu_load=None,
        max_gpu_mem=None,
        rss=None,
        vms=None,
        uss=None,
        pss=None,
        gpu_load=None,
        gpu_mem=None,
    ):
        #: Running time in seconds
        self.running_time = running_time or 0
        #: Maximal RSS in MB
        self.max_rss = max_rss
        self.rss = rss
        #: Maximal VMS in MB
        self.max_vms = max_vms
        self.vms = vms
        #: Maximal USS in MB
        self.max_uss = max_uss
        self.uss = uss
        #: Maximal PSS in MB
        self.max_pss = max_pss
        self.pss = pss
        #: I/O read in bytes
        self.io_in = io_in
        #: I/O written in bytes
        self.io_out = io_out
        #: Count of CPU seconds, divide by running time to get mean load estimate
        self.cpu_seconds = cpu_seconds or 0
        #: First time when we measured CPU load, for estimating total running time
        self.first_time = None
        #: Previous point when measured CPU load, for estimating total running time
        self.prev_time = None

        self.max_gpu_load = max_gpu_load
        self.gpu_load = gpu_load
        self.max_gpu_mem = max_gpu_mem
        self.gpu_mem = gpu_mem

    def to_tsv(self, rt=False):
        """Return ``str`` with the TSV representation of this record"""

        def to_tsv_str(x):
            """Conversion of value to str for TSV (None becomes "-")"""
            if x is None:
                return "-"
            elif isinstance(x, float):
                return "{:.2f}".format(x)
            elif isinstance(x, list):
                return ",".join(["{:.2f}".format(f) for f in x])
            else:
                return str(x)

        def timedelta_to_str(x):
            """Conversion of timedelta to str without fractions of seconds"""
            mm, ss = divmod(x.seconds, 60)
            hh, mm = divmod(mm, 60)
            s = "%d:%02d:%02d" % (hh, mm, ss)
            if x.days:

                def plural(n):
                    return n, abs(n) != 1 and "s" or ""

                s = ("%d day%s, " % plural(x.days)) + s
            return s

        if rt:
            return "\t".join(
                map(
                    to_tsv_str,
                    (
                        "{:.4f}".format(self.running_time),
                        timedelta_to_str(datetime.timedelta(seconds=self.running_time)),
                        self.rss,
                        self.vms,
                        self.uss,
                        self.pss,
                        self.io_in,
                        self.io_out,
                        100.0 * self.cpu_seconds / self.running_time,
                        self.gpu_load,
                        self.gpu_mem,
                    ),
                )
            )
        else:
            return "\t".join(
                map(
                    to_tsv_str,
                    (
                        "{:.4f}".format(self.running_time),
                        timedelta_to_str(datetime.timedelta(seconds=self.running_time)),
                        self.max_rss,
                        self.max_vms,
                        self.max_uss,
                        self.max_pss,
                        self.io_in,
                        self.io_out,
                        100.0 * self.cpu_seconds / self.running_time,
                        self.max_gpu_load,
                        self.max_gpu_mem,
                    ),
                )
            )


class DaemonTimer(threading.Thread):
    """Variant of threading.Timer that is deaemonized"""

    def __init__(self, interval, function, args=None, kwargs=None):
        threading.Thread.__init__(self, daemon=True)
        self.interval = interval
        self.function = function
        self.args = args if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}
        self.finished = threading.Event()

    def cancel(self):
        """Stop the timer if it hasn't finished yet."""
        self.finished.set()

    def run(self):
        self.finished.wait(self.interval)
        if not self.finished.is_set():
            self.function(*self.args, **self.kwargs)
        self.finished.set()


class ScheduledPeriodicTimer:
    """Scheduling of periodic events

    Up to self._interval, schedule actions per second, above schedule events
    in self._interval second gaps.
    """

    def __init__(self, interval):
        self._times_called = 0
        self._interval = interval
        self._timer = None
        self._stopped = True
        self._gpu = False
        self._rtpath = None
        self.start_time = None

    def start(self):
        """Start the intervalic timer"""
        self.start_time = time.time()
        self.work()
        self._times_called += 1
        self._stopped = False
        if self._times_called > self._interval:
            self._timer = DaemonTimer(self._interval, self._action)
        else:
            self._timer = DaemonTimer(BENCHMARK_INTERVAL_SHORT, self._action)
        self._timer.start()

    def _action(self):
        """Internally, called by timer"""
        self.work()
        self._times_called += 1
        if self._times_called > self._interval:
            self._timer = DaemonTimer(self._interval, self._action)
        else:
            self._timer = DaemonTimer(BENCHMARK_INTERVAL_SHORT, self._action)
        self._timer.start()

    def work(self):
        """Override to perform the action"""
        raise NotImplementedError("Override me!")

    def cancel(self):
        """Call to cancel any events"""
        self._timer.cancel()
        self._stopped = True


class BenchmarkTimer(ScheduledPeriodicTimer):
    """Allows easy observation of a given PID for resource usage"""

    def __init__(self, pid, bench_record, interval=BENCHMARK_INTERVAL, gpus=None, rt_path=None):
        ScheduledPeriodicTimer.__init__(self, interval)
        #: PID of observed process
        self.pid = pid
        self.main = psutil.Process(self.pid)
        #: ``BenchmarkRecord`` to write results to
        self.bench_record = bench_record
        #: Cache of processes to keep track of cpu percent
        self.procs = {}

        if gpus:
            self._gpu = True
            self.gpus = gpus
            self.bench_record.max_gpu_load = [-1] * len(gpus)
            self.bench_record.max_gpu_mem = [-1] * len(gpus)
            self.bench_record.gpu_load = [-1] * len(gpus)
            self.bench_record.gpu_mem = [-1] * len(gpus)
        if rt_path:
            self._rtpath = rt_path
            write_benchmark_records([], self._rtpath, head=True)


    def work(self):
        """Write statistics"""
        try:
            self._update_record()
        except psutil.NoSuchProcess:
            pass  # skip, process died in flight
        except AttributeError:
            pass  # skip, process died in flight

        if self._rtpath:
            write_benchmark_records([self.bench_record], self._rtpath, head=False, mode='a', rt=True)

    def _update_record(self):
        """Perform the actual measurement"""
        # Memory measurements
        rss, vms, uss, pss = 0, 0, 0, 0
        # I/O measurements
        io_in, io_out = 0, 0
        check_io = True
        # CPU seconds
        cpu_seconds = 0

        if self._gpu:
            # GPU measurements
            gpu_load, gpu_mem = 0, 0

        # Iterate over process and all children
        try:
            this_time = time.time()
            self.bench_record.running_time = this_time - self.start_time
            for proc in chain((self.main,), self.main.children(recursive=True)):
                proc = self.procs.setdefault(proc.pid, proc)
                with proc.oneshot():
                    if self.bench_record.prev_time:
                        cpu_seconds += (
                            proc.cpu_percent()
                            / 100
                            * (this_time - self.bench_record.prev_time)
                        )
                    meminfo = proc.memory_full_info()
                    rss += meminfo.rss
                    vms += meminfo.vms
                    uss += meminfo.uss
                    pss += meminfo.pss
                    if check_io:
                        try:
                            ioinfo = proc.io_counters()
                            io_in += ioinfo.read_bytes
                            io_out += ioinfo.write_bytes
                        except NotImplementedError as nie:
                            # OS doesn't track IO
                            check_io = False
            self.bench_record.prev_time = this_time
            if not self.bench_record.first_time:
                self.bench_record.prev_time = this_time
            rss /= 1024 * 1024
            vms /= 1024 * 1024
            uss /= 1024 * 1024
            pss /= 1024 * 1024
            if check_io:
                io_in /= 1024 * 1024
                io_out /= 1024 * 1024
            else:
                io_in = None
                io_out = None
        except psutil.Error as e:
            return

        if self._gpu:
            for gpu_i, gpu in enumerate(self.gpus):
                gpu_load = GPUtil.getGPUs()[gpu].load*100
                gpu_mem = GPUtil.getGPUs()[gpu].memoryUsed # MB
                self.bench_record.max_gpu_load[gpu_i] = max(self.bench_record.max_gpu_load[gpu_i] or 0, gpu_load)
                self.bench_record.max_gpu_mem[gpu_i] = max(self.bench_record.max_gpu_mem[gpu_i] or 0, gpu_mem)
                self.bench_record.gpu_load[gpu_i] = gpu_load
                self.bench_record.gpu_mem[gpu_i] = gpu_mem

        # Update benchmark record's RSS and VMS
        self.bench_record.max_rss = max(self.bench_record.max_rss or 0, rss)
        self.bench_record.max_vms = max(self.bench_record.max_vms or 0, vms)
        self.bench_record.max_uss = max(self.bench_record.max_uss or 0, uss)
        self.bench_record.max_pss = max(self.bench_record.max_pss or 0, pss)
        self.bench_record.rss = rss
        self.bench_record.vms = vms
        self.bench_record.uss = uss
        self.bench_record.pss = pss

        self.bench_record.io_in = io_in
        self.bench_record.io_out = io_out
        self.bench_record.cpu_seconds += cpu_seconds


@contextlib.contextmanager
def benchmarked(pid=None, benchmark_record=None, interval=BENCHMARK_INTERVAL, gpus=None, rt_path=None):
    """Measure benchmark parameters while within the context manager

    Yields a ``BenchmarkRecord`` with the results (values are set after
    leaving context).

    If ``pid`` is ``None`` then the PID of the current process will be used.
    If ``benchmark_record`` is ``None`` then a new ``BenchmarkRecord`` is
    created and returned, otherwise, the object passed as this parameter is
    returned.

    Usage::

        with benchmarked() as bench_result:
            pass
    """
    result = benchmark_record or BenchmarkRecord()
    if pid is False:
        yield result
    else:
        start_time = time.time()
        bench_thread = BenchmarkTimer(int(pid or os.getpid()), result, interval, gpus=gpus, rt_path=rt_path)
        bench_thread.start()
        yield result
        bench_thread.cancel()
        result.running_time = time.time() - start_time


def print_benchmark_records(records, file_, head=True, rt=False):
    """Write benchmark records to file-like object"""
    if head:
        print(BenchmarkRecord.get_header(), file=file_)
    for r in records:
        print(r.to_tsv(rt=rt), file=file_)


def write_benchmark_records(records, path, head=True, mode="w", rt=False):
    """Write benchmark records to file at path"""
    with open(path, mode) as f:
        print_benchmark_records(records, f, head=head, rt=rt)
