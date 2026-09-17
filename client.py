import socket
from threading import Thread
import os

class Client:
    def __init__(self, HOST, TCP_PORT):
        self.socket = socket.socket()
        self.socket.connect((HOST, TCP_PORT))
        # TODO: check for uniqueness, ask for credentials, store in Firebase for user auth
        self.name = input("Enter your name: ")
        self.talk()

    def talk(self):
        # TODO: proper user authentication
        self.socket.send(self.name.encode())
        Thread(target=self.receive).start()
        self.send()

# TODO: integrate web interface for message sending/viewing
    def receive(self):
        while True:
            server_msg = self.socket.recv(1024).decode()
            if not server_msg.strip():
                os._exit(0) # not sys.exit for a child process, don't want to flush buffers before main process is done
            print("\033[1;31;40m]" + server_msg + "\033[0m") # Colors server text red

    def send(self):
        while True:
            cli_input = input("")
            cli_msg = self.name + ": " + cli_input
            self.socket.send(cli_msg.encode())


if __name__ == '__main__':
    Client('127.0.0.1', 9000)

