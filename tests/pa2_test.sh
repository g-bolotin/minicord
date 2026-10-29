#!/bin/bash
# Before running, start the server cluster in a terminal window with ./run.sh --config servers.json

echo "=== Step 2: Verify Health (Server A, B, C respectively) ==="
curl -s "http://127.0.0.1:8101/health"; echo ""
curl -s "http://127.0.0.1:8102/health"; echo ""
curl -s "http://127.0.0.1:8103/health"; echo ""

echo ""
echo "=== Step 3: Verify Initial Process and Leader Status (Server A, B) ==="
curl -s "http://127.0.0.1:8101/servers"; echo ""
curl -s "http://127.0.0.1:8101/leader"; echo ""
curl -s "http://127.0.0.1:8102/leader"; echo ""

echo ""
echo "=== Step 4: Demonstrate User Sharing (Users on servers A & B show on C)==="
curl -s -X POST "http://127.0.0.1:8101/users" \
  -H "Content-Type: application/json" \
  -d '{"username": "alice"}'; echo ""
curl -s -X POST "http://127.0.0.1:8102/users" \
  -H "Content-Type: application/json" \
  -d '{"username": "bob"}'; echo ""
echo "Users on Server C (8103):"
curl -s "http://127.0.0.1:8103/users"; echo ""

echo ""
echo "=== Step 5: Demonstrate Request Forwarding ==="
echo "(Server C -> Primary Server A for #cs249)"
curl -s -X POST "http://127.0.0.1:8103/channels/%23cs249/members" \
  -H "X-User: alice"; echo ""
curl -s -X POST "http://127.0.0.1:8103/channels/%23cs249/messages" \
  -H "Content-Type: application/json" \
  -H "X-User: alice" \
  -d '{"text": "This request was forwarded"}'; echo ""

echo ""
echo "=== Step 6: Demonstrate Message Replication (on backup server B) ==="
curl -s "http://127.0.0.1:8102/channels/%23cs249/messages"; echo ""

echo ""
echo "=== Step 7 & 8: Failure Detection & Leader Selection ==="
echo "To test Steps 7 & 8 interactively:"
echo "  1. Run: kill -9 \$(lsof -ti :8103)"
echo "  2. Wait 6 seconds for 3 missed heartbeats"
echo "  3. Run: curl \"http://127.0.0.1:8101/servers\" && curl \"http://127.0.0.1:8102/servers\""
echo "  4. Run: curl \"http://127.0.0.1:8101/leader\"  && curl \"http://127.0.0.1:8102/leader\""