import asyncio
import sys
import os
import json
from typing import Optional, Dict, Any, List
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
    它负责连接、路由工具调用、管理对话历史和生命周期。
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
        # 对话历史记录
        self.messages: List[Dict[str, Any]] = []
        self._initialize_history()

    def _initialize_history(self):
        """设置或重置对话历史记录的初始状态。"""
        self.messages = [
            {"role": "system", "content": "你是一个有用的助手。你可以使用提供的工具来回答问题。"}
        ]
        print("\n[对话历史已重置]")

    def clear_history(self):
        """外部调用的方法，用于清空对话历史。"""
        self._initialize_history()

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
        
        exit_stack = AsyncExitStack()
        try:
            stdio_transport = await exit_stack.enter_async_context(stdio_client(server_params))
            stdio, write = stdio_transport
            session = await exit_stack.enter_async_context(ClientSession(stdio, write))

            await session.initialize()
            response = await session.list_tools()
            tools = response.tools
            
            self.connections[server_id] = ServerConnection(session, exit_stack, tools)
            print(f"✅ 成功连接到 '{server_id}'，可用工具: {[tool.name for tool in tools]}")

        except Exception as e:
            print(f"❌ 连接到 '{server_id}' 失败: {e}")
            await exit_stack.aclose()
            raise

    def _get_all_tools_for_llm(self) -> list:
        """
        整合所有已连接服务器的工具，并为它们创建唯一的名称。
        """
        all_tools = []
        for server_id, conn in self.connections.items():
            for tool in conn.tools:
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
        处理用户查询，维护对话历史。
        """
        if not self.connections:
            return "错误：未连接到任何服务器。请先连接服务器。"

        # 将当前用户查询添加到历史记录中
        self.messages.append({"role": "user", "content": query})
        available_tools = self._get_all_tools_for_llm()

        completion = self.client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": os.getenv("YOUR_SITE_URL", ""),
                "X-Title": os.getenv("YOUR_SITE_NAME", ""),
            },
            model="anthropic/claude-3.5-sonnet",
            messages=self.messages, # 使用完整的历史记录
            tools=available_tools,
            tool_choice="auto"
        )

        response_message = completion.choices[0].message
        final_text = []

        # 将模型的响应（包括思考过程和工具调用请求）添加到历史记录
        self.messages.append(response_message)

        if response_message.content:
            final_text.append(response_message.content)

        if response_message.tool_calls:
            for tool_call in response_message.tool_calls:
                unique_function_name = tool_call.function.name
                
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

                try:
                    function_args = json.loads(function_args_str)
                    target_session = self.connections[server_id].session
                    result = await target_session.call_tool(original_function_name, function_args)
                    
                    # 将工具执行结果添加到历史记录
                    self.messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": unique_function_name,
                        "content": result.content,
                    })
                except Exception as e:
                    error_content = f"执行工具时出错: {e}"
                    print(f"❌ {error_content}")
                    self.messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": unique_function_name,
                        "content": error_content,
                    })

            # 第二次调用语言模型，此时 self.messages 已包含工具结果
            second_completion = self.client.chat.completions.create(
                model="anthropic/claude-3.5-sonnet",
                messages=self.messages,
            )
            final_response_message = second_completion.choices[0].message
            # 将最终的文本响应添加到历史记录和输出中
            self.messages.append(final_response_message)
            final_text.append(final_response_message.content)

        return "\n".join(filter(None, final_text))

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
    print("输入您的查询，或输入 '/clear' 重置对话，或输入 'quit' 退出。")

    while True:
        try:
            query = input("\n查询: ").strip()

            if query.lower() == 'quit':
                break
            
            if query.lower() == '/clear':
                manager.clear_history()
                continue

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

