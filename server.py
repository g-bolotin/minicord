# Galit Bolotin (galit.bolotin@sjsu.edu)
# CS249
# Last edited: 10/29/26

import socket
import threading
import urllib
from threading import Thread
import re
import json
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import argparse
import time
import uuid

USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
CHANNEL_PATTERN = re.compile(r'^#[a-zA-Z0-9_-]+$')

HTTP_CODES = {
    "BAD_REQUEST": 400,
    "NOT_AUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "CONFLICT": 409,
    "INVALID_USERNAME": 412,
    "INVALID_CHANNEL": 412,
    "SERVER_ERROR": 500,
    "SERVICE_UNAVAILABLE": 503,
    "CREATED": 201,
    "SUCCESS": 200
}

class User:
    def __init__(self):
        self.username = ""  # Usernames may contain letters, digits, hyphens, and underscores. Must be unique.
        self.channels = set()

    @staticmethod
    def is_valid_username(username: str) -> bool:
        """Validates usernames contain only letters, digits, hyphens, and underscores."""
        return bool(USERNAME_PATTERN.fullmatch(username))


class Channel:
    def __init__(self, name: str = "", primary: str = "", backup: str = ""):
        self.name = name  # Channel names must begin with # and may contain letters, digits, hyphens, and underscores. Must be unique.
        self.primary = primary
        self.backup = backup
        self.messages: dict[int, Message] = {}  # MID : Message
        self.members = set()
        self._message_id_counter = 1

    def get_next_message_id(self) -> int:
        """Assigns sequential unique message IDs per channel."""
        current_id = self._message_id_counter
        self._message_id_counter += 1
        return current_id

    @staticmethod
    def is_valid_channel(channel_name: str) -> bool:
        """Validates channel names begin with # and contain letters, digits, hyphens, and underscores."""
        return bool(CHANNEL_PATTERN.fullmatch(channel_name))


class Message:
    def __init__(self):
        self.message_id = 0
        self.channel = ""
        self.username = ""
        self.text = ""

    def to_dict(self):
        """For serializing the message to JSON."""
        return {
            "message_id": self.message_id,
            "channel": self.channel,
            "username": self.username,
            "text": self.text
        }


class AppState:
    """Maintains local application state rather than external database."""
    def __init__(self, server_id: str, servers_config: list[dict]):
        self.id_lock = threading.RLock()  # Allows same thread to reacquire lock safely

        self.server_id = server_id
        # Map server_id ("A", "B", "C") -> config dict
        self.servers_config: dict[str, dict] = {s["id"]: s for s in servers_config}
        self.self_config = self.servers_config[server_id]

        # Track availability and missed heartbeats for each server
        self.peer_alive: dict[str, bool] = {s["id"]: True for s in servers_config}
        self.missed_heartbeats: dict[str, int] = {s["id"]: 0 for s in servers_config}

        # Highest priority server at startup is initial leader (Server C, priority 3)
        highest_server = max(servers_config, key=lambda s: s["priority"])
        self.current_leader: str = highest_server["id"]

        self.users: dict[str, User] = {}  # username : User
        self.channels: dict[str, Channel] = {}  # channel name : Channel

        # Pre-populate the required default channels and ownership rules
        self._init_default_channels()

    def _init_default_channels(self):
        default_channels = [
            ("#cs249", "A", "B"),
            ("#distributed", "B", "C"),
            ("#systems", "C", "A"),
        ]
        for name, primary, backup in default_channels:
            self.channels[name] = Channel(name=name, primary=primary, backup=backup)


