import { getDb, getDeviceActuators, getAllDevices } from "../lib/sqlite.js";

// Initialize database
getDb();

console.log("Checking device actuators in database...\n");

const devices = getAllDevices();
console.log(`Found ${devices.length} device(s):\n`);

for (const device of devices) {
  console.log(`Device: ${device.name ?? device.id} (${device.id})`);
  console.log(`  Location: ${device.location}`);
  console.log(`  Online: ${device.online}`);
  console.log(`  Actuators: ${device.capabilities.actuators.length}`);
  console.log(`  Custom names:`, device.actuatorNames);
  console.log();
}

console.log("\nAll actuators (relay view):");
const actuators = getDeviceActuators();
console.log(JSON.stringify(actuators.map(a => ({
  id: a.id,
  name: a.customName ?? a.name ?? a.id,
  state: a.state ?? false,
  deviceId: a.deviceId,
  location: a.location,
  deviceOnline: a.deviceOnline,
})), null, 2));
