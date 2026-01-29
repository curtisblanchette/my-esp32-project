import { getDb, createRelayConfig } from "../lib/sqlite.js";

// Initialize database
getDb();

// Create initial relay configurations
const relays = [
  { id: 'relay1', name: 'Living Room Light', pin: 12, enabled: true },
  { id: 'relay2', name: 'Fan', pin: 13, enabled: true },
  { id: 'relay3', name: 'Heater', pin: 14, enabled: true },
];

console.log("Seeding relay configurations...");

for (const relay of relays) {
  try {
    const created = createRelayConfig(relay);
    console.log(`✓ Created relay: ${created.name} (${created.id})`);
  } catch (error) {
    console.error(`✗ Failed to create relay ${relay.id}:`, error);
  }
}

console.log("Done!");
