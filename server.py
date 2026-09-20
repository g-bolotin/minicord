# Server listens to all clients. A client sends a message, and this message is broadcast to all other clients.
# Message history need to be stored in chats that clients can pull up any time.
import socket
import threading
from threading import Thread
import re
import json

USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
CHANNEL_PATTERN = re.compile(r'^#[a-zA-Z0-9_-]+$')

class User:
    def __init__(self):
        self.username = "" # TODO: Usernames may contain letters, digits, hyphens, and underscores. Must be unique.
        self.channels = set()

    @staticmethod
    def is_valid_username(username: str) -> bool:
        """Validates usernames contain only letters, digits, hyphens, and underscores."""
        return bool(USERNAME_PATTERN.fullmatch(username))


class Channel:
    def __init__(self):
        self.name = ""  # TODO: Channel names must begin with # and may contain letters, digits, hyphens, and underscores. Must be unique.
        self.messages: dict[int, Message] = {}  # MID : Message
        self.members = set()

    @staticmethod
    def is_valid_channel(channel_name: str) -> bool:
        """Validates channel names begin with # and contain letters, digits, hyphens, and underscores."""
        return bool(CHANNEL_PATTERN.fullmatch(channel_name))


class Message:
    def __init__(self):
        self.mid = 0
        self.channel_name = ""
        self.username = ""
        self.content = ""
        self.timestamp = 0


class AppState:
    """Maintains local application state rather than external database."""
    def __init__(self):
        self._message_id_counter = 1
        self.id_lock = threading.Lock()

        self.users: dict[str, User] = {} # username : User
        self.channels: dict[str, Channel] = {} # channel name : Channel

    def get_next_message_id(self):
        """Message UIDs are determined at app run and incremental."""
        with self.id_lock:
            current_id = self._message_id_counter
            self._message_id_counter += 1
            return current_id


class Server:
    # TODO: Is there a better way to broadcast to clients rather than iteratively?
    clients: list[User] = []  # List of all clients connected to the server

    def __init__(self, HOST, TCP_PORT):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((HOST, TCP_PORT))
        self.socket.listen()  # can put a number here to limit max connections
        print(f"Server is waiting for connections on {TCP_PORT}...\n")

    def listen(self):
        while True:
            cli_sock, addr = self.socket.accept()
            print(f"Received connection from {addr}")

            # Client sends name
            # TODO: Client sends over information, server creates new user object and stores in clients
            cli_name = cli_sock.recv(2048).decode()
            client = {'cli_name': cli_name, 'cli_sock': cli_sock}

            # TODO: Create a chatroom or join an existing chatroom
            Server.clients.append(client)
            Thread(target=self.handle_new_client, args=(client,)).start()

    @staticmethod
    def handle_register(args: list[str], cli_sock, app_state):
        if not args:
            error_resp = json.dumps({"status": "error", "code": "INVALID_USERNAME", "message": "Missing username"}) + "\n"
            cli_sock.send(error_resp.encode('utf-8'))
            return

        username = args[0]
        with app_state.id_lock:
            if username in app_state.users:
                error_resp = json.dumps({"status": "error", "code": "INVALID_USERNAME", "message": "User exists"}) + "\n"
                cli_sock.send(error_resp.encode('utf-8'))

            else:
                new_user = User()
                new_user.username = username
                app_state.users[username] = new_user

                success_resp = json.dumps({"status": "ok", "operation": "register", "username": username}) + "\n"
                cli_sock.send(success_resp.encode('utf-8'))

    def handle_login(self, args: list[str], cli_sock, app_state):
        # TODO: Implement LOGIN logic here
        pass

    def handle_join(self, args: list[str], cli_sock, app_state):
        # TODO: Implement JOIN logic here
        pass

    def handle_leave(self, args: list[str], cli_sock, app_state):
        # TODO: Implement LEAVE logic here
        pass

    def handle_new_client(self, cli_sock, app_state):
        COMMAND_HANDLERS = {
            "REGISTER": self.handle_register,
            "LOGIN": self.handle_login,
            "JOIN": self.handle_join
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
        cli_sock.close()



    def broadcast(self, sender, message, svr_msg=False):
        for cli in self.clients:
            cli_sock = cli['cli_sock']
            cli_name = cli['cli_name']
            if cli_name != sender or svr_msg:
                cli_sock.send(message.encode())

def launch_server(HOST, PORT):
    server = Server(HOST, PORT)
    server.listen()


if __name__ == '__main__':
    # TODO: Parse arguments
    t = Thread(target=launch_server, args=('127.0.0.1', 9000))
    t.daemon = True
    t.start()