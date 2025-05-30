import asyncio
import argparse
import hashlib
import time
from peer.peer import Peer
from datetime import datetime
import random
from torrent.parser import parse_torrent_file
from tracker.tracker_client import TrackerClient


class TorrentClient:
    def __init__(
        self, torrent_file: str, port: int = 6886, local_only=(False, None, None)
    ):
        self.metadata = parse_torrent_file(torrent_file)
        self.port = port
        self.local_only, self.local_ip, self.local_port = local_only
        self.tracker = TrackerClient(
            announce_url=self.metadata.announce,
            info_hash=self.metadata.info_hash,
            peer_id=None,
            port=self.port,
            total_length=self.metadata.total_length,
        )
        self.connected_peers: list[Peer] = []
        self.allowed_downloaders: list[Peer] = []
        self.optimistic_unchoke: Peer = None
        self.last_optimistic_unchoke_time = datetime.now()
        self.file = (
            open("local_version_" + self.metadata.name, "w+b")
            if local_only
            else open(self.metadata.name, "w+b")
        )
        self.bitfield = [False] * len(self.metadata.pieces)
        self.last_print = 0
        self.start_time = time.time()
        self._endgame_tasks = {}
        self.rarest = []

    async def run(self):
        if self.local_only:
            print(
                f"Local only mode. Connected to peer at ip - {self.local_ip} and port - {self.local_port}"
            )
            self.connected_peers = [Peer(self.local_ip, self.local_port, self)]
        else:
            peers = self.tracker.started()
            print(f"\nSuccessfully contacted tracker.")
            print(f"Interval: {self.tracker.interval}")
            print(f"Min Interval: {self.tracker.min_interval}")
            print(f"Tracker ID: {self.tracker.tracker_id}")
            print(f"Seeders (complete): {self.tracker.complete}")
            print(f"Leechers (incomplete): {self.tracker.incomplete}")
            print(f"Discovered {len(peers)} peers:")

            for ip, port in peers:
                print(f"  {ip}:{port}")
            self.connected_peers = [Peer(ip, port, self) for ip, port in peers]

        for peer in self.connected_peers:
            asyncio.create_task(peer.connect())

        if not self.local_only:
            asyncio.create_task(self.update_tracker_loop())
            asyncio.create_task(self.server_loop())
        asyncio.create_task(self.periodic_choke_status())
        asyncio.create_task(self.periodic_optimistic_choke())

        await self.download()

        print("=====DONE!!!!=====")
        if not self.local_only:
            # only makes sense to run if we contacted the tracker originally
            self.tracker.completed()

        await self.seed()

    async def update_tracker_loop(self):
        while True:
            try:
                peers = self.tracker.update(
                    self.tracker.downloaded, self.tracker.uploaded
                )
                print(
                    f"Updated tracker: {self.tracker.downloaded}B down, {self.tracker.uploaded}B up"
                )
                current_peers = set(
                    (peer.host, peer.port) for peer in self.connected_peers
                )
                for host, port in peers:
                    if (host, port) not in current_peers:
                        print(f"Discovered new peer: {host}:{port}")
                        self.connected_peers.append(Peer(host, port, self))
                        asyncio.create_task(self.connected_peers[-1].connect())
            except Exception as e:
                print(f"Failed to update tracker: {e}")
            await asyncio.sleep(self.tracker.interval)

    async def server_loop(self):
        server = await asyncio.start_server(
            self.handle_connect,
            "0.0.0.0",
            self.port,
        )

        async with server:
            await server.serve_forever()

    async def handle_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        host, port = writer.get_extra_info("peername")
        self.connected_peers.append(Peer(host, port, self))
        asyncio.create_task(self.connected_peers[-1].accept_connection(reader, writer))

    async def periodic_choke_status(self):
        while True:
            self._set_choke_status()
            await self.update_choke_status()
            await asyncio.sleep(10)

    async def update_choke_status(self):
        for peer in self.connected_peers:
            if (
                peer in self.allowed_downloaders
                or (
                    self.allowed_downloaders
                    and peer.successful_requests
                    > self.allowed_downloaders[0].successful_requests
                )
                or peer == self.optimistic_unchoke
            ):
                if peer.am_choking == True and peer.running and peer.writer:
                    await peer._send(1)
                peer.am_choking = False
            else:
                if peer.am_choking == False and peer.running and peer.writer:
                    await peer._send(0)
                peer.am_choking = True

    def _set_choke_status(self):
        """Run every 10 seconds"""
        downloaders: list[Peer] = []

        # Get 4 peers that are interested and have best upload to us
        if all(self.bitfield):
            sort_func = lambda p: p.successful_uploads
        else:
            sort_func = lambda p: p.successful_requests
        sorted_peers_upload = sorted(self.connected_peers, key=sort_func, reverse=True)

        # For debug logging
        # s = ""
        # for p in sorted_peers_upload:
        #     s+= f"{p.host}:{p.port} Reqs {p.successful_requests} uploads {p.successful_uploads} : "

        # print(f"Peers by upload: {s}")
        downloaders = list(filter(lambda p: (p.peer_interested), sorted_peers_upload))[
            :4
        ]

        self.allowed_downloaders = downloaders
        print(f"Downloaders: {self.allowed_downloaders}")

    async def periodic_optimistic_choke(self):
        while True:
            self._set_optimistic_unchoke()
            await self.update_choke_status()
            await asyncio.sleep(30)

    def _has_connected_since_last_optimist_check(self, peer: Peer) -> bool:
        if peer.connection_start == None:
            return False
        else:
            return peer.connection_start >= self.last_optimistic_unchoke_time

    def _set_optimistic_unchoke(self):
        """Run every 30 seconds"""
        peer_weights_by_index: list[int] = [
            1 for i in range(0, len(self.connected_peers))
        ]

        indexes_new_clients_since_last_optimistic_unchoke = list(
            filter(
                lambda p: (self._has_connected_since_last_optimist_check(p[1])),
                enumerate(self.connected_peers),
            )
        )

        # 3 times weight to new connections
        for i, peer in indexes_new_clients_since_last_optimistic_unchoke:
            peer_weights_by_index[i] = 3

        if self.connected_peers:
            list_unchoke = random.choices(self.connected_peers, peer_weights_by_index)
            self.optimistic_unchoke = list_unchoke[0]
        else:
            self.optimistic_unchoke = None

        print(
            f"Optimistic unchoke of {self.optimistic_unchoke.host}:{self.optimistic_unchoke.port}"
        )
        self.last_optimistic_unchoke_time = datetime.now()

    def get_rarest_pieces(self):
        counts = [
            (i, len([p for p in self.connected_peers if p.has(i)]))
            for i, bit in enumerate(self.bitfield)
            if not bit
        ]

        counts.sort(key=lambda item: item[1])

        return [index for index, _ in counts]

    async def download(self):
        # while pieces := [i for i, bit in enumerate(self.bitfield) if not bit]:

        tasks_to_peers = {}
        tasks_to_piece = {}
        tasks = set()
        while not all(self.bitfield):
            piece, peers = await self.next_piece(
                tasks_to_piece.values(),
                tasks_to_peers.values(),
            )
            while peers == []:
                if len(tasks) == 0:
                    await asyncio.sleep(0)
                else:
                    done, _ = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        tasks.remove(task)
                        del tasks_to_peers[task]
                        del tasks_to_piece[task]
                    if all(self.bitfield):
                        self.file.flush()
                        return
                piece, peers = await self.next_piece(
                    tasks_to_piece.values(),
                    tasks_to_peers.values(),
                )
                if piece is None:
                    piece, peers = await self.next_piece(
                        tasks_to_piece.values(),
                        tasks_to_peers.values(),
                        True,
                    )
            peer = random.choice(peers)
            task = asyncio.create_task(self.download_piece(peer, piece))
            tasks.add(task)
            tasks_to_peers[task] = peer
            tasks_to_piece[task] = piece

        self.file.flush()

    async def next_piece(
        self, pending_pieces: list[int], pending_peers: list[Peer], recompute=False
    ):
        if recompute:
            self.rarest = self.get_rarest_pieces()
        avail_piece = next(
            (
                i
                for i in self.rarest
                if not self.bitfield[i] and i not in pending_pieces
            ),
            None,
        )
        if avail_piece is None:
            return (None, [])
        avail_peers = [
            p
            for p in self.connected_peers
            if p not in pending_peers and p.has(avail_piece)
        ]
        return (avail_piece, avail_peers)

    async def download_piece(self, peer: Peer, piece_index: int):
        # sort list of peers we are currently connected to
        # non running peers get sorted to the bottom
        # print(f"\nScored peer list for piece {next_piece}")
        if self.bitfield[piece_index]:
            return
        if self.in_endgame_mode():
            piece = await self.endgame_download(piece_index)
        else:
            piece = await self.download_piece_from_peer(peer, piece_index)

        if piece is not None:
            speed = self.tracker.downloaded / (time.time() - self.start_time)
            self.file.seek(piece_index * self.metadata.piece_length)
            self.file.write(piece)

            print(
                f"\nDownloaded piece {piece_index} from {peer.peer_id} [{speed*8/1024/1024:.1f}Mbps]"
            )
            self.bitfield[piece_index] = True
            for p in self.connected_peers:
                asyncio.create_task(p.notify_downloaded_piece(piece_index))
            self.tracker.downloaded += len(piece)

    async def download_piece_from_peer(self, peer: Peer, piece_index: int):
        if not peer.has(piece_index):
            return None
        try:
            semaphore = asyncio.Semaphore(10)
            piece = b""
            piece_length = min(
                self.metadata.piece_length,
                self.metadata.total_length - piece_index * self.metadata.piece_length,
            )
            tasks = []
            for start in range(0, piece_length, 16384):
                block_length = min(16384, piece_length - start)
                tasks.append(
                    self.download_block(
                        semaphore, peer, piece_index, start, block_length
                    )
                )
            try:
                piece = b"".join(await asyncio.gather(*tasks))
            except Exception as e:
                peer.failed_requests += 1
                print(
                    f"Failed to get piece {piece_index} from {peer.peer_id}: {repr(e)}"
                )
                return None  # might want to retry OR skip the piece - come back to this later - OR if all peers fail you can re-add the piece to the queue to retry, but that seems like too much

            piece_hash = hashlib.sha1(piece).digest()
            if piece_hash != self.metadata.pieces[piece_index]:
                print(f"Hash mismatch for piece {piece_index} from {peer.peer_id}")
                return None

            return piece
        except Exception as e:
            print(f"Failed to download piece {piece_index} from {peer.peer_id}: {e}")
            return None

    # def _register_endgame_task(self, piece_index, start, task, peer):
    #     if not hasattr(self, "_endgame_tasks"):
    #         self._endgame_tasks = {}
    #     self._endgame_tasks.setdefault((piece_index, start), []).append((task, peer))

    # def _cancel_duplicate_requests(self, piece_index, start, exclude=None):
    #     for task, peer in self._endgame_tasks.get((piece_index, start), []):
    #         if peer != exclude:
    #             task.cancel()

    async def endgame_download(self, piece_index: int):
        tasks = [
            asyncio.create_task(self.download_piece_from_peer(peer, piece_index))
            for peer in self.connected_peers
        ]
        piece = None
        while piece is None:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                tasks.remove(task)
                if task.result() is not None:
                    piece = task.result()
                    break

        print(f"Cancelling endgame requests for {piece_index}")
        for task in tasks:
            task.cancel()

        return piece

    async def download_block(
        self,
        semaphore: asyncio.Semaphore,
        peer: Peer,
        piece_index: int,
        start: int,
        block_length: int,
    ):
        async with semaphore:
            block = await peer.request(piece_index, start, block_length)
            peer.last_download_time = time.time()
            return block

    async def seed(self):
        while True:
            # self.print_state()
            await asyncio.sleep(0)

    def print_state(self):
        now = time.time()
        if now - self.last_print > 1:
            print(self.connected_peers)
            self.last_print = now

    async def close(self):
        self.file.close()
        if not self.local_only:
            self.tracker.stopped()

    def in_endgame_mode(self):
        remaining = sum(1 for bit in self.bitfield if not bit)
        return remaining <= 5


