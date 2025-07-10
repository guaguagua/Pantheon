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
    def __init__(self, role_config: Optional[Dict[str, Any]] = None):
        self.connections: Dict[str, ServerConnection] = {}
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        self.TOOL_NAME_SEPARATOR = "__"
        self.messages: List[Dict[str, Any]] = []
        self.role_config = role_config or {}
        self._initialize_history()

    def _initialize_history(self, role_name: str = "electronic_expert"):
        """设置或重置对话历史记录的初始状态。"""
        # 从角色配置中获取系统消息，如果没有配置则使用默认值
        if self.role_config and role_name in self.role_config:
            role_info = self.role_config[role_name]
            system_message = {
                "role": role_info.get("role", "system"),
                "content": role_info.get("content", "你是一个有用的助手。你可以使用提供的工具来回答问题。")
            }
        else:
            # 默认系统消息
            system_message = {
                "role": "system", 
                "content": "你是一个有用的助手。你可以使用提供的工具来回答问题。"
            }
        
        self.messages = [system_message]
        print(f"\n[对话历史已重置，使用角色: {role_name}]")

    def clear_history(self):
        """外部调用的方法，用于清空对话历史。"""
        self._initialize_history()

    def set_role(self, role_name: str):
        """设置新的角色并重置对话历史。"""
        self._initialize_history(role_name)

    async def connect_to_server(self, server_id: str, config: Dict[str, Any]):
        """
        根据提供的配置连接到一个新的 MCP 服务器。

        Args:
            server_id (str): 服务器的唯一标识符 (来自 JSON 的 key)。
            config (Dict[str, Any]): 该服务器的配置对象 (来自 JSON 的 value)。
        """
        if server_id in self.connections:
            print(f"警告：已连接到服务器 '{server_id}'。跳过重复连接。")
            return

        # 检查 "command" 和 "args" 是否存在
        command = config.get("command")
        args = config.get("args")

        if not command or not isinstance(args, list):
            print(f"❌ 配置 '{server_id}' 无效：缺少 'command' 或 'args'。跳过此服务器。")
            return
            
        print(f"正在根据配置连接到服务器 '{server_id}'...")
        print(f"  ▶️  Command: {command} {' '.join(args)}")

        server_params = StdioServerParameters(
            command=command,
            args=args,
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
            # 在 gather 中，一个任务的异常不会停止其他任务，所以这里只打印错误
            # 如果需要一个失败就全部停止，则需要更复杂的处理

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
        # 移除检查，允许在没有服务器连接的情况下继续，此时工具列表为空。
        # if not self.connections:
        #     return "错误：未连接到任何服务器。请先连接服务器。"

        self.messages.append({"role": "user", "content": query})
        available_tools = self._get_all_tools_for_llm()

        # 如果没有可用的工具，则不向 LLM 发送 tools 参数
        tool_kwargs = {}
        if available_tools:
            tool_kwargs['tools'] = available_tools
            tool_kwargs['tool_choice'] = "auto"

        completion = self.client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": os.getenv("YOUR_SITE_URL", ""),
                "X-Title": os.getenv("YOUR_SITE_NAME", ""),
            },
            model="anthropic/claude-3.5-sonnet",
            messages=self.messages,
            **tool_kwargs
        )

        response_message = completion.choices[0].message
        self.messages.append(response_message)
        final_text = []

        if response_message.content:
            final_text.append(response_message.content)

        if response_message.tool_calls:
            tool_results = []
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

            second_completion = self.client.chat.completions.create(
                model="anthropic/claude-3.5-sonnet",
                messages=self.messages,
            )
            final_response_message = second_completion.choices[0].message
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


def handle_command(query: str, manager: MCPManager) -> str:
    """
    处理用户命令。
    
    Args:
        query (str): 用户输入的查询
        manager (MCPManager): MCP管理器实例
        
    Returns:
        str: 返回命令处理结果
            - 'quit': 退出程序
            - 'continue': 继续循环，不处理查询
            - 'process': 处理为普通查询
    """
    if not query.startswith('/'):
        return 'process'
    
    command_parts = query[1:].split(' ', 1)
    command = command_parts[0].lower()
    
    if command == 'quit':
        return 'quit'
    elif command == 'clear':
        manager.clear_history()
        return 'continue'
    elif command == 'role':
        if len(command_parts) > 1:
            role_name = command_parts[1].strip()
            if role_name:
                manager.set_role(role_name)
            else:
                print("请指定角色名称，例如: /role technical")
        else:
            print("请指定角色名称，例如: /role technical")
        return 'continue'
    else:
        print(f"未知命令: /{command}")
        print("可用命令: /quit, /clear, /role <角色名>")
        return 'continue'


async def chat_loop(manager: MCPManager):
    """
    运行主交互式聊天循环。
    """
    print("\n多服务器 MCP 客户端已启动！")
    print("输入您的查询，或输入 '/clear' 重置对话，'/role <角色名>' 切换角色，或输入 '/quit' 退出。")

    while True:
        try:
            query = input("\n查询: ").strip()

            # 处理命令
            command_result = handle_command(query, manager)
            if command_result == 'quit':
                break
            elif command_result == 'continue':
                continue

            # 处理普通查询
            response = await manager.process_query(query)
            print("\n" + response)

        except (KeyboardInterrupt, EOFError):
            print("\n检测到退出信号。")
            break
        except Exception as e:
            print(f"\n发生错误: {str(e)}")


def load_role_config(config_path: str = "roles.json") -> Dict[str, Any]:
    """
    加载角色配置文件。
    
    Args:
        config_path (str): 角色配置文件路径
        
    Returns:
        Dict[str, Any]: 角色配置字典，如果加载失败则返回空字典
    """
    role_config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                role_config = json.load(f)
            print(f"✅ 已加载角色配置文件: {config_path}")
            print(f"可用角色: {list(role_config.keys())}")
        except json.JSONDecodeError as e:
            print(f"警告：解析角色配置文件失败: {e}，将使用默认配置")
    else:
        print(f"ℹ️ 角色配置文件 '{config_path}' 不存在，将使用默认配置")
    
    return role_config


def load_server_config(config_path: str = "servers.json") -> Dict[str, Any]:
    """
    加载MCP服务器配置文件。
    
    Args:
        config_path (str): 服务器配置文件路径
        
    Returns:
        Dict[str, Any]: 服务器配置字典，如果加载失败则返回空字典
    """
    server_configs = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            server_configs = config_data.get("mcpServers", {})
            print(f"✅ 已加载服务器配置文件: {config_path}")
            if server_configs:
                print(f"可用服务器: {list(server_configs.keys())}")
            else:
                print("警告：配置文件中未找到 'mcpServers' 或其为空。")
        except json.JSONDecodeError as e:
            print(f"警告：解析服务器配置文件失败: {e}，将使用空配置")
    else:
        print(f"ℹ️ 服务器配置文件 '{config_path}' 不存在，将使用空配置")
    
    return server_configs


async def connect_to_servers(manager: MCPManager, server_configs: Dict[str, Any]):
    """
    连接到所有配置的MCP服务器。
    
    Args:
        manager (MCPManager): MCP管理器实例
        server_configs (Dict[str, Any]): 服务器配置字典
    """
    connect_tasks = []
    if server_configs:
        for server_id, config in server_configs.items():
            if config.get("disabled", False):
                print(f"ℹ️ 服务器 '{server_id}' 已被禁用，跳过。")
                continue
            connect_tasks.append(manager.connect_to_server(server_id, config))
        
        if connect_tasks:
            await asyncio.gather(*connect_tasks)

    # 如果没有任何连接，只打印警告而不是退出
    if not manager.connections:
        print("\n⚠️  未能连接到任何 MCP 服务器。将以无工具模式继续。")


async def main():
    """
    主函数，用于运行客户端。
    """
    # 加载配置
    role_config = load_role_config()
    server_configs = load_server_config()
    
    # 创建管理器
    manager = MCPManager(role_config)
    
    # 连接到服务器
    await connect_to_servers(manager, server_configs)

    try:
        await chat_loop(manager)
    finally:
        await manager.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
