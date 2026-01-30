import { useState, useCallback, useRef, useEffect } from "react";

interface UseInlineEditOptions {
  initialValue: string;
  onSave: (value: string) => Promise<boolean>;
  onSuccess?: (value: string) => void;
  onError?: (error: unknown) => void;
}

export function useInlineEdit(options: UseInlineEditOptions) {
  const { initialValue, onSave, onSuccess, onError } = options;
  const [isEditing, setIsEditing] = useState(false);
  const [editedValue, setEditedValue] = useState(initialValue);
  const [isSaving, setIsSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const startEditing = useCallback(() => {
    setIsEditing(true);
    setEditedValue(initialValue);
  }, [initialValue]);

  const cancelEditing = useCallback(() => {
    setIsEditing(false);
    setEditedValue(initialValue);
  }, [initialValue]);

  const save = useCallback(async () => {
    const trimmedValue = editedValue.trim();
    if (!trimmedValue || trimmedValue === initialValue) {
      cancelEditing();
      return;
    }

    setIsSaving(true);
    try {
      const success = await onSave(trimmedValue);
      if (success) {
        onSuccess?.(trimmedValue);
        setIsEditing(false);
      } else {
        onError?.(new Error("Save failed"));
        cancelEditing();
      }
    } catch (error) {
      onError?.(error);
      cancelEditing();
    } finally {
      setIsSaving(false);
    }
  }, [editedValue, initialValue, onSave, onSuccess, onError, cancelEditing]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        save();
      } else if (e.key === "Escape") {
        cancelEditing();
      }
    },
    [save, cancelEditing]
  );

  // Focus input when editing starts
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  // Sync with external value changes
  useEffect(() => {
    if (!isEditing) {
      setEditedValue(initialValue);
    }
  }, [initialValue, isEditing]);

  return {
    isEditing,
    editedValue,
    isSaving,
    inputRef,
    setEditedValue,
    startEditing,
    cancelEditing,
    save,
    handleKeyDown,
  };
}