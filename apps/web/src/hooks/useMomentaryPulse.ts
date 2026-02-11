import { useState, useCallback, useRef, useEffect } from "react";

interface UseMomentaryPulseOptions {
  onPulse: () => Promise<boolean>;
  onError?: (error: unknown) => void;
  cooldownMs?: number;
}

export function useMomentaryPulse(options: UseMomentaryPulseOptions) {
  const { onPulse, onError, cooldownMs = 1500 } = options;
  const [isPulsing, setIsPulsing] = useState(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const pulse = useCallback(async () => {
    if (isPulsing) return;

    setIsPulsing(true);

    try {
      const success = await onPulse();
      if (!success) {
        setIsPulsing(false);
        onError?.(new Error("Pulse failed"));
        return;
      }
    } catch (error) {
      setIsPulsing(false);
      onError?.(error);
      return;
    }

    timeoutRef.current = window.setTimeout(() => {
      setIsPulsing(false);
      timeoutRef.current = null;
    }, cooldownMs);
  }, [isPulsing, onPulse, onError, cooldownMs]);

  return { isPulsing, pulse };
}
