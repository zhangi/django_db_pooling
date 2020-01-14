from typing import Dict

import django
from django.core import signals
from django.db import connections
from django.db.utils import ConnectionHandler
from gevent.queue import Queue

__all__ = ['set_pool_size', 'apply_patch']

MAX_POOL_SIZE = 1
MAX_OUTSTANDING = None
TIMEOUT = None


def make_connection_shareable_below_2_2(conn):
    conn.allow_thread_sharing = True


def make_connection_shareable_above_2_2(conn):
    """
        After Django 2.2, BaseDatabaseWrapper.allow_thread_sharing become
        a property. It can become shareable by calling inc_thread_sharing.
    """
    conn.inc_thread_sharing()


if django.VERSION < (2, 2):
    make_connection_shareable = make_connection_shareable_below_2_2
else:
    make_connection_shareable = make_connection_shareable_above_2_2


def set_pool_size(n, max_outstanding=None, timeout=60):
    """
        set the pool size, max outstanding connections and timeout.

        :param n:
            The size of pool, i.e. how many connections can be recycled.
        :param max_outstanding:
            The maximum outstanding connections outside of the pool.
            If the outstanding connections equals the `max_outstanding`,
            the acquire operation will be blocked until another outstanding
            connection is freed or recycled to pool.
            Values less than `n` will be ignored.
            Default to unlimited.
        :param timeout:
            The maximum wait time for a blocking acquire operation. Not
            applicable when the acquire operation can be immediately fulfilled
            (e.g, when the pool is not empty or the number of connections are
            less than `max_outstanding`.
            Default to 60 seconds.

        :return:
            None.
        """
    global MAX_POOL_SIZE
    global MAX_OUTSTANDING
    global TIMEOUT
    MAX_POOL_SIZE = n
    if max_outstanding and max_outstanding >= n:
        MAX_OUTSTANDING = max_outstanding
    else:
        MAX_OUTSTANDING = None
    TIMEOUT = timeout
    assert n > 0


_original_get_item = ConnectionHandler.__getitem__


class ConnectionPool:
    def __init__(self, alias):
        self._queue = Queue()
        self._outstanding = 0
        self._alias = alias

    def acquire(self):
        if len(self._queue) > 0 or self._outstanding == MAX_OUTSTANDING:
            conn = self._queue.get(timeout=TIMEOUT)
        else:
            # create new instance of connection
            conn = _original_get_item(connections, self._alias)
            # connection can be recycled by different green-lets
            make_connection_shareable(conn)
        self._outstanding += 1
        return conn

    def release(self, conn):
        self._outstanding -= 1
        if len(self._queue) < MAX_POOL_SIZE:
            self._queue.put(conn)
        else:
            conn.close()


def _new_get_item(self, alias):
    # reuse green-let local for conn inside a single request
    if hasattr(self._connections, alias):
        return getattr(self._connections, alias)

    # reuse conn from connection pool and bind to green-let local
    if not hasattr(ConnectionHandler, 'connection_pool'):
        ConnectionHandler.connection_pool = {}

    if alias not in self.connection_pool:
        self.connection_pool[alias] = ConnectionPool(alias)

    conn = self.connection_pool[alias].acquire()
    conn.close_if_unusable_or_obsolete()
    setattr(self._connections, alias, conn)
    return conn


def recycle_old_connections(**_):
    ConnectionHandler.connection_pool: Dict[str, ConnectionPool]
    aliases = []
    for alias in connections:
        aliases.append(alias)
        conn = connections[alias]
        ConnectionHandler.connection_pool[alias].release(conn)

    for alias in aliases:
        del connections[alias]      # unbind conn from green-let local


def apply_patch():
    signals.request_finished.connect(recycle_old_connections)
    ConnectionHandler.__getitem__ = _new_get_item