class HTTPHandler(BaseHTTPRequestHandler):
    def __init__(self, app_state: AppState, *args, **kwargs):
        self.app_state = app_state
        super().__init__(*args, **kwargs)

    def do_POST(self):
        # Decode URL to convert %23 into #
        decoded_path = urllib.parse.unquote(self.path)
        path_parts = decoded_path.strip("/").split("/")

        # POST user
        if decoded_path == "/users":
            try:
                # Load json file
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b""
                data = json.loads(post_data.decode('utf-8'))

            except (ValueError, json.JSONDecodeError):
                self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error", "code": "INVALID_JSON", "message": "Invalid JSON payload"}')
                return

            # Pull username data from json, create user
            username = data.get("username") if isinstance(data, dict) else None
            http_code, resp_body = create_user_globally(self.app_state, username)

            # Send response
            self.send_response(http_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_body).encode('utf-8'))

        # POST channel member
        # Path format: /channels/{channel_name}/members
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "members":
            channel_name = path_parts[1]
            username = self.headers.get('X-User')

            http_code, resp_body = route_channel_request(
                self.app_state, "JOIN", channel_name, username=username
            )
            self.send_response(http_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_body).encode('utf-8'))

        # POST Message
        # Path format: /channels/{channel_name}/messages
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "messages":
            channel_name = path_parts[1]
            username = self.headers.get('X-User')

            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length) if content_length > 0 else b""
                data = json.loads(post_data.decode('utf-8'))
            except (ValueError, json.JSONDecodeError):
                self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error", "code": "INVALID_JSON", "message": "Invalid JSON payload"}')
                return

            text = data.get("text") if isinstance(data, dict) else None
            http_code, resp_body = route_channel_request(
                self.app_state, "SEND", channel_name, username=username, text=text
            )
            self.send_response(http_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_body).encode('utf-8'))

        # Unknown Command
        else:
            self.send_response(HTTP_CODES.get("NOT_FOUND"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "Endpoint not found"}')

    def do_GET(self):
        # Separate query params
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # %23 to #
        decoded_path = urllib.parse.unquote(parsed_url.path)
        path_parts = [p for p in decoded_path.strip("/").split("/") if p]

        if decoded_path == "/health":
            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            response = json.dumps({
                "status": "ok",
                "server_id": self.app_state.server_id
            })
            self.wfile.write(response.encode('utf-8'))

        elif decoded_path == "/servers":
            with self.app_state.id_lock:
                servers_list = [
                    {"server_id": sid, "alive": self.app_state.peer_alive[sid]}
                    for sid in sorted(self.app_state.servers_config.keys())
                ]
            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"servers": servers_list}).encode('utf-8'))

        elif decoded_path == "/leader":
            with self.app_state.id_lock:
                leader_id = self.app_state.current_leader
                leader_alive = self.app_state.peer_alive.get(leader_id, False)
            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "leader": leader_id,
                "alive": leader_alive
            }).encode('utf-8'))

        # GET users
        elif decoded_path == "/users":
            with self.app_state.id_lock:
                user_list = list(self.app_state.users.keys())

            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = json.dumps({"users": user_list})
            self.wfile.write(response.encode('utf-8'))

        # GET channels
        elif decoded_path == "/channels":
            with self.app_state.id_lock:
                channel_list = list(self.app_state.channels.keys())

            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = json.dumps({"channels": channel_list})
            self.wfile.write(response.encode('utf-8'))

        # GET channel members
        # Path format: /channels/{channel_name}/users
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "users":
            channel_name = path_parts[1]
            http_code, resp_body = route_channel_request(
                self.app_state, "USERS", channel_name
            )
            self.send_response(http_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_body).encode('utf-8'))

        # GET Messages
        # Path format: /channels/{channel_name}/messages
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "messages":
            channel_name = path_parts[1]
            limit_str = query_params.get("limit", [None])[0]
            limit_val = None
            if limit_str is not None:
                if limit_str.isdigit():
                    limit_val = int(limit_str)
                else:
                    self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(
                        b'{"status": "error", "code": "BAD_REQUEST", "message": "Invalid limit parameter"}')
                    return

            http_code, resp_body = route_channel_request(
                self.app_state, "HISTORY", channel_name, limit=limit_val
            )
            self.send_response(http_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_body).encode('utf-8'))

        # Unknown command
        else:
            self.send_response(HTTP_CODES.get("NOT_FOUND"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "Endpoint not found"}')

    def do_DELETE(self):
        # %23 to #
        parsed_url = urllib.parse.urlparse(self.path)
        decoded_path = urllib.parse.unquote(parsed_url.path)
        path_parts = [p for p in decoded_path.strip("/").split("/") if p]

        # Path format: /channels/{channel_name}/members/{username}
        if len(path_parts) == 4 and path_parts[0] == "channels" and path_parts[2] == "members":
            channel_name = path_parts[1]
            username = path_parts[3]

            http_code, resp_body = route_channel_request(
                self.app_state, "LEAVE", channel_name, username=username
            )
            self.send_response(http_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp_body).encode('utf-8'))

        # Unknown Command
        else:
            self.send_response(HTTP_CODES.get("NOT_FOUND"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "Endpoint not found"}')

    # HTTP error handling for unsupported functions
    def _send_method_not_allowed(self):
        self.send_response(HTTP_CODES.get("METHOD_NOT_ALLOWED", 405))
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "error", "code": "METHOD_NOT_ALLOWED", "message": "Unsupported HTTP method"}')

    def do_PUT(self):
        self._send_method_not_allowed()

    def do_PATCH(self):
        self._send_method_not_allowed()


