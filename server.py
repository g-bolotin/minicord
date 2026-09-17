# Server listens to all clients. A client sends a message, and this message is broadcast to all other clients.
# Message history need to be stored in chats that clients can pull up any time.
import sys
from flask import Flask
import socket
from threading import Thread

class Server:
    # TODO: Is there a better way to broadcast to clients rather than iteratively?
    clients = [] # List of all clients connected to the server

    def __init__(self, HOST, TCP_PORT):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((HOST, TCP_PORT))
        self.socket.listen()  # can put a number here to limit max connections
        print(f"Server is waiting for connections on {TCP_PORT}...\n")

    def listen(self):
        while True:
            cli_sock, addr = self.socket.accept()
            print(f"Received connection from {addr}")

            # TODO: Store login info in a database, have basic login functionality (firebase?)
            # Client sends name
            cli_name = cli_sock.recv(1024).decode()
            client = {'cli_name': cli_name, 'cli_sock': cli_sock}

            # TODO: Create a chatroom or join an existing chatroom
            Server.clients.append(client)
            Thread(target=self.handle_new_client, args=(client,)).start()

    def handle_new_client(self, client):
        cli_name = client['cli_name']
        cli_sock = client['cli_sock']

        # Broadcast new client connection
        self.broadcast(cli_name, "Welcome " + cli_name + " to the server!\n", svr_msg=True)

        # TODO: integrate web interface for message sending/viewing
        while True:
            # Listen for messages, broadcast to all clients in the chat
            cli_msg = cli_sock.recv(1024).decode()

            # If message is /leave, remove client and close socket
            if cli_msg.strip() == cli_name + ": /leave" or not cli_msg.strip():
                self.broadcast(cli_name, cli_name + " has left the server.\n", svr_msg=True)
                Server.clients.remove(client)
                cli_sock.close()
                break
            else:
                self.broadcast(cli_name, cli_msg)

    def broadcast(self, sender, message, svr_msg=False):
        for cli in self.clients:
            cli_sock = cli['cli_sock']
            cli_name = cli['cli_name']
            if cli_name != sender or svr_msg:
                cli_sock.send(message.encode())

# Flask Server (Web Interface)
app = Flask(__name__)


@app.route('/')
def hello():
    return sys.argv

def launch_server(HOST, PORT):
    server = Server(HOST, PORT)
    server.listen()


if __name__ == '__main__':
    # TODO: Parse arguments
    t = Thread(target=launch_server, args=('127.0.0.1', 9000))
    t.daemon = True
    t.start()
    app.run(debug=True, use_reloader=False)