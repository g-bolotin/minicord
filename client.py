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
        print("---- Commands ----")
        print("REGISTER <name> - Create user\n"
              "LOGIN <name> - login with existing user\n"
              "JOIN <channel_name> - join channel, must prefix name with #\n"
              "SEND <channel> <message> - send message to specified channel\n"
              "HISTORY <channel> [limit] - show message history, optional limit num messages\n"
              "LIST [users <channel> | channels] - list all users belonging to a channel, or list all channels \n"
              "LEAVE <channel> - leave channel\n"
              "LOGOUT - log out from current user\n"
              "QUIT - stop client\n")
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