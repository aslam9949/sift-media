"""Rate-limit-safe Telegram send queue (constraint 5).

Never a raw send loop. Enforces three independent limits:
  * global      ~25 msg/sec  (Telegram caps ~30/s)
  * per chat    ~1 msg/sec
  * per chat    19 msg/min   (groups cap at 20/min)
Plus retry with backoff, and honours Telegram's RetryAfter (flood control).
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

import config

log = logging.getLogger("send_queue")

_A_TAG = re.compile(r'<a\s+href="([^"]*)"\s*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_ANY_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Fallback rendering: unwrap anchors to 'text (url)', drop other markup."""
    text = _A_TAG.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    text = _ANY_TAG.sub("", text)
    return html_lib.unescape(text)


@dataclass(order=True)
class Job:
    priority: int
    seq: int
    chat_id: str = field(compare=False)
    text: str = field(compare=False)
    disable_preview: bool = field(compare=False, default=True)
    attempts: int = field(compare=False, default=0)
    plain_retry: bool = field(compare=False, default=False)


class SendQueue:
    """One queue per bot token. Priority 0 = urgent (alerts), 5 = normal news."""

    def __init__(self, token: str, name: str = "bot"):
        self.name = name
        self.token = token
        self._bot: Bot | None = None
        self._q: asyncio.PriorityQueue[Job] = asyncio.PriorityQueue()
        self._seq = 0
        self._worker: asyncio.Task | None = None
        self._last_global = 0.0
        self._last_chat: dict[str, float] = defaultdict(float)
        self._chat_minute: dict[str, deque[float]] = defaultdict(deque)
        self.sent = 0
        self.failed = 0

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(
                token=self.token,
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML,
                    link_preview_is_disabled=True,
                ),
            )
        return self._bot

    async def enqueue(self, chat_id: str, text: str, priority: int = 5) -> None:
        if not chat_id:
            log.warning("[%s] drop message — no chat_id configured", self.name)
            return
        self._seq += 1
        await self._q.put(Job(priority=priority, seq=self._seq, chat_id=str(chat_id), text=text))

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name=f"sendq-{self.name}")

    async def join(self) -> None:
        await self._q.join()

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
        if self._bot:
            await self._bot.session.close()

    # ---------------------------------------------------------- throttling
    async def _throttle(self, chat_id: str) -> None:
        # global
        gap = 1.0 / max(config.RATE_GLOBAL_PER_SEC, 1.0)
        wait = self._last_global + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

        # per-chat, per-second
        wait = self._last_chat[chat_id] + config.RATE_PER_CHAT_SEC - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

        # per-chat, per-minute sliding window
        win = self._chat_minute[chat_id]
        now = time.monotonic()
        while win and now - win[0] > 60:
            win.popleft()
        if len(win) >= config.RATE_PER_CHAT_PER_MIN:
            sleep_for = 60 - (now - win[0]) + 0.5
            log.info("[%s] per-minute cap on %s — pausing %.1fs", self.name, chat_id, sleep_for)
            await asyncio.sleep(max(sleep_for, 0))
            while win and time.monotonic() - win[0] > 60:
                win.popleft()

    def _stamp(self, chat_id: str) -> None:
        now = time.monotonic()
        self._last_global = now
        self._last_chat[chat_id] = now
        self._chat_minute[chat_id].append(now)

    # ------------------------------------------------------------- worker
    async def _run(self) -> None:
        while True:
            job = await self._q.get()
            try:
                await self._deliver(job)
            except Exception as exc:  # never let the worker die
                log.exception("[%s] worker error: %s", self.name, exc)
            finally:
                self._q.task_done()

    async def _deliver(self, job: Job) -> None:
        if config.DRY_RUN:
            log.info("[DRY_RUN %s] -> %s: %s", self.name, job.chat_id, job.text[:90].replace("\n", " | "))
            self.sent += 1
            return

        await self._throttle(job.chat_id)
        try:
            await self.bot.send_message(
                chat_id=job.chat_id,
                text=job.text,
                disable_notification=job.priority > 3,
            )
            self._stamp(job.chat_id)
            self.sent += 1
        except TelegramRetryAfter as exc:
            delay = getattr(exc, "retry_after", 5) + 1
            log.warning("[%s] flood control: retry in %ss", self.name, delay)
            await asyncio.sleep(delay)
            await self._requeue(job)
        except TelegramForbiddenError as exc:
            self.failed += 1
            log.error("[%s] forbidden for %s (bot not admin?): %s", self.name, job.chat_id, exc)
        except TelegramBadRequest as exc:
            # Almost always a malformed-HTML entity. Don't lose the post: strip
            # the markup and resend once as plain text.
            msg = str(exc).lower()
            if ("entity" in msg or "parse" in msg or "tag" in msg) and not job.plain_retry:
                log.warning("[%s] HTML rejected (%s) — resending as plain text", self.name, str(exc)[:120])
                job.plain_retry = True
                job.text = _strip_html(job.text)
                await self._requeue(job)
            else:
                self.failed += 1
                log.error("[%s] bad request for %s: %s", self.name, job.chat_id, str(exc)[:200])
        except Exception as exc:
            job.attempts += 1
            if job.attempts <= 3:
                backoff = 2 ** job.attempts
                log.warning("[%s] send failed (try %d), backoff %ss: %s", self.name, job.attempts, backoff, exc)
                await asyncio.sleep(backoff)
                await self._requeue(job)
            else:
                self.failed += 1
                log.error("[%s] giving up after %d attempts: %s", self.name, job.attempts, exc)

    async def _requeue(self, job: Job) -> None:
        self._seq += 1
        job.seq = self._seq
        await self._q.put(job)


_queues: dict[str, SendQueue] = {}


def get_queue(token: str, name: str) -> SendQueue:
    if name not in _queues:
        _queues[name] = SendQueue(token, name)
    return _queues[name]