def send_peer_message(app_state: AppState, target_id: str, payload: dict, timeout: float = 2.0, expect_response: bool = True) -> dict | None:
    """Sends a newline-terminated JSON message to a peer server over TCP."""
    target_cfg = app_state.servers_config.get(target_id)
    if not target_cfg:
        return None

    host = target_cfg["host"]
    port = target_cfg["peer_port"]

    try:
        # Open socket and attempt to send payload
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
            msg_bytes = (json.dumps(payload) + "\n").encode('utf-8')
            sock.sendall(msg_bytes)

            if not expect_response:
                return {"status": "sent"}

            # Read until newline
            buffer = ""
            while "\n" not in buffer:
                chunk = sock.recv(4096).decode('utf-8')
                if not chunk:
                    break
                buffer += chunk

            line = buffer.strip()
            if not line:
                return None
            return json.loads(line.split("\n")[0])

    except (socket.timeout, ConnectionError, OSError, json.JSONDecodeError):
        return None


def create_user_globally(app_state: AppState, username: str) -> tuple[int, dict]:
    """
    User creation is handled by the group leader so registrations across different servers
    can't create duplicate usernames. The created user is replicated across all live peers.
    """
    if not username or not isinstance(username, str):
        return HTTP_CODES["BAD_REQUEST"], {
            "status": "error",
            "code": "BAD_REQUEST",
            "message": "Missing username"
        }

    if not User.is_valid_username(username):
        return HTTP_CODES["BAD_REQUEST"], {
            "status": "error",
            "code": "BAD_REQUEST",
            "message": "Username contains invalid characters"
        }

    with app_state.id_lock:
        leader_id = app_state.current_leader
        is_leader = (leader_id == app_state.server_id) or not app_state.peer_alive.get(leader_id, True)

    # If this server is NOT the leader, ask the leader to authorize & replicate the user
    if not is_leader:
        req = {
            "type": "USER_SYNC",
            "action": "CREATE",
            "username": username,
            "from": app_state.server_id
        }
        resp = send_peer_message(app_state, leader_id, req, timeout=2.0, expect_response=True)
        if resp and "http_code" in resp and "body" in resp:
            # Ensure local copy is also stored immediately on success
            if resp["http_code"] == HTTP_CODES["CREATED"]:
                with app_state.id_lock:
                    if username not in app_state.users:
                        u = User()
                        u.username = username
                        app_state.users[username] = u
            return resp["http_code"], resp["body"]

    # Fallback if the leader just failed before heartbeat timeout caught it.
    # Process locally and replicate to live peers

    # Process as leader or fallback
    with app_state.id_lock:
        if username in app_state.users:
            return HTTP_CODES["CONFLICT"], {
                "status": "error",
                "code": "CONFLICT",
                "message": "User exists"
            }

        new_user = User()
        new_user.username = username
        app_state.users[username] = new_user

        alive_peers = [
            sid for sid in app_state.servers_config
            if sid != app_state.server_id and app_state.peer_alive.get(sid, True)
        ]

    # Replicate the new user to all live peers outside lock
    for peer_id in alive_peers:
        rep_msg = {
            "type": "USER_SYNC",
            "action": "REPLICATE",
            "username": username,
            "from": app_state.server_id
        }
        send_peer_message(app_state, peer_id, rep_msg, timeout=2.0, expect_response=True)

    print(f"[Server {app_state.server_id}] Registered user '{username}' and replicated to peers {alive_peers}", flush=True)
    return HTTP_CODES["CREATED"], {"username": username}


