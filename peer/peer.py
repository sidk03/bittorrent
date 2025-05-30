from __future__ import annotations
import asyncio
import struct
from datetime import datetime
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from client import TorrentClient


class Peer:
    def __init__(self, host: str, port: int, client: TorrentClient):
        self.host = host
        self.port = port
        self.client = client
        self.am_choking = True
        self.am_interested = False
        self.peer_choking = True
        self.peer_interested = False
        self.running = False
        self.bitfield = [False] * len(client.metadata.pieces)
        self.block_futures: dict[tuple[int, int], asyncio.Future] = {}
        self.block_timings: dict[tuple[int, int], float] = {}
        self.reader: asyncio.StreamReader = None
        self.writer: asyncio.StreamWriter = None
        self.peer_id: bytes = None
        self.request_tasks: dict[tuple[int, int, int], asyncio.Task] = {}
        self.successful_requests = 0
        self.successful_uploads = 0
        self.failed_requests = 0
        self.last_download_time = None
        self.connection_start = None
        self.speed = 0

    async def connect(self):
        try:
            async with asyncio.timeout(5):
                self.reader, self.writer = await asyncio.open_connection(
                    self.host, self.port
                )
                await self._handshake()
                await self._accept_handshake()
                print(f"Connected to {self.peer_id}")
                self.connection_start = datetime.now()

            await self._send_bitfield()
        except Exception as e:
            print(f"Handshake failed for {self.host}: {repr(e)}")
            if self.writer:
                self.writer.close()
                await self.writer.wait_closed()
            return

        self.running = True
        asyncio.create_task(self._loop())
        asyncio.create_task(self._keepalive())

    async def accept_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        self.reader = reader
        self.writer = writer
        try:
            async with asyncio.timeout(5):
                await self._accept_handshake()
                await self._handshake()
                print(f"Connected to {self.peer_id}")
                self.connection_start = datetime.now()

            await self._send_bitfield()
        except Exception as e:
            print(f"Handshake failed for {self.host}: {repr(e)}")
            self.writer.close()
            await self.writer.wait_closed()
            return

        self.running = True
        asyncio.create_task(self._loop())
        asyncio.create_task(self._keepalive())

    async def request(self, index: int, begin: int, length=16384):
        assert self.has(index)

        self.block_futures[(index, begin)] = asyncio.Future()
        self.block_timings[(index, begin)] = time.time()
        await self._send(6, struct.pack("!III", index, begin, length))
        async with asyncio.timeout(5):
            return await self.block_futures[(index, begin)]

    def has(self, index: int):
        if not self.running:
            return False
        if self.peer_choking:
            return False
        if not self.am_interested:
            return False
        return self.bitfield[index]

    async def notify_downloaded_piece(self, piece_index: int):
        if self.running:
            await self._send(4, struct.pack("!I", piece_index))
            await self._update_interest()

    async def _update_interest(self):
        am_interested = next(
            (True for a, b in zip(self.bitfield, self.client.bitfield) if a and not b),
            False,
        )
        if am_interested != self.am_interested:
            print(
                f"{'interested' if am_interested else 'not interested'} in {self.peer_id}"
            )
            self.am_interested = am_interested
            await self._send(2 if am_interested else 3)

    async def _handshake(self):
        msg = struct.pack(
            "!B19s8s20s20s",
            19,
            b"BitTorrent protocol",
            b"\x00" * 8,
            self.client.metadata.info_hash,
            self.client.tracker.peer_id,
        )
        self.writer.write(msg)
        await self.writer.drain()

    async def _accept_handshake(self):
        data = await self.reader.readexactly(68)
        length, proto, _, infohash, peer_id = struct.unpack("!B19s8s20s20s", data)
        assert length == 19
        assert proto == b"BitTorrent protocol"
        assert infohash == self.client.metadata.info_hash
        self.peer_id = peer_id

    async def _send_bitfield(self):
        data = []
        for i in range(0, -(-len(self.client.bitfield) // 8)):
            byte = 0
            for j in range(8):
                bit = (
                    self.client.bitfield[i * 8 + j]
                    if i * 8 + j < len(self.client.bitfield)
                    else False
                )
                byte = (byte << 1) | (1 if bit else 0)
            data.append(byte)
        await self._send(5, bytes(data))

    async def _loop(self):
        try:
            while self.running:
                async with asyncio.timeout(180):
                    data = await self.reader.readexactly(4)
                (length,) = struct.unpack("!I", data)
                if length > 0:
                    data = await self.reader.readexactly(1)
                    (msg_id,) = struct.unpack("!B", data)
                    data = await self.reader.readexactly(length - 1)
                    if msg_id == 0:
                        # choke
                        self.peer_choking = True
                    elif msg_id == 1:
                        # unchoke
                        self.peer_choking = False
                    elif msg_id == 2:
                        # interested
                        self.peer_interested = True
                        # TODO update allowed_downloaders
                        # await self._send(1)  # for testing
                        # self.am_choking = False
                    elif msg_id == 3:
                        # not interested
                        # TODO update allowed_downloaders
                        # await self._send(1)  # for testing
                        self.peer_interested = False
                    elif msg_id == 4:
                        # have
                        (index,) = struct.unpack("!I", data)
                        self.bitfield[index] = True
                        await self._update_interest()
                    elif msg_id == 5:
                        # bitfield
                        for i, byte in enumerate(data):
                            for j in range(8):
                                bit = (byte >> (7 - j)) & 1 == 1
                                if i * 8 + j >= len(self.bitfield):
                                    assert not bit
                                else:
                                    self.bitfield[i * 8 + j] = bit
                        await self._update_interest()
                    elif msg_id == 6:
                        # request
                        index, begin, length = struct.unpack("!III", data)
                        if (
                            not self.am_choking
                            and self.peer_interested
                            and self.client.bitfield[index]
                        ):
                            self.client.file.seek(
                                index * self.client.metadata.piece_length + begin
                            )
                            block = self.client.file.read(length)
                            task = asyncio.create_task(
                                self._send(7, struct.pack("!II", index, begin) + block)
                            )
                            self.successful_uploads += 1
                            self.client.tracker.uploaded += length
                            self.request_tasks[(index, begin, length)] = task
                    elif msg_id == 7:
                        # piece
                        index, begin = struct.unpack("!II", data[:8])
                        block = data[8:]
                        key = (index, begin)
                        future = self.block_futures.pop(key, None)
                        if future and not future.done():
                            future.set_result(block)
                        timing = self.block_timings.pop(key, None)
                        if key in self.block_timings:
                            time_spent = time.time() - timing
                            self.speed = (
                                self.speed * self.successful_requests + time_spent
                            ) / (self.successful_requests + 1)
                        self.successful_requests += 1
                    elif msg_id == 8:
                        # cancel
                        index, begin, length = struct.unpack("!III", data)
                        task = self.request_tasks.get((index, begin, length))
                        if task:
                            task.cancel()
        except Exception as e:
            print(f"Peer {self.peer_id} disconnected: {repr(e)}")
        finally:
            self.running = False
            self.writer.close()
            await self.writer.wait_closed()

    async def _keepalive(self):
        while self.running:
            msg = struct.pack("!I", 0)
            self.writer.write(msg)
            await self.writer.drain()
            await asyncio.sleep(120)

    async def _send(self, msg_id: int, payload=b""):
        msg = struct.pack("!IB", 1 + len(payload), msg_id) + payload
        self.writer.write(msg)
        await self.writer.drain()

    def __repr__(self):
        if not self.running:
            return "X"
        if self.peer_choking:
            return f"{self.peer_id}(choke)"
        return f"{self.peer_id}()"
