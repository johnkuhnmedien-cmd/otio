import assert from "node:assert/strict";
import test from "node:test";
import {
  controlPoint,
  quadraticAngle,
  quadraticPartialPath,
  quadraticPath,
  quadraticPoint,
} from "../src/route-math.js";
import { geoMercator } from "d3-geo";
import { viewportPolygon } from "../src/map-viewport.js";
import {
  cameraPoint,
  cameraState,
  transitionWideScale,
  uprightAngle,
  vehicleOrientation,
} from "../src/camera-motion.js";
import { chapterCountdown, localizedMapHeading } from "../src/map-copy.js";

test("route math is deterministic and keeps exact endpoints", () => {
  const from = { x: 100, y: 400 };
  const to = { x: 900, y: 220 };
  const control = controlPoint(from, to, 0.25);

  assert.deepEqual(quadraticPoint(from, control, to, 0), from);
  assert.deepEqual(quadraticPoint(from, control, to, 1), to);
  assert.deepEqual(
    quadraticPoint(from, control, to, 0.5),
    quadraticPoint(from, control, to, 0.5),
  );
  assert.match(quadraticPath(from, control, to), /^M 100\.000 400\.000 Q /);
  const midpoint = quadraticPoint(from, control, to, 0.5);
  assert.match(
    quadraticPartialPath(from, control, to, 0.5),
    new RegExp(`${midpoint.x.toFixed(3)} ${midpoint.y.toFixed(3)}$`),
  );
  assert.equal(
    quadraticPartialPath(from, control, to, 1),
    quadraticPath(from, control, to),
  );
  assert.equal(
    quadraticAngle(from, control, to, 0.4),
    quadraticAngle(from, control, to, 0.4),
  );
});

test("USA viewport fills the frame instead of selecting the rest of the world", () => {
  const projection = geoMercator().fitExtent(
    [
      [72, 80],
      [1920 - 72, 1080 - 72],
    ],
    viewportPolygon([
      [-125, 24],
      [-66, 50],
    ]),
  );
  const monumentValley = projection([-109.8568, 36.998]);
  const grandCanyon = projection([-112.1129, 36.1069]);

  assert.ok(projection.scale() > 1000);
  assert.ok(monumentValley[0] > 72 && monumentValley[0] < 1848);
  assert.ok(monumentValley[1] > 80 && monumentValley[1] < 1008);
  assert.ok(grandCanyon[0] > 72 && grandCanyon[0] < 1848);
  assert.ok(grandCanyon[1] > 80 && grandCanyon[1] < 1008);
});

test("intro camera holds wide, performs a ramp zoom, and ends on the first place", () => {
  const center = { x: 960, y: 540 };
  const place = { x: 320, y: 460 };
  const opening = cameraState({
    animationMode: "intro",
    progress: 0,
    from: place,
    to: place,
    center,
  });
  const middle = cameraState({
    animationMode: "intro",
    progress: 0.5,
    from: place,
    to: place,
    center,
  });
  const ending = cameraState({
    animationMode: "intro",
    progress: 1,
    from: place,
    to: place,
    center,
  });

  assert.equal(opening.scale, 1);
  assert.ok(middle.scale > 1 && middle.scale < ending.scale);
  assert.equal(ending.scale, 2.55);
  assert.deepEqual(cameraPoint(place, ending, center), center);
});

test("transition camera zooms out to the midpoint and back into the destination", () => {
  const center = { x: 960, y: 540 };
  const from = { x: 300, y: 500 };
  const to = { x: 1500, y: 380 };
  const opening = cameraState({
    animationMode: "transition",
    progress: 0,
    from,
    to,
    center,
  });
  const wide = cameraState({
    animationMode: "transition",
    progress: 0.5,
    from,
    to,
    center,
  });
  const ending = cameraState({
    animationMode: "transition",
    progress: 1,
    from,
    to,
    center,
  });

  const expectedWideScale = transitionWideScale(from, to, center);
  assert.equal(opening.scale, 4.25);
  assert.ok(Math.abs(wide.scale - expectedWideScale) < 1e-10);
  assert.deepEqual(wide.focus, { x: 900, y: 440 });
  assert.equal(ending.scale, 4.25);
  assert.deepEqual(cameraPoint(to, ending, center), center);

  const justAfterWide = cameraState({
    animationMode: "transition",
    progress: 0.52,
    from,
    to,
    center,
  });
  assert.ok(justAfterWide.focus.x > wide.focus.x);
  assert.ok(justAfterWide.scale > wide.scale);
});

test("transition camera only zooms far out when the route needs the space", () => {
  const center = { x: 960, y: 540 };
  const closeScale = transitionWideScale(
    { x: 850, y: 510 },
    { x: 1020, y: 560 },
    center,
  );
  const mediumScale = transitionWideScale(
    { x: 500, y: 400 },
    { x: 1420, y: 680 },
    center,
  );
  const distantScale = transitionWideScale(
    { x: 40, y: 120 },
    { x: 1880, y: 960 },
    center,
  );

  assert.equal(closeScale, 2.35);
  assert.ok(mediumScale < closeScale);
  assert.equal(distantScale, 0.78);
});

test("vehicle angle stays upright in both travel directions", () => {
  assert.equal(uprightAngle(0), 0);
  assert.equal(uprightAngle(80), 80);
  assert.equal(uprightAngle(100), -80);
  assert.equal(uprightAngle(170), -10);
  assert.equal(uprightAngle(-100), 80);
  assert.equal(uprightAngle(-170), 10);
  assert.deepEqual(vehicleOrientation(10), { angle: 10, flipX: false });
  assert.deepEqual(vehicleOrientation(170), { angle: -10, flipX: true });
  assert.deepEqual(vehicleOrientation(-170), { angle: 10, flipX: true });
});

test("chapter number counts down from total chapters to one", () => {
  assert.equal(chapterCountdown(32, 1), 32);
  assert.equal(chapterCountdown(32, 2), 31);
  assert.equal(chapterCountdown(32, 32), 1);
});

test("map heading follows the language stored in the dramaturgy", () => {
  assert.equal(localizedMapHeading("EN", false), "Travel Route");
  assert.equal(localizedMapHeading("en-US", true), "Destination");
  assert.equal(localizedMapHeading("FR", false), "Itinéraire");
  assert.equal(localizedMapHeading("fr-FR", true), "Destination");
  assert.equal(localizedMapHeading("DE", false), "Reiseroute");
  assert.equal(localizedMapHeading("de-DE", true), "Reiseziel");
  assert.equal(localizedMapHeading("IT", false), "Itinerario");
  assert.equal(localizedMapHeading("it-IT", true), "Destinazione");
  assert.equal(localizedMapHeading("ES", false), "Ruta de viaje");
  assert.equal(localizedMapHeading("pt-BR", true), "Destino");
  assert.equal(localizedMapHeading("xx", false), "Travel Route");
});
