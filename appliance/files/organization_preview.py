"""One cross-process preview slot per appliance state; separate from file mutation leases."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from appliance.agent_api.documents import DocumentWorkerCleanupError
from appliance.files.organization_store import OrganizationStore
from appliance.state_lock import StateLockError


class OrganizationPreviewAdmission:
    def __init__(self, state_dir: Path):
        # Private state helper supplies verified directories and actual OS leases.
        # This directory contains only its lock; it is not another task database.
        self._slot = OrganizationStore(Path(state_dir) / "document-extraction" / "preview-slot")
        self._mutex = threading.Lock()
        self.cancel = threading.Event()
        self._quarantined: list = []

    @contextmanager
    def acquire(self):
        if self.cancel.is_set() or not self._mutex.acquire(blocking=False):
            raise StateLockError("document preview unavailable or already running")
        lease = self._slot.lease()
        entered, quarantined = False, False
        try:
            lease.__enter__()
            entered = True
            if self.cancel.is_set():
                raise StateLockError("document preview is shutting down")
            yield self.cancel
        except DocumentWorkerCleanupError:
            # Do not release capacity based on an unproven process-tree cleanup.
            self._quarantined.append(lease)
            quarantined = True
            raise
        finally:
            if entered and not quarantined:
                lease.__exit__(None, None, None)
            if not quarantined:
                self._mutex.release()

    def shutdown(self):
        self.cancel.set()


__all__ = ["OrganizationPreviewAdmission"]
