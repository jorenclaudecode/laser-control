#!/bin/zsh
# macOS helper: start the bridge (serves the page on http://localhost:8766 and passes the
# engraver's USB port to any browser) and open the page in Edge. Extra options go to bridge.py,
# e.g. ./serve.sh --lan
cd "$(dirname "$0")"
( sleep 1; open -a "Microsoft Edge" http://localhost:8766 2>/dev/null || open http://localhost:8766 ) &
exec python3 bridge.py "$@"
