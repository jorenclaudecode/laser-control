#!/usr/bin/env python3
"""
Laser Control bridge - lets any browser (Safari, Firefox, iPhone/iPad, ...) drive the engraver.

Browsers other than Chrome/Edge can't open USB serial ports. This small program runs on the
computer the engraver is plugged into: it serves the Laser Control page and passes the engraver's
serial port through to the page over a WebSocket. Open the address it prints.

    python3 bridge.py                 # this computer only: http://localhost:8766
    python3 bridge.py --lan           # also phones/tablets on the same Wi-Fi (prints the address)
    python3 bridge.py --port COM5     # pick the serial port yourself (default: auto-detect)
    python3 bridge.py --tcp 192.168.4.1:8080   # engraver over Wi-Fi/telnet instead of USB (experimental)
    python3 bridge.py --list          # show serial ports

macOS / Linux: Python 3.7+ only, nothing to install.
Windows: also needs pyserial  ->  py -m pip install pyserial
The serial port is only opened while a page is connected, so Chrome/Edge can still use it directly
when the bridge is idle. Only one page can be connected at a time.
MIT licence - see LICENSE.
"""
import argparse, base64, glob, hashlib, json, os, select, socket, struct, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

try:
    import serial                      # pyserial: required on Windows, optional elsewhere
    from serial.tools import list_ports
except ImportError:
    serial = None

# ---------------------------------------------------------------- engraver links

PORT_PATTERNS = ["/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.usbmodem*",
                 "/dev/ttyUSB*", "/dev/ttyACM*"]
KNOWN_VIDS = {0x1A86: "CH340/CH9102", 0x10C4: "CP210x", 0x0403: "FTDI", 0x2341: "Arduino", 0x303A: "ESP32", 0x0483: "STM32"}


def find_ports():
    """Likely engraver ports, best first."""
    if serial:
        found = []
        for p in list_ports.comports():
            if p.vid in KNOWN_VIDS:
                found.append((0, p.device, "%s (%s)" % (p.device, KNOWN_VIDS[p.vid])))
            elif p.vid is not None:
                found.append((1, p.device, "%s (%s)" % (p.device, p.description)))
        return [(d, label) for _, d, label in sorted(found)]
    ports = []
    for pat in PORT_PATTERNS:
        for d in sorted(glob.glob(pat)):
            if d not in [x for x, _ in ports]:
                ports.append((d, d))
    return ports


class SerialLink:
    """Raw serial port. pyserial when available, otherwise termios (macOS/Linux)."""

    def __init__(self, device, baud):
        self.device = device
        if serial:
            # DTR and RTS stay at their defaults (both asserted): no auto-reset of ESP32 boards.
            self.s = serial.Serial(device, baud, timeout=0, write_timeout=2)
            self.fd = None
        else:
            import termios
            self.s = None
            self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            speed = getattr(termios, "B%d" % baud)
            attr = termios.tcgetattr(self.fd)
            attr[0] = 0                                                  # iflag: raw
            attr[1] = 0                                                  # oflag
            attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL       # 8N1, no HUPCL (no reset on close)
            attr[3] = 0                                                  # lflag: no echo, non-canonical
            attr[4] = attr[5] = speed
            attr[6][termios.VMIN] = 0
            attr[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attr)

    def read(self, timeout=0.05):
        if self.s:
            n = self.s.in_waiting
            if n:
                return self.s.read(n)
            time.sleep(timeout)
            return b""
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return b""
        data = os.read(self.fd, 4096)
        if not data:
            raise IOError("serial port closed")
        return data

    def write(self, data):
        if self.s:
            self.s.write(data)
            return
        view = memoryview(data)
        while view:
            try:
                n = os.write(self.fd, view)
                view = view[n:]
            except BlockingIOError:
                select.select([], [self.fd], [], 1)

    def close(self):
        try:
            self.s.close() if self.s else os.close(self.fd)
        except Exception:
            pass

    def describe(self):
        return self.device


class TcpLink:
    """Engraver reachable over the network (e.g. Grbl_ESP32 telnet). Experimental."""

    def __init__(self, hostport):
        host, port = hostport.rsplit(":", 1)
        self.name = hostport
        self.sock = socket.create_connection((host, int(port)), timeout=5)
        self.sock.setblocking(False)

    def read(self, timeout=0.05):
        r, _, _ = select.select([self.sock], [], [], timeout)
        if not r:
            return b""
        data = self.sock.recv(4096)
        if not data:
            raise IOError("connection closed by the engraver")
        return data

    def write(self, data):
        self.sock.setblocking(True)
        self.sock.sendall(data)
        self.sock.setblocking(False)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def describe(self):
        return "tcp://" + self.name


# ---------------------------------------------------------------- websocket (RFC 6455, minimal)

def ws_send(sock, payload, opcode=2):
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    sock.sendall(head + payload)


def recv_exact(rfile, n):
    data = rfile.read(n)
    if data is None or len(data) < n:
        raise ConnectionError("websocket closed")
    return data


def ws_recv(rfile):
    """Returns (opcode, payload). Client frames are always masked."""
    b1, b2 = recv_exact(rfile, 2)
    opcode, masked, n = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", recv_exact(rfile, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", recv_exact(rfile, 8))[0]
    if n > 1 << 20:
        raise ConnectionError("frame too large")
    mask = recv_exact(rfile, 4) if masked else b"\0\0\0\0"
    data = bytearray(recv_exact(rfile, n))
    for i in range(n):
        data[i] ^= mask[i & 3]
    return opcode, bytes(data)