def trigger_leader_selection(app_state: AppState):
    """Selects the highest priority available server as leader and announces if self is chosen."""
    announce_targets = []
    new_leader_id = None

    with app_state.id_lock:
        # Keep Self marked alive
        app_state.peer_alive[app_state.server_id] = True

        # Find all currently alive servers
        alive_servers = [
            cfg for sid, cfg in app_state.servers_config.items()
            if app_state.peer_alive.get(sid, False)
        ]
        if not alive_servers:
            return

        # Selection: highest priority value wins (C=3, B=2, A=1)
        highest_server = max(alive_servers, key=lambda s: s["priority"])
        new_leader_id = highest_server["id"]

        if app_state.current_leader != new_leader_id:
            old_leader = app_state.current_leader
            app_state.current_leader = new_leader_id
            print(
                f"[Server {app_state.server_id}] Leader {old_leader} unavailable. "
                f"Selected new leader: Server {new_leader_id} (priority {highest_server['priority']})",
                flush=True
            )

            # If this server is the newly selected leader, announce to surviving peers
            if new_leader_id == app_state.server_id:
                announce_targets = [
                    sid for sid in app_state.servers_config
                    if sid != app_state.server_id and app_state.peer_alive.get(sid, False)
                ]

    # Send LEADER_ANNOUNCE outside lock so network I/O doesn't block local state
    for target_id in announce_targets:
        announce_msg = {
            "type": "LEADER_ANNOUNCE",
            "leader": new_leader_id
        }
        print(f"[Server {app_state.server_id}] Sending LEADER_ANNOUNCE (leader={new_leader_id}) to Server {target_id}", flush=True)
        send_peer_message(app_state, target_id, announce_msg, timeout=2.0, expect_response=True)


def heartbeat_monitor_loop(app_state: AppState):
    """Sends PING heartbeats to peers every 2 seconds."""
    sequence = 1
    peer_ids = [sid for sid in app_state.servers_config if sid != app_state.server_id]

    while True:
        time.sleep(2.0)  # 2s wait

        for peer_id in peer_ids:
            ping_msg = {
                "type": "PING",
                "from": app_state.server_id,
                "sequence": sequence
            }
            resp = send_peer_message(app_state, peer_id, ping_msg, timeout=2.0, expect_response=True)

            leader_needs_update = False

            # Valid PONG must match the request sequence number
            if resp and resp.get("type") == "PONG" and resp.get("sequence") == sequence:
                with app_state.id_lock:
                    was_alive = app_state.peer_alive.get(peer_id, True)
                    app_state.missed_heartbeats[peer_id] = 0
                    app_state.peer_alive[peer_id] = True
                    if not was_alive:
                        print(f"[Server {app_state.server_id}] Peer Server {peer_id} is reachable again.", flush=True)
            else:
                with app_state.id_lock:
                    app_state.missed_heartbeats[peer_id] += 1
                    missed = app_state.missed_heartbeats[peer_id]

                    if app_state.peer_alive.get(peer_id, True):
                        print(f"[Server {app_state.server_id}] Missed heartbeat from Server {peer_id} ({missed}/3)", flush=True)

                    # Mark unavailable after 3 consecutive missed heartbeats
                    if missed >= 3 and app_state.peer_alive.get(peer_id, True):
                        app_state.peer_alive[peer_id] = False
                        print(f"[Server {app_state.server_id}] Server {peer_id} marked UNAVAILABLE after 3 missed heartbeats.", flush=True)
                        if app_state.current_leader == peer_id:
                            leader_needs_update = True

            if leader_needs_update:
                trigger_leader_selection(app_state)

        sequence += 1


def execute_channel_op_on_primary(
    app_state: AppState,
    operation: str,
    channel_name: str,
    username: str = None,
    text: str = None,
    limit: int = None
) -> tuple[int, dict]:
    """Executes a channel operation on the channel's primary server."""
    if not Channel.is_valid_channel(channel_name):
        return HTTP_CODES["BAD_REQUEST"], {
            "status": "error",
            "code": "INVALID_CHANNEL",
            "message": "Invalid channel format"
        }

    with app_state.id_lock:
        if channel_name not in app_state.channels:
            return HTTP_CODES["NOT_FOUND"], {
                "status": "error",
                "code": "NOT_FOUND",
                "message": "Channel does not exist"
            }

        channel = app_state.channels[channel_name]

        # Join Channel
        if operation == "JOIN":
            if not username:
                return HTTP_CODES["NOT_AUTHENTICATED"], {
                    "status": "error",
                    "code": "NOT_AUTHENTICATED",
                    "message": "Missing X-User header"
                }
            if username not in app_state.users:
                return HTTP_CODES["NOT_FOUND"], {
                    "status": "error",
                    "code": "NOT_FOUND",
                    "message": "User does not exist"
                }
            channel.members.add(username)
            app_state.users[username].channels.add(channel_name)
            return HTTP_CODES["SUCCESS"], {
                "channel": channel_name,
                "username": username,
                "joined": True
            }

        # List Channel Members
        elif operation == "USERS":
            member_list = list(channel.members)
            return HTTP_CODES["SUCCESS"], {
                "channel": channel_name,
                "users": member_list
            }

        # Leave Channel
        elif operation == "LEAVE":
            if not username or username not in app_state.users:
                return HTTP_CODES["NOT_FOUND"], {
                    "status": "error",
                    "code": "NOT_FOUND",
                    "message": "User does not exist"
                }
            channel.members.discard(username)
            app_state.users[username].channels.discard(channel_name)
            return HTTP_CODES["SUCCESS"], {
                "channel": channel_name,
                "username": username,
                "left": True
            }

        # Send Message
        elif operation == "SEND":
            if not username:
                return HTTP_CODES["NOT_AUTHENTICATED"], {
                    "status": "error",
                    "code": "NOT_AUTHENTICATED",
                    "message": "Missing X-User header"
                }
            if not text or not isinstance(text, str):
                return HTTP_CODES["BAD_REQUEST"], {
                    "status": "error",
                    "code": "BAD_REQUEST",
                    "message": "Missing message text"
                }
            if username not in app_state.users:
                return HTTP_CODES["NOT_FOUND"], {
                    "status": "error",
                    "code": "NOT_FOUND",
                    "message": "User does not exist"
                }
            if username not in channel.members:
                return HTTP_CODES["BAD_REQUEST"], {
                    "status": "error",
                    "code": "UNAUTHORIZED",
                    "message": "User is not a member of this channel"
                }

            msg = Message()
            msg.message_id = channel.get_next_message_id()
            msg.channel = channel_name
            msg.username = username
            msg.text = text
            channel.messages[msg.message_id] = msg
            msg_dict = msg.to_dict()
            backup_id = channel.backup

        # Message History
        elif operation == "HISTORY":
            all_messages = [
                {
                    "message_id": m.message_id,
                    "username": m.username,
                    "text": m.text
                }
                for m in channel.messages.values()
            ]
            if limit is not None and limit >= 0:
                all_messages = all_messages[-limit:] if limit > 0 else []
            return HTTP_CODES["SUCCESS"], {
                "channel": channel_name,
                "messages": all_messages
            }

        else:
            return HTTP_CODES["BAD_REQUEST"], {
                "status": "error",
                "code": "BAD_REQUEST",
                "message": f"Unsupported channel operation: {operation}"
            }

        # Replicate newly created message from primary server to backup server
        if backup_id and backup_id != app_state.server_id:
            rep_req = {
                "type": "REPLICATE",
                "channel": channel_name,
                "message": {
                    "message_id": msg_dict["message_id"],
                    "username": msg_dict["username"],
                    "text": msg_dict["text"]
                }
            }
            print(
                f"[Server {app_state.server_id}] Replicating message {msg_dict['message_id']} in {channel_name} "
                f"-> Backup Server {backup_id}",
                flush=True
            )
            ack = send_peer_message(app_state, backup_id, rep_req, timeout=2.0, expect_response=True)
            if (
                    ack
                    and ack.get("type") == "REPLICATE_ACK"
                    and ack.get("channel") == channel_name
                    and ack.get("message_id") == msg_dict["message_id"]
            ):
                print(
                    f"[Server {app_state.server_id}] Received REPLICATE_ACK for {channel_name} "
                    f"message {msg_dict['message_id']} from Backup Server {ack.get('from')}",
                    flush=True
                )
            else:
                print(
                    f"[Server {app_state.server_id}] Warning: Backup Server {backup_id} did not acknowledge "
                    f"replication for {channel_name} message {msg_dict['message_id']} (continuing as Primary)",
                    flush=True
                )

        return HTTP_CODES["CREATED"], msg_dict


