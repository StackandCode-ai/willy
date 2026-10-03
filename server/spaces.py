"""
Per-account spaces: every account's devices, reminders, watches, activity, files and
conversations live in a Space of their own, so two people on one hub never see each other's.

The hub code keeps using names like `device_manager` and `morning_service`; those are
SpaceAttr proxies that resolve to the current request's space (a ContextVar set when the
request or socket is authenticated). Tasks started while handling a request inherit it.
Code running outside any request (startup, background loops for the home space, tests)
gets the home space: the hub owner's, which holds what existed before accounts.
"""

import contextvars
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from server.accounts import HOME_SPACE

_current: contextvars.ContextVar[Optional["Space"]] = contextvars.ContextVar("willy_space", default=None)


class Space:
    def __init__(self, space_id: str, data_dir: Path):
        self.space_id = space_id
        self.data_dir = Path(data_dir)
        self.objects: Dict[str, Any] = {}
        self.started = False

    @property
    def is_home(self) -> bool:
        return self.space_id == HOME_SPACE

    def __repr__(self) -> str:
        return f"<Space {self.space_id}>"


class SpaceRegistry:
    def __init__(self, base_dir: Path, build: Optional[Callable[[Space], None]] = None,
                 start: Optional[Callable[[Space], None]] = None):
        self.base_dir = Path(base_dir)
        self.build = build
        self.start = start
        self.spaces: Dict[str, Space] = {}

    def data_dir_for(self, space_id: str) -> Path:
        # The home space keeps the original data folder, so nothing moves on upgrade.
        if space_id == HOME_SPACE:
            return self.base_dir
        safe = "".join(ch for ch in space_id if ch.isalnum() or ch in "-_")[:40]
        if not safe:
            raise ValueError("bad space id")
        return self.base_dir / "spaces" / safe

    def get(self, space_id: str) -> Space:
        space = self.spaces.get(space_id)
        if space is None:
            space = Space(space_id, self.data_dir_for(space_id))
            space.data_dir.mkdir(parents=True, exist_ok=True)
            if self.build:
                self.build(space)
            self.spaces[space_id] = space
        return space

    def home(self) -> Space:
        return self.get(HOME_SPACE)

    def ensure_started(self, space: Space) -> None:
        """Starts the space's background loops (needs a running event loop)."""
        if space.started or self.start is None:
            return
        token = _current.set(space)
        try:
            self.start(space)  # tasks created here inherit this space
            space.started = True
        finally:
            _current.reset(token)


registry: Optional[SpaceRegistry] = None


def configure(base_dir: Path, build: Callable[[Space], None], start: Callable[[Space], None]) -> SpaceRegistry:
    global registry
    registry = SpaceRegistry(base_dir, build, start)
    return registry


def current() -> Space:
    space = _current.get()
    if space is None:
        if registry is None:
            raise RuntimeError("spaces not configured")
        return registry.home()
    return space


def use(space: Space) -> contextvars.Token:
    """Makes `space` current for this task (and tasks it starts)."""
    return _current.set(space)


def reset(token: contextvars.Token) -> None:
    _current.reset(token)


class SpaceAttr:
    """Stands in for a per-space object: attribute access goes to the current space's instance."""

    __slots__ = ("_key",)

    def __init__(self, key: str):
        object.__setattr__(self, "_key", key)

    def _target(self) -> Any:
        return current().objects[object.__getattribute__(self, "_key")]

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target(), item)

    def __setattr__(self, item: str, value: Any) -> None:
        setattr(self._target(), item, value)

    def __delattr__(self, item: str) -> None:  # lets mock.patch.object undo a patch on the proxy
        delattr(self._target(), item)

    def __getitem__(self, key):
        return self._target()[key]

    def __setitem__(self, key, value):
        self._target()[key] = value

    def __delitem__(self, key):
        del self._target()[key]

    def __contains__(self, key) -> bool:
        return key in self._target()

    def __iter__(self):
        return iter(self._target())

    def __len__(self) -> int:
        return len(self._target())

    def __bool__(self) -> bool:
        return bool(self._target())

    def __repr__(self) -> str:
        return f"<SpaceAttr {object.__getattribute__(self, '_key')} of {current()!r}>"
