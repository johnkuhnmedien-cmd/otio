export function localizedMapHeading(language, isIntro) {
  const primary = String(language)
    .trim()
    .toLocaleLowerCase()
    .split(/[-_]/, 1)[0];
  const copy = {
    de: ["Reiseziel", "Reiseroute"],
    en: ["Destination", "Travel Route"],
    es: ["Destino", "Ruta de viaje"],
    fr: ["Destination", "Itinéraire"],
    it: ["Destinazione", "Itinerario"],
    nl: ["Bestemming", "Reisroute"],
    pl: ["Cel podróży", "Trasa podróży"],
    pt: ["Destino", "Rota de viagem"],
  };
  const [introHeading, routeHeading] = copy[primary] ?? copy.en;
  return isIntro ? introHeading : routeHeading;
}

export function chapterCountdown(chapterCount, chapterOrdinal) {
  return Math.max(1, chapterCount - chapterOrdinal + 1);
}
