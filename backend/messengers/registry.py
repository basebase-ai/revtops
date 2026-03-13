"""
Messenger registry: auto-discovery of messenger classes.

Mirrors ``connectors/registry.py``.  Scans ``backend/messengers/`` for
classes that subclass :class:`BaseMessenger` and expose a ``meta`` attribute
of type :class:`MessengerMeta`.

Usage::

    from messengers.registry import discover_messengers

    registry = discover_messengers()          # {"slack": SlackMessenger, ...}
    messenger_cls = registry["slack"]
    messenger = messenger_cls()
    result = await messenger.process_inbound(message)
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from messengers.base import BaseMessenger, MessengerMeta

logger = logging.getLogger(__name__)

_SKIP_MODULES: frozenset[str] = frozenset({
    "base",
    "registry",
})


def discover_messengers() -> dict[str, type[BaseMessenger]]:
    """Build messenger registry from in-tree modules.

    Scans every Python module under ``backend/messengers/``, skipping
    private modules (``_*``) and infrastructure modules listed in
    ``_SKIP_MODULES``.  For each module it looks for classes that:

    * are a subclass of :class:`BaseMessenger` (but not BaseMessenger itself)
    * have a ``meta`` attribute of type :class:`MessengerMeta`

    The registry is keyed by ``meta.slug``.
    """
    from messengers.base import BaseMessenger, MessengerMeta

    registry: dict[str, type[BaseMessenger]] = {}

    messengers_dir: Path = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(messengers_dir)]):
        if module_info.name.startswith("_") or module_info.name in _SKIP_MODULES:
            continue
        try:
            module = importlib.import_module(f"messengers.{module_info.name}")
        except Exception:
            logger.warning(
                "Failed to import messenger module %s",
                module_info.name,
                exc_info=True,
            )
            continue

        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseMessenger)
                and obj is not BaseMessenger
                and hasattr(obj, "meta")
                and isinstance(getattr(obj, "meta", None), MessengerMeta)
            ):
                meta: MessengerMeta = obj.meta
                registry[meta.slug] = obj

    return registry
