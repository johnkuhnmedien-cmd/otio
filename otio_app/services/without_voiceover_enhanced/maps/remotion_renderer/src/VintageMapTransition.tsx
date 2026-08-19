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
  const selected = countryCollection.features.filter(
    (country) => numericCountryId(country) === wanted,
  );
  if (wanted === "372" && northernIreland) selected.push(northernIreland);
  return selected;
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
    return {
      path,
      from,
      to,
      control: controlPoint(from, to, 0.22),
      selected: selectedFeatures(props.countryNumericId),
    };
  }, [props.countryNumericId, props.from, props.to, props.viewBounds]);

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
