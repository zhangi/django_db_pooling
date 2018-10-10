from django.db import connections
from django.db.utils import ConnectionHandler
from django.core import signals


MAX_POOL_SIZE = 6


def set_pool_size(n):
    global MAX_POOL_SIZE
    MAX_POOL_SIZE = n


_original_get_item = ConnectionHandler.__getitem__


def _new_get_item(self, alias):
    # reuse green-let local for conn inside a single request
    if hasattr(self._connections, alias):
        return getattr(self._connections, alias)

    # reuse conn from connection pool and bind to green-let local
    if hasattr(self, 'connection_pool'):
        if alias in self.connection_pool:
            if self.connection_pool[alias]:
                conn = self.connection_pool[alias].pop()
                conn.close_if_unusable_or_obsolete()
                setattr(self._connections, alias, conn)
                return conn

    # create new instance of connection
    conn = _original_get_item(self, alias)
    conn.allow_thread_sharing = True    # connection can be recycled by different green-lets
    return conn


def recycle_old_connections(**_):
    if not hasattr(ConnectionHandler, 'connection_pool'):
        ConnectionHandler.connection_pool = {}

    aliases = []
    for alias in connections:
        aliases.append(alias)
        conn = connections[alias]
        if alias not in ConnectionHandler.connection_pool:
            ConnectionHandler.connection_pool[alias] = []
        if len(ConnectionHandler.connection_pool[alias]) < MAX_POOL_SIZE:
            ConnectionHandler.connection_pool[alias].append(conn)
        else:
            conn.close()
    for alias in aliases:
        del connections[alias]      # unbind conn from green-let local


def apply_patch():
    signals.request_finished.connect(recycle_old_connections)
    ConnectionHandler.__getitem__ = _new_get_item
