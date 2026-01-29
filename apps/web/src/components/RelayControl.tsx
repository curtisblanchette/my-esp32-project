import React, { useState, useRef, useEffect } from "react";
import { setRelayState, updateRelayName, type RelayStatus } from "../api";

interface RelayControlProps {
  relay: RelayStatus;
  onStateChange?: (relayId: string, newState: boolean) => void;
  onNameChange?: (relayId: string, newName: string) => void;
  onError?: (message: string, type: "error" | "warning") => void;
}

export function RelayControl(props: RelayControlProps): React.ReactElement {
  const [isToggling, setIsToggling] = useState(false);
  const [localState, setLocalState] = useState(props.relay.state);
  const [isEditingName, setIsEditingName] = useState(false);
  const [editedName, setEditedName] = useState(props.relay.name);
  const [isSavingName, setIsSavingName] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleToggle = async () => {
    if (isToggling) return;
    
    const newState = !localState;
    setIsToggling(true);
    setLocalState(newState);

    try {
      const success = await setRelayState(props.relay.id, newState);
      if (success) {
        props.onStateChange?.(props.relay.id, newState);
      } else {
        console.error("Failed to toggle relay state");
        setLocalState(!newState);
      }
    } catch (error) {
      console.error("Error toggling relay state:", error);
      setLocalState(!newState);
    } finally {
      setIsToggling(false);
    }
  };

  const handleNameDoubleClick = () => {
    setIsEditingName(true);
    setEditedName(props.relay.name);
  };

  const handleNameSave = async () => {
    const trimmedName = editedName.trim();
    if (!trimmedName || trimmedName === props.relay.name) {
      setIsEditingName(false);
      setEditedName(props.relay.name);
      return;
    }

    setIsSavingName(true);
    try {
      const success = await updateRelayName(props.relay.id, trimmedName);
      if (success) {
        props.onNameChange?.(props.relay.id, trimmedName);
        setIsEditingName(false);
      } else {
        console.error("Failed to update relay name");
        props.onError?.("Failed to save relay name.", "warning");
        setEditedName(props.relay.name);
        setIsEditingName(false);
      }
    } catch (error) {
      console.error("Error updating relay name:", error);
      props.onError?.("Failed to save relay name.", "error");
      setEditedName(props.relay.name);
      setIsEditingName(false);
    } finally {
      setIsSavingName(false);
    }
  };

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleNameSave();
    } else if (e.key === "Escape") {
      setIsEditingName(false);
      setEditedName(props.relay.name);
    }
  };

  useEffect(() => {
    setLocalState(props.relay.state);
  }, [props.relay.state]);

  useEffect(() => {
    if (isEditingName && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditingName]);

  useEffect(() => {
    if (!isEditingName) {
      setEditedName(props.relay.name);
    }
  }, [props.relay.name, isEditingName]);

  return (
    <div className="glass-card rounded-2xl p-4 flex-1 min-w-[min(280px,100%)]">
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1">
          {isEditingName ? (
            <input
              ref={inputRef}
              type="text"
              value={editedName}
              onChange={(e) => setEditedName(e.target.value)}
              onBlur={handleNameSave}
              onKeyDown={handleNameKeyDown}
              disabled={isSavingName}
              className="text-sm font-medium mb-1 px-2 py-1 border border-blue-500/50 rounded bg-white/5 focus:outline-none focus:border-blue-500 w-full disabled:opacity-50"
            />
          ) : (
            <div className="flex items-center gap-1.5 mb-1 group">
              <div
                className="text-sm font-medium cursor-pointer hover:text-blue-500 transition-colors"
                onClick={handleNameDoubleClick}
                title="Click to edit"
              >
                {props.relay.name}
              </div>
              <button
                onClick={handleNameDoubleClick}
                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity p-0.5"
                title="Edit name"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
              </button>
            </div>
          )}
          <div className="flex items-center gap-2">
            <div
              className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium transition-colors ${
                localState
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300/75"
                  : "bg-gray-500/20 text-gray-600 dark:text-gray-400"
              }`}
            >
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  localState ? "bg-emerald-500/75" : "bg-gray-500"
                }`}
              />
              {localState ? "ON" : "OFF"}
            </div>
          </div>
        </div>

        <button
          onClick={handleToggle}
          disabled={isToggling}
          className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed ${
            localState ? "bg-emerald-500/65" : "bg-gray-300 dark:bg-gray-600"
          }`}
          aria-label={`Toggle ${props.relay.name}`}
        >
          <span
            className={`inline-block h-6 w-6 transform rounded-full bg-white shadow-lg transition-transform ${
              localState ? "translate-x-7" : "translate-x-1"
            }`}
          />
        </button>
      </div>
    </div>
  );
}
