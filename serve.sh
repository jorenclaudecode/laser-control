#!/bin/zsh
# macOS helper: serve Laser Control on http://localhost:8766 (Web Serial needs localhost or https) and open it in Edge.
cd "$(dirname "$0")"
( sleep 1; open -a "Microsoft Edge" http://localhost:8766 ) &
exec python3 -m http.server 8766 --bind 127.0.0.1
