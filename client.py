from torrent.parser import parse_torrent_file
from tracker.tracker_client import TrackerClient

def main():
    metadata = parse_torrent_file('./flatland.torrent')
    tracker = TrackerClient(
        announce_url=metadata.announce,
        info_hash=metadata.info_hash,
        peer_id=None,
        port=6886, 
        total_length=metadata.total_length
    )
    try:
        peers = tracker.started()
        print(f"\nSuccessfully contacted tracker.")
        print(f"Interval: {tracker.interval}")
        print(f"Min Interval: {tracker.min_interval}")
        print(f"Tracker ID: {tracker.tracker_id}")
        print(f"Seeders (complete): {tracker.complete}")
        print(f"Leechers (incomplete): {tracker.incomplete}")
        print(f"Discovered {len(peers)} peers:")
        for ip, port in peers:
            print(f"  {ip}:{port}")
    except Exception as e:
        print(f"Tracker request failed: {e}")

if __name__ == "__main__":
    main()