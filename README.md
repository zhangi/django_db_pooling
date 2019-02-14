# django_db_pooling
A patch to enable Django database connection pooling with gevent Gunicorn 
workers.

### Usage Guide

1. `pip install django_db_pooling`
1. in wsgi.py: 

        import os
        import pymysql
        from django.core.wsgi import get_wsgi_application
        from django_db_pooling import pooling
        
        pymysql.install_as_MySQLdb()
        
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "xxxxx.settings")
        
        application = get_wsgi_application()
        
        pooling.set_pool_size(4)
        pooling.apply_patch()
        
1. set [`conn_max_age`](https://docs.djangoproject.com/en/dev/ref/settings/#conn-max-age) 
to a value larger than 0 and less than MySQL's `wait_timeout`, e.g 60 seconds is usually
a good enough value.


### How it works

The connection pool patches the `__getitem__` method of  
`django.db.utils.ConnectionHandler` in such a way so that the db connection
 can be reused across multiple requests/green-lets until `conn_max_age` is
 reached.

By default, Django manage the database connection object in a thread local 
attributed `_connections` in `django.db.utils.ConnectionHandler` so that
each thread has its own database connection object to avoid race condition.
It also allows reuse of database connection by specifying a positive value or
None on `conn_max_age` setting to avoid initiating and releasing connection 
for each request. However, it leads to problem when used with the gevent worker
in Gunicorn where the thread local attribute is patched by a green-let local 
attribute: if a green-let is not reused, the database connection object
associated with green-let will not be released or recycled until it is garbage
collected or closed by MySQL. Under a medium or heavy load, those idle 
connections may accumulate and the connection limit of MySQL is finally reached
so that no more new connection can be established.

To solve the above issue, this patch adds a non-local attribute 
`connection_pool`. It is a dictionary with keys being the connection alias name 
and values being a list of pooled connection objects. Whenever a new request 
starts, connection object in the pool will be reused if there is any or else new 
connection object is created. The connection object will be bound to the 
`_connections` attribute to associate with the current request. Underlying 
connection is closed and reopened if the `conn_max_age` is reached. When current 
request finishes, the connection object will be recycled to the pool if pool still
has capacity, which is set by the method `set_pool_size()`(default to 1 if not 
specified). Otherwise the connection object will be freed and the underlying 
connection is closed immediately.

Under extrem heavy load , it might be a good idea to restrict the concurrency per 
worker in Gunicorn 
([--worker_connections](http://docs.gunicorn.org/en/stable/settings.html#worker-connections))
to ensure the overall concurrent database connections are less than the maximum 
permitted simultaneous connections in MySQL 
([--max_connections](https://dev.mysql.com/doc/refman/5.7/en/server-system-variables.html#sysvar_max_connections)).
Alternatively, the `set_pool_size()` method accepts an additional argument 
`max_outstanding`(default to `None` which means unlimited) to limit the maximum concurrent connections per worker basis.