"""Typed environment readers shared by the workflows."""

import os


def env_flag(name, default=False):
    """True unless the variable is '0', '', 'false' or 'False'; `default` when unset."""
    value = os.getenv(name)
    return default if value is None else value not in {'0', '', 'false', 'False'}


def bounded_int(name, value, default, low, high):
    """`value` if given, else int(env[name] or default); ValueError outside [low, high]."""
    value = int(os.getenv(name, str(default))) if value is None else value
    if type(value) is not int or not low <= value <= high:
        raise ValueError(name + ' must be an integer from ' + str(low) + ' to ' + str(high))
    return value
