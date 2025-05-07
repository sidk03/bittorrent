# bittorrent
BitTorrent client in Python

Current Plan and File Structure -> Doing now 
1. Take in a single torrent file thorugh the command line
2. Parse Torrent file -> set up HTTP(s) connection with the Tracker and get peers with file
3. Support for multi file torrent

Project Structure ->
1. client.py -> in root, enterance to the project, takes in torrent file 
2. torrent -> dir, parses torrent file and gets metadata info, has 2 parts one to parse the file and one a class to store the metadata
3. tracker -> sets up connection with tracker (no HTTP(S) libraries)

