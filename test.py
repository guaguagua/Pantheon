import sys
import asyncio
import json
from typing import Any, Dict

async def send_mcp_request(method: str, params: Dict[str, Any]) -> None:
    request = {
        "jsonrpc": "2.0",
        "id": "test",
        "method": method,
        "params": params
    }
    print(json.dumps(request))
    sys.stdout.flush()

    # Read response from stdout (from server)
    while True:
        line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        try:
            response = json.loads(line.strip())
            if "result" in response:
                print("Server Response:\n", response["result"])
            elif "error" in response:
                print("Error from server:", response["error"])
            break
        except json.JSONDecodeError:
            continue  # Skip non-JSON lines (like logging)

async def test_client():
    print("Testing get_forecast for latitude=37.7749, longitude=-122.4194")
    await send_mcp_request("get_forecast", {"latitude": 37.7749, "longitude": -122.4194})

    print("\nTesting get_alerts for state=CA")
    await send_mcp_request("get_alerts", {"state": "CA"})

if __name__ == "__main__":
    asyncio.run(test_client())
