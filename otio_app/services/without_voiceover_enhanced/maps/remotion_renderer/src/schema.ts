import { z } from "zod";

const LocationSchema = z.object({
  label: z.string().min(1).max(100),
  longitude: z.number().min(-180).max(180),
  latitude: z.number().min(-85).max(85),
});

export const MapTransitionSchema = z.object({
  mapSequenceId: z.string().uuid(),
  projectId: z.string().uuid(),
  chapterId: z.string().min(1).max(200),
  from: LocationSchema,
  to: LocationSchema,
  exportLabel: z.string().min(1).max(200),
  countryNumericId: z.string().regex(/^\d{1,3}$/),
  countryLabel: z.string().min(1).max(100),
  language: z.string().min(2).max(20),
  chapterOrdinal: z.number().int().min(1).max(500),
  chapterCount: z.number().int().min(1).max(500),
  animationMode: z.enum(["intro", "transition"]),
  transportMode: z.enum(["car", "train", "plane", "boat"]),
  showVehicle: z.boolean(),
  routeKind: z.enum([
    "deterministic_ramp_zoom",
    "deterministic_quadratic_curve",
  ]),
  durationInFrames: z.number().int().min(90).max(900),
  fps: z.number().int().min(24).max(60),
  outputResolution: z.enum(["hd", "4k"]),
  outputWidth: z.union([z.literal(1920), z.literal(3840)]),
  outputHeight: z.union([z.literal(1080), z.literal(2160)]),
  seed: z.string().min(1).max(200),
  styleVersion: z.literal("otio-vintage-map-v12"),
  viewBounds: z.tuple([
    z.tuple([z.number(), z.number()]),
    z.tuple([z.number(), z.number()]),
  ]),
});

export type MapTransitionProps = z.infer<typeof MapTransitionSchema>;

export const defaultMapTransition: MapTransitionProps = {
  mapSequenceId: "9c343f5c-f67c-5abe-99ae-c1cbf6ee20d9",
  projectId: "00000000-0000-4000-8000-000000000001",
  chapterId: "chapter-monument-valley-grand-canyon",
  from: {
    label: "Monument Valley",
    longitude: -109.8568,
    latitude: 36.998,
  },
  to: {
    label: "Grand Canyon",
    longitude: -112.1129,
    latitude: 36.1069,
  },
  exportLabel: "Grand Canyon",
  countryNumericId: "840",
  countryLabel: "USA",
  language: "EN",
  chapterOrdinal: 1,
  chapterCount: 1,
  animationMode: "transition",
  transportMode: "car",
  showVehicle: true,
  routeKind: "deterministic_quadratic_curve",
  durationInFrames: 225,
  fps: 25,
  outputResolution: "4k",
  outputWidth: 3840,
  outputHeight: 2160,
  seed: "monument-valley-grand-canyon-v1",
  styleVersion: "otio-vintage-map-v12",
  viewBounds: [
    [-125, 24],
    [-66, 50],
  ],
};
