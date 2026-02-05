export type SensorReading = {
  temp: number;
  humidity: number;
  updatedAt: number;
  sourceIp?: string;
  sourceTopic?: string;
  deviceId?: string;
};
