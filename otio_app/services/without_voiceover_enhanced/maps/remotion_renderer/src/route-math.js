export function controlPoint(from, to, bend = 0.2) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const normalX = -dy / length;
  const normalY = dx / length;
  return {
    x: (from.x + to.x) / 2 + normalX * length * bend,
    y: (from.y + to.y) / 2 + normalY * length * bend,
  };
}

export function quadraticPoint(from, control, to, progress) {
  const t = Math.max(0, Math.min(1, progress));
  const inverse = 1 - t;
  return {
    x:
      inverse * inverse * from.x +
      2 * inverse * t * control.x +
      t * t * to.x,
    y:
      inverse * inverse * from.y +
      2 * inverse * t * control.y +
      t * t * to.y,
  };
}

export function quadraticAngle(from, control, to, progress) {
  const t = Math.max(0, Math.min(1, progress));
  const dx = 2 * (1 - t) * (control.x - from.x) + 2 * t * (to.x - control.x);
  const dy = 2 * (1 - t) * (control.y - from.y) + 2 * t * (to.y - control.y);
  return (Math.atan2(dy, dx) * 180) / Math.PI;
}

export function quadraticPath(from, control, to) {
  return `M ${from.x.toFixed(3)} ${from.y.toFixed(3)} Q ${control.x.toFixed(
    3,
  )} ${control.y.toFixed(3)} ${to.x.toFixed(3)} ${to.y.toFixed(3)}`;
}

export function quadraticPartialPath(from, control, to, progress) {
  const t = Math.max(0, Math.min(1, progress));
  const firstControl = {
    x: from.x + (control.x - from.x) * t,
    y: from.y + (control.y - from.y) * t,
  };
  const secondControl = {
    x: control.x + (to.x - control.x) * t,
    y: control.y + (to.y - control.y) * t,
  };
  const end = {
    x: firstControl.x + (secondControl.x - firstControl.x) * t,
    y: firstControl.y + (secondControl.y - firstControl.y) * t,
  };

  return quadraticPath(from, firstControl, end);
}
