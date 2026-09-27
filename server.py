# Server listens to all clients. A client sends a message, and this message is broadcast to all other clients.
# Message history need to be stored in chats that clients can pull up any time.
import socket
import threading
import urllib
from threading import Thread
import re
import json
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
CHANNEL_PATTERN = re.compile(r'^#[a-zA-Z0-9_-]+$')

HTTP_CODES = {
    "BAD_REQUEST": 400,
    "NOT_AUTHENTICATED": 401,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INVALID_USERNAME": 412,
    "INVALID_CHANNEL": 412,
    "SERVER_ERROR": 500,
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
    def __init__(self):
        self.name = ""  # Channel names must begin with # and may contain letters, digits, hyphens, and underscores. Must be unique.
        self.messages: dict[int, Message] = {}  # MID : Message
        self.members = set()

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
    def __init__(self):
        self._message_id_counter = 1
        self.id_lock = threading.RLock()  # Allows same thread to reacquire lock safely

        self.users: dict[str, User] = {}  # username : User
        self.channels: dict[str, Channel] = {}  # channel name : Channel
        self.active_conns: dict[socket.socket, str] = {}  # active socket : username

    def get_next_message_id(self):
        """Message UIDs are determined at app run and incremental."""
        with self.id_lock:
            current_id = self._message_id_counter
            self._message_id_counter += 1
            return current_id


class HTTPHandler(BaseHTTPRequestHandler):
    def __init__(self, app_state: AppState, *args, **kwargs):
        self.app_state = app_state
        super().__init__(*args, **kwargs)

    def do_POST(self):
        # Decode URL to convert %23 into #
        decoded_path = urllib.parse.unquote(self.path)
        path_parts = decoded_path.strip("/").split("/")

        # POST user
        if self.path == "/users":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            username = data.get("username")

            with self.app_state.id_lock:
                # Username already exists
                if username in self.app_state.users:
                    self.send_response(HTTP_CODES.get("CONFLICT"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "CONFLICT", "message": "User exists"}')

                # Create new user
                elif User.is_valid_username(username):
                    new_user = User()
                    new_user.username = username
                    self.app_state.users[username] = new_user

                    self.send_response(HTTP_CODES.get("CREATED"))
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()

                    response = json.dumps({"username": username})
                    self.wfile.write(response.encode('utf-8'))

                else:
                    self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "BAD_REQUEST", "message": "Username contains invalid characters"}')

        # POST channel member
        # Path format: /channels/{channel_name}/members
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "members":
            channel_name = path_parts[1]
            username = self.headers.get('X-User')  # Read the required authentication header

            if not username:
                self.send_response(HTTP_CODES.get("NOT_AUTHENTICATED"))
                self.end_headers()
                self.wfile.write(b'{"status": "error", "code": "NOT_AUTHENTICATED", "message": "Missing X-User header"}')
                return

            with self.app_state.id_lock:
                if username not in self.app_state.users:
                    self.send_response(HTTP_CODES.get("NOT_FOUND"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "User does not exist"}')
                    return

                if not Channel.is_valid_channel(channel_name):
                    self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "BAD_REQUEST", "message": "Invalid channel format"}')
                    return

                # Create channel if it doesn't exist
                if channel_name not in self.app_state.channels:
                    new_channel = Channel()
                    new_channel.name = channel_name
                    self.app_state.channels[channel_name] = new_channel

                # Join channel
                self.app_state.channels[channel_name].members.add(username)
                self.app_state.users[username].channels.add(channel_name)

                self.send_response(HTTP_CODES.get("SUCCESS"))
                self.send_header('Content-Type', 'application/json')
                self.end_headers()

                response = json.dumps({
                    "channel": channel_name,
                    "username": username,
                    "joined": True
                })
                self.wfile.write(response.encode('utf-8'))

        # POST Message
        # Path format: /channels/{channel_name}/messages
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "messages":
            channel_name = path_parts[1]
            username = self.headers.get('X-User')

            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            text = data.get("text")

            if not username or not text:
                self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                self.end_headers()
                self.wfile.write(b'{"status": "error", "message": "Missing header or text"}')
                return

            with self.app_state.id_lock:
                if channel_name not in self.app_state.channels:
                    self.send_response(HTTP_CODES.get("NOT_FOUND"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "message": "Channel not found"}')
                    return

                if username not in self.app_state.channels[channel_name].members:
                    self.send_response(HTTP_CODES.get("BAD_REQUEST"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "message": "Not in channel"}')
                    return

                msg = Message()
                msg.message_id = self.app_state.get_next_message_id()
                msg.channel = channel_name
                msg.username = username
                msg.text = text

                self.app_state.channels[channel_name].messages[msg.message_id] = msg

            self.send_response(HTTP_CODES.get("CREATED"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(msg.to_dict()).encode('utf-8'))

    def do_GET(self):
        # Separate query params
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # %23 to #
        decoded_path = urllib.parse.unquote(parsed_url.path)
        path_parts = [p for p in decoded_path.strip("/").split("/") if p]

        # GET users
        if self.path == "/users":
            with self.app_state.id_lock:
                user_list = list(self.app_state.users.keys())

            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = json.dumps({"users": user_list})
            self.wfile.write(response.encode('utf-8'))

        # GET channels
        elif self.path == "/channels":
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

            with self.app_state.id_lock:
                if channel_name not in self.app_state.channels:
                    self.send_response(HTTP_CODES.get("NOT_FOUND"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "Channel does not exist"}')
                    return

                # Convert set to list for JSON serialization
                member_list = list(self.app_state.channels[channel_name].members)

            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = json.dumps({
                "channel": channel_name,
                "users": member_list
            })
            self.wfile.write(response.encode('utf-8'))

        # GET Messages
        # Path format: /channels/{channel_name}/messages
        elif len(path_parts) == 3 and path_parts[0] == "channels" and path_parts[2] == "messages":
            channel_name = path_parts[1]

            # parse_qs returns lists for values, so we extract the first item if it exists
            limit = query_params.get("limit", [None])[0]

            with self.app_state.id_lock:
                if channel_name not in self.app_state.channels:
                    self.send_response(HTTP_CODES.get("NOT_FOUND"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "message": "Channel not found"}')
                    return

                all_messages = [msg.to_dict() for msg in self.app_state.channels[channel_name].messages.values()]

                if limit and limit.isdigit():
                    all_messages = all_messages[-int(limit):]

            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = json.dumps({"channel": channel_name, "messages": all_messages})
            self.wfile.write(response.encode('utf-8'))

        # Unknown command
        else:
            self.send_response(HTTP_CODES.get("NOT_FOUND"))
            self.end_headers()
            self.wfile.write(b'{"status": "error", "message": "Endpoint not found"}')

    def do_DELETE(self):
        # %23 to #
        decoded_path = urllib.parse.unquote(self.path)
        path_parts = decoded_path.strip("/").split("/")

        # Path format: /channels/{channel_name}/members/{username}
        if len(path_parts) == 4 and path_parts[0] == "channels" and path_parts[2] == "members":
            channel_name = path_parts[1]
            username = path_parts[3]

            with self.app_state.id_lock:
                # Validate that user and channel exists
                if channel_name not in self.app_state.channels:
                    self.send_response(HTTP_CODES.get("NOT_FOUND"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "Channel does not exist"}')
                    return

                if username not in self.app_state.users:
                    self.send_response(HTTP_CODES.get("NOT_FOUND"))
                    self.end_headers()
                    self.wfile.write(b'{"status": "error", "code": "NOT_FOUND", "message": "User does not exist"}')
                    return

                # Remove user from channel
                self.app_state.channels[channel_name].members.discard(username)
                self.app_state.users[username].channels.discard(channel_name)

            self.send_response(HTTP_CODES.get("SUCCESS"))
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            response = json.dumps({
                "channel": channel_name,
                "username": username,
                "left": True
            })
            self.wfile.write(response.encode('utf-8'))

        else:
            self.send_response(HTTP_CODES.get("NOT_FOUND"))
            self.end_headers()
            self.wfile.write(b'{"status": "error", "message": "Endpoint not found"}')


class Server:
    def __init__(self, HOST, TCP_PORT, app_state: AppState):
        self.app_state = app_state  # Store reference to shared state
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Prevents "address already in use" errors
        self.socket.bind((HOST, TCP_PORT))
        self.socket.listen()
        print(f"TCP Server is waiting for connections on {TCP_PORT}...")

    def listen(self):
        while True:
            cli_sock, addr = self.socket.accept()
            print(f"Received TCP connection from {addr}")
            Thread(target=self.handle_new_client, args=(cli_sock, addr, self.app_state)).start()

    @staticmethod
    def handle_register(args: list[str], cli_sock, app_state):
        """Handle registration from TCP side."""
        if not args:
            error_resp = json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Missing username"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        username = args[0]
        with app_state.id_lock:
            if username in app_state.users:
                error_resp = json.dumps({"status": "error", "code": "CONFLICT", "message": "User exists"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))

            else:
                new_user = User()
                new_user.username = username
                app_state.users[username] = new_user

                success_resp = json.dumps({"status": "ok", "operation": "register", "username": username}) + "\n"
                cli_sock.send(success_resp.encode('utf-8'))

    @staticmethod
    def handle_login(args: list[str], cli_sock, app_state):
        """Handle login from TCP side."""
        if not args:
            error_resp = json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Missing username"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        username = args[0]

        with app_state.id_lock:
            if username not in app_state.users:
                error_resp = json.dumps(
                    {"status": "error", "code": "NOT_FOUND", "message": "User does not exist"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))
            else:
                # Bind the socket to the user
                app_state.active_conns[cli_sock] = username

                # Basic login, no security since that's out of scope
                success_resp = json.dumps({"status": "ok", "operation": "login", "username": username}) + "\n"
                cli_sock.send(success_resp.encode('utf-8'))

    @staticmethod
    def handle_join(args: list[str], cli_sock, app_state):
        if not args:
            error_resp = json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Missing channel name"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        channel_name = args[0]

        with app_state.id_lock:
            # Identify the user
            username = app_state.active_conns.get(cli_sock)
            if not username:
                error_resp = json.dumps({"status": "error", "code": "NOT_AUTHENTICATED", "message": "You must login first"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))
                return

            # Validate channel format
            if not Channel.is_valid_channel(channel_name):
                error_resp = json.dumps({"status": "error", "code": "INVALID_CHANNEL", "message": "Invalid channel format"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))
                return

            # Create channel if it does not exist
            if channel_name not in app_state.channels:
                new_channel = Channel()
                new_channel.name = channel_name
                app_state.channels[channel_name] = new_channel

            # Add user to channel members
            app_state.channels[channel_name].members.add(username)
            app_state.users[username].channels.add(channel_name)

            success_resp = json.dumps({"status": "ok", "operation": "join", "channel": channel_name}) + "\n"
            cli_sock.send(success_resp.encode('utf-8'))

    @staticmethod
    def handle_leave(args: list[str], cli_sock, app_state):
        if not args:
            error_resp = json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Missing channel name"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        channel_name = args[0]

        with app_state.id_lock:
            # Check if user is logged in
            username = app_state.active_conns.get(cli_sock)
            if not username:
                error_resp = json.dumps({"status": "error", "code": "NOT_AUTHENTICATED", "message": "You must login first"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))
                return

            # Check if channel exists
            if channel_name not in app_state.channels:
                error_resp = json.dumps({"status": "error", "code": "NOT_FOUND", "message": "Channel does not exist"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))
                return

            # Remove the user from the channel
            # discard() instead of remove() to avoid crashing if the user wasn't in the set
            app_state.channels[channel_name].members.discard(username)
            app_state.users[username].channels.discard(channel_name)

            success_resp = json.dumps({"status": "ok", "operation": "leave", "channel": channel_name}) + "\n"
            cli_sock.send(success_resp.encode('utf-8'))

    @staticmethod
    def handle_logout(args: list[str], cli_sock, app_state):
        with app_state.id_lock:
            # Check if user is actually logged in
            if cli_sock in app_state.active_conns:
                username = app_state.active_conns.pop(cli_sock)
                success_resp = json.dumps({"status": "ok", "operation": "logout", "username": username}) + "\n"
                cli_sock.send(success_resp.encode('utf-8'))
            else:
                error_resp = json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Not logged in"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))

    @staticmethod
    def handle_list(args: list[str], cli_sock, app_state):
        if not args:
            error_resp = json.dumps(
                {"status": "error", "code": "BAD_REQUEST", "message": "Missing LIST arguments"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        sub_command = args[0].upper()

        # LIST CHANNELS
        if sub_command == "CHANNELS":
            with app_state.id_lock:
                channel_list = list(app_state.channels.keys())

            success_resp = json.dumps({"status": "ok", "channels": channel_list}) + "\n"
            cli_sock.send(success_resp.encode('utf-8'))

        # LIST USERS
        elif sub_command == "USERS":
            if len(args) < 2:
                error_resp = json.dumps(
                    {"status": "error", "code": "BAD_REQUEST", "message": "Missing channel name"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))
                return

            channel_name = args[1]
            with app_state.id_lock:
                if channel_name not in app_state.channels:
                    error_resp = json.dumps(
                        {"status": "error", "code": "NOT_FOUND", "message": "Channel does not exist"}) + "\n"
                    cli_sock.send(error_resp.encode('utf-8'))
                    return

                # Sets are not JSON serializable, convert to a list
                member_list = list(app_state.channels[channel_name].members)

            success_resp = json.dumps({"status": "ok", "channel": channel_name, "users": member_list}) + "\n"
            cli_sock.send(success_resp.encode('utf-8'))

        else:
            error_resp = json.dumps(
                {"status": "error", "code": "BAD_REQUEST", "message": "Unknown LIST command"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))

    @staticmethod
    def handle_send(args: list[str], cli_sock, app_state):
        if len(args) < 2:
            error_resp = json.dumps(
                {"status": "error", "code": "BAD_REQUEST", "message": "Missing channel or text"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        channel_name = args[0]
        # SEND splits incoming string with " ", need to reconstruct full message
        text = " ".join(args[1:])

        with app_state.id_lock:
            username = app_state.active_conns.get(cli_sock)
            if not username:
                cli_sock.send(
                    json.dumps({"status": "error", "code": "NOT_AUTHENTICATED", "message": "Not logged in"}) + "\n")
                return

            if channel_name not in app_state.channels:
                cli_sock.send(
                    json.dumps({"status": "error", "code": "NOT_FOUND", "message": "Channel does not exist"}) + "\n")
                return

            if username not in app_state.channels[channel_name].members:
                cli_sock.send(json.dumps(
                    {"status": "error", "code": "BAD_REQUEST", "message": "You must join the channel first"}) + "\n")
                return

            # Create and store the message
            msg = Message()
            msg.message_id = app_state.get_next_message_id()
            msg.channel = channel_name
            msg.username = username
            msg.text = text

            app_state.channels[channel_name].messages[msg.message_id] = msg

            success_resp = json.dumps({"status": "ok", "operation": "send", "message_id": msg.message_id}) + "\n"
            cli_sock.send(success_resp.encode('utf-8'))

    @staticmethod
    def handle_history(args: list[str], cli_sock, app_state):
        if not args:
            cli_sock.send(json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Missing channel"}) + "\n")
            return

        channel_name = args[0]
        limit = None
        if len(args) > 1 and args[1].isdigit():
            limit = int(args[1])

        with app_state.id_lock:
            if channel_name not in app_state.channels:
                cli_sock.send(
                    json.dumps({"status": "error", "code": "NOT_FOUND", "message": "Channel not found"}) + "\n")
                return

            # Get all messages as a list of dictionaries
            all_messages = [msg.to_dict() for msg in app_state.channels[channel_name].messages.values()]

            # Apply limit (ex. last 20 messages)
            if limit:
                all_messages = all_messages[-limit:]

            success_resp = json.dumps({"status": "ok", "channel": channel_name, "messages": all_messages}) + "\n"
            cli_sock.send(success_resp.encode('utf-8'))

    def handle_new_client(self, cli_sock, addr, app_state):
        COMMAND_HANDLERS = {
            "REGISTER": self.handle_register,
            "LOGIN": self.handle_login,
            "JOIN": self.handle_join,
            "LIST": self.handle_list,
            "LEAVE": self.handle_leave,
            "LOGOUT": self.handle_logout,
            "SEND": self.handle_send,
            "HISTORY": self.handle_history
        }

        while True:
            try:
                buffer = cli_sock.recv(1024).decode('utf-8')

                # Client disconnected normally
                if not buffer:
                    break

                lines = buffer.strip().split('\n')
                for line in lines:
                    if not line:
                        continue

                    parts = line.split(" ")
                    command = parts[0].upper()
                    args = parts[1:]  # Everything after the command

                    handler_function = COMMAND_HANDLERS.get(command)

                    if handler_function:
                        handler_function(args, cli_sock, app_state)
                    else:
                        error_resp = json.dumps({"status": "error", "code": "BAD_REQUEST", "message": "Unknown command"}) + "\n"
                        cli_sock.send(error_resp.encode('utf-8'))

            # Client crashed or force-closed
            except ConnectionResetError:
                break

        # Cleanup
        dc_user = None

        with app_state.id_lock:
            if cli_sock in app_state.active_conns:
                # Remove them from the active connections map and remember who they were
                dc_user = app_state.active_conns.pop(cli_sock)

            # TODO: Iterate through app_state.channels and remove disconnected_user from channel.members

        cli_sock.close()

        if dc_user:
            print(f"User '{dc_user}' disconnected {addr}.")
        else:
            print(f"Unauthenticated client disconnected from {addr}.")


def launch_server(HOST, PORT):
    app_state = AppState()
    server = Server(HOST, PORT, app_state)
    server.listen()


if __name__ == '__main__':
    if __name__ == '__main__':
        shared_state = AppState()

        # Launch TCP Server in a background thread
        tcp_server = Server('127.0.0.1', 9000, shared_state)
        t = Thread(target=tcp_server.listen, daemon=True)
        t.start()

        # Launch HTTP Server on the main thread
        # Use a lambda to inject the shared_state into the HTTPHandler
        handler = lambda *args, **kwargs: HTTPHandler(shared_state, *args, **kwargs)
        http_server = ThreadingHTTPServer(('127.0.0.1', 8080), handler)

        print("HTTP Server is waiting for connections on 8080...\n")
        try:
            http_server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down servers.")