
import asyncio
import sys
import os
import json
from typing import Optional, Dict, Any
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()  # 从 .env 文件加载环境变量

# --- 数据结构 ---
# 为每个连接创建一个简单的数据容器，方便管理
class ServerConnection:
    def __init__(self, session: ClientSession, exit_stack: AsyncExitStack, tools: list):
        self.session = session
        self.exit_stack = exit_stack
        self.tools = tools

class MCPManager:
    """
    一个可以管理多个 MCP 服务器连接的管理器。
    它负责连接、路由工具调用和生命周期管理。
    """
    def __init__(self):
        """
        初始化 MCPManager。
        """
        # self.connections 字典将服务器ID映射到 ServerConnection 对象
        self.connections: Dict[str, ServerConnection] = {}
        # OpenAI 客户端可以被所有会话共享
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        # 用于生成唯一工具名称的分隔符
        self.TOOL_NAME_SEPARATOR = "__"

    def _create_server_id(self, script_path: str) -> str:
        """根据脚本路径创建一个简短、唯一的服务器ID。"""
        return Path(script_path).stem.replace(" ", "_")

    async def connect_to_server(self, server_script_path: str):
        """
        连接到一个新的 MCP 服务器并存储其会话。

        Args:
            server_script_path: 服务器脚本的路径 (.py or .js)。
        """
        server_id = self._create_server_id(server_script_path)
        if server_id in self.connections:
            print(f"警告：已连接到服务器 '{server_id}'。跳过重复连接。")
            return

        print(f"正在连接到服务器 '{server_id}' (来自 {server_script_path})...")

        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("服务器脚本必须是 .py 或 .js 文件")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )
        
        # 每个连接都需要自己的 AsyncExitStack 来独立管理其生命周期
        exit_stack = AsyncExitStack()
        try:
            stdio_transport = await exit_stack.enter_async_context(stdio_client(server_params))
            stdio, write = stdio_transport
            session = await exit_stack.enter_async_context(ClientSession(stdio, write))

            await session.initialize()

            response = await session.list_tools()
            tools = response.tools
            
            # 存储连接信息
            self.connections[server_id] = ServerConnection(session, exit_stack, tools)
            print(f"✅ 成功连接到 '{server_id}'，可用工具: {[tool.name for tool in tools]}")

        except Exception as e:
            print(f"❌ 连接到 '{server_id}' 失败: {e}")
            # 如果连接失败，确保清理已创建的资源
            await exit_stack.aclose()
            raise

    def _get_all_tools_for_llm(self) -> list:
        """
        整合所有已连接服务器的工具，并为它们创建唯一的名称，
        以便语言模型可以区分它们。
        """
        all_tools = []
        for server_id, conn in self.connections.items():
            for tool in conn.tools:
                # 格式: {server_id}__{tool_name}
                unique_tool_name = f"{server_id}{self.TOOL_NAME_SEPARATOR}{tool.name}"
                all_tools.append({
                    "type": "function",
                    "function": {
                        "name": unique_tool_name,
                        "description": f"[来自服务器: {server_id}] {tool.description}",
                        "parameters": tool.inputSchema
                    }
                })
        return all_tools

    async def process_query(self, query: str) -> str:
        """
        处理用户查询。它会将所有可用工具发送给语言模型，
        并根据模型的选择将工具调用路由到正确的服务器。

        Args:
            query: 用户的查询。

        Returns:
            语言模型的最终响应。
        """
        if not self.connections:
            return "错误：未连接到任何服务器。请先连接服务器。"

        messages = [{"role": "user", "content": query}]
        available_tools = self._get_all_tools_for_llm()

        # 第一次调用语言模型
        completion = self.client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": os.getenv("YOUR_SITE_URL", ""),
                "X-Title": os.getenv("YOUR_SITE_NAME", ""),
            },
            model="anthropic/claude-3.5-sonnet",
            messages=messages,
            tools=available_tools,
            tool_choice="auto"
        )

        response_message = completion.choices[0].message
        final_text = []

        if response_message.content:
            final_text.append(response_message.content)

        # 处理工具调用（如果有）
        if response_message.tool_calls:
            messages.append(response_message)
            for tool_call in response_message.tool_calls:
                unique_function_name = tool_call.function.name
                
                # 解析唯一的工具名称以找到 server_id 和原始工具名称
                try:
                    server_id, original_function_name = unique_function_name.split(self.TOOL_NAME_SEPARATOR, 1)
                except ValueError:
                    print(f"错误：无法解析工具名称 '{unique_function_name}'")
                    continue
                
                function_args_str = tool_call.function.arguments
                
                if server_id not in self.connections:
                    print(f"错误：模型尝试调用一个不存在或未连接的服务器 '{server_id}' 的工具。")
                    continue

                print(f"▶️ 正在路由调用到服务器 '{server_id}' -> 工具 '{original_function_name}'...")
                final_text.append(f"[调用服务器 '{server_id}' 的工具 {original_function_name}，参数: {function_args_str}]")

                # 执行工具调用
                try:
                    # 使用 json.loads 而不是 eval，更安全
                    function_args = json.loads(function_args_str)
                    target_session = self.connections[server_id].session
                    result = await target_session.call_tool(original_function_name, function_args)
                    
                    # 将工具结果附加到消息历史中
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": unique_function_name,
                        "content": result.content,
                    })
                except Exception as e:
                    error_content = f"执行工具时出错: {e}"
                    print(f"❌ {error_content}")
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": unique_function_name,
                        "content": error_content,
                    })


            # 第二次调用语言模型，附带工具调用的结果
            second_completion = self.client.chat.completions.create(
                model="anthropic/claude-3.5-sonnet",
                messages=messages,
            )
            final_text.append(second_completion.choices[0].message.content)

        return "\n".join(final_text)

    async def cleanup(self):
        """
        清理所有资源并关闭所有服务器连接。
        """
        print("\n正在关闭所有服务器连接...")
        for server_id, conn in self.connections.items():
            try:
                await conn.exit_stack.aclose()
                print(f"🔌 连接 '{server_id}' 已关闭。")
            except Exception as e:
                print(f"关闭连接 '{server_id}' 时出错: {e}")
        self.connections.clear()


async def chat_loop(manager: MCPManager):
    """
    运行主交互式聊天循环。
    """
    print("\n多服务器 MCP 客户端已启动！")
    print("输入您的查询，或输入 'quit' 退出。")

    while True:
        try:
            query = input("\n查询: ").strip()

            if query.lower() == 'quit':
                break

            response = await manager.process_query(query)
            print("\n" + response)

        except (KeyboardInterrupt, EOFError):
            print("\n检测到退出信号。")
            break
        except Exception as e:
            print(f"\n发生错误: {str(e)}")


async def main():
    """
    主函数，用于运行客户端。
    """
    if len(sys.argv) < 2:
        print("用法: python client.py <path_to_server1> [<path_to_server2> ...]")
        sys.exit(1)

    manager = MCPManager()
    server_scripts = sys.argv[1:]
    
    # 并发连接到所有指定的服务器
    connect_tasks = [manager.connect_to_server(script) for script in server_scripts]
    await asyncio.gather(*connect_tasks, return_exceptions=True)

    if not manager.connections:
        print("\n未能连接到任何服务器。正在退出。")
        sys.exit(1)

    try:
        await chat_loop(manager)
    finally:
        await manager.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
