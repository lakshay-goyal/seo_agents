"""Tiny step logger: timestamped lines so a run is easy to trace.

Everyone imports these three functions. start() begins a step and
returns a timer; done() ends it and prints how long it took.
"""

import time
from contextlib import contextmanager
from threading import Event, Thread


def now():
    return time.strftime("%H:%M:%S")


def log(message):
    print("[" + now() + "] " + message, flush=True)


def start(name):
    log("START " + name)
    return time.time()


def done(name, begun):
    took = round(time.time() - begun, 1)
    log("DONE " + name + " (" + str(took) + "s)")


@contextmanager
def progress(name, interval=15):
    """Show that a blocking operation is still active, without claiming progress."""
    begun = start(name)
    stopped = Event()

    def heartbeat():
        while not stopped.wait(interval):
            log(name + ': waiting (' + str(round(time.time() - begun, 1)) + 's elapsed)')

    worker = Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    except BaseException:
        stopped.set()
        worker.join()
        log('FAILED ' + name + ' (' + str(round(time.time() - begun, 1)) + 's)')
        raise
    else:
        stopped.set()
        worker.join()
        done(name, begun)
