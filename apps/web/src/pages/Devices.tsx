import React, { useCallback, useMemo, useState } from "react";
import { DndContext, DragOverlay, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent, type DragStartEvent, type DragOverEvent } from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy } from "@dnd-kit/sortable";

import { saveDeviceOrder, type LatestReading, type Command, type Device, type RelayStatus } from "../api";
import { DevicePanel } from "../components/DevicePanel";
import { DeviceDiscoveryState } from "../components/DeviceDiscoveryState";

type DiscoveryPhase = "discovering" | "complete";

type DevicesProps = {
  devices: Device[];
  setDevices: React.Dispatch<React.SetStateAction<Device[]>>;
  discoveryPhase: DiscoveryPhase;
  latestByDevice: Record<string, LatestReading>;
  commands: Command[];
  isConnected: boolean;
  wsRelayUpdates: RelayStatus[] | null;
  addError: (message: string, source?: string) => void;
};

export function Devices({
  devices,
  setDevices,
  discoveryPhase,
  latestByDevice,
  commands,
  isConnected,
  wsRelayUpdates,
  addError,
}: DevicesProps): React.ReactElement {
  // DnD sensors — distance threshold prevents triggering on clicks/taps
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } })
  );
  const [activeId, setActiveId] = useState<string | null>(null);

  const sortedDevices = useMemo(
    () => [...devices].sort((a, b) => a.displayOrder - b.displayOrder),
    [devices]
  );

  const activeDevice = useMemo(
    () => (activeId ? sortedDevices.find((d) => d.id === activeId) ?? null : null),
    [activeId, sortedDevices]
  );

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveId(String(event.active.id));
  }, []);

  const handleDragOver = useCallback((event: DragOverEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    setDevices((prev) => {
      const sorted = [...prev].sort((a, b) => a.displayOrder - b.displayOrder);
      const oldIndex = sorted.findIndex((d) => d.id === active.id);
      const newIndex = sorted.findIndex((d) => d.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return prev;
      const reordered = arrayMove(sorted, oldIndex, newIndex);
      return reordered.map((d, i) => ({ ...d, displayOrder: i }));
    });
  }, [setDevices]);

  const handleDragEnd = useCallback((_event: DragEndEvent) => {
    setActiveId(null);
    // Persist current order to backend
    setDevices((prev) => {
      const sorted = [...prev].sort((a, b) => a.displayOrder - b.displayOrder);
      saveDeviceOrder(sorted.map((d) => d.id)).catch(console.error);
      return prev;
    });
  }, [setDevices]);

  return (
    <div className="flex-1 w-full flex justify-center px-3 py-5 pb-40 md:px-5 md:pb-24">
      <div className="w-full">
        <div className="flex-1 min-w-0 flex flex-wrap justify-center flex-row gap-5">
          {discoveryPhase === "discovering" && devices.length === 0 ? (
            <DeviceDiscoveryState />
          ) : sortedDevices.length > 0 ? (
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={handleDragStart} onDragOver={handleDragOver} onDragEnd={handleDragEnd}>
              <SortableContext items={sortedDevices.map((d) => d.id)} strategy={rectSortingStrategy}>
                {sortedDevices.map((device) => (
                  <DevicePanel
                    key={device.id}
                    device={device}
                    latestReading={latestByDevice[device.id] || null}
                    commands={commands}
                    isConnected={isConnected}
                    wsRelayUpdates={wsRelayUpdates}
                    onError={addError}
                  />
                ))}
              </SortableContext>
              <DragOverlay dropAnimation={null}>
                {activeDevice && (
                  <DevicePanel
                    device={activeDevice}
                    latestReading={latestByDevice[activeDevice.id] || null}
                    commands={commands}
                    isConnected={isConnected}
                    wsRelayUpdates={wsRelayUpdates}
                    onError={addError}
                    isOverlay
                  />
                )}
              </DragOverlay>
            </DndContext>
          ) : (
            <div className="flex-1 min-w-0 border border-panel-border rounded-2xl p-5 backdrop-blur-[10px] flex items-center justify-center min-h-[200px]">
              <div className="text-sm opacity-60">
                No devices found on the network. Make sure your devices are powered on and connected.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}