def route_channel_request(
    app_state: AppState,
    operation: str,
    channel_name: str,
    username: str = None,
    text: str = None,
    limit: int = None
) -> tuple[int, dict]:
    """Routes a channel operation to the channel's primary server (or keeps it local if primary/backup)."""
    if not Channel.is_valid_channel(channel_name):
        return HTTP_CODES["BAD_REQUEST"], {
            "status": "error",
            "code": "INVALID_CHANNEL",
            "message": "Invalid channel format"
        }

    with app_state.id_lock:
        if channel_name not in app_state.channels:
            return HTTP_CODES["NOT_FOUND"], {
                "status": "error",
                "code": "NOT_FOUND",
                "message": "Channel does not exist"
            }
        channel = app_state.channels[channel_name]
        primary_id = channel.primary
        backup_id = channel.backup

    # If this server is the channel's primary, execute locally
    if app_state.server_id == primary_id:
        print(
            f"[Server {app_state.server_id}] Processing {operation} for {channel_name} locally as Primary",
            flush=True
        )
        return execute_channel_op_on_primary(app_state, operation, channel_name, username, text, limit)

    # If this is a HISTORY read on the channel's designated backup server, serve local replica
    if operation == "HISTORY" and app_state.server_id == backup_id:
        with app_state.id_lock:
            channel = app_state.channels[channel_name]
            local_messages = [
                {
                    "message_id": m.message_id,
                    "username": m.username,
                    "text": m.text
                }
                for m in channel.messages.values()
            ]
            if limit is not None and limit >= 0:
                local_messages = local_messages[-limit:] if limit > 0 else []

        print(
            f"[Server {app_state.server_id}] Serving HISTORY for {channel_name} locally from BACKUP replica "
            f"({len(local_messages)} messages stored locally)",
            flush=True
        )
        return HTTP_CODES["SUCCESS"], {
            "channel": channel_name,
            "messages": local_messages
        }

    # Otherwise, forward the request over TCP to the channel's primary server
    req_id = uuid.uuid4().hex[:8]
    forward_msg = {
        "type": "FORWARD",
        "request_id": req_id,
        "operation": operation,
        "channel": channel_name,
        "username": username,
        "text": text,
        "limit": limit,
        "from": app_state.server_id
    }

    print(
        f"[Server {app_state.server_id}] Forwarding {operation} for {channel_name} "
        f"(request_id={req_id}) -> Primary Server {primary_id}",
        flush=True
    )

    resp = send_peer_message(app_state, primary_id, forward_msg, timeout=2.0, expect_response=True)
    if not resp or resp.get("type") != "FORWARD_RESPONSE" or resp.get("request_id") != req_id:
        print(
            f"[Server {app_state.server_id}] Forwarding {operation} for {channel_name} failed: "
            f"Primary Server {primary_id} is unavailable",
            flush=True
        )
        return HTTP_CODES["SERVICE_UNAVAILABLE"], {
            "status": "error",
            "code": "PRIMARY_UNAVAILABLE",
            "message": f"Primary server {primary_id} for channel {channel_name} is unavailable"
        }

    return resp["http_code"], resp["body"]

