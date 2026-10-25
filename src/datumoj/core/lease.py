"""Namespace write leases using only the atomic StateObjectStore contract.

Tokens are bearer credentials. Only acquire returns one to the CLI; subsequent
commands accept DATUMOJ_LEASE_TOKEN, never a token in argv or a persistent log.
"""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from datumoj.core.contracts import ContractError, PreconditionFailed, StateObjectStore
from datumoj.core.models import WriteLease
from datumoj.core.storage import s3_remote_configured

LEASE_KEY = "write-lease.json"


class LeaseError(ContractError):
    """Sanitized failure; never includes the lease or storage configuration."""


def remote_configured() -> bool:
    """Partial remote configuration must also fail closed, never run locally."""
    return s3_remote_configured()


class LeaseManager:
    def __init__(self, store: StateObjectStore, *,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 ttl: timedelta = timedelta(minutes=5)):
        if ttl <= timedelta(0):
            raise LeaseError("租约时长必须为正")
        self.store, self.now, self.ttl = store, now, ttl

    def _now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LeaseError("租约需要带时区的可信时间")
        return value

    def _read(self) -> tuple[WriteLease, str] | None:
        found = self.store.get(LEASE_KEY)
        if found is None:
            return None
        try:
            lease = WriteLease.model_validate_json(found[0])
            self._holder(lease.holder)
            return lease, found[1]
        except Exception:
            raise LeaseError("租约格式无效") from None

    @staticmethod
    def _holder(holder: str) -> None:
        kind, separator, run_id = holder.partition("/")
        if kind not in {"local", "actions"} or not separator or not run_id or any(
            ord(c) < 32 for c in holder
        ):
            raise LeaseError("租约持有者无效")

    def status(self) -> dict:
        found = self._read()
        if found is None:
            return {"held": False}
        lease = found[0]
        # No token or untrusted holder string in public status/log output.
        return {"held": True, "active": self._now() < lease.expires_at,
                "acquired_at": lease.acquired_at.isoformat(), "expires_at": lease.expires_at.isoformat()}

    def acquire(self, holder: str, *, force: bool = False) -> WriteLease:
        self._holder(holder)
        found = self._read()
        now = self._now()
        if found is not None and (not force or now < found[0].expires_at):
            raise LeaseError("写入租约已占用；仅可强制接管过期租约")
        lease = WriteLease(holder=holder, token=secrets.token_urlsafe(32), acquired_at=now,
                           expires_at=now + self.ttl)
        try:
            if found is None:
                self.store.put_if_absent(LEASE_KEY, lease.model_dump_json().encode())
            else:
                self.store.put_if_match(LEASE_KEY, lease.model_dump_json().encode(), found[1])
        except PreconditionFailed:
            raise LeaseError("租约竞争失败，本轮作废") from None
        return lease

    def _owned(self, holder: str, token: str, *, active: bool = True) -> tuple[WriteLease, str]:
        found = self._read()
        if (found is None or found[0].holder != holder or not token
                or not hmac.compare_digest(found[0].token.encode(), token.encode())):
            raise LeaseError("写入租约不属于本次持有者")
        if active and self._now() >= found[0].expires_at:
            raise LeaseError("写入租约已到期，本轮作废")
        return found

    def check(self, lease: WriteLease) -> None:
        self._owned(lease.holder, lease.token)

    def credentials(self, *, active: bool = True) -> WriteLease:
        """Resolve a process-local bearer token without exposing it in errors."""
        found = self._read()
        if found is None:
            raise LeaseError("缺少有效写入租约")
        holder = os.environ.get("DATUMOJ_LEASE_HOLDER", found[0].holder)
        return self._owned(holder, os.environ.get("DATUMOJ_LEASE_TOKEN", ""), active=active)[0]

    def renew(self, holder: str, token: str) -> WriteLease:
        lease, etag = self._owned(holder, token)
        now = self._now()
        if now >= lease.expires_at:
            raise LeaseError("写入租约已到期，本轮作废")
        expires = now + self.ttl
        if expires <= lease.expires_at:
            raise LeaseError("续期时间必须晚于旧到期时间")
        renewed = lease.model_copy(update={"expires_at": expires})
        try:
            self.store.put_if_match(LEASE_KEY, renewed.model_dump_json().encode(), etag)
        except PreconditionFailed:
            raise LeaseError("租约续期竞争失败，本轮作废") from None
        return renewed

    def release(self, holder: str, token: str) -> None:
        _, etag = self._owned(holder, token, active=False)
        try:
            self.store.delete_if_match(LEASE_KEY, etag)
        except PreconditionFailed:
            raise LeaseError("租约释放竞争失败") from None

    @contextmanager
    def maintain(self, lease: WriteLease) -> Iterator[Callable[[], None]]:
        """Renew synchronous work; callers check at each subsequent write boundary."""
        stopped = Event()
        lost = Event()

        def heartbeat() -> None:
            while not stopped.wait(self.ttl.total_seconds() / 3):
                try:
                    self.renew(lease.holder, lease.token)
                except Exception:
                    lost.set()
                    return

        def check() -> None:
            if lost.is_set():
                raise LeaseError("写入租约续期失败，本轮作废")
            self.check(lease)

        check()
        thread = Thread(target=heartbeat, name="datumoj-lease", daemon=True)
        thread.start()
        try:
            yield check
        finally:
            stopped.set()
            thread.join()
