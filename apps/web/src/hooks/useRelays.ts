import { useState, useEffect, useCallback } from "react";
import { fetchRelayStatus, type RelayStatus } from "../api";

export function useRelays() {
  const [relays, setRelays] = useState<RelayStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const applyRelays = useCallback((relayList: RelayStatus[]) => {
    setRelays(relayList);
    setError(null);
  }, []);

  // Fetch initial relay data
  useEffect(() => {
    const controller = new AbortController();

    async function loadRelays() {
      setIsLoading(true);
      try {
        const relayList = await fetchRelayStatus(controller.signal);
        applyRelays(relayList);
      } catch (err) {
        if (err instanceof Error && err.name !== "AbortError") {
          console.error("Failed to fetch relays:", err);
          setError("Failed to load relays");
        }
      } finally {
        setIsLoading(false);
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

  // Check if any device is offline
  const hasOfflineDevices = relays.some((r) => r.deviceOnline === false);

  return {
    relays,
    isLoading,
    error,
    hasOfflineDevices,
    applyRelays,
    handleStateChange,
    handleNameChange,
  };
}
