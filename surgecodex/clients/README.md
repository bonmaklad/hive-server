# Clients

Create one subfolder per client.

Recommended structure:

```text
clients/<client>/

runtime/<client>/
  tickets/
  state/
```

## One-Ticket Lock

Each client must maintain:

```text
runtime/<client>/state/active-ticket.json
```

If that file indicates an active ticket, no second ticket may begin for that client until the first is released or explicitly cleared.
