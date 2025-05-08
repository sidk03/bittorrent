import random
from urllib.parse import urlparse
import socket
import struct
import bencodepy


class TrackerClient:
    def __init__(self, announce_url : str, info_hash : bytes, peer_id : bytes, port : int, total_length : int):
        self.announce_url = announce_url
        self.info_hash = info_hash
        self.peer_id = peer_id if peer_id else f"-PC0001-{random.randint(0, 1e12):012}".encode()
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
        return ''.join(f'%{byte:02X}' for byte in b)
    
    def _build_request(self, event : str = None) -> bytes:
        parsed = urlparse(self.announce_url)
        host = parsed.hostname
        port = parsed.port or 80
        path = parsed.path or '/'

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
            f"GET {path}{query} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: CMSC417Client\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        return host, port, request
    
    def _send_request(self, request_bytes: bytes, host: str, port: int) -> bytes:
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
        return response[header_end+4:]
    
    def announce(self, event: str = None) -> list[tuple[str, int]]:
        host, port, request = self._build_request(event)
        body = self._send_request(request, host, port)
        decoded = bencodepy.decode(body)

        # Error handling
        if b'failure reason' in decoded:
            reason = decoded[b'failure reason'].decode()
            raise Exception(f"Tracker failure: {reason}")

        # Optional warning
        if b'warning message' in decoded:
            warning = decoded[b'warning message'].decode()
            print(f"[Tracker warning] {warning}")

        # Store tracker response data
        self.interval = decoded.get(b'interval', None)
        self.min_interval = decoded.get(b'min interval', None)
        self.tracker_id = decoded.get(b'tracker id', self.tracker_id)
        self.complete = decoded.get(b'complete', None)
        self.incomplete = decoded.get(b'incomplete', None)

        peers_raw = decoded[b'peers']
        peers = []
        for i in range(0, len(peers_raw), 6):
            ip = socket.inet_ntoa(peers_raw[i:i+4])
            peer_port = struct.unpack('>H', peers_raw[i+4:i+6])[0]
            peers.append((ip, peer_port))

        return peers
    
    def started(self) -> list[tuple[str, int]]:
        return self.announce(event='started')

    def completed(self) -> list[tuple[str, int]]:
        self.left = 0
        self.downloaded = self.total_length
        return self.announce(event='completed')

    def stopped(self) -> list[tuple[str, int]]:
        return self.announce(event='stopped')

    def update(self, downloaded: int, uploaded: int) -> list[tuple[str, int]]:
        self.downloaded = downloaded
        self.uploaded = uploaded
        self.left = max(0, self.total_length - downloaded)
        return self.announce()
    
