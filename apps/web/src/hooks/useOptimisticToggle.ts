import { useState, useCallback } from "react";

interface UseOptimisticToggleOptions {
  initialState: boolean;
  onToggle: (newState: boolean) => Promise<boolean>;
  onSuccess?: (newState: boolean) => void;
  onError?: (error: unknown) => void;
}

export function useOptimisticToggle(options: UseOptimisticToggleOptions) {
  const { initialState, onToggle, onSuccess, onError } = options;
  const [state, setState] = useState(initialState);
  const [isToggling, setIsToggling] = useState(false);

  const toggle = useCallback(async () => {
    if (isToggling) return;

    const newState = !state;
    setIsToggling(true);
    setState(newState);

    try {
      const success = await onToggle(newState);
      if (success) {
        onSuccess?.(newState);
      } else {
        setState(!newState);
        onError?.(new Error("Toggle failed"));
      }
    } catch (error) {
      setState(!newState);
      onError?.(error);
    } finally {
      setIsToggling(false);
    }
  }, [state, isToggling, onToggle, onSuccess, onError]);

  const syncState = useCallback((externalState: boolean) => {
    setState(externalState);
  }, []);

  return { state, isToggling, toggle, syncState };
}