class Server:
    def __init__(self, HOST, TCP_PORT, app_state: AppState):
        self.app_state = app_state
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((HOST, TCP_PORT))
        self.socket.listen()

    def listen(self):
        while True:
            cli_sock, addr = self.socket.accept()
            Thread(target=self.handle_peer_connection, args=(cli_sock, addr, self.app_state), daemon=True).start()

    @staticmethod
    def handle_ping(msg: dict, cli_sock: socket.socket, app_state: AppState):
        """Handles incoming heartbeat PING and replies with PONG matching sequence."""
        seq = msg.get("sequence", 1)
        pong_resp = {
            "type": "PONG",
            "from": app_state.server_id,
            "sequence": seq
        }
        cli_sock.sendall((json.dumps(pong_resp) + "\n").encode('utf-8'))

    @staticmethod
    def handle_leader_announce(msg: dict, cli_sock: socket.socket, app_state: AppState):
        """Updates local leader state when a new leader announces itself."""
        new_leader = msg.get("leader")
        if new_leader:
            with app_state.id_lock:
                app_state.current_leader = new_leader
                app_state.peer_alive[new_leader] = True
            print(f"[Server {app_state.server_id}] Received LEADER_ANNOUNCE: new cluster leader is Server {new_leader}",
                  flush=True)
        ack = {"type": "LEADER_ANNOUNCE_ACK", "from": app_state.server_id}
        cli_sock.sendall((json.dumps(ack) + "\n").encode('utf-8'))

    @staticmethod
    def handle_replicate(msg: dict, cli_sock: socket.socket, app_state: AppState):
        """Stores a replicated message on the specified backup server and returns REPLICATE_ACK."""
        # Get fields
        channel_name = msg.get("channel", "")
        msg_data = msg.get("message", {})
        msg_id = msg_data.get("message_id")
        username = msg_data.get("username", "")
        text = msg_data.get("text", "")

        # Check if message exists for replication
        # If channel doesn't exist as a replica, update app state with new replica channel
        if channel_name and msg_id is not None:
            with app_state.id_lock:
                if channel_name not in app_state.channels:
                    app_state.channels[channel_name] = Channel(name=channel_name)

                channel = app_state.channels[channel_name]

                # Check that duplicate replication messages don't create duplicate records
                if msg_id not in channel.messages:
                    replicated_msg = Message()
                    replicated_msg.message_id = msg_id
                    replicated_msg.channel = channel_name
                    replicated_msg.username = username
                    replicated_msg.text = text
                    channel.messages[msg_id] = replicated_msg

                    if msg_id >= channel._message_id_counter:
                        channel._message_id_counter = msg_id + 1

            print(
                f"[Server {app_state.server_id}] Backup stored replicated message_id={msg_id} "
                f"for {channel_name} (from user '{username}')",
                flush=True
            )

        ack = {
            "type": "REPLICATE_ACK",
            "channel": channel_name,
            "message_id": msg_id,
            "from": app_state.server_id
        }
        cli_sock.sendall((json.dumps(ack) + "\n").encode('utf-8'))

    @staticmethod
    def handle_forward(msg: dict, cli_sock: socket.socket, app_state: AppState):
        """Handles a FORWARD request received by the channel's primary server."""
        # Get fields
        req_id = msg.get("request_id", "")
        operation = msg.get("operation", "")
        channel_name = msg.get("channel", "")
        username = msg.get("username")
        text = msg.get("text")
        limit = msg.get("limit")
        sender = msg.get("from", "?")

        print(
            f"[Server {app_state.server_id}] Received FORWARD ({operation} on {channel_name}, "
            f"request_id={req_id}) from Server {sender}",
            flush=True
        )

        # Send request to handler function
        http_code, body = execute_channel_op_on_primary(
            app_state, operation, channel_name, username=username, text=text, limit=limit
        )

        # Return response
        status_str = "ok" if http_code in (200, 201) else "error"
        forward_resp = {
            "type": "FORWARD_RESPONSE",
            "request_id": req_id,
            "status": status_str,
            "http_code": http_code,
            "body": body
        }
        if operation == "SEND" and status_str == "ok":
            forward_resp["message_id"] = body.get("message_id")

        cli_sock.sendall((json.dumps(forward_resp) + "\n").encode('utf-8'))

    @staticmethod
    def handle_user_sync(msg: dict, cli_sock: socket.socket, app_state: AppState):
        """Handles global user creation at leader and user replication at peers."""
        action = msg.get("action")
        username = msg.get("username")

        if action == "REPLICATE":
            if username:
                with app_state.id_lock:
                    # If username doesn't exist as a replica, create the user and update app state
                    if username not in app_state.users:
                        new_user = User()
                        new_user.username = username
                        app_state.users[username] = new_user
                print(f"[Server {app_state.server_id}] Replicated user '{username}' from Server {msg.get('from')}",
                      flush=True)
            ack = {"type": "USER_SYNC_ACK", "status": "ok", "username": username}
            cli_sock.sendall((json.dumps(ack) + "\n").encode('utf-8'))

        elif action == "CREATE":
            http_code, body = create_user_globally(app_state, username)
            resp = {
                "type": "USER_SYNC_RESPONSE",
                "http_code": http_code,
                "body": body
            }
            cli_sock.sendall((json.dumps(resp) + "\n").encode('utf-8'))

    def handle_peer_connection(self, cli_sock: socket.socket, addr, app_state: AppState):
        PEER_HANDLERS = {
            "PING": self.handle_ping,
            "LEADER_ANNOUNCE": self.handle_leader_announce,
            "REPLICATE": self.handle_replicate,
            "FORWARD": self.handle_forward,
            "USER_SYNC": self.handle_user_sync
        }

        buffer = ""
        try:
            while True:
                # Receive json message bytes
                chunk = cli_sock.recv(4096).decode('utf-8')
                if not chunk:
                    break
                buffer += chunk

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        err = {"status": "error", "code": "INVALID_JSON", "message": "Invalid peer JSON"}
                        cli_sock.sendall((json.dumps(err) + "\n").encode('utf-8'))
                        continue

                    # Based on received message type, send to appropriate handler
                    msg_type = msg.get("type")
                    handler_fn = PEER_HANDLERS.get(msg_type)
                    if handler_fn:
                        handler_fn(msg, cli_sock, app_state)
                    else:
                        err = {"status": "error", "code": "UNKNOWN_TYPE",
                               "message": f"Unknown peer message type: {msg_type}"}
                        cli_sock.sendall((json.dumps(err) + "\n").encode('utf-8'))

        # Client crashed or force-closed
        except (ConnectionError, OSError):
            pass

        # Cleanup
        finally:
            cli_sock.close()