# ---------------------------------------------------------------- HTTP server

class State:
    args = None
    busy = threading.Lock()   # one connected page at a time
    target = None             # description of the engraver currently open


def local_host_ok(host):
    h = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    return h in ("localhost", "127.0.0.1", "::1")


class Handler(BaseHTTPRequestHandler):
    server_version = "LaserControlBridge/" + VERSION

    def log_message(self, fmt, *a):
        pass  # keep the terminal for the useful messages

    def deny(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def host_allowed(self):
        # Against DNS rebinding: unless --lan, only accept requests addressed to localhost.
        return State.args.lan or local_host_ok(self.headers.get("Host"))

    def do_GET(self):
        if not self.host_allowed():
            return self.deny(403, "Forbidden host")
        path = self.path.split("?", 1)[0]
        if path == "/ws":
            return self.websocket()
        if path == "/bridge-info":
            ports = find_ports()
            info = {"bridge": True, "version": VERSION, "busy": State.busy.locked(), "target": State.target,
                    "tcp": State.args.tcp, "port": State.args.port or (ports[0][0] if ports else None),
                    "ports": [label for _, label in ports]}
            body = json.dumps(info).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        self.deny(404, "Not found")

    def websocket(self):
        # Only the page served by this bridge (same origin) may connect: a random website open in
        # another tab must never be able to drive the laser through localhost.
        origin = self.headers.get("Origin", "")
        own = "http://" + (self.headers.get("Host") or "")
        if origin != own and origin not in State.args.allow_origin:
            print("Refused a connection from %s (not this page)" % (origin or "unknown origin"))
            return self.deny(403, "Origin not allowed")
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or self.headers.get("Upgrade", "").lower() != "websocket":
            return self.deny(400, "Expected a WebSocket")
        if not State.busy.acquire(blocking=False):
            return self.deny(409, "Another page is already connected")
        link = None
        try:
            try:
                link = open_link()
            except Exception as e:
                return self.deny(503, "Can't open the engraver: %s" % e)
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()
            State.target = link.describe()
            print("Page connected  ->  engraver on %s" % State.target)
            self.pump(link)
        finally:
            if link:
                link.close()
            State.target = None
            State.busy.release()
            print("Page disconnected, engraver port released")

    def pump(self, link):
        sock, stop = self.connection, threading.Event()
        send_lock = threading.Lock()

        def engraver_to_page():
            try:
                while not stop.is_set():
                    data = link.read(0.05)
                    if data:
                        with send_lock:
                            ws_send(sock, data)
            except Exception as e:
                if not stop.is_set():
                    print("Engraver connection lost: %s" % e)
            finally:
                stop.set()
                try:
                    with send_lock:
                        ws_send(sock, struct.pack(">H", 1011), opcode=8)
                except Exception:
                    pass
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

        t = threading.Thread(target=engraver_to_page, daemon=True)
        t.start()
        try:
            while not stop.is_set():
                opcode, data = ws_recv(self.rfile)
                if opcode in (1, 2, 0):
                    link.write(data)
                elif opcode == 8:
                    break
                elif opcode == 9:
                    with send_lock:
                        ws_send(sock, data, opcode=10)
        except Exception:
            pass
        finally:
            stop.set()
            t.join(1)


def open_link():
    a = State.args
    if a.tcp:
        return TcpLink(a.tcp)
    dev = a.port
    if not dev:
        ports = find_ports()
        if not ports:
            raise IOError("no USB serial port found - is the engraver plugged in and switched on?")
        dev = ports[0][0]
    return SerialLink(dev, a.baud)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # no packet is sent; just picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "this-computers-ip"


def main():
    ap = argparse.ArgumentParser(description="Laser Control bridge: drive the engraver from any browser.")
    ap.add_argument("--port", help="serial port (default: auto-detect), e.g. /dev/ttyUSB0 or COM5")
    ap.add_argument("--baud", type=int, default=115200, help="serial speed (default 115200)")
    ap.add_argument("--tcp", metavar="HOST:PORT", help="connect to the engraver over the network instead of USB (experimental)")
    ap.add_argument("--http-port", type=int, default=8766, help="web page port (default 8766)")
    ap.add_argument("--lan", action="store_true", help="allow phones/tablets on your network (anyone on it can then use the laser)")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="URL", help="also accept pages from this origin")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    State.args = args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)   # show messages right away, also when piped
    except Exception:
        pass

    if args.list:
        ports = find_ports()
        print("\n".join(label for _, label in ports) if ports else "No USB serial ports found.")
        return
    if os.name == "nt" and not serial and not args.tcp:
        sys.exit("On Windows the bridge needs pyserial:  py -m pip install pyserial")
    if not os.path.exists(os.path.join(HERE, "index.html")):
        sys.exit("index.html must be in the same folder as bridge.py")

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        srv = ThreadingHTTPServer((host, args.http_port), Handler)
    except OSError as e:
        sys.exit("Port %d is in use (%s). Close the other program or use --http-port." % (args.http_port, e))
    srv.daemon_threads = True
    ports = find_ports()
    print("Laser Control bridge %s" % VERSION)
    print("  Engraver: %s" % (args.tcp and "tcp://" + args.tcp or args.port or (ports[0][1] + " (auto)" if ports else "not found yet - plug it in")))
    print("  Open:     http://localhost:%d" % args.http_port)
    if args.lan:
        print("  Phones/tablets on your network: http://%s:%d" % (lan_ip(), args.http_port))
        print("  Note: anyone on this network can now reach the laser controls.")
    print("Press Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
