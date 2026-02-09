import { expireCommands } from "../lib/sqlite.js";
import { broadcastCommand } from "./websocket.js";

const EXPIRATION_CHECK_INTERVAL_MS = 5000; // Check every 5 seconds

export function startCommandExpirationJob(): void {
  console.log(`Starting command expiration job (interval: ${EXPIRATION_CHECK_INTERVAL_MS / 1000}s)`);

  setInterval(() => {
    checkAndExpireCommands();
  }, EXPIRATION_CHECK_INTERVAL_MS);

  // Run initial check after a short delay
  setTimeout(() => {
    checkAndExpireCommands();
  }, 2000);
}

function checkAndExpireCommands(): void {
  try {
    const expiredCommands = expireCommands();

    if (expiredCommands.length > 0) {
      console.log(`Expired ${expiredCommands.length} command(s):`, expiredCommands.map((c) => c.id).join(", "));

      // Broadcast each expired command individually (upserts on frontend)
      for (const cmd of expiredCommands) {
        broadcastCommand(cmd);
      }
    }
  } catch (err) {
    console.error("Command expiration job failed:", err);
  }
}