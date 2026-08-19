const clamp = (value, minimum = 0, maximum = 1) =>
  Math.min(maximum, Math.max(minimum, value));

const mix = (from, to, progress) => from + (to - from) * progress;

const smootherStep = (progress) => {
  const value = clamp(progress);
  return value * value * value * (value * (value * 6 - 15) + 10);
};

const phase = (progress, start, end) =>
  smootherStep((progress - start) / Math.max(0.0001, end - start));

const mixPoint = (from, to, progress) => ({
  x: mix(from.x, to.x, progress),
  y: mix(from.y, to.y, progress),
});

export function transitionWideScale(from, to, center) {
  const halfDeltaX = Math.abs(to.x - from.x) / 2;
  const halfDeltaY = Math.abs(to.y - from.y) / 2;
  const horizontalFit =
    halfDeltaX < 1 ? Number.POSITIVE_INFINITY : (center.x * 0.72) / halfDeltaX;
  const verticalFit =
    halfDeltaY < 1 ? Number.POSITIVE_INFINITY : (center.y * 0.64) / halfDeltaY;

  return clamp(Math.min(horizontalFit, verticalFit, 2.35), 0.78, 2.35);
}

export function cameraState({ animationMode, progress, from, to, center }) {
  if (animationMode === "intro") {
    const zoom = phase(progress, 0.14, 0.76);
    return {
      focus: mixPoint(center, to, zoom),
      scale: mix(1, 2.55, zoom),
    };
  }

  const travel = smootherStep(progress);
  const closeAmount = (1 + Math.cos(2 * Math.PI * travel)) / 2;
  const wideScale = transitionWideScale(from, to, center);
  return {
    focus: mixPoint(from, to, travel),
    scale: mix(wideScale, 4.25, closeAmount),
  };
}

export function cameraPoint(point, camera, center) {
  return {
    x: center.x + (point.x - camera.focus.x) * camera.scale,
    y: center.y + (point.y - camera.focus.y) * camera.scale,
  };
}

export function uprightAngle(angle) {
  let normalized = ((angle + 180) % 360 + 360) % 360 - 180;
  if (normalized > 90) normalized -= 180;
  if (normalized < -90) normalized += 180;
  return normalized;
}

export function vehicleOrientation(angle) {
  const normalized = ((angle + 180) % 360 + 360) % 360 - 180;
  return {
    angle: uprightAngle(angle),
    flipX: normalized > 90 || normalized < -90,
  };
}
