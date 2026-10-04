"""Drain owned blocking I/O before releasing its clients or private workspace."""
import asyncio


async def drain_on_cancel(future):
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Cancelling a to_thread wrapper cannot cancel its SDK call. The process
        # guard still bounds abrupt shutdown; cooperative cleanup waits for I/O.
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not future.cancelled():
            future.exception()
        raise


async def blocking(operation, *args, **kwargs):
    return await drain_on_cancel(asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs)))
