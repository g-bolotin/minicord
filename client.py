import socket
from threading import Thread
import os

class Client:
    def __init__(self, HOST, TCP_PORT):
        self.socket = socket.socket()
        self.socket.connect((HOST, TCP_PORT))

        username = input("Enter a unique username to register: ") # TODO: follow script args input using curl and other specs
        self.register(username)

    def register(self, username: str):
        """Format: REGISTER <username>\n"""
        command = f"REGISTER {username}\n"
        self.socket.send(command.encode('utf-8'))
        response = self.socket.recv(1024).decode('utf-8')
        print("Server replied:", response)


if __name__ == '__main__':
    Client('127.0.0.1', 9000)

