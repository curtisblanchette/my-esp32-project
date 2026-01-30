import { useState, useEffect, useCallback } from "react";
import { fetchRelayStatus, type RelayStatus } from "../api";

const MOCK_RELAYS: RelayStatus[] = [
  { id: "relay1", name: "Living Room Light", state: true, updatedAt: Date.now() },
  { id: "relay2", name: "Fan", state: false, updatedAt: Date.now() },
  { id: "relay3", name: "Heater", state: false, updatedAt: Date.now() },
];

export function useRelays() {
  const [relays, setRelays] = useState<RelayStatus[]>([]);
  const [isInMockMode, setIsInMockMode] = useState(false);

  const applyRelays = useCallback((relayList: RelayStatus[]) => {
    if (relayList.length > 0) {
      setRelays(relayList);
      setIsInMockMode(false);
    } else {
      console.log("No relays configured, showing mock data");
      setIsInMockMode(true);
      setRelays(MOCK_RELAYS.map((r) => ({ ...r, updatedAt: Date.now() })));
    }
  }, []);

  // Fetch initial relay data
  useEffect(() => {
    const controller = new AbortController();

    async function loadRelays() {
      try {
        const relayList = await fetchRelayStatus(controller.signal);
        applyRelays(relayList);
      } catch (error) {
        console.error("Failed to fetch relays:", error);
        setIsInMockMode(true);
        setRelays(MOCK_RELAYS.map((r) => ({ ...r, updatedAt: Date.now() })));
      }
    }

    loadRelays();

    return () => {
      controller.abort();
    };
  }, [applyRelays]);

  const handleStateChange = useCallback((relayId: string, newState: boolean) => {
    setRelays((prev) =>
      prev.map((r) => (r.id === relayId ? { ...r, state: newState, updatedAt: Date.now() } : r))
    );
  }, []);

  const handleNameChange = useCallback((relayId: string, newName: string) => {
    setRelays((prev) =>
      prev.map((r) => (r.id === relayId ? { ...r, name: newName, updatedAt: Date.now() } : r))
    );
  }, []);

  return {
    relays,
    isInMockMode,
    applyRelays,
    handleStateChange,
    handleNameChange,
  };
}