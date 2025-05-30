# bittorrent

BitTorrent client in Python

## Basics How to use

- Create python virtual environment: `python3 -m venv .venv`
- Activate virtual environment: `source ./.venv/bin/activate`
- Install requirements: `pip3 install -r requirements.txt`
- Run client `python3 client.py --file {file}` 
  - Replace `{file}` with the file name of a **single file** torrent
- You will see repeated download messages like `Downloaded piece 3 from b'-WW0206-90E/8RKeyVT/' [7.7Mbps]`
- You should eventually see `=====DONE!!!!=====`
- After this you client is seeding the file to others

## Program Arguments

- `-h`, `--help`: show this help message and exit
- `--file FILE`, `-f FILE`: Path to the .torrent file (default: flatland)
- `--port PORT`, `-p PORT`: Port number to listen on (default: 6886)
- `--local-ip LOCAL_IP`: IP address of local peer
- `--local-port LOCAL_PORT`: Port number of local peer

Note: If you pass `--local-ip` and `--local-port` this will force the client to only download data from that client. This is not normal operations and should only be used for testing.

1. UDP
2. HTTPS
3. Rarest First
4. Endgame Mode