"""Datenmodelle für Analyse-Ergebnisse (JSON-Ausgabe)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from otio_app.defaults import DEFAULT_GEMINI_MODEL


class VoiceWord(BaseModel):
    start_sec: float
    end_sec: float
    word: str


class VoiceSegment(BaseModel):
    start_sec: float
    end_sec: float
    text: str
    words: List[VoiceWord] = Field(default_factory=list)


class VoiceFileAnalysis(BaseModel):
    path: str
    duration_sec: Optional[float] = None
    segments: List[VoiceSegment] = Field(default_factory=list)
    error: Optional[str] = None


class VoiceAnalysisDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    language: str
    files: List[VoiceFileAnalysis] = Field(default_factory=list)


class MediaProbeInfo(BaseModel):
    duration_sec: Optional[float] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    pixel_format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    container: Optional[str] = None


class CleanMediaEntry(BaseModel):
    original_path: str
    clean_path: Optional[str] = None
    status: str = "pending"
    needs_transcode: bool = False
    decode_ok: bool = True
    probe: Optional[MediaProbeInfo] = None
    error: Optional[str] = None
    transcoded_at: Optional[datetime] = None


class CleanMediaManifest(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folder: str
    entries: List[CleanMediaEntry] = Field(default_factory=list)


class AssetAnalysisSignature(BaseModel):
    """Datei- und Analyse-Identität für Cache-Aktualität (Asset Analysis v3)."""

    analysis_schema_version: str = ""
    prompt_version: str = ""
    sampler_version: str = ""
    resolved_model_id: str = ""
    file_size: Optional[int] = None
    file_mtime_ns: Optional[int] = None
    content_fingerprint: str = ""


class AssetMotionProfile(BaseModel):
    type: str = "unknown"
    intensity: Optional[int] = None
    direction: str = "unknown"
    confidence: Optional[float] = None


class AssetFramingProfile(BaseModel):
    type: str = "medium"
    shot_scale: str = "unknown"


class AssetLookProfile(BaseModel):
    brightness: Optional[int] = None
    contrast: Optional[int] = None
    saturation: Optional[int] = None
    color_temperature: str = "unknown"
    dominant_colors: List[str] = Field(default_factory=list)


class AssetQualityProfile(BaseModel):
    technical_quality: Optional[int] = None
    composition_quality: Optional[int] = None
    visual_appeal: Optional[int] = None
    subject_clarity: Optional[int] = None
    hero_potential: Optional[int] = None
    defect_severity: Optional[int] = None


class AssetDefect(BaseModel):
    type: str = "other"
    severity: int = 0
    note: str = ""


class AssetMediaAnalysis(BaseModel):
    path: str
    description: str = ""
    frames_used: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    asset_id: str = ""
    asset_origin: str = "local_original"
    supplement_request_id: str = ""
    rights_status: str = ""
    source_url: str = ""
    provider: str = ""
    media_type: str = ""
    # Gemessene Mediendauer (ffprobe); optional — ältere Inventare ohne Feld bleiben gültig.
    # Gehört NICHT in den Inventory-Hash (Stale-Erkennung nur inhaltlich).
    duration_seconds: Optional[float] = None
    # Schwarz-/Lead-In am Asset-Anfang (ffmpeg blackdetect); nutzbare Länge =
    # duration_seconds - usable_in_s.
    usable_in_s: Optional[float] = None
    # Strukturierte Frame-Analyse; flache Felder bleiben für Altbestände/Kompatibilität.
    motion: str = ""
    framing: str = ""
    people: Optional[bool] = None
    people_action: Optional[str] = None
    defects: Optional[str] = None
    aspect_ratio: float = 0.0
    aspect_ratio_policy: str = ""
    is_16_9: bool = False
    supplement_validation_status: str = ""
    supplement_validation_score: float = 0.0
    # Woher das beschaffte Asset kam (``funnel``, ``inbox``, ``manual``, …) und
    # die Begründung der Beschaffung. Die Begründung gehört bewusst NICHT in
    # ``description``: dort steht ausschließlich die Bildbeschreibung aus der
    # regulären Asset-Analyse.
    supplement_intake_source: str = ""
    supplement_intake_note: str = ""
    approved_for_cut_plan: bool = False
    generated_prompt: str = ""
    search_query: str = ""
    license_metadata: dict[str, str] = Field(default_factory=dict)
    analysis_status: str = ""
    # Kompatibilität: ab v3 = tatsächlich genutzte/aufgelöste Modell-ID (wie
    # description_model_resolved). Ältere Caches können hier den UI-/Request-Wert
    # enthalten.
    description_model: str = ""
    description_prompt_version: str = ""
    description_generated_at: Optional[datetime] = None
    # Asset Analysis v3 (additiv; fehlende Felder in Alt-JSONs bleiben gültig).
    analysis_schema_version: str = ""
    analysis_scope: str = ""
    analysis_signature: Optional[AssetAnalysisSignature] = None
    analysis_parse_ok: Optional[bool] = None
    analysis_confidence: Optional[float] = None
    caption: str = ""
    content_tags: List[str] = Field(default_factory=list)
    motion_profile: Optional[AssetMotionProfile] = None
    framing_profile: Optional[AssetFramingProfile] = None
    look_profile: Optional[AssetLookProfile] = None
    quality_profile: Optional[AssetQualityProfile] = None
    defect_items: List[AssetDefect] = Field(default_factory=list)
    # UI-/Request-Wert vor resolve_gemini_model (kann leer sein).
    description_model_requested: str = ""
    # Tatsächlich an Gemini übergebene Modell-ID nach resolve_gemini_model.
    description_model_resolved: str = ""
    # Nur bei parse_ok=False diagnostisch (begrenzt); bei Erfolg leer.
    analysis_raw_response: str = ""


#: ``asset_origin`` lokal gedrehter Originale. Alles andere ist beschafftes
#: Material (Stock, Inbox, generiert) und lebt außerhalb des Medienordners.
LOCAL_ORIGINAL_ASSET_ORIGIN = "local_original"


def is_supplement_asset(asset: "AssetMediaAnalysis") -> bool:
    """True für beschafftes Material — unabhängig vom Ablageort.

    Bewusst über ``asset_origin`` statt über den Pfad: Clean Media legt auch
    Originale unter ``_otio*/clean/`` ab, und jeder neue Funnel bringt einen
    neuen Ablageort mit.
    """
    origin = (getattr(asset, "asset_origin", "") or "").strip()
    return bool(origin) and origin != LOCAL_ORIGINAL_ASSET_ORIGIN


def supplement_asset_paths(folder: "AssetFolderAnalysis") -> set[str]:
    """Pfade aller Supplement-Zeilen eines Ordner-Inventars."""
    return {
        asset.path
        for asset in (folder.assets or [])
        if asset.path and is_supplement_asset(asset)
    }


class AssetFolderAnalysis(BaseModel):
    folder: str
    description: str = ""
    media_files: List[str] = Field(default_factory=list)
    frames_used: List[str] = Field(default_factory=list)
    assets: List[AssetMediaAnalysis] = Field(default_factory=list)
    error: Optional[str] = None


class InventoryDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    items: List[AssetFolderAnalysis] = Field(default_factory=list)


class ManualFolderCompletionDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folders: List[str] = Field(default_factory=list)


class VoiceFolderMappingEntry(BaseModel):
    voice_file: str
    folder: Optional[str] = None
    match_method: str = "filename"
    confirmed: bool = False


class VoiceFolderMappingDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    confirmed: bool = False
    entries: List[VoiceFolderMappingEntry] = Field(default_factory=list)


class EditPlanSettings(BaseModel):
    shot_min_sec: float = 3.0
    shot_max_sec: float = 8.0
    audio_offset_sec: float = 1.0
    section_outro_sec: float = 5.0
    video_head_trim_sec: float = 0.5
    video_head_trim_policy: str = "fixed_trim"
    voiceover_trim_policy: str = "disabled"
    voiceover_trim_start_sec: float = 0.0
    voiceover_trim_end_sec: float = 0.0
    text_splitters: List[str] = Field(default_factory=lambda: [", und ", ", ", " und "])
    fallback_order: List[str] = Field(
        default_factory=lambda: ["local", "adobe_stock", "pexels", "gemini_image"]
    )
    gemini_model: str = DEFAULT_GEMINI_MODEL


class VoiceoverPlan(BaseModel):
    """Voice-over-Block im Schnittplan — keine Head-Trim-Regel auf Audio."""

    path: str
    timeline_start_sec: float = 0.0
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    duration_sec: float = 0.0
    timeline_end_sec: float = 0.0
    duration_source: str = "ffprobe"
    trim_policy: str = "disabled"


class EditPlanRule(BaseModel):
    """Eine Schnittregel — dauerhaft pro Projekt gespeichert."""

    id: str
    rule_type: str
    enabled: bool = True
    params: dict[str, int | float | str | bool] = Field(default_factory=dict)
    label: str = ""


class EditPlanRulesDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    rules: List[EditPlanRule] = Field(default_factory=list)
    gemini_prompt: str = ""


class EditPlanShot(BaseModel):
    voice_file: str
    folder: str
    voice_start_sec: float
    voice_end_sec: float
    duration_sec: float
    asset_path: Optional[str] = None
    asset_source: str = "local"
    asset_id: str = ""
    asset_origin: str = ""
    supplement_request_id: str = ""
    rights_status: str = ""
    source_url: str = ""
    provider: str = ""
    media_type: str = ""
    motif: str = ""
    passage_text: str = ""
    confidence: Optional[str] = None
    section_outro: bool = False
    beat_id: str = ""
    coverage_status: str = ""
    match_quality: str = ""


class TimelineItemTransform(BaseModel):
    scaling_mode: str = "fill"
    zoom_x: float = 1.0
    zoom_y: float = 1.0
    position_x: float = 0.0
    position_y: float = 0.0


class TitleStyle(BaseModel):
    """Stil für opening_title — einzige Quelle für Title-Renderer."""

    text: str = ""
    timeline_width: int = 1920
    timeline_height: int = 1080
    duration_sec: float = 5.0
    fps: float = 25.0
    requested_font_family: str = ""
    resolved_font_family: str = ""
    resolved_font_file_path: str = ""
    resolved_font_face_index: int = 0
    font_fallback_used: bool = False
    font_resolution_warning: str = ""
    font_size_px: float = 72.0
    font_color: str = "#FFFFFF"
    shadow_enabled: bool = True
    shadow_color: str = "#000000"
    shadow_opacity: float = 0.5
    shadow_offset_x: float = 3.0
    shadow_offset_y: float = 3.0
    position: str = "lower_third"
    margin_x: int = 76
    margin_y: int = 108
    fade_in_sec: float = 0.35
    fade_out_sec: float = 0.35
    render_format: str = "prores4444"
    alpha_required: bool = True
    render_hash: str = ""
    render_manifest_path: str = ""
    output_mov_path: str = ""
    output_png_path: str = ""


class TimelineItem(BaseModel):
    timeline_item_id: str
    type: str
    section_id: str
    folder_name: str
    voice_file: str = ""
    asset_id: str = ""
    shot_id: str = ""
    resolved_media_path: str = ""
    original_asset_path: Optional[str] = None
    asset_role: str = ""
    timeline_in_sec: float = 0.0
    timeline_out_sec: float = 0.0
    duration_sec: float = 0.0
    final_duration_sec: float = 0.0
    source_in_sec: float = 0.0
    source_out_sec: float = 0.0
    voice_start_sec: float = 0.0
    voice_end_sec: float = 0.0
    selection_reason: str = ""
    confidence: float = 0.0
    transform: TimelineItemTransform = Field(default_factory=TimelineItemTransform)
    warnings: List[str] = Field(default_factory=list)
    media_source_type: str = "local"
    motif: str = ""
    passage_text: str = ""
    allow_black: bool = False
    track: str = "V1"
    title_style: Optional[TitleStyle] = None
    # Legacy-Felder — nur für opening_title / Migration; video_shot bleibt leer.
    text: str = ""
    requested_font_family: str = ""
    resolved_font_family: str = ""
    resolved_font_file_path: str = ""
    font_fallback_used: bool = False
    font_size_px: float = 0.0
    font_size: float = 0.0
    shadow_enabled: bool = False
    shadow_opacity: float = 0.0
    shadow_offset_x: float = 0.0
    shadow_offset_y: float = 0.0
    position: str = ""
    fade_in_sec: float = 0.0
    fade_out_sec: float = 0.0
    render_required: bool = False
    rendered_media_path: str = ""
    render_hash: str = ""
    asset_origin: str = ""
    supplement_request_id: str = ""
    rights_status: str = ""
    source_url: str = ""
    provider: str = ""
    asset_type: str = ""
    match_quality: str = ""
    beat_id: str = ""
    coverage_status: str = ""
    background_style: str = ""
    image_zoom_x: float = 1.0
    image_zoom_y: float = 1.0


class SegmentCoverage(BaseModel):
    beat_id: str
    passage_text: str
    visual_requirement: str = ""
    required_asset_type: str = "video_preferred"
    preferred_mood: str = ""
    preferred_shot_type: str = ""
    must_show: List[str] = Field(default_factory=list)
    avoid_showing: List[str] = Field(default_factory=list)
    local_candidate_asset_ids: List[str] = Field(default_factory=list)
    best_local_match_score: float = 0.0
    best_local_asset_id: str = ""
    coverage_status: str = "LOCAL_MISSING"
    supplement_request_id: Optional[str] = None
    voice_file: str = ""
    folder_name: str = ""
    duration_needed_sec: float = 0.0


class SupplementRequest(BaseModel):
    supplement_request_id: str
    section_id: str
    folder_name: str
    location_name: str = ""
    search_context: str = ""
    beat_id: str
    passage_text: str
    visual_requirement: str = ""
    required_asset_type: str = "video_preferred"
    acceptable_asset_types: List[str] = Field(default_factory=lambda: ["video", "image"])
    duration_needed_sec: float = 5.0
    priority: str = "must_have"
    reason: str = ""
    local_best_asset_id: str = ""
    local_best_match_score: float = 0.0
    status: str = "PENDING_SOURCE_SELECTION"
    allowed_sources: List[str] = Field(
        default_factory=lambda: ["adobe_stock", "pexels", "google_search", "nano_banana"]
    )
    selected_source: Optional[str] = None
    search_queries: dict[str, List[str]] = Field(default_factory=dict)
    search_queries_attempted: List[str] = Field(default_factory=list)
    best_query: str = ""
    query_used: str = ""
    # Phase 11.1 (Cut Plan): vom Aufrufer explizit vorab generierte Queries
    # (z. B. per LLM), die VOR allen deterministischen Fallback-Varianten
    # ausprobiert werden — siehe build_pexels_query_variants/-photo_variants
    # in supplement_search.py. Bewusst ein EIGENES, neues Feld statt die
    # bestehende search_queries-Nutzung zu ändern: Standardwert ist eine
    # leere Liste, sodass sich für JEDEN bestehenden Aufrufer (Produktions-
    # Pipeline eingeschlossen), der dieses Feld nicht setzt, absolut nichts
    # am Suchverhalten ändert.
    llm_generated_queries: List[str] = Field(default_factory=list)
    # Phase 11.2 (Cut Plan): überschreibt die Standard-Kandidatenobergrenze
    # (siehe MAX_CANDIDATES_PER_REQUEST in supplement_sources/pexels.py) nur
    # für DIESEN Request. 0 = Adapter-Standard verwenden (unverändertes
    # Verhalten für alle bestehenden Aufrufer, die dieses Feld nicht setzen).
    max_candidates: int = 0
    allow_broader_search: bool = False
    photo_aspect_policy: str = "prefer_16_9"
    video_aspect_ratio_tolerance: float = 0.03
    generation_prompt: Optional[str] = None
    last_error: str = ""
    last_error_at: Optional[datetime] = None
    failed_url: str = ""
    provider_status_at_failure: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupplementCandidate(BaseModel):
    candidate_id: str
    supplement_request_id: str
    provider: str
    provider_asset_id: str = ""
    title: str = ""
    description: str = ""
    preview_url: str = ""
    download_url: str = ""
    creator: str = ""
    creator_url: str = ""
    license: str = ""
    license_url: str = ""
    rights_status: str = ""
    media_type: str = "video"
    width: int = 0
    height: int = 0
    duration_sec: float = 0.0
    source_page_url: str = ""
    estimated_cost: float = 0.0
    requires_purchase: bool = False
    requires_user_approval: bool = True
    match_score: float = 0.0
    match_reason: str = ""
    status: str = "CANDIDATE"
    provider_status: str = ""
    is_mock: bool = False
    download_enabled: bool = True
    last_error: str = ""
    failed_url: str = ""
    query_used: str = ""
    folder_name: str = ""
    location_name: str = ""
    location_terms_required: List[str] = Field(default_factory=list)
    location_terms_present: List[str] = Field(default_factory=list)
    location_match: str = ""
    pexels_video_file_id: str = ""
    pexels_quality: str = ""
    pexels_file_type: str = ""
    pexels_fps: float = 0.0
    selected_video_file_width: int = 0
    selected_video_file_height: int = 0
    selected_video_file_aspect_ratio: float = 0.0
    aspect_ratio: float = 0.0
    aspect_ratio_policy: str = ""
    is_16_9: bool = False
    supplement_validation_status: str = ""
    supplement_validation_score: float = 0.0
    approved_for_cut_plan: bool = False
    # Phase 12.2a: Adobe-Stock-spezifische Snapshot-Daten aus der Search-API
    # (Rest/Media/1/Search/Files) — analog zu den bestehenden pexels_*-
    # Feldern. adobe_comps enthält die rohe "comps"-Struktur (Standard/
    # Video_HD/Video_4K mit url/width/height je Lizenzvariante), damit eine
    # spätere automatische Lizenzierung/Download (siehe Phase 12.4) weiß,
    # welche Varianten überhaupt verfügbar sind, ohne die Suche zu wiederholen.
    adobe_media_type_id: int = 0
    adobe_is_gentech: bool = False
    adobe_comps: dict = Field(default_factory=dict)
    adobe_content_type: str = ""


class SupplementAssetSidecar(BaseModel):
    asset_id: str
    supplement_request_id: str
    provider: str
    provider_asset_id: str = ""
    source_url: str = ""
    download_url: str = ""
    query_used: str = ""
    location_name: str = ""
    location_match: str = ""
    media_type: str = ""
    aspect_ratio: float = 0.0
    aspect_ratio_policy: str = ""
    is_16_9: bool = False
    supplement_validation_status: str = ""
    supplement_validation_score: float = 0.0
    approved_for_cut_plan: bool = False
    license: str = ""
    license_url: str = ""
    creator: str = ""
    creator_url: str = ""
    acquisition_method: str = ""
    prompt: str = ""
    search_query: str = ""
    original_local_path: str = ""
    downloaded_at: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    original_filename: str = ""
    local_path: str = ""
    file_hash: str = ""
    rights_status: str = ""
    cost: float = 0.0
    requires_attribution: bool = False
    approval_status: str = ""
    model: str = ""
    negative_prompt: str = ""
    generation_aspect_ratio: str = ""
    output_resolution: str = ""
    generation_settings: dict[str, str | float | bool] = Field(default_factory=dict)
    synthid_expected: bool = False


class SupplementRequestsDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    requests: List[SupplementRequest] = Field(default_factory=list)
    candidates: List[SupplementCandidate] = Field(default_factory=list)


class SupplementManifestEntry(BaseModel):
    supplement_request_id: str
    asset_id: str
    local_path: str
    provider: str
    rights_status: str
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupplementManifest(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    entries: List[SupplementManifestEntry] = Field(default_factory=list)


class SupplementErrorEntry(BaseModel):
    request_id: str
    candidate_id: str = ""
    provider: str
    url: str = ""
    query_used: str = ""
    error_type: str
    error_message: str
    http_status: int = 0
    content_type: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action_required: str = ""
    provider_status_at_failure: str = ""


class SupplementErrorDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    errors: List[SupplementErrorEntry] = Field(default_factory=list)


class InventoryDeltaEntry(BaseModel):
    asset_id: str
    path: str
    asset_origin: str
    supplement_request_id: str = ""
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InventoryDeltaDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folder_name: str
    entries: List[InventoryDeltaEntry] = Field(default_factory=list)


class MaxAssetUsageViolation(BaseModel):
    rule: str = "max_asset_usage"
    asset_id: str
    usage_count: int
    max_allowed: int
    severity: str = "BLOCKER"


class EditPlanDocument(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    project_id: str
    folder_name: Optional[str] = None
    confirmed: bool = False
    settings: EditPlanSettings = Field(default_factory=EditPlanSettings)
    voiceover: Optional[VoiceoverPlan] = None
    shots: List[EditPlanShot] = Field(default_factory=list)
    timeline_items: List[TimelineItem] = Field(default_factory=list)
    allow_black_outro: bool = False
    segment_coverage: List[SegmentCoverage] = Field(default_factory=list)
    inventory_hash_at_plan_time: str = ""
    supplement_request_ids: List[str] = Field(default_factory=list)
    plan_generation_notes: List[str] = Field(default_factory=list)
    candidate_status: str = ""
    validation_status: str = ""
    gemini_retry_attempts: int = 0
    used_rules: dict = Field(default_factory=dict)
