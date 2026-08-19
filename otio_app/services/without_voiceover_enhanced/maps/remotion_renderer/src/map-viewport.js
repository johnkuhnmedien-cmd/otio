export function viewportPolygon(viewBounds) {
  const [[west, south], [east, north]] = viewBounds;
  return {
    type: "Polygon",
    // D3 interprets spherical polygon winding differently from flat GeoJSON.
    // This clockwise ring selects the small requested map window instead of
    // its complement (which made a USA view look like a world map).
    coordinates: [
      [
        [west, south],
        [west, north],
        [east, north],
        [east, south],
        [west, south],
      ],
    ],
  };
}
