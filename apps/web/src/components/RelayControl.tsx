import React, { useEffect } from "react";
import { setRelayState, updateRelayName, type RelayStatus } from "../api";
import { useOptimisticToggle } from "../hooks/useOptimisticToggle";
import { useInlineEdit } from "../hooks/useInlineEdit";

interface RelayControlProps {
  relay: RelayStatus;
  onStateChange?: (relayId: string, newState: boolean) => void;
  onNameChange?: (relayId: string, newName: string) => void;
  onError?: (message: string, source?: string) => void;
}

export function RelayControl(props: RelayControlProps): React.ReactElement {
  const { relay, onStateChange, onNameChange, onError } = props;
  const isOffline = relay.deviceOnline === false;

  const {
    state: localState,
    isToggling,
    toggle,
    syncState,
  } = useOptimisticToggle({
    initialState: relay.state,
    onToggle: (newState) => setRelayState(relay.id, newState),
    onSuccess: (newState) => onStateChange?.(relay.id, newState),
    onError: (error) => {
      console.error("Error toggling relay state:", error);
      onError?.(`Failed to toggle ${relay.name}`, "Relay");
    },
  });

  const {
    isEditing: isEditingName,
    editedValue: editedName,
    isSaving: isSavingName,
    inputRef,
    setEditedValue: setEditedName,
    startEditing,
    save: saveName,
    handleKeyDown,
  } = useInlineEdit({
    initialValue: relay.name,
    onSave: (name) => updateRelayName(relay.id, name),
    onSuccess: (name) => onNameChange?.(relay.id, name),
    onError: (error) => {
      console.error("Failed to save relay name:", error);
      onError?.(`Failed to rename ${relay.name}`, "Relay");
    },
  });

  // Sync toggle state with external prop changes
  useEffect(() => {
    syncState(relay.state);
  }, [relay.state, syncState]);

  return (
    <div className={`glass-card rounded-2xl p-4 flex-1 min-w-[min(280px,100%)] ${isOffline ? "opacity-60" : ""}`}>
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1">
          {isEditingName ? (
            <input
              ref={inputRef}
              type="text"
              value={editedName}
              onChange={(e) => setEditedName(e.target.value)}
              onBlur={saveName}
              onKeyDown={handleKeyDown}
              disabled={isSavingName}
              className="text-sm font-medium mb-1 px-2 py-1 border border-blue-500/50 rounded bg-white/5 focus:outline-none focus:border-blue-500 w-full disabled:opacity-50"
            />
          ) : (
            <div className="flex items-center gap-1.5 mb-1 group">
              <div
                className="text-sm font-medium cursor-pointer hover:text-blue-500 transition-colors"
                onClick={startEditing}
                title="Click to edit"
              >
                {relay.name}
              </div>
              <button
                onClick={startEditing}
                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity p-0.5"
                title="Edit name"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
                  />
                </svg>
              </button>
            </div>
          )}
          <div className="flex items-center gap-2">
            {isOffline ? (
              <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-red-500/15 text-red-600 dark:text-red-400">
                <div className="w-1.5 h-1.5 rounded-full bg-red-500" />
                Offline
              </div>
            ) : (
              <div
                className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium transition-colors ${
                  localState
                    ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300/75"
                    : "bg-gray-500/20 text-gray-600 dark:text-gray-400"
                }`}
              >
                <div className={`w-1.5 h-1.5 rounded-full ${localState ? "bg-emerald-500/75" : "bg-gray-500"}`} />
                {localState ? "ON" : "OFF"}
              </div>
            )}
          </div>
        </div>

        <button
          onClick={toggle}
          disabled={isToggling || isOffline}
          className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed ${
            localState ? "bg-emerald-500/65" : "bg-gray-300 dark:bg-gray-600"
          }`}
          aria-label={`Toggle ${relay.name}`}
          title={isOffline ? "Device is offline" : undefined}
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