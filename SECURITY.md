# Security policy

Thanks for taking the time to report something.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting instead: the **Security** tab →
**Report a vulnerability**. That opens a private channel visible only to the
maintainer, so a problem can be fixed before it is described publicly.

This is a personal fork maintained by one person in their own time. Reports are
handled on a best-effort basis, with no guaranteed response time and no bug
bounty. You will get an acknowledgement when the report is read.

## What is worth reporting

This project is an MCP server that holds **Garmin Connect authentication tokens**
on behalf of its users, and can be deployed as a network-reachable service with
OAuth2. The things most worth reporting are anything that would let someone:

- reach another user's Garmin session or tokens,
- bypass the email allowlist or the token-import secret,
- read or write files on the server host through a tool parameter,
- forge, replay, or escalate an OAuth2 authorization.

Findings in the local stdio mode are lower severity by nature: it runs on the
user's own machine with their own credentials, so "the tool can read a local
file" is expected there and refused in remote mode.

## Supported versions

Only the current `main` branch. This fork does not publish releases and does not
backport fixes.

## Upstream

This is a fork of [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp).
If the issue is in upstream code rather than this fork's additions — the
allowlist, token import, OAuth2 remote server, or deployment — please report it
to upstream as well, since their users are affected too and this fork cannot fix
it for them.
