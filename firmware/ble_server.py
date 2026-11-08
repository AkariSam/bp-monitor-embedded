"""Pico 2W GATT 服务端（aioble）。

只负责 BLE 传输：注册服务、广播、接收控制写入、发送压力/状态/错误/结果。
测量状态和合成数据在 ``ble_bench_core``，字节编码在 ``ble_protocol``。
"""

import asyncio
import aioble
import bluetooth

import ble_protocol
import ble_tx


def _uuid(value):
    if isinstance(value, int):
        return bluetooth.UUID(value)
    text = str(value)
    if len(text) == 4:
        try:
            return bluetooth.UUID(int(text, 16))
        except ValueError:
            pass
    return bluetooth.UUID(text)


class BleServer(object):
    def __init__(self):
        self.connection = None
        self.on_control = None
        self.on_connected = None

        self._bp_uuid = _uuid(ble_protocol.SERVICE_BLOOD_PRESSURE)
        self._bp_service = aioble.Service(self._bp_uuid)
        self._result = aioble.Characteristic(
            self._bp_service,
            _uuid(ble_protocol.CHAR_BP_MEASUREMENT),
            indicate=True,
        )

        self._custom_service = aioble.Service(_uuid(ble_protocol.SERVICE_CUSTOM))
        self._pressure = aioble.Characteristic(
            self._custom_service,
            _uuid(ble_protocol.CHAR_PRESSURE),
            notify=True,
        )
        self._status = aioble.Characteristic(
            self._custom_service,
            _uuid(ble_protocol.CHAR_STATUS),
            notify=True,
        )
        self._error = aioble.Characteristic(
            self._custom_service,
            _uuid(ble_protocol.CHAR_ERROR),
            notify=True,
        )
        self._control = aioble.Characteristic(
            self._custom_service,
            _uuid(ble_protocol.CHAR_CONTROL),
            write=True,
            capture=True,
        )

        aioble.register_services(self._bp_service, self._custom_service)

    # ---- 发送 ----

    async def send(self, kind, payload):
        connection = self.connection
        if connection is None or payload is None:
            return False
        try:
            if kind == "result":
                # aioble 的 indicate 是异步的，必须等待客户端确认。
                await self._result.indicate(connection, payload, timeout_ms=500)
            elif kind == "pressure":
                self._pressure.notify(connection, payload)
            elif kind == "status":
                self._status.notify(connection, payload)
            elif kind == "error":
                self._error.notify(connection, payload)
            else:
                return False
        except Exception as exc:
            print("BLE send failed:", kind, exc)
            return False
        return True

    async def send_frame(self, frame):
        sent = 0
        for kind, payload in ble_tx.tx_events(frame):
            if await self.send(kind, payload):
                sent += 1
        return sent

    async def send_pressure(self, p_mmHg):
        event = ble_tx.pressure_event(p_mmHg)
        if event is None:
            return False
        return await self.send(event[0], event[1])

    # ---- 连接与控制 ----

    async def _control_loop(self, connection):
        while True:
            try:
                _, data = await self._control.written()
            except Exception:
                return
            event = ble_protocol.decode_control(data)
            if event is not None and self.on_control is not None:
                try:
                    self.on_control(event)
                except Exception as exc:
                    print("control handler failed:", exc)

    async def _handle_connection(self, connection):
        self.connection = connection
        print("BLE connected:", connection.device)
        if self.on_connected is not None:
            try:
                await self.on_connected()
            except Exception as exc:
                print("connect handler failed:", exc)

        control_task = asyncio.create_task(self._control_loop(connection))
        try:
            await connection.disconnected(timeout_ms=None)
        finally:
            control_task.cancel()
            self.connection = None
            print("BLE disconnected")

    async def _advertise_loop(self):
        while True:
            try:
                async with await aioble.advertise(
                    250000,
                    name="BP-Monitor-Bench",
                    services=[self._bp_uuid],
                    appearance=0x0540,
                ) as connection:
                    await self._handle_connection(connection)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print("BLE advertise failed:", exc)
                await asyncio.sleep_ms(500)

    async def run(self):
        await self._advertise_loop()
