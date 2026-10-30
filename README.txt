PA2: Distributed Mini Discord with Multiple Local Processes
Galit Bolotin
10/29/26

System Architecture and Process Configuration
------------------------------------------------
Runs as three independent local processes (Servers A, B, and C) on localhost.
These servers are configured on startup in servers.json:
- Server A: HTTP port 8101, TCP port 9101, Priority 1
- Server B: HTTP port 8102, TCP port 9102, Priority 2
- Server C: HTTP port 8103, TCP port 9103, Priority 3 (Initial Cluster Leader)

Each server maintains its own in-memory application state (AppState class) and runs 3 concurrent pieces:
1. ThreadingHTTPServer for client-facing HTTP JSON requests
2. TCP socket for JSON server-to-server communication
3. Background heartbeat monitor thread for peer liveness checking and priority-based leader selection

Dependencies and Setup Instructions
--------------------------------------
- Python 3.10+ with standard libraries
- Check that dependencies in requirements.txt are installed
- Check that run.sh (and tests/pa2_test.sh, if you'd like to run the test script) has execute permissions.
  If not, run chmod +x <script file>

Startup Command
------------------
Start all three server processes concurrently using: ./run.sh --config servers.json

User-Sharing Mechanism
-------------------------
User registrations (POST /users) are serialized through the current cluster leader (for example, Server C initially) with the TCP socket USER_SYNC ("action": "CREATE") message.
The leader checks username format and uniqueness under its reentrant lock (RLock) to prevent concurrent duplicate registrations across different servers.
Once the user is created, the leader broadcasts a USER_SYNC ("action": "REPLICATE") message over TCP to all healthy peers so every server holds the full user set in local memory.
GET /users reads directly from local memory.

Channel Ownership and Request-Forwarding Design
--------------------------------------------------
Default channels are established on all servers at startup with fixed ownership.
- #cs249:       Primary = A, Backup = B
- #distributed: Primary = B, Backup = C
- #systems:     Primary = C, Backup = A

When a client sends a channel request (JOIN, LEAVE, USERS, SEND, HISTORY) to any server:
- If the receiving server is the channel's primary server, it processes the operation locally.
- If the receiving server is the channel's backup server and the operation is HISTORY (GET /channels/{channel}/messages), it serves the locally replicated messages directly from its backup state and logs the local backup read.
- Otherwise, the receiving server forwards the request over TCP to the channel's primary server using a newline-terminated JSON message:
  Request:  {"type": "FORWARD", "request_id": "<id>", "operation": "JOIN|LEAVE|USERS|SEND|HISTORY", "channel": "#cs249", "username": "alice", "text": "...", "limit": 20, "from": "C"}
  Response: {"type": "FORWARD_RESPONSE", "request_id": "<id>", "status": "ok|error", "http_code": 200, "body": {...}, "message_id": 1}
- If the primary server is unavailable, the receiving server returns "503 Service Unavailable" instead of crashing.

Replication Design and Acknowledgement Behavior
--------------------------------------------------
Message replication uses synchronous primary-to-backup replication with fallback.
- When a channel's primary server creates a new message and assigns its per-channel sequential message_id, it also immediately sends a REPLICATE message over TCP to the channel's designated backup server:
  {"type": "REPLICATE", "channel": "#cs249", "message": {"message_id": 1, "username": "alice", "text": "Hello"}}
- The backup server stores the message (ignoring duplicate message_ids) and responds with:
  {"type": "REPLICATE_ACK", "channel": "#cs249", "message_id": 1, "from": "B"}
- The primary server waits up to 2 seconds for REPLICATE_ACK before returning "201 Created" to the HTTP client, guaranteeing that the backup replica is always kept updated. If the backup server is unavailable or times out, the primary server logs a warning and still returns "201 Created" to remain operational.

Heartbeat Failure-Detection Design
-------------------------------------
A dedicated background thread on each server runs a heartbeat ping every 2 seconds.
- Sends {"type": "PING", "from": "<self>", "sequence": <seq>} over TCP to each peer and waits up to 2 seconds for {"type": "PONG", "from": "<peer>", "sequence": <seq>}.
- A valid PONG resets that peer's missed heartbeat counter to 0 and marks it alive.
- A timeout or connection error increments the peer's missed heartbeat counter. After 3 consecutive missed heartbeats, the peer is marked unavailable ("alive": false), which is reflected in GET /servers.

Leader-Selection Mechanism and Announcement Messages
-------------------------------------------------------
- Each server knows the fixed priorities of all servers from servers.json (A=1, B=2, C=3). Server C is the leader on initialization.
- When the current leader is marked unavailable after 3 missed heartbeats, each surviving server deterministically selects the highest priority live server as the new cluster leader.
- If the surviving server selects itself as the new leader (e.g., Server B when Server C fails), it sends a TCP announcement to all other surviving peers:
  {"type": "LEADER_ANNOUNCE", "leader": "B"}
- Peers receiving LEADER_ANNOUNCE update their current_leader state and reply with LEADER_ANNOUNCE_ACK. GET /leader reports the selected leader.
- Channel primary ownership is separate from cluster leadership.

Concurrency and Shared-State Handling
----------------------------------------
- Each server uses ThreadingHTTPServer, per-connection threads for TCP, and background heartbeat thread concurrently.
- All reads and writes to shared in-memory data structures (users, channels, members, messages, message_id counters, peer liveness, current_leader) are protected by a reentrant lock (threading.RLock).
- Outbound TCP socket calls (forwarding, replication, heartbeats, leader announcements) are performed outside the lock with a 2s socket timeout so slow/failed peers don't deadlock or block local request processing.

Testing Instructions
------------------------
1. Start the three servers in a terminal window:
   ./run.sh --config servers.json
2. Run the automated verification script in a second terminal window:
   ./tests/test_pa2.sh
3. To test failure detection and leader election (Steps 7 & 8):
   kill -9 $(lsof -ti :8103)
   Wait for the 3 missed heartbeats, then run:
   curl "http://127.0.0.1:8101/servers"
   curl "http://127.0.0.1:8101/leader"
   curl "http://127.0.0.1:8102/leader"

Demo Video Timestamps (Filename: cs249_pa2_demo.mp4)
-------------------------------------------------------
- Start All Three Servers:                 1:26
- Verify Health:                           1:57
- Verify Initial Process & Leader Status:  2:20
- Demonstrate User Sharing:                2:55
- Demonstrate Request Forwarding:          4:39
- Demonstrate Message Replication:         8:00
- Demonstrate Failure Detection:           8:12
- Demonstrate Leader Selection:            8:50
