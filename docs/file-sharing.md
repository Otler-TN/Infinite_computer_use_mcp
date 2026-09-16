# Send a laptop file into a conversation

`UploadFile` turns an existing Windows file into a downloadable export. With the standard HTTP/ngrok launcher, the result includes a working HTTP(S) URL, original filename, MIME type, size, SHA-256 hash, expiry and an MCP resource. An agent can put the URL directly in its reply. No OpenAI API key or third-party file-hosting account is needed.

## Agent usage

When the user asks to upload, send, attach or give them a file from this laptop, call **UploadFile**:

```json
{"path":"C:\\Users\\YOUR_NAME\\Desktop\\report.pdf"}
```

Use the actual file path selected by the user or returned by the tool that created it. The tool returns `download_url`. Present it as `[Download report.pdf](download_url)` using the real returned value. Do not invent `sandbox:` links or present a Windows path as a remote chat download.

The server copies the file into `.run/uploads/` and serves that copy through the same port and ngrok tunnel as MCP. Later changes to the original do not change the exported bytes. This supports PDFs, Word/Excel/PowerPoint documents, ZIP archives, images and other regular files. There is no HTTP file-size cap in this implementation; disk space, upload time and client/tunnel limits still apply. Zip a directory before sharing it.

On ngrok's free plan, a browser may first show a **Visit Site** page before downloading. For programmatic downloads, send the returned `download_headers`, including `ngrok-skip-browser-warning: true`. This is ngrok's documented client behavior; see [ngrok's explanation](https://ngrok.com/docs/pricing-limits/free-plan-limits#removing-the-interstitial-page).

The default link lifetime is **24 hours**. To change it or the download filename:

```json
{"path":"C:\\Users\\YOUR_NAME\\Desktop\\report.pdf","filename":"Final report.pdf","expires_in_seconds":3600}
```

Expiry can be 1 second through 30 days. The server and tunnel must stay available. Exports survive server restarts while their stored copy remains and the same hostname is reachable. After a tunnel hostname change, use `mode="info"` to obtain the current URL:

```json
{"mode":"info","upload_id":"THE_RETURNED_UPLOAD_ID"}
```

Revoke the link and remove its copy with:

```json
{"mode":"revoke","upload_id":"THE_RETURNED_UPLOAD_ID"}
```

The original file stays intact. Already downloaded copies and downloads already in progress cannot be recalled. Expired exports are rejected and removed during a later publish operation. Copies temporarily held open by Windows are cleaned on a subsequent publish after an hour.

## Native attachment versus download link

This tool supplies the actual file and a download route. A native attachment in a client's own conversation storage is a separate client operation. If the agent has a file workspace and an attachment tool, it can fetch `download_url` there and attach that downloaded file. `UploadFile` itself does not claim to create that native attachment or fabricate a ChatGPT file ID.

For clients that understand embedded MCP files, use `include_content=true`; exports up to 1 MiB include exact binary content. Larger exports still return links. Standard MCP resource links and embedded resources are defined in the [MCP tool specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); their presentation is chosen by the [MCP host](https://modelcontextprotocol.io/specification/2025-11-25/server/resources).

OpenAI documents separate, optional widget file-upload helpers. Those are APIs provided to a ChatGPT widget, not a server-side method granting this MCP access to the current conversation's storage. See the [official file API reference](https://developers.openai.com/plugins/reference#file-apis).

## Local stdio and programmatic clients

A stdio-only server has no HTTP listener, so `download_url` is null unless an HTTP deployment is explicitly configured. It still returns `resource_uri`, which supports MCP `resources/read` for files up to 16 MiB. Larger files can be paged using `FileTransfer` on the returned `snapshot_path`. Both routes preserve binary bytes.

The launcher supplies `WINDOWS_MCP_INSTANCE_STATE` so the tool reads the current local/public URL. For a manually managed reverse proxy, set `WINDOWS_MCP_PUBLIC_BASE_URL` to the externally reachable server base URL, such as `https://laptop.example`; the file route is `/files/<upload_id>/<filename>`. Never use an unrelated service URL: that service must actually route requests to this server. HTTP downloads support GET, HEAD and byte ranges for resuming.

To send a file **to** the laptop, use the existing `FileTransfer` write mode with base64 chunks. `UploadFile` handles delivery **from** the laptop.

## Refresh and verify

Refresh the existing MCP connection in the client and start a new conversation if needed. The complete inventory is now **24 tools**. Calling a tool that is absent from cached metadata requires that refresh.

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_mcp.py https://YOUR-DOMAIN.ngrok-free.dev/mcp --exercise --report .run\verification-upload.json
```

The additional check creates a disposable binary file, exports it, verifies its hash and exact bytes through embedded content, MCP resources and HTTP, checks a partial download, revokes the link and confirms HTTP 404, then removes its original fixture.

Export URLs use unguessable IDs and expire, but anyone possessing a URL can download its file while it is valid. The MCP itself remains in the requested no-auth mode, so reachable MCP callers can also create exports using the server's Windows privileges.
