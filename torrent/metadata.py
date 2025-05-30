from dataclasses import dataclass
from typing import List

@dataclass
class TorrentMetadata:
    announce : str
    piece_length : int
    pieces : List[bytes]
    info_hash : bytes
    total_length : int
    name : str