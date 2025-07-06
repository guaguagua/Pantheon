import asyncio
import sys
import os
from typing import Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()  # load environment variables from .env

class MCPClient:
    """
    A client for interacting with an MCP server and using language models via OpenRouter.
    """
    def __init__(self):
        """
        Initializes the MCPClient.
        """
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        # Initialize the OpenAI client to connect to OpenRouter
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )

    async def connect_to_server(self, server_script_path: str):
        """
        Connects to an MCP server defined by a script.

        Args:
            server_script_path: The file path to the server script (.py or .js).
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        # Establish connection to the server
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        await self.session.initialize()

        # List available tools from the server
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:", [tool.name for tool in tools])

    async def process_query(self, query: str) -> str:
        """
        Processes a user query by interacting with the language model and executing tools.

        Args:
            query: The user's query.

        Returns:
            The final response from the language model.
        """
        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        # Format tools for the OpenAI API
        available_tools = [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        } for tool in response.tools]

        # First call to the language model
        completion = self.client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": os.getenv("YOUR_SITE_URL", ""), # Optional
                "X-Title": os.getenv("YOUR_SITE_NAME", ""),      # Optional
            },
            model="anthropic/claude-3.5-sonnet", # Using a powerful model available on OpenRouter
            messages=messages,
            tools=available_tools,
            tool_choice="auto"
        )

        response_message = completion.choices[0].message
        final_text = []

        if response_message.content:
            final_text.append(response_message.content)

        # Handle tool calls if any
        if response_message.tool_calls:
            messages.append(response_message)
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = tool_call.function.arguments

                # Execute the tool
                result = await self.session.call_tool(function_name, eval(function_args))
                final_text.append(f"[Calling tool {function_name} with args {function_args}]")

                # Append tool result to messages
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": result.content,
                })

            # Second call to the language model with the tool results
            second_completion = self.client.chat.completions.create(
                 extra_headers={
                    "HTTP-Referer": os.getenv("YOUR_SITE_URL", ""), # Optional
                    "X-Title": os.getenv("YOUR_SITE_NAME", ""),      # Optional
                },
                model="anthropic/claude-3.5-sonnet",
                messages=messages,
            )
            final_text.append(second_completion.choices[0].message.content)

        return "\n".join(final_text)

    async def chat_loop(self):
        """
        Runs the main interactive chat loop.
        """
        print("\nMCP Client Started!")
        print("Type your queries or 'quit' to exit.")

        while True:
            try:
                query = input("\nQuery: ").strip()

                if query.lower() == 'quit':
                    break

                response = await self.process_query(query)
                print("\n" + response)

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self):
        """
        Cleans up resources and closes connections.
        """
        await self.exit_stack.aclose()


async def main():
    """
    Main function to run the client.
    """
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_server(sys.argv[1])
        await client.chat_loop()
    finally:
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())

