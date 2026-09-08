import React, { useMemo } from "react";
import { Car, Plane, Ship, TrainFront } from "lucide-react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  random,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  geoArea,
  geoBounds,
  geoCentroid,
  geoContains,
  geoGraticule10,
  geoMercator,
  geoPath,
  type GeoPermissibleObjects,
} from "d3-geo";
import { feature } from "topojson-client";
import countriesTopology from "world-atlas/countries-10m.json";
import type { MapTransitionProps } from "./schema";
import {
  controlPoint,
  quadraticAngle,
  quadraticPartialPath,
  quadraticPoint,
} from "./route-math";
import { cameraPoint, cameraState, vehicleOrientation } from "./camera-motion";
import { chapterCountdown, localizedMapHeading } from "./map-copy";
import { viewportPolygon } from "./map-viewport";

const WIDTH = 1920;
const HEIGHT = 1080;
const CENTER = { x: WIDTH / 2, y: HEIGHT / 2 };

type CountryFeature = {
  id?: string | number;
  type: "Feature";
  geometry: {
    type: string;
    coordinates: unknown;
  };
  properties?: Record<string, unknown>;
};

const countryCollection = feature(
  countriesTopology as never,
  (countriesTopology as { objects: { countries: never } }).objects.countries,
) as unknown as { features: CountryFeature[] };

function numericCountryId(country: CountryFeature) {
  return String(country.id ?? "").padStart(3, "0");
}

function northernIrelandFeature(): CountryFeature | null {
  const unitedKingdom = countryCollection.features.find(
    (country) => numericCountryId(country) === "826",
  );
  if (!unitedKingdom || unitedKingdom.geometry.type !== "MultiPolygon") {
    return null;
  }
  const polygons = unitedKingdom.geometry.coordinates as number[][][][];
  for (const coordinates of polygons) {
    const candidate = {
      type: "Feature" as const,
      properties: { name: "Northern Ireland" },
      geometry: { type: "Polygon", coordinates },
    };
    if (geoContains(candidate as never, [-5.9302, 54.5964])) {
      return candidate;
    }
  }
  return null;
}

const northernIreland = northernIrelandFeature();

function selectedFeatures(countryNumericId: string) {
  const wanted = countryNumericId.padStart(3, "0");
  // world-atlas leaves Kosovo / Somaliland / N. Cyprus without an id → "000".
  // Never treat that as a country fill (Montenegro would otherwise light up Kosovo).
  if (wanted === "000") return [];
  const selected = countryCollection.features.filter(
    (country) => numericCountryId(country) === wanted,
  );
  if (wanted === "372" && northernIreland) selected.push(northernIreland);
  return selected;
}

function atlasFeatureForLabel(item: {
  numericId?: string;
  atlasName?: string;
}): CountryFeature | null {
  const numeric = String(item.numericId || "").padStart(3, "0");
  if (numeric && numeric !== "000") {
    const found = countryCollection.features.find(
      (country) => numericCountryId(country) === numeric,
    );
    if (found) return found;
  }
  const atlas = String(item.atlasName || "").trim().toLowerCase();
  if (!atlas) return null;
  return (
    countryCollection.features.find(
      (country) =>
        String(country.properties?.name || "").trim().toLowerCase() === atlas,
    ) || null
  );
}

function pointInView(
  longitude: number,
  latitude: number,
  viewBounds: [[number, number], [number, number]],
) {
  const west = viewBounds[0][0];
  const south = viewBounds[0][1];
  const east = viewBounds[1][0];
  const north = viewBounds[1][1];
  return longitude >= west && longitude <= east && latitude >= south && latitude <= north;
}