def launch_server(HOST, PORT):
    app_state = AppState()
    server = Server(HOST, PORT, app_state)
    server.listen()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Mini Discord Distributed Server")
    parser.add_argument('--config', type=str, default='servers.json', help="Path to servers.json config file")
    parser.add_argument('--id', type=str, default='A', choices=['A', 'B', 'C'], help="Server ID (A, B, or C)")

    args = parser.parse_args()

    with open(args.config, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

    servers_list = config_data.get("servers", [])
    shared_state = AppState(args.id, servers_list)

    host = shared_state.self_config["host"]
    http_port = shared_state.self_config["http_port"]
    peer_port = shared_state.self_config["peer_port"]

    # Launch Peer TCP Server in a background thread
    tcp_server = Server(host, peer_port, shared_state)
    t = Thread(target=tcp_server.listen, daemon=True)
    t.start()

    # Launch Heartbeat Monitor in a background thread
    hb_thread = Thread(target=heartbeat_monitor_loop, args=(shared_state,), daemon=True)
    hb_thread.start()

    # Launch HTTP Server on the main thread
    handler = lambda *a, **kwargs: HTTPHandler(shared_state, *a, **kwargs)
    http_server = ThreadingHTTPServer((host, http_port), handler)

    print(f"[Server {args.id}] HTTP listening on {host}:{http_port}, Peer TCP on {host}:{peer_port} (Leader: {shared_state.current_leader})")
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[Server {args.id}] Shutting down.")