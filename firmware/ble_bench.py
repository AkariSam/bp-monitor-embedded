"""Pico 2W Phase 2A 入口：合成测量数据 + GATT 服务端。

烧录到 Pico 后，把本文件作为 ``main.py`` 运行，或执行：
    import ble_bench
    ble_bench.run()
"""

import asyncio
import time

import ble_tx
from ble_bench_core import SyntheticBench
from ble_server import BleServer


async def _bench_loop(server, core):
    last_tick = time.ticks_ms()
    last_pressure = last_tick

    while True:
        now = time.ticks_ms()
        dt = time.ticks_diff(now, last_tick)
        if dt < 0:
            dt = 0
        last_tick = now

        core.tick(dt)
        frame = core.take_frame()
        if frame is not None:
            await server.send_frame(frame)

        if core.pressure_enabled and time.ticks_diff(now, last_pressure) >= 1000:
            last_pressure = now
            pressure = core.pressure
            if pressure is not None:
                await server.send_pressure(pressure)

        await asyncio.sleep_ms(20)


async def _main():
    core = SyntheticBench()
    server = BleServer()

    def on_control(event):
        core.handle_event(event)

    async def on_connected():
        # 重连后只补发一次当前状态；不补发历史结果和错误。
        event = ble_tx.status_event(core.current_frame())
        if event is not None:
            await server.send(event[0], event[1])

    server.on_control = on_control
    server.on_connected = on_connected

    print("BP-Monitor-Bench starting")
    await asyncio.gather(server.run(), _bench_loop(server, core))


def run():
    asyncio.run(_main())


if __name__ == "__main__":
    run()
