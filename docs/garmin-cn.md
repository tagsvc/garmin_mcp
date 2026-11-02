# Garmin Connect China (garmin.cn)

For an account on Garmin Connect China (garmin.cn) rather than the
international service, set `GARMIN_IS_CN` to `true` (`1` and `yes` also work)
for both the auth CLI and the server. Back to the [README](../README.md).

China accounts need the **local stdio server**. The remote server
(`garmin-mcp-remote`) and the `login_to_garmin` / `check_garmin_auth` tools
always use the international service; `GARMIN_IS_CN` applies to
`garmin-mcp-auth` and `garmin-mcp` only.

## Authenticate

```bash
# Pre-authenticate with Garmin Connect China
GARMIN_IS_CN=true garmin-mcp-auth

# Or use the CLI flag
garmin-mcp-auth --is-cn
```

`garmin-mcp-auth --diagnose` prints the region it resolved, so you can check
which service a token directory belongs to.

## Claude Desktop

The server needs `GARMIN_IS_CN` as well, in the `env` section:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/tagsvc/garmin_mcp",
        "garmin-mcp"
      ],
      "env": {
        "GARMIN_IS_CN": "true"
      }
    }
  }
}
```

## Docker

Uncomment `- GARMIN_IS_CN=true` in the `environment:` list of
`docker-compose.yml`. A line in `.env` alone does not reach the container: the
compose file reads `.env` only to fill its `${...}` placeholders, and it has no
placeholder for `GARMIN_IS_CN`.
