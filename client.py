import socket
import sys


class Client:
    def __init__(self, HOST, TCP_PORT):
        self.socket = socket.socket()
        try:
            self.socket.connect((HOST, TCP_PORT))
        except ConnectionRefusedError:
            print("Could not connect to server.")
            sys.exit(1)

        print("Connected to Minicord TCP Interface.")
        print("Commands: REGISTER, LOGIN, QUIT")
        self.command_loop()

    def command_loop(self):
        while True:
            try:
                # Get raw input from the user
                cli_input = input("> ")
                if not cli_input.strip():
                    continue

                if cli_input.strip().upper() == "QUIT":
                    self.socket.close()
                    break

                # Format with newline and send
                command = f"{cli_input}\n"
                self.socket.send(command.encode('utf-8'))

                # Wait for the server's response
                response = self.socket.recv(1024).decode('utf-8')
                if not response:
                    print("Server closed connection.")
                    break

                print("Server:", response.strip())

            except KeyboardInterrupt:
                self.socket.close()
                break


if __name__ == '__main__':
    Client('127.0.0.1', 9000)