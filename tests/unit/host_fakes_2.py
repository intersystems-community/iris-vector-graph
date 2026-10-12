"""Test double for package host-sites-2: a Host over a scripted raw Native API object.

The migrated engine and store sites reach the Native API through ``host_for``, which calls ``iris.createIRIS`` at
call time. Tests that used to replace ``engine._iris_obj`` with a fake raw handle now patch ``iris.createIRIS``
(context-manager form) or replace ``engine._host`` with a function returning ``host_over(fake)`` (attribute form).
"""

from iris_vector_graph.host import ClientHost


def host_over(native):
    """A ClientHost whose Native API handle is ``native``; Host operations call ``native``'s methods."""
    host = ClientHost(None)
    host._native = native
    return host
