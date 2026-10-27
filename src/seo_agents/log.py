"""Tiny step logger: timestamped lines so a run is easy to trace.

Everyone imports these three functions. start() begins a step and
returns a timer; done() ends it and prints how long it took.
"""

import time


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
