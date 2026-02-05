import { getDb, upsertDevice, updateActuatorName } from "../lib/sqlite.js";

// Initialize database
getDb();

// Create a test device with actuators (simulating device birth)
const testDevice = {
  id: "test-device",
  location: "test",
  name: "Test Device",
  platform: "simulator",
  firmware: "1.0.0",
  capabilities: {
    sensors: [],
    actuators: [
      { id: "relay1", type: "relay", name: "Relay 1", state: false },
      { id: "relay2", type: "relay", name: "Relay 2", state: false },
      { id: "relay3", type: "relay", name: "Relay 3", state: false },
    ],
  },
  lastSeen: Date.now(),
  online: true,
};

console.log("Seeding test device with actuators...");

try {
  const device = upsertDevice(testDevice);
  console.log(`✓ Created device: ${device.name} (${device.id})`);

  // Set custom names for relays
  const customNames = [
    { id: "relay1", name: "Living Room Light" },
    { id: "relay2", name: "Fan" },
    { id: "relay3", name: "Heater" },
  ];

  for (const { id, name } of customNames) {
    const updated = updateActuatorName(device.id, id, name);
    if (updated) {
      console.log(`✓ Set custom name for ${id}: ${name}`);
    } else {
      console.error(`✗ Failed to set name for ${id}`);
    }
  }
} catch (error) {
  console.error("✗ Failed to create test device:", error);
}

console.log("Done!");
