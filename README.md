# Infinite_computer_use_mcp

Connect **ChatGPT to your Windows PC** through an MCP browser endpoint and let the latest ChatGPT models that support MCP tools use your computer: see the desktop, click, type, open apps, run PowerShell, manage files, and return files to the conversation.

## Connect it to ChatGPT

1. Download this repo, extract it, and run **`Open MCP Manager.cmd`**.
2. Open **Setup & settings**, choose **Remote access**, install/repair, and add your ngrok auth token.
3. Start MCP, then open **Connect an AI app** and copy the public HTTPS endpoint ending in **`/mcp`**.
4. In ChatGPT, add that URL as your MCP/browser endpoint using **Streamable HTTP / No Authentication**.
5. Start a chat with a ChatGPT model that supports connected MCP tools and ask it to use your computer.

Try things like: **“List the files on my Desktop”**, **“Open Paint and draw something”**, **“Run this PowerShell task”**, or **“Send me this file from my PC.”**

> [!CAUTION]
> The remote endpoint is unauthenticated. Treat its URL like a password, keep it private, and stop MCP when you are not using it. See [SECURITY.md](SECURITY.md).
