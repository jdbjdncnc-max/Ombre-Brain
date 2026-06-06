# Zeta Memory Gateway

This repository includes a Zeta memory gateway for Ombre Brain.

The gateway is designed for an Operit frontend:

- Operit saves raw conversation turns automatically.
- Operit recalls 3-5 relevant memories before each Zeta turn and injects them as hidden context.
- Zeta decides which moments deserve long-term structured memory.
- Raw conversation references use `convo://session_id/turn_id`.
- Diary integration is intentionally decoupled for now; future refs can use `diary://...`.

See `packages/operit_zeta_memory.js` for the Operit bridge.
