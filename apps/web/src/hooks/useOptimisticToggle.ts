import { useState, useCallback, useRef, useEffect } from "react";

interface UseOptimisticToggleOptions {
  initialState: boolean;
  onToggle: (newState: boolean) => Promise<boolean>;
  onSuccess?: (newState: boolean) => void;
  onError?: (error: unknown) => void;
  /** Timeout in ms to wait for ack before clearing loading state (default: 10000) */
  ackTimeout?: number;
}

export function useOptimisticToggle(options: UseOptimisticToggleOptions) {
  const { initialState, onToggle, onSuccess, onError, ackTimeout = 10000 } = options;
  const [state, setState] = useState(initialState);
  const [isToggling, setIsToggling] = useState(false);
  const pendingStateRef = useRef<boolean | null>(null);
  const timeoutRef = useRef<number | null>(null);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const toggle = useCallback(async () => {
    if (isToggling) return;

    const newState = !state;
    setIsToggling(true);
    pendingStateRef.current = newState;

    // Set timeout to clear loading state if ack never arrives
    timeoutRef.current = window.setTimeout(() => {
      if (pendingStateRef.current !== null) {
        pendingStateRef.current = null;
        setIsToggling(false);
        onError?.(new Error("Timeout waiting for device acknowledgment"));
      }
    }, ackTimeout);

    try {
      const success = await onToggle(newState);
      if (!success) {
        // API call failed immediately
        if (timeoutRef.current) {
          window.clearTimeout(timeoutRef.current);
          timeoutRef.current = null;
        }
        pendingStateRef.current = null;
        setIsToggling(false);
        onError?.(new Error("Toggle failed"));
      }
      // On success, keep isToggling true - wait for WebSocket to confirm state
    } catch (error) {
      if (timeoutRef.current) {
        window.clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      pendingStateRef.current = null;
      setIsToggling(false);
      onError?.(error);
    }
  }, [state, isToggling, onToggle, onError, ackTimeout]);

  const syncState = useCallback((externalState: boolean) => {
    setState(externalState);
    // If we were waiting for this state change, clear the pending state
    if (pendingStateRef.current === externalState) {
      if (timeoutRef.current) {
        window.clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      pendingStateRef.current = null;
      setIsToggling(false);
      onSuccess?.(externalState);
    }
  }, [onSuccess]);

  return { state, isToggling, toggle, syncState };
}