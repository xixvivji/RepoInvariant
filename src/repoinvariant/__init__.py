"""RepoInvariant public package API with lazy model exports for safe CLI bootstrap."""

__all__ = ["Finding", "Location", "ScanResult", "Severity"]
__version__ = "0.5.1"


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from repoinvariant import models

    value = getattr(models, name)
    globals()[name] = value
    return value
