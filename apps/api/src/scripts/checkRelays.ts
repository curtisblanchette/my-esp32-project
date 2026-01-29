import { getDb, getAllRelayConfigs } from "../lib/sqlite.js";

// Initialize database
getDb();

console.log("Checking relay configurations in database...");

const relays = getAllRelayConfigs();

console.log(`Found ${relays.length} relays:`);
console.log(JSON.stringify(relays, null, 2));
