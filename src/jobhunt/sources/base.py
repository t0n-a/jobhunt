from collections.abc import Iterable
from typing import Protocol

from jobhunt.jobs.models import RawPosting


class SourceError(RuntimeError):
    """An adapter could not fetch. The scan logs it and continues."""


class Source(Protocol):
    name: str

    def fetch(self) -> Iterable[RawPosting]: ...


registry: dict[str, type] = {}


def register(cls: type) -> type:
    """Class decorator adding an adapter to the registry under its `name`."""
    registry[cls.name] = cls
    return cls