def parse_args():
    parser = argparse.ArgumentParser(description="BitTorrent Client")
    parser.add_argument( "--file","-f", type=str,default="./flatland.torrent",help="Path to the .torrent file (default: flatland)")
    parser.add_argument( "--port", "-p", type=int,default=6886,help="Port number to listen on (default: 6886)")
    parser.add_argument("--local-ip", type=str, help="IP address of local peer")
    parser.add_argument("--local-port", type=int, help="Port number of local peer")
    return parser.parse_args()


async def main():
    args = parse_args()
    local_mode = True if args.local_ip and args.local_port else False
    client = TorrentClient(
        args.file, args.port, local_only=(local_mode, args.local_ip, args.local_port)
    )
    try:
        await client.run()
    finally:
        await client.close()

    # path = sys.argv[1] if len(sys.argv) > 1 else "./flatland.torrent"
    # port = int(sys.argv[2]) if len(sys.argv) > 2 else 6886

    # if not (1024 <= port <= 65535):
    #     print(f"Port {port} is out of range")
    #     sys.exit(1)

    # local_only = "--local" in sys.argv
    # client = TorrentClient(path, port, local_only=local_only)
    # try:
    #     await client.run()
    # finally:
    #     await client.close()


if __name__ == "__main__":
    asyncio.run(main())
