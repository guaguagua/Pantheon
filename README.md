1. https://modelcontextprotocol.io/quickstart/server
# Create a new directory for our project
uv init weather
cd weather

# Create virtual environment and activate it
uv venv
.venv\Scripts\activate

# Install dependencies
uv add mcp[cli] httpx

# Create our server file
new-item weather.py

2. vscode cline config
```json
"weather": {
  "command": "uv",
  "args": [
    "--directory",
    "D:\\tmp\\mcp_learn\\weather",
    "run",
    "mcp_server.py"
  ]
}
```

3. uv run mcp_client.py servers.json
- make .env file to save key

4. mcp command server
- https://glama.ai/mcp/servers/@alxspiker/Windows-Command-Line-MCP-Server?locale=zh-CN
  - git clone https://github.com/alxspiker/Windows-Command-Line-MCP-Server.git
  - cd Windows-Command-Line-MCP-Server
  - npm install
  - npm run build

5. promote
- 执行 bat脚本的方式
  -  调用服务器 'windows-cmd' 的工具 execute_command，参数: {"command": "run.bat", "workingDir": "D:\\tmp\\mcp_learn\\weather\\servers\\ngspice"}
- 操作系统
- example
  - 命令执行

  