function interiorLonLat(
  feature: CountryFeature | null,
  fallback: { longitude: number; latitude: number },
  viewBounds: [[number, number], [number, number]],
): { longitude: number; latitude: number } {
  const insideCountry = (longitude: number, latitude: number) =>
    !feature || geoContains(feature as never, [longitude, latitude]);
  const usable = (longitude: number, latitude: number) =>
    Number.isFinite(longitude) &&
    Number.isFinite(latitude) &&
    pointInView(longitude, latitude, viewBounds) &&
    insideCountry(longitude, latitude);

  if (feature) {
    const centroid = geoCentroid(feature as never);
    if (usable(centroid[0], centroid[1])) {
      return { longitude: centroid[0], latitude: centroid[1] };
    }
    if (feature.geometry.type === "MultiPolygon") {
      let bestPiece: { longitude: number; latitude: number } | null = null;
      let bestArea = -1;
      for (const coordinates of feature.geometry.coordinates as number[][][][]) {
        const polygon = {
          type: "Feature" as const,
          properties: {},
          geometry: { type: "Polygon" as const, coordinates },
        };
        const area = Math.abs(geoArea(polygon as never));
        const point = geoCentroid(polygon as never);
        if (area > bestArea && usable(point[0], point[1])) {
          bestArea = area;
          bestPiece = { longitude: point[0], latitude: point[1] };
        }
      }
      if (bestPiece) return bestPiece;
    }

    const bounds = geoBounds(feature as never);
    const minLon = Math.max(viewBounds[0][0], bounds[0][0]);
    const minLat = Math.max(viewBounds[0][1], bounds[0][1]);
    const maxLon = Math.min(viewBounds[1][0], bounds[1][0]);
    const maxLat = Math.min(viewBounds[1][1], bounds[1][1]);
    if (maxLon > minLon && maxLat > minLat) {
      let best: { longitude: number; latitude: number } | null = null;
      let bestScore = -1;
      const steps = 16;
      const neighborLon = ((maxLon - minLon) / steps) * 1.15;
      const neighborLat = ((maxLat - minLat) / steps) * 1.15;
      for (let i = 1; i < steps; i += 1) {
        for (let j = 1; j < steps; j += 1) {
          const longitude = minLon + (i / steps) * (maxLon - minLon);
          const latitude = minLat + (j / steps) * (maxLat - minLat);
          if (!usable(longitude, latitude)) continue;
          let score = 0;
          const dirs: Array<[number, number]> = [
            [neighborLon, 0],
            [-neighborLon, 0],
            [0, neighborLat],
            [0, -neighborLat],
            [neighborLon, neighborLat],
            [-neighborLon, neighborLat],
            [neighborLon, -neighborLat],
            [-neighborLon, -neighborLat],
          ];
          for (const [deltaLon, deltaLat] of dirs) {
            if (insideCountry(longitude + deltaLon, latitude + deltaLat)) score += 1;
          }
          if (score > bestScore) {
            bestScore = score;
            best = { longitude, latitude };
          }
        }
      }
      if (best) return best;
    }
  }

  if (usable(fallback.longitude, fallback.latitude)) return fallback;
  return fallback;
}

function visibleCountryFit(
  feature: CountryFeature | null,
  viewBounds: [[number, number], [number, number]],
  projection: (point: [number, number]) => [number, number] | null,
): { maxWidth: number; fontSize: number } {
  if (!feature) {
    return { maxWidth: 168, fontSize: 15 };
  }
  const bounds = geoBounds(feature as never);
  const minLon = Math.max(viewBounds[0][0], bounds[0][0]);
  const minLat = Math.max(viewBounds[0][1], bounds[0][1]);
  const maxLon = Math.min(viewBounds[1][0], bounds[1][0]);
  const maxLat = Math.min(viewBounds[1][1], bounds[1][1]);
  const west = projection([minLon, (minLat + maxLat) / 2]);
  const east = projection([maxLon, (minLat + maxLat) / 2]);
  const south = projection([(minLon + maxLon) / 2, minLat]);
  const north = projection([(minLon + maxLon) / 2, maxLat]);
  const width =
    west && east ? Math.hypot(east[0] - west[0], east[1] - west[1]) : 140;
  const height =
    south && north ? Math.hypot(north[0] - south[0], north[1] - south[1]) : 90;
  const maxWidth = Math.max(72, Math.min(176, width * 0.56));
  const fontSize = Math.max(11, Math.min(16, Math.min(width, height) * 0.08));
  return { maxWidth, fontSize };
}

function transportIcon(mode: MapTransitionProps["transportMode"]) {
  if (mode === "plane") return Plane;
  if (mode === "train") return TrainFront;
  if (mode === "boat") return Ship;
  return Car;
}

