import random
import ssl
from urllib.parse import urlparse
import socket
import struct
import bencodepy


class TrackerClient:
    def __init__(
        self,
        announce_url: str,
        info_hash: bytes,
        peer_id: bytes,
        port: int,
        total_length: int,
    ):
        self.announce_url = announce_url
        self.info_hash = info_hash
        self.peer_id = (
            peer_id
            if peer_id
            else f"-PC0001-{random.randint(0, int(1e12)):012}".encode()
        )
        self.port = port
        self.total_length = total_length
        self.uploaded = 0
        self.downloaded = 0
        self.left = total_length

        # fields from the Tracker
        self.interval = None
        self.min_interval = None
        self.tracker_id = None
        self.complete = None
        self.incomplete = None

    def _percent_encode(self, b: bytes) -> str:
        return "".join(f"%{byte:02X}" for byte in b)

    def _build_request(self, event: str = None) -> bytes:
        parsed = urlparse(self.announce_url)
        proto = parsed.scheme
        host = parsed.hostname
        port = parsed.port or (80 if proto == "http" else 443)
        path = parsed.path or "/"

        query = (
            f"?info_hash={self._percent_encode(self.info_hash)}"
            f"&peer_id={self._percent_encode(self.peer_id)}"
            f"&port={self.port}"
            f"&uploaded={self.uploaded}"
            f"&downloaded={self.downloaded}"
            f"&left={self.left}"
            f"&compact=1"
        )
        if event:
            query += f"&event={event}"

        request = (
            f"GET {path}{query} HTTP/1.0\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: CMSC417Client\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        return proto, host, port, request

    def _send_https_request(self, request_bytes: bytes, host: str, port: int) -> bytes:
        context = ssl.create_default_context()

        with socket.create_connection((host, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as secure_sock:
                secure_sock.sendall(request_bytes)
                response = b""
                while True:
                    data = secure_sock.recv(4096)
                    if not data:
                        break
                    response += data
        header_end = response.find(b"\r\n\r\n")
        if header_end == -1:
            raise Exception("Invalid HTTPS response")
        return response[header_end + 4 :]

    def _send_http_request(self, request_bytes: bytes, host: str, port: int) -> bytes:
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(request_bytes)
            response = b""
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                response += data
        header_end = response.find(b"\r\n\r\n")
        if header_end == -1:
            raise Exception("Invalid HTTP response")
        return response[header_end + 4 :]
    
    def _send_recv_udp_msg(self, host : str, port : int, event : str):
        ACTION_CONNECT, ACTION_ANNOUNCE, MAGIC_CONST = 0,1,0x41727101980
        EVENT_MAP = {None: 0, "completed": 1, "started": 2, "stopped": 3}

        def send_and_receive(sock, msg, timeout):
            sock.settimeout(timeout)
            try:
                sock.sendto(msg, (host, port))
                return sock.recvfrom(4096)[0]
            except socket.timeout:
                return None
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Try CONNECT with retries
        for retry in range(8):
            timeout = 15 * (2**retry)
            t_id = random.randint(0, 2**32-1)
            connect_msg = struct.pack("!QLL", MAGIC_CONST, ACTION_CONNECT, t_id)
            resp = send_and_receive(sock, connect_msg, timeout)
            if resp and len(resp) >= 16:
                action, resp_tid, connection_id = struct.unpack("!LLQ", resp[:16])
                if action == ACTION_CONNECT and resp_tid == t_id:
                    break  # success
            if retry == 7:
                raise Exception("Failed to get connection ID from UDP tracker")
        else:
            raise Exception("No valid response from UDP tracker")
        
        # Try ANNOUNCE with retries
        for retry in range(8):
            timeout = 15 * (2 ** retry)
            transaction_id = random.randint(0, 2**32 - 1)
            key = random.randint(0, 2**32 - 1)
            num_want = 0xFFFFFFFF
            event_id = EVENT_MAP[event]

            announce_msg = struct.pack(
                "!QLL20s20sQQQLLLLH",
                connection_id,
                ACTION_ANNOUNCE,
                transaction_id,
                self.info_hash,
                self.peer_id,
                self.downloaded,
                self.left,
                self.uploaded,
                event_id,
                0,      # IP address (0 = default)
                key,
                num_want,
                self.port
            )

            resp = send_and_receive(sock, announce_msg, timeout)
            if resp and len(resp) >= 20:
                action, resp_tid, interval, leechers, seeders = struct.unpack("!LLLLL", resp[:20])
                if action == ACTION_ANNOUNCE and resp_tid == transaction_id:
                    self.interval = interval
                    self.complete = seeders
                    self.incomplete = leechers

                    peers = []
                    for i in range(20, len(resp), 6):
                        ip = socket.inet_ntoa(resp[i:i+4])
                        peer_port = struct.unpack(">H", resp[i+4:i+6])[0]
                        peers.append((ip, peer_port))
                    return peers
            if retry == 7:
                raise Exception("Failed to announce to UDP tracker")
                 

    def announce(self, event: str = None) -> list[tuple[str, int]]:
        proto, host, port, request = self._build_request(event)
        if proto == "http":
            body = self._send_http_request(request, host, port)
        elif proto == "https":
            body = self._send_https_request(request, host, port)
        elif proto == "udp":
            return self._send_recv_udp_msg(host, port, event)
        else:
            raise ValueError(f"Unsupported protocol: {proto}")
        decoded = bencodepy.decode(body)

        # Error handling
        if b"failure reason" in decoded:
            reason = decoded[b"failure reason"].decode()
            raise Exception(f"Tracker failure: {reason}")

        # Optional warning
        if b"warning message" in decoded:
            warning = decoded[b"warning message"].decode()
            print(f"[Tracker warning] {warning}")

        # Store tracker response data
        self.interval = decoded.get(b"interval", None)
        self.min_interval = decoded.get(b"min interval", None)
        self.tracker_id = decoded.get(b"tracker id", self.tracker_id)
        self.complete = decoded.get(b"complete", None)
        self.incomplete = decoded.get(b"incomplete", None)

        peers_raw = decoded[b"peers"]
        peers = []
        if isinstance(peers_raw, bytes):
            for i in range(0, len(peers_raw), 6):
                ip = socket.inet_ntoa(peers_raw[i : i + 4])
                peer_port = struct.unpack(">H", peers_raw[i + 4 : i + 6])[0]
                peers.append((ip, peer_port))
        else:
            for peer in peers_raw:
                ip = peer[b"ip"].decode()
                peer_port = peer[b"port"]
                peers.append((ip, peer_port))

        return peers

    def started(self) -> list[tuple[str, int]]:
        return self.announce(event="started")

    def completed(self) -> list[tuple[str, int]]:
        self.left = 0
        self.downloaded = self.total_length
        return self.announce(event="completed")

    def stopped(self) -> list[tuple[str, int]]:
        return self.announce(event="stopped")

    def update(self, downloaded: int, uploaded: int) -> list[tuple[str, int]]:
        self.downloaded = downloaded
        self.uploaded = uploaded
        self.left = max(0, self.total_length - downloaded)
        return self.announce()
