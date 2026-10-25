"use client";

import "leaflet/dist/leaflet.css";

import { CircleMarker, MapContainer, TileLayer, Tooltip } from "react-leaflet";

import type { Card } from "@/lib/types";

/** OpenStreetMap tiles with one marker per recommended place (circle markers: no image assets to bundle). */
export default function PlacesMap({ places }: { places: Card[] }) {
  const points = places.filter((p) => p.coordinates) as (Card & { coordinates: { lat: number; lon: number } })[];
  if (points.length === 0) return null;
  const bounds = points.map((p) => [p.coordinates.lat, p.coordinates.lon] as [number, number]);

  return (
    <div className="h-64 overflow-hidden rounded-[20px] border border-border">
      <MapContainer
        bounds={bounds.length > 1 ? bounds : undefined}
        center={bounds.length === 1 ? bounds[0] : undefined}
        zoom={bounds.length === 1 ? 15 : undefined}
        boundsOptions={{ padding: [30, 30] }}
        scrollWheelZoom={false}
        className="h-full w-full"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {points.map((place) => (
          <CircleMarker
            key={place.id}
            center={[place.coordinates.lat, place.coordinates.lon]}
            radius={8}
            pathOptions={{ color: "#2e7d4f", fillColor: "#47c27a", fillOpacity: 0.85 }}
          >
            <Tooltip>{place.name}</Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
