from torrent.metadata import TorrentMetadata
import hashlib
import bencodepy

def parse_torrent_file(path : str) -> TorrentMetadata:
    with open(path, "rb") as file:
        torrent_data =  bencodepy.decode(file.read())

    announce = torrent_data[b'announce'].decode('utf-8')
    info = torrent_data[b'info']
    info_bencoded = bencodepy.encode(info)
    info_hash = hashlib.sha1(info_bencoded).digest()
    piece_length = info[b'piece length']
    pieces_raw = info[b'pieces']
    name = info[b'name'].decode('utf-8')
    pieces = [pieces_raw[i:i+20] for i in range(0, len(pieces_raw), 20)]
    total_length = info[b'length']  # Assuming single-file mode for now

    return TorrentMetadata(
        announce=announce,
        piece_length=piece_length,
        pieces=pieces,
        info_hash=info_hash,
        total_length=total_length,
        name=name
    )
    
    
    
    










