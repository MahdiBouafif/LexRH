# MCP server

`make mcp` starts the server over stdio. Tools:

| Tool | Kind | What it does |
|---|---|---|
| `search_law(question, as_of?)` | read | Cited answer from the Code du travail as it stood on `as_of`, or a refusal with a reason |
| `compute_notice(hire_date, notification_date, fault?)` | compute | Legal notice period (L1234-1) |
| `compute_severance(hire_date, notification_date, reference_salary, fault?)` | compute | Legal severance pay (L1234-9, R1234-2) |

`fault` is `none`, `serious` or `gross`. Dates are ISO (`2026-11-01`). The calculators are plain
code (`src/lexrh/hr/rules.py`), unit-tested, and return legal minimums only.

## Use it from Claude Desktop

Add to `claude_desktop_config.json` (use your own path):

```json
{
  "mcpServers": {
    "lexrh": {
      "command": "uv",
      "args": ["--directory", "/Users/you/LexRH/LexRH", "run", "python", "-m", "lexrh.mcp_server.server"]
    }
  }
}
```

The database must be running (`make up`) and `.env` filled in, as for `make ask`.
