#!/usr/bin/env python3
import argparse
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room
import pty
import os
import subprocess
import select
import termios
import struct
import fcntl
import shlex
import logging
import sys

logging.getLogger("werkzeug").setLevel(logging.ERROR)

__version__ = "0.5.1"

app = Flask(__name__, template_folder=".", static_folder=".", static_url_path="")
app.config["SECRET_KEY"] = "secret!"
# app.config["fd"] = None
# app.config["child_pid"] = None

app.sessions = {}

socketio = SocketIO(app, cors_allowed_origins=[])


def set_winsize(fd, row, col, xpix=0, ypix=0):
    logging.debug("setting window size with termios")
    winsize = struct.pack("HHHH", row, col, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def read_and_forward_pty_output(sid):
    max_read_bytes = 1024 * 20
    logging.debug(f"read_and_forward_pty_output started {sid}")
    while app.sessions.get(sid):
        try:
            (data_ready, _, _) = select.select([app.sessions[sid]], [], [], 0)
            if data_ready:
                output = os.read(app.sessions[sid], max_read_bytes).decode(errors="ignore")
                socketio.emit("pty-output", {"output": output}, namespace="/pty", room=sid)
        except Exception:
            # There was no input anymore (ctrl-d)
            logging.debug(f"read_and_forward_pty_output except {sid}")
            break
        socketio.sleep(0.01)
    logging.debug(f"read_and_forward_pty_output stopped {sid}")
    # socketio.emit("pty-disconnect", {"output": "disconnected"}, namespace="/pty", room=sid)


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("pty-input", namespace="/pty")
def pty_input(data):
    """write to the child pty. The pty sees this as if you are typing in a real
    terminal.
    """
    sid = request.sid
    if app.sessions.get(sid):
        # logging.debug(f"received input from browser: {sid} {data['input']}")
        os.write(app.sessions[sid], data["input"].encode())


@socketio.on("resize", namespace="/pty")
def resize(data):
    sid = request.sid
    if app.sessions.get(sid):
        logging.debug(f"Resizing window to {data['rows']}x{data['cols']}")
        set_winsize(app.sessions[sid], data["rows"], data["cols"])


@socketio.on("connect", namespace="/pty")
def connect():
    """new client connected"""
    sid = request.sid
    # ns = request.namespace
    logging.info(f"new client connected: {sid}")
    # if app.config["child_pid"]:
    if app.sessions.get(sid):
        # already started child process, don't start another
        return

    # create child process attached to a pty we can read from and write to
    (child_pid, fd) = pty.fork()
    if child_pid == 0:
        # this is the child process fork.
        # anything printed here will show up in the pty, including the output
        # of this subprocess
        subprocess.run(app.config["cmd"])
    else:
        join_room(sid)
        # this is the parent process fork.
        # store child fd in sid
        app.sessions[sid] = fd
        # set_winsize(fd, 50, 50)
        # logging/print statements must go after this because... I have no idea why
        # but if they come before the background task never starts
        socketio.start_background_task(read_and_forward_pty_output, sid)

        logging.info(f"child pid is {child_pid}")
        cmd = " ".join(shlex.quote(c) for c in app.config["cmd"])
        logging.info(
            f"starting background task with command `{cmd}` to continously read "
            "and forward pty output to client"
        )
        logging.info("task started")


@socketio.on("disconnect", namespace="/pty")
def disconnect():
    """client disconnected"""
    logging.info("client disconnected")
    sid = request.sid
    app.sessions[sid] = None
    leave_room(sid)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "A fully functional terminal in your browser. "
            "https://github.com/cs01/pyxterm.js"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-p", "--port", default=5000, help="port to run server on", type=int
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="host to run server on (use 0.0.0.0 to allow access from other hosts)",
    )
    parser.add_argument("--debug", action="store_true", help="debug the server")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--command", default="bash", help="Command to run in the terminal"
    )
    parser.add_argument(
        "--cmd-args",
        default="",
        help="arguments to pass to command (i.e. --cmd-args='arg1 arg2 --flag')",
    )
    args = parser.parse_args()
    if args.version:
        print(__version__)
        exit(0)
    app.config["cmd"] = [args.command] + shlex.split(args.cmd_args)
    green = "\033[92m"
    end = "\033[0m"
    log_format = (
        green
        + "pyxtermjs > "
        + end
        + "%(levelname)s (%(funcName)s:%(lineno)s) %(message)s"
    )
    logging.basicConfig(
        format=log_format,
        stream=sys.stdout,
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    logging.info(f"serving on http://{args.host}:{args.port}")
    socketio.run(app, debug=args.debug, port=args.port, host=args.host)


if __name__ == "__main__":
    main()