export const VintageMapTransition: React.FC<MapTransitionProps> = (props) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps, width } = useVideoConfig();
  const outputScale = width / WIDTH;
  const progress = frame / Math.max(1, durationInFrames - 1);
  const isIntro = props.animationMode === "intro";
  const routeProgress = interpolate(progress, [0.12, 0.88], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });
  const fadeFrames = Math.max(1, Math.round(fps * 0.5));
  const fadeIn = interpolate(frame, [0, fadeFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.cubic),
  });
  const fadeOut = interpolate(
    frame,
    [durationInFrames - 1 - fadeFrames, durationInFrames - 1],
    [1, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.inOut(Easing.cubic),
    },
  );

  const geometry = useMemo(() => {
    const viewport = viewportPolygon(props.viewBounds) as GeoPermissibleObjects;
    const projection = geoMercator().fitExtent(
      [
        [72, 80],
        [WIDTH - 72, HEIGHT - 72],
      ],
      viewport,
    );
    const path = geoPath(projection);
    const fromProjected = projection([props.from.longitude, props.from.latitude]);
    const toProjected = projection([props.to.longitude, props.to.latitude]);
    if (!fromProjected || !toProjected) {
      throw new Error("Die Kartenkoordinaten konnten nicht projiziert werden.");
    }
    const from = { x: fromProjected[0], y: fromProjected[1] };
    const to = { x: toProjected[0], y: toProjected[1] };
    const viewBounds = props.viewBounds as [[number, number], [number, number]];
    const geography = (props.geographyLabels ?? []).flatMap((item) => {
      const feature =
        item.kind === "country" ? atlasFeatureForLabel(item) : null;
      const anchored =
        item.kind === "country"
          ? interiorLonLat(feature, item, viewBounds)
          : item;
      const projected = projection([anchored.longitude, anchored.latitude]);
      if (!projected) return [];
      const fit =
        item.kind === "country"
          ? visibleCountryFit(feature, viewBounds, projection)
          : { maxWidth: 200, fontSize: 17 };
      return [{ ...item, x: projected[0], y: projected[1], ...fit }];
    });
    return {
      path,
      from,
      to,
      control: controlPoint(from, to, 0.22),
      selected: selectedFeatures(props.countryNumericId),
      geography,
    };
  }, [props.countryNumericId, props.from, props.geographyLabels, props.to, props.viewBounds]);

  const camera = cameraState({
    animationMode: props.animationMode,
    progress,
    from: geometry.from,
    to: geometry.to,
    center: CENTER,
  });
  const toScreen = cameraPoint(geometry.to, camera, CENTER);
  const routeMarker = quadraticPoint(
    geometry.from,
    geometry.control,
    geometry.to,
    routeProgress,
  );
  const marker = cameraPoint(routeMarker, camera, CENTER);
  const markerOrientation = vehicleOrientation(
    quadraticAngle(
      geometry.from,
      geometry.control,
      geometry.to,
      routeProgress,
    ),
  );
  const Icon = transportIcon(props.transportMode);
  const arrivalPulse = interpolate(progress, [0.68, 0.82, 0.94], [0, 1, 0.7], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const dust = useMemo(
    () =>
      Array.from({ length: 90 }, (_, index) => ({
        x: random(`${props.seed}-dust-x-${index}`) * WIDTH,
        y: random(`${props.seed}-dust-y-${index}`) * HEIGHT,
        radius: 0.6 + random(`${props.seed}-dust-r-${index}`) * 2.2,
        opacity: 0.035 + random(`${props.seed}-dust-o-${index}`) * 0.08,
      })),
    [props.seed],
  );

  const cameraTransform = `translate(${CENTER.x} ${CENTER.y}) scale(${camera.scale}) translate(${-camera.focus.x} ${-camera.focus.y})`;
  const labels = [{ location: props.to, point: toScreen }];
  const geographyOnScreen = geometry.geography
    .map((item) => ({
      ...item,
      point: cameraPoint({ x: item.x, y: item.y }, camera, CENTER),
    }))
    .filter(
      (item) =>
        item.point.x > 90 &&
        item.point.x < WIDTH - 90 &&
        item.point.y > 120 &&
        item.point.y < HEIGHT - 130,
    )
    .filter((item) => {
      const deltaX = item.point.x - toScreen.x;
      const deltaY = item.point.y - toScreen.y;
      return deltaX * deltaX + deltaY * deltaY > 120 * 120;
    });

  return (
    <AbsoluteFill style={{ backgroundColor: "#000", overflow: "hidden" }}>
      <div
        style={{
          backgroundColor: "#d8c7a4",
          color: "#30271d",
          fontFamily: "Georgia, 'Times New Roman', serif",
          height: HEIGHT,
          overflow: "hidden",
          position: "absolute",
          transform: `scale(${outputScale})`,
          transformOrigin: "top left",
          width: WIDTH,
        }}
      >
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 34% 28%, rgba(255,248,220,.74), transparent 36%), radial-gradient(circle at 73% 75%, rgba(101,70,36,.12), transparent 42%), repeating-linear-gradient(8deg, rgba(79,54,31,.028) 0px, rgba(79,54,31,.028) 1px, transparent 1px, transparent 7px)",
          boxShadow: "inset 0 0 120px rgba(55,38,21,.34)",
        }}
      />
      <svg width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} style={{ position: "absolute" }}>
        <g transform={cameraTransform}>
          <path
            d={geometry.path(geoGraticule10()) ?? ""}
            fill="none"
            stroke="#5e5548"
            strokeOpacity={0.12}
            strokeWidth={1.2}
            vectorEffect="non-scaling-stroke"
          />
          {countryCollection.features.map((country, index) => (
            <path
              d={geometry.path(country as unknown as GeoPermissibleObjects) ?? ""}
              fill="#a99c82"
              fillOpacity={0.48}
              key={`${country.id ?? "country"}-${index}`}
              stroke="#665f52"
              strokeOpacity={0.52}
              strokeWidth={1.15}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {geometry.selected.map((country, index) => (
            <path
              d={geometry.path(country as unknown as GeoPermissibleObjects) ?? ""}
              fill="#b65c42"
              fillOpacity={0.9}
              key={`selected-${country.id ?? index}`}
              stroke="#563225"
              strokeLinejoin="round"
              strokeOpacity={0.94}
              strokeWidth={2.6}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {!isIntro && (
            <>
              <path
                d={quadraticPartialPath(
                  geometry.from,
                  geometry.control,
                  geometry.to,
                  routeProgress,
                )}
                fill="none"
                stroke="#2f2a22"
                strokeLinecap="round"
                strokeWidth={5.5}
                vectorEffect="non-scaling-stroke"
              />
              <circle cx={geometry.from.x} cy={geometry.from.y} fill="#f7edcf" r={10} stroke="#30271d" strokeWidth={3} />
              <circle cx={geometry.from.x} cy={geometry.from.y} fill="#8d4938" r={4} />
            </>
          )}
          <circle
            cx={geometry.to.x}
            cy={geometry.to.y}
            fill="none"
            opacity={0.26 + arrivalPulse * 0.34}
            r={21 + arrivalPulse * 8}
            stroke="#fff1bd"
            strokeWidth={5}
          />
          <circle cx={geometry.to.x} cy={geometry.to.y} fill="#fff1c8" r={16 + arrivalPulse * 2} stroke="#30271d" strokeWidth={4} />
          <circle cx={geometry.to.x} cy={geometry.to.y} fill="#d9563d" r={6.5 + arrivalPulse} />
        </g>
      </svg>

      {!isIntro && (
        <div
          style={{
            background: "#d9563d",
            border: "4px solid #fff1c8",
            borderRadius: "50%",
            boxShadow: "0 0 0 3px #30271d, 0 5px 13px rgba(45,34,20,.25)",
            height: 18,
            left: marker.x,
            position: "absolute",
            top: marker.y,
            transform: "translate(-50%, -50%)",
            width: 18,
          }}
        />
      )}

      {!isIntro && props.showVehicle && (
        <div
          style={{
            alignItems: "center",
            background: "#f5e7bd",
            border: "3px solid #30271d",
            borderRadius: "50%",
            boxShadow: "0 8px 20px rgba(45,34,20,.25)",
            display: "flex",
            height: 62,
            justifyContent: "center",
            left: marker.x,
            position: "absolute",
            top: marker.y,
            transform: `translate(-50%, -50%) rotate(${markerOrientation.angle}deg)`,
            width: 62,
          }}
        >
          <Icon
            color="#30271d"
            size={34}
            strokeWidth={2.4}
            style={{ transform: markerOrientation.flipX ? "scaleX(-1)" : "none" }}
          />
        </div>
      )}

      {labels.map(({ location, point }, index) => (
        <div
          key={`${location.label}-${index}`}
          style={{
            background: "rgba(255,241,200,.98)",
            border: "2px solid rgba(48,39,29,.72)",
            boxShadow: `0 7px ${20 + arrivalPulse * 10}px rgba(65,36,21,.24)`,
            fontFamily: "Arial, sans-serif",
            fontSize: 27,
            fontWeight: 800,
            left: point.x + 28,
            letterSpacing: "0.02em",
            opacity: 1,
            padding: "10px 15px",
            position: "absolute",
            textTransform: "uppercase",
            top: point.y - 78,
            transform: `scale(${1.06 + arrivalPulse * 0.05})`,
            transformOrigin: "left bottom",
          }}
        >
          {location.label}
        </div>
      ))}

      {geographyOnScreen.map((item) => (
        <div
          key={item.id}
          style={{
            background:
              item.kind === "sea"
                ? "linear-gradient(180deg, rgba(236,241,238,0.93), rgba(220,228,226,0.9))"
                : "linear-gradient(180deg, rgba(255,249,232,0.97), rgba(243,227,188,0.95))",
            border:
              item.kind === "sea"
                ? "1px solid rgba(70, 88, 102, 0.42)"
                : "1px solid rgba(74, 56, 38, 0.58)",
            borderRadius: 3,
            boxShadow:
              item.kind === "sea"
                ? "inset 0 0 0 1px rgba(255,255,255,0.28), 0 2px 8px rgba(35, 48, 58, 0.14)"
                : "inset 0 0 0 1px rgba(255,252,240,0.62), 0 2px 8px rgba(45, 34, 20, 0.18)",
            boxSizing: "border-box",
            color: item.kind === "sea" ? "#3a4a58" : "#3a3228",
            fontFamily: "Georgia, 'Times New Roman', serif",
            fontSize: item.fontSize,
            fontStyle: item.kind === "sea" ? "italic" : "normal",
            fontWeight: item.kind === "sea" ? 600 : 700,
            left: item.point.x,
            letterSpacing: item.kind === "sea" ? "0.03em" : "0.045em",
            lineHeight: 1.15,
            maxWidth: item.maxWidth,
            opacity: 0.97,
            overflowWrap: "break-word",
            padding: item.kind === "sea" ? "5px 10px" : "4px 8px",
            pointerEvents: "none",
            position: "absolute",
            textAlign: "center",
            textTransform: item.kind === "sea" ? "none" : "uppercase",
            top: item.point.y,
            transform: "translate(-50%, -50%)",
            whiteSpace: "normal",
          }}
        >
          {item.label}
        </div>
      ))}

      <div
        style={{
          background: "rgba(247,237,207,.78)",
          borderBottom: "5px solid #b65c42",
          left: 76,
          padding: "10px 0 8px",
          position: "absolute",
          top: 58,
        }}
      >
        <div style={{ fontFamily: "Arial, sans-serif", fontSize: 23, fontWeight: 850, letterSpacing: "0.16em", textTransform: "uppercase" }}>
          {localizedMapHeading(props.language, isIntro)} · {props.countryLabel}
        </div>
      </div>

      <div
        style={{
          bottom: 62,
          fontSize: 47,
          fontStyle: "italic",
          left: 76,
          lineHeight: 1.08,
          maxWidth: WIDTH - 360,
          position: "absolute",
          textShadow: "0 2px 12px rgba(247,237,207,.75)",
        }}
      >
          {isIntro ? props.to.label : `${props.from.label} → ${props.to.label}`}
      </div>

      <div
        style={{
          fontSize: 126,
          fontVariantNumeric: "tabular-nums",
          fontWeight: 700,
          lineHeight: 0.82,
          position: "absolute",
          right: 76,
          top: 65,
        }}
      >
        {chapterCountdown(props.chapterCount, props.chapterOrdinal)}
      </div>

      {dust.map((particle, index) => (
        <div
          key={index}
          style={{
            background: "#3d2c1c",
            borderRadius: "50%",
            height: particle.radius,
            left: particle.x,
            opacity: particle.opacity,
            position: "absolute",
            top: particle.y,
            width: particle.radius,
          }}
        />
      ))}
      </div>
      <AbsoluteFill
        style={{
          backgroundColor: "#000",
          opacity: 1 - Math.min(fadeIn, fadeOut),
        }}
      />
    </AbsoluteFill>
  );
};
