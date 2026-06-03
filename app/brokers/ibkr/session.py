"""IB Gateway session manager.

``ib_async`` is asyncio-based and its event loop is bound to a single thread.
The allocation engine runs in a background thread, and Flask request threads
also call the broker. To make every public broker call thread-safe we run the
``IB`` client on a dedicated background thread with its own asyncio event loop,
started lazily here. Public broker methods marshal their coroutines onto that
loop with :func:`asyncio.run_coroutine_threadsafe`.
"""

import asyncio
import logging
import threading

from ib_async import IB

log = logging.getLogger(__name__)

# Default time (seconds) to wait for any marshalled coroutine to complete.
DEFAULT_CALL_TIMEOUT = 30.0
# Time (seconds) to wait for the initial connection to the gateway.
CONNECT_TIMEOUT = 20.0


class IBSession:
    """Owns the dedicated asyncio loop/thread and the ``IB`` connection.

    Thread-safety model:
      * A single background thread runs the asyncio event loop.
      * The ``IB`` client lives entirely on that loop.
      * ``run(coro)`` schedules a coroutine on the loop from any thread and
        blocks the caller until it completes (or times out).
      * ``ensure_auth()`` lazily starts the loop/thread and connects, and
        reconnects transparently if the connection drops.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connect_timeout = connect_timeout

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ib: IB | None = None
        # Guards loop/thread/connection startup so concurrent callers don't
        # race to create two loops or two connections.
        self._lock = threading.RLock()

    # -- loop / thread lifecycle -------------------------------------------

    def _start_loop(self):
        """Start the dedicated asyncio loop on a background thread (idempotent)."""
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            return

        ready = threading.Event()

        def _run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(
            target=_run_loop, name="ibkr-event-loop", daemon=True
        )
        self._thread.start()
        ready.wait()
        log.info("[ibkr] event loop thread started")

    def run(self, coro, timeout: float = DEFAULT_CALL_TIMEOUT):
        """Run a coroutine on the dedicated loop from any thread and block.

        Ensures the loop/connection are up first.
        """
        self.ensure_auth()
        return self._run_nowait(coro, timeout)

    def _run_nowait(self, coro, timeout: float = DEFAULT_CALL_TIMEOUT):
        """Run a coroutine on the loop without triggering ensure_auth.

        Used internally during connection setup to avoid recursion.
        """
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # -- connection --------------------------------------------------------

    def _on_disconnected(self):
        log.warning("[ibkr] disconnected from gateway")

    async def _connect(self):
        if self._ib is None:
            self._ib = IB()
            self._ib.disconnectedEvent += self._on_disconnected
        if self._ib.isConnected():
            return
        log.info(
            "[ibkr] connecting to gateway %s:%s clientId=%s",
            self.host, self.port, self.client_id,
        )
        await self._ib.connectAsync(
            self.host, self.port, clientId=self.client_id,
            timeout=self.connect_timeout,
        )
        log.info("[ibkr] connected to gateway")

    def ensure_auth(self):
        """Lazily start the loop/thread, connect, and reconnect if dropped."""
        with self._lock:
            self._start_loop()
            ib = self._ib
            if ib is not None and ib.isConnected():
                return
            # (Re)connect on the dedicated loop.
            self._run_nowait(self._connect(), timeout=self.connect_timeout + 5)

    @property
    def ib(self) -> IB:
        """The connected ``IB`` client (call :meth:`ensure_auth` first)."""
        if self._ib is None:
            raise RuntimeError("IB session not started — call ensure_auth() first")
        return self._ib

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def disconnect(self):
        """Disconnect and stop the loop (best-effort)."""
        with self._lock:
            if self._ib is not None and self._loop is not None:
                try:
                    self._run_nowait(self._async_disconnect(), timeout=10)
                except Exception:
                    log.exception("[ibkr] error during disconnect")
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)

    async def _async_disconnect(self):
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
