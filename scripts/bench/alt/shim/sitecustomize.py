"""Point benchmark scripts at the side's namespace (spec 250).

Python imports `sitecustomize` at startup when its directory is on PYTHONPATH. Many benchmark scripts hard-code
host, port or namespace (USER, 1972, 21972, ...). When IVG_BENCH_NS is set this module rewrites the arguments of
`iris.connect` and `iris.dbapi.connect`, and the container name given to `iris_devtester`'s
`IRISContainer.attach`, so every connection a suite makes goes to the side's own namespace.

Variables: IVG_BENCH_NS (required to activate; must be one of the harness namespaces), IVG_BENCH_PORT,
IVG_BENCH_HOST (default localhost), IVG_BENCH_CONTAINER (container for devtester attach).

Self-contained on purpose: it runs in every Python process of a benchmark, before anything else.
"""

import os

OWN_NAMESPACES = ("IVGBENCHA", "IVGBENCHB", "IVGBENCHSMOKE")
_MARK = "_ivg_bench_wrapped"


def _target(env):
    ns = env.get("IVG_BENCH_NS")
    if not ns:
        return None
    if ns not in OWN_NAMESPACES:
        raise RuntimeError(f"IVG_BENCH_NS={ns!r} is not one of {OWN_NAMESPACES}")
    return env.get("IVG_BENCH_HOST", "localhost"), int(env.get("IVG_BENCH_PORT", "31972")), ns


def rewrite_connect_args(args, kwargs, env=None):
    env = os.environ if env is None else env
    target = _target(env)
    if target is None:
        return args, kwargs
    host, port, ns = target
    args = tuple(args)
    kwargs = dict(kwargs)
    if args and isinstance(args[0], str) and ":" in args[0] and "/" in args[0]:
        args = (f"{host}:{port}/{ns}",) + args[1:]
    elif len(args) >= 3:
        args = (host, port, ns) + args[3:]
    for key in ("hostname", "host"):
        if key in kwargs:
            kwargs[key] = host
    if "port" in kwargs:
        kwargs["port"] = port
    if "namespace" in kwargs:
        kwargs["namespace"] = ns
    return args, kwargs


def attach_name(name, env=None):
    env = os.environ if env is None else env
    return env.get("IVG_BENCH_CONTAINER") or name


def _wrap_connect(owner, env):
    fn = getattr(owner, "connect", None)
    if fn is None or getattr(fn, _MARK, False):
        return

    def connect(*a, **k):
        a, k = rewrite_connect_args(a, k, env)
        return fn(*a, **k)

    connect.__wrapped__ = fn
    connect.__doc__ = getattr(fn, "__doc__", None)
    setattr(connect, _MARK, True)
    owner.connect = connect


def _wrap_attach(env):
    try:
        from iris_devtester import IRISContainer
    except Exception:
        return
    fn = IRISContainer.attach
    if getattr(fn, _MARK, False):
        return
    orig = fn.__func__ if hasattr(fn, "__func__") else fn

    def attach(cls, name, *a, **k):
        return orig(cls, attach_name(name, env), *a, **k)

    setattr(attach, _MARK, True)
    IRISContainer.attach = classmethod(attach)


def install(env=None):
    env = os.environ if env is None else env
    if _target(env) is None:
        return
    import sys

    iris = sys.modules.get("iris")
    if iris is None:
        import iris  # noqa: F811
    _wrap_connect(iris, env)
    dbapi = sys.modules.get("iris.dbapi") or getattr(iris, "dbapi", None)
    if dbapi is None:
        try:
            import iris.dbapi as dbapi
        except Exception:
            dbapi = None
    if dbapi is not None:
        _wrap_connect(dbapi, env)
    if env.get("IVG_BENCH_CONTAINER"):
        _wrap_attach(env)


try:
    install()
except Exception as _exc:  # never break interpreter startup, but say why connections may be unredirected
    if os.environ.get("IVG_BENCH_NS"):
        import sys as _sys

        print(f"ivg bench shim not installed: {_exc}", file=_sys.stderr)
