"""Claude-powered document analysis for construction / EOT claims.

Uses the official Anthropic Python SDK. The model classifies an uploaded
document and returns a structured JSON summary (what the document is about and
how it relates to an Extension-of-Time claim).
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from app.schemas.document import DocumentAnalysis

logger = logging.getLogger(__name__)

# Load apps/backend/.env explicitly (works regardless of the working directory)
# so ANTHROPIC_API_KEY / ANTHROPIC_MODEL are available.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Default to the most capable model; override with ANTHROPIC_MODEL if desired
# (e.g. "claude-sonnet-4-6" for lower cost on high volume). Used for the heavier
# delay-event extraction (reasoning over the whole data room).
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")

# Per-document classification is a simple, high-volume task — default to the fast,
# cheap Haiku tier (no extended thinking) so hundreds of documents process quickly.
# Override with ANTHROPIC_ANALYSIS_MODEL (e.g. "claude-sonnet-4-6") for more depth.
ANALYSIS_MODEL = os.getenv("ANTHROPIC_ANALYSIS_MODEL", "claude-haiku-4-5")

# Delay-event extraction needs reasoning but should still be reasonably fast —
# default to Sonnet 4.6. Override with ANTHROPIC_EXTRACTION_MODEL (e.g.
# "claude-opus-4-8" for maximum depth, "claude-haiku-4-5" for maximum speed).
EXTRACTION_MODEL = os.getenv("ANTHROPIC_EXTRACTION_MODEL", "claude-sonnet-4-6")

# The whole data room goes into the delay-event and contractor-admissibility
# prompts, and on a large project that overran the model's context window (1.27M
# tokens against Sonnet 4.6's 1M), failing the run with a 400 "prompt is too
# long". Documents are packed into batches under this budget instead — no
# document is ever truncated, and the per-batch results are consolidated
# afterwards. The budget sits well under the window because dense OCR/table text
# tokenizes worse than prose.
EXTRACTION_BATCH_TOKENS = int(os.getenv("EXTRACTION_BATCH_TOKENS", "250000"))
EXTRACTION_BATCH_CONCURRENCY = int(os.getenv("EXTRACTION_BATCH_CONCURRENCY", "3"))
# Conservative chars-per-token for sizing batches: this data room measured ~2.7,
# and under-estimating tokens is what causes the 400 we are avoiding.
_CHARS_PER_TOKEN = 2.2
# The contractor register is clean prose, not OCR: it measured 3.95 chars/token,
# so it is sized with a ratio of its own — still conservative, but not so
# conservative that a room which fits one request gets split anyway.
_DIGEST_CHARS_PER_TOKEN = 3.5

# Stable instructions — cached as a prompt prefix so repeated calls are cheaper.
SYSTEM_PROMPT = (
    "You are a construction claims analyst supporting Extension of Time (EOT) and "
    "delay/disruption claims under standards such as FIDIC, NEC4 and CPWD. You are "
    "given the extracted text of a single document uploaded to a claim file. Your job "
    "is to classify the document and explain, in plain professional language, what it "
    "is about and how it may be relevant to an EOT claim.\n\n"
    "Rules:\n"
    "- Be specific and factual. Only use information present in the document text.\n"
    "- Do NOT invent clause numbers, dates, parties or figures that are not in the text.\n"
    "- If the text is empty or insufficient, infer cautiously from the filename and "
    "lower your confidence accordingly.\n"
    "- 'document_type' should be a concise category, e.g. 'Engineer's Instruction', "
    "'Site Access Programme', 'Daily Site Diary', 'Correspondence / Letter', "
    "'Baseline Programme', 'Variation Order', 'Notice of Claim', 'Meeting Minutes', "
    "'Payment Application', 'Drawing / Drawing Register' or 'Other'.\n"
    "- 'relevance_to_claim' should state, in one or two sentences, how this document "
    "supports or undermines an EOT/delay claim (e.g. evidences an employer-caused "
    "delay, establishes a contractual notice, records a critical-path activity).\n"
    "- 'confidence' is an integer 0-100 reflecting how confident you are overall.\n"
    "- This is a summary, not a transcription. Keep 'summary' to at most six "
    "sentences, and cap 'key_points' at 8 entries, 'parties' at 10 and 'key_dates' "
    "at 12 — select the most claim-relevant ones. Registers, chronologies and site "
    "diaries can list hundreds of entries; never enumerate them all."
)

# JSON schema mirroring app.schemas.document.DocumentAnalysis (structured outputs).
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "relevance_to_claim": {"type": "string"},
        "supports_eot": {"type": "boolean"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "parties": {"type": "array", "items": {"type": "string"}},
        "key_dates": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "integer"},
    },
    "required": [
        "document_type",
        "title",
        "summary",
        "relevance_to_claim",
        "supports_eot",
        "key_points",
        "parties",
        "key_dates",
        "confidence",
    ],
    "additionalProperties": False,
}

# Output budget for one document classification. The schema can't bound list
# lengths (structured outputs reject maxItems), so the prompt asks for brevity and
# this leaves headroom for a document that ignores it.
_ANALYSIS_MAX_TOKENS = 8000

# Fallbacks for fields missing from a truncated response, so a partial
# classification still validates against DocumentAnalysis.
_ANALYSIS_DEFAULTS = {
    "document_type": "Other",
    "title": "",
    "summary": "",
    "relevance_to_claim": "",
    "supports_eot": False,
    "key_points": [],
    "parties": [],
    "key_dates": [],
    "confidence": 0,
}


@lru_cache(maxsize=1)
def _client() -> anthropic.AsyncAnthropic:
    # Resolves ANTHROPIC_API_KEY from the environment. A generous per-request
    # timeout (and a few built-in retries) so a long streamed extraction — e.g. a
    # whole contract book — is never cut short by the SDK's default time limit.
    #
    # An identity-linked API key isn't bound to a single workspace, so every
    # request has to name the workspace it acts in or the API rejects it with a
    # 400. A plain workspace-scoped key carries that itself and needs no header,
    # so this is sent only when the variable is set.
    headers = {}
    workspace = (os.getenv("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return anthropic.AsyncAnthropic(
        timeout=900.0, max_retries=4, default_headers=headers or None
    )


# ── OCR (for scanned PDFs / images) ─────────────────────────────────────────
# Uses Claude's native vision: the file is sent as a document/image block and the
# model transcribes it. No external OCR engine/binary needed. Bounded by the API's
# 32 MB request limit, so very large files are skipped (caller keeps the empty text).
_OCR_MAX_BYTES = 24 * 1024 * 1024  # safe headroom under the 32 MB request limit
MAX_OCR_CHARS = 60_000             # bound transcription length (token budget)
_IMAGE_MEDIA = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}


async def ocr_document(
    raw: bytes, filename: str, mime: str | None = None, model: str | None = None
) -> str:
    """Transcribe a scanned PDF or image to plain text via Claude vision.

    Returns "" if the file is too large, an unsupported type, or OCR fails — the
    caller then falls back to filename-only classification. `model` overrides the
    default vision model (used to route through a model known to be enabled).
    """
    if not raw or len(raw) > _OCR_MAX_BYTES:
        return ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    b64 = base64.standard_b64encode(raw).decode("ascii")

    if ext == "pdf":
        file_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    elif ext in _IMAGE_MEDIA:
        file_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": _IMAGE_MEDIA[ext], "data": b64},
        }
    else:
        return ""

    instruction = (
        "Transcribe ALL text in this document verbatim as plain text, preserving "
        "reading order and tables as best you can. Output only the transcription, "
        "with no commentary."
    )

    try:
        async with _client().messages.stream(
            model=model or ANALYSIS_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": [file_block, {"type": "text", "text": instruction}]}],
        ) as stream:
            response = await stream.get_final_message()
    except Exception as e:  # noqa: BLE001
        logger.warning("ocr_document failed for %s: %s", filename, e)
        return ""

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text[:MAX_OCR_CHARS]


# ── Robust PDF OCR by page rendering ────────────────────────────────────────
# A single "document" block fails for a PDF over the API's page/size limits (a
# large scanned contract can be hundreds of pages). Instead render each page to
# an image with PyMuPDF and transcribe the pages in small batches — this works
# for scanned PDFs of any length and stays under per-request limits.
#
# Pages are rendered as DOWNSCALED JPEG: a high-resolution colour scan as PNG can
# be 5-15 MB per page and blow past the API's ~5 MB-per-image limit (so every
# vision call is rejected and OCR silently returns nothing). JPEG capped at a
# ~1600 px long edge keeps each page well under a megabyte.
_OCR_PAGE_DPI = 150                                     # legible without bloating tokens
_OCR_MAX_LONG_EDGE = 1600                               # API downsizes above ~1568 px anyway
_OCR_JPEG_QUALITY = int(os.getenv("OCR_JPEG_QUALITY", "70"))
_OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "80"))   # cap work on a huge document
_OCR_PAGE_BATCH = int(os.getenv("OCR_PAGE_BATCH", "3"))  # pages per vision request
_OCR_CONCURRENCY = int(os.getenv("OCR_CONCURRENCY", "5"))  # vision requests in flight at once


def _render_pdf_pages(raw: bytes, max_pages: int) -> list[bytes]:
    """Render up to `max_pages` PDF pages to compact JPEG bytes (PyMuPDF). Blocking.

    Each page is rendered at ~150 DPI, downscaled so its long edge is ≤ ~1600 px,
    and encoded as JPEG — small enough to stay under the API's per-image limit.
    A page that fails to render is skipped rather than aborting the whole file.
    """
    import fitz  # PyMuPDF

    images: list[bytes] = []
    with fitz.open(stream=raw, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            try:
                pix = page.get_pixmap(dpi=_OCR_PAGE_DPI)
                long_edge = max(pix.width, pix.height)
                if long_edge > _OCR_MAX_LONG_EDGE:
                    scale = _OCR_MAX_LONG_EDGE / long_edge
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale * _OCR_PAGE_DPI / 72,
                                                             scale * _OCR_PAGE_DPI / 72))
                # JPEG has no alpha; get_pixmap defaults to alpha=False so this is safe.
                images.append(pix.tobytes("jpg", jpg_quality=_OCR_JPEG_QUALITY))
            except Exception as e:  # noqa: BLE001 — skip an unreadable page, keep the rest
                logger.warning("ocr_pdf_pages: page %d failed to render: %s", i, e)
    return images


async def ocr_pdf_pages(
    raw: bytes,
    *,
    max_pages: int = _OCR_MAX_PAGES,
    max_chars: int = MAX_OCR_CHARS,
    model: str | None = None,
) -> tuple[str, str]:
    """Transcribe a (possibly scanned, possibly large) PDF page by page.

    Renders each page to a compact image and OCRs the pages in batches with Claude
    vision, so it is not bound by the API's whole-PDF page/size limits. Returns
    (text, error): `error` is "" on success, otherwise a short reason (the file
    couldn't be rendered, or the vision calls failed) suitable for surfacing.
    """
    if not raw:
        return "", "empty file"
    try:
        images = await asyncio.to_thread(_render_pdf_pages, raw, max_pages)
    except Exception as e:  # noqa: BLE001
        logger.warning("ocr_pdf_pages: could not render PDF: %s", e)
        return "", f"could not render PDF ({type(e).__name__})"
    if not images:
        logger.warning("ocr_pdf_pages: PDF rendered 0 pages")
        return "", "PDF rendered no pages"

    instruction = (
        "Transcribe ALL text in these document pages verbatim as plain text, in "
        "reading order, preserving clause numbers and tables as best you can. Output "
        "only the transcription, with no commentary."
    )

    # Batches are transcribed concurrently (bounded by _OCR_CONCURRENCY) — a large
    # scan is dozens of vision calls, and running them back-to-back took many
    # minutes. Results are re-joined in page order.
    sem = asyncio.Semaphore(_OCR_CONCURRENCY)

    async def _transcribe_batch(start: int) -> tuple[str, str]:
        """Returns (text, error) for the batch beginning at page `start`."""
        batch = images[start:start + _OCR_PAGE_BATCH]
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(img).decode("ascii"),
                },
            }
            for img in batch
        ]
        content.append({"type": "text", "text": instruction})
        async with sem:
            try:
                async with _client().messages.stream(
                    model=model or ANALYSIS_MODEL,
                    max_tokens=8000,
                    messages=[{"role": "user", "content": content}],
                ) as stream:
                    response = await stream.get_final_message()
            except Exception as e:  # noqa: BLE001
                logger.warning("ocr_pdf_pages: vision call failed on batch at page %d: %s", start, e)
                return "", f"{type(e).__name__}: {str(e)[:160]}"
        return "".join(b.text for b in response.content if b.type == "text").strip(), ""

    results = await asyncio.gather(
        *(_transcribe_batch(start) for start in range(0, len(images), _OCR_PAGE_BATCH))
    )
    parts = [text for text, _err in results if text]
    last_err = next((err for _text, err in reversed(results) if err), "")
    ok_batches = sum(1 for _text, err in results if not err)

    text = "\n\n".join(parts).strip()[:max_chars]
    if text:
        return text, ""
    if ok_batches and not last_err:
        return "", "pages transcribed as blank (no readable text on the pages)"
    return "", last_err or "vision OCR produced no text"


def _salvage_truncated_object(payload: str) -> dict | None:
    """Recover the complete members of a JSON object that was cut off mid-write.

    A response that stops at max_tokens ends with a half-written key or value, so
    `json.loads` rejects the whole payload and a document the model had otherwise
    classified fine is recorded as a failure. Replaying the payload and remembering
    the last position where a top-level member ended keeps the fields it finished.
    Returns None if not even one member completed.
    """
    start = payload.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    last_member_end = -1  # index of the comma closing the last complete member

    for i in range(start, len(payload)):
        ch = payload[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:  # the object did close — it wasn't truncated after all
                try:
                    return json.loads(payload[start : i + 1])
                except json.JSONDecodeError:
                    return None
        elif ch == "," and depth == 1:
            last_member_end = i

    if last_member_end == -1:
        return None
    try:
        return json.loads(payload[start:last_member_end] + "}")
    except json.JSONDecodeError:
        return None


def _build_user_content(text: str, filename: str, truncated: bool, claim_context: str) -> str:
    parts = [f"Filename: {filename}"]
    if claim_context:
        parts.append(claim_context)
    if text:
        note = " (truncated)" if truncated else ""
        parts.append(f"\n--- Extracted document text{note} ---\n{text}")
    else:
        parts.append(
            "\n(No machine-readable text could be extracted — classify from the "
            "filename and lower your confidence.)"
        )
    return "\n".join(parts)


async def analyze_document(
    *,
    text: str,
    filename: str,
    truncated: bool,
    claim_ref: str | None = None,
    claim_title: str | None = None,
    standard: str | None = None,
) -> DocumentAnalysis:
    """Classify and summarise a document via Claude; returns a validated model."""
    ctx_bits = []
    if claim_ref:
        ctx_bits.append(f"Associated claim: {claim_ref}")
    if claim_title:
        ctx_bits.append(f"Claim subject: {claim_title}")
    if standard:
        ctx_bits.append(f"Contract standard: {standard}")
    claim_context = " | ".join(ctx_bits)

    user_content = _build_user_content(text, filename, truncated, claim_context)

    # Classification is simple — run it on the fast model with no extended thinking
    # and no effort knob (Haiku doesn't take `effort`). Structured output guarantees
    # the JSON shape. This keeps per-document latency and cost low at scale.
    #
    # max_tokens is generous relative to the summary we ask for: structured output
    # guarantees the shape but not that it fits, and a document with hundreds of
    # dated entries (a chronology, a site-diary register) will happily run past a
    # tight ceiling and leave the JSON cut off mid-string.
    response = await _client().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=_ANALYSIS_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA},
        },
    )

    # With output_config.format, the JSON is guaranteed in the text block.
    payload = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        # Truncated at max_tokens. Keep whatever the model completed rather than
        # failing the document outright — the leading fields (type, title, summary)
        # are the ones the data room actually shows.
        data = _salvage_truncated_object(payload)
        if data is None:
            raise ValueError(
                "The AI response was cut short before any field completed "
                f"(stop_reason={response.stop_reason}). Try analysing again."
            ) from None
        logger.warning(
            "analyze_document: truncated response for %s (stop_reason=%s); "
            "salvaged %d of %d fields",
            filename, response.stop_reason, len(data), len(_ANALYSIS_DEFAULTS),
        )
    return DocumentAnalysis(**{**_ANALYSIS_DEFAULTS, "title": filename, **data})


# ── Delay-event extraction ──────────────────────────────────────────────────
# Reads the project's data-room documents and drafts a register of delay events
# for an EOT claim. Output mirrors app.schemas.delay_event so the events drop
# straight into the same table the analyst reviews.

_EVENTS_SYSTEM_PROMPT = (
    "You are a forensic delay analyst building an Extension of Time (EOT) claim "
    "under standards such as FIDIC, NEC4 and CPWD. You are given the extracted "
    "text of the documents uploaded to a project's data room. Identify discrete "
    "delay events evidenced by these documents.\n\n"
    "Rules:\n"
    "- Only assert events that the document text actually supports. Do NOT invent "
    "events, dates, clause numbers, parties or figures that are not present.\n"
    "- If the documents contain no evidence of any delay event, return an empty list.\n"
    "- 'cause' attributes responsibility: 'Employer' (incl. Engineer), "
    "'Contractor', 'Concurrent', 'Force Majeure', or 'Neutral'.\n"
    "- 'clause' is the contractual basis of the event. When the input includes a "
    "PROJECT CLAUSE LIBRARY section, you MUST choose from that library: cite the "
    "clause number(s) whose provisions entitle the party to relief for this event, "
    "copied EXACTLY as numbered there (e.g. 'Sub-Clause 8.5, Sub-Clause 20.2'). Do "
    "not cite clause numbers from memory or from a different contract edition — a "
    "number not present in the library is wrong unless the documents themselves "
    "quote it. A library clause marked [Modified by Particular Conditions] applies "
    "as amended. Only when no clause library is provided may you cite the clause "
    "evidenced by the document text alone.\n"
    "- 'admissibility' is your view of whether the event would succeed as an EOT "
    "claim: 'Likely admissible', 'At risk', 'Inadmissible', or 'Not assessed'.\n"
    "- 'daysImpact' is the integer number of days of delay; 0 if unknown.\n"
    "- 'startDate'/'endDate' use ISO format (YYYY-MM-DD); empty string if unknown.\n"
    "- 'sourceDocuments' lists the exact filenames (from those provided) that "
    "evidence the event.\n"
    "- 'chronology' is the ordered sequence of correspondence/site events; each "
    "actor is 'Contractor', 'Engineer', 'Employer' or 'System'. Keep it to the key "
    "milestones (roughly 3-8 entries) with a one-line 'detail' — the full "
    "submission chronology is drafted separately, so do not expand it here.\n"
    "- 'narrative' is a tight paragraph, not a full write-up.\n"
    "- 'aiConfidence' is an integer 0-100 for how well the documents support the event."
)

_EVENT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "category": {"type": "string"},
        "narrative": {"type": "string"},
        "cause": {
            "type": "string",
            "enum": ["Employer", "Contractor", "Concurrent", "Force Majeure", "Neutral"],
        },
        "clause": {"type": "string"},
        "startDate": {"type": "string"},
        "endDate": {"type": "string"},
        "daysImpact": {"type": "integer"},
        "criticalPath": {"type": "boolean"},
        "admissibility": {
            "type": "string",
            "enum": ["Likely admissible", "At risk", "Inadmissible", "Not assessed"],
        },
        "aiConfidence": {"type": "integer"},
        "sourceDocuments": {"type": "array", "items": {"type": "string"}},
        "chronology": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "actor": {
                        "type": "string",
                        "enum": ["Contractor", "Engineer", "Employer", "System"],
                    },
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["date", "actor", "title", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "title", "category", "narrative", "cause", "clause", "startDate", "endDate",
        "daysImpact", "criticalPath", "admissibility", "aiConfidence",
        "sourceDocuments", "chronology",
    ],
    "additionalProperties": False,
}

_EVENTS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": _EVENT_ITEM_SCHEMA}},
    "required": ["events"],
    "additionalProperties": False,
}


def _clause_library_block(clauses: list[dict]) -> str:
    """Render a project's Clause Library as a prompt section the model cites from.

    One line per clause — exact number, title, a trimmed summary and the PCC
    modification flag — so events can only reference clauses that actually exist
    in this project's library.
    """
    lines = ["\n===== PROJECT CLAUSE LIBRARY (the 'clause' field must cite these exact numbers) ====="]
    for c in clauses:
        num = (c.get("clause_number") or "").strip()
        title = (c.get("clause_title") or "").strip()
        if not num and not title:
            continue
        desc = " ".join((c.get("clause_description") or "").split())
        if len(desc) > 220:
            desc = desc[:220].rstrip() + "…"
        line = f"- {num} — {title}" if num else f"- {title}"
        if desc:
            line += f": {desc}"
        if c.get("modified"):
            note = " ".join((c.get("modification_note") or "").split())
            line += f" [Modified by Particular Conditions{': ' + note if note else ''}]"
        lines.append(line)
    return "\n".join(lines)


def _salvage_array_items(payload: str, key: str) -> list[dict]:
    """Recover the complete objects from a `{"key": [ ... ` array cut off mid-write.

    A response that stops at max_tokens leaves valid JSON objects followed by a
    half-written one, so `json.loads` fails on the whole payload and every event
    is lost. Scanning for balanced top-level objects inside the array keeps the
    ones the model did finish.
    """
    start = payload.find(f'"{key}"')
    if start == -1:
        return []
    start = payload.find("[", start)
    if start == -1:
        return []

    items: list[dict] = []
    depth = 0
    obj_start = -1
    in_string = False
    escaped = False
    for i in range(start + 1, len(payload)):
        ch = payload[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start != -1:
                try:
                    items.append(json.loads(payload[obj_start : i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = -1
        elif ch == "]" and depth == 0:
            break
    return items


def _batch_by_token_budget(
    documents: list[dict],
    budget_tokens: int,
    chars_per_token: float = _CHARS_PER_TOKEN,
) -> list[list[dict]]:
    """Pack documents into batches that each fit inside a single request.

    Greedy and order-preserving, so documents uploaded together stay together and
    one event's evidence usually lands in a single batch. A document larger than
    the budget gets a batch of its own rather than being cut short: batching
    exists precisely so that no document has to be truncated.

    `chars_per_token` defaults to the ratio for raw extracted text. Pass a higher
    one for prose — the register in `_contractor_digest` measures 3.95, so sizing
    it at 2.2 would split a room that comfortably fits one request, and a needless
    split doubles the cost of every batch of events that reads it.
    """
    budget_chars = max(1, int(budget_tokens * chars_per_token))
    batches: list[list[dict]] = []
    current: list[dict] = []
    used = 0
    for d in documents:
        # +200 covers the "===== Document: name [type] =====" header and newlines.
        size = len(d.get("text") or "") + len(d.get("name") or "") + 200
        if current and used + size > budget_chars:
            batches.append(current)
            current, used = [], 0
        current.append(d)
        used += size
    if current:
        batches.append(current)
    return batches or [[]]


async def extract_delay_events(
    *,
    documents: list[dict],
    standard: str | None = None,
    project_name: str | None = None,
    clauses: list[dict] | None = None,
) -> list[dict]:
    """Draft a register of delay events from the project's documents.

    `documents` is a list of {"name", "type", "text", "truncated"} dicts.
    `clauses` is the project's Clause Library (project_clauses rows); when given,
    each event's 'clause' field is constrained to cite those exact clause numbers,
    so the Delay Events tab links straight back to the library. Returns a list of
    plain dicts (one per event) with the AI's structured fields plus the raw
    `sourceDocuments` filenames — the caller maps those to source records.

    A data room that doesn't fit the model's context window is split into batches
    that are extracted in parallel and then consolidated. Batching never drops or
    truncates a document — every page still reaches the model — so the only cost
    is that one event can be drafted twice from evidence that landed in different
    batches, which the consolidation pass folds back into a single event.
    """
    ctx_bits = []
    if project_name:
        ctx_bits.append(f"Project: {project_name}")
    if standard:
        ctx_bits.append(f"Contract standard: {standard}")
    header = " | ".join(ctx_bits)

    batches = _batch_by_token_budget(documents, EXTRACTION_BATCH_TOKENS)
    if len(batches) == 1:
        return await _extract_events_batch(batches[0], header, clauses)

    logger.info(
        "Data room exceeds one request — extracting delay events from %d documents "
        "in %d batches",
        len(documents),
        len(batches),
    )
    sem = asyncio.Semaphore(max(1, EXTRACTION_BATCH_CONCURRENCY))

    async def _run(index: int, batch: list[dict]) -> list[dict]:
        async with sem:
            return await _extract_events_batch(
                batch, header, clauses, part=(index + 1, len(batches))
            )

    drafted = await asyncio.gather(*(_run(i, b) for i, b in enumerate(batches)))
    return await _consolidate_delay_events([ev for batch in drafted for ev in batch])


async def _extract_events_batch(
    documents: list[dict],
    header: str,
    clauses: list[dict] | None,
    part: tuple[int, int] | None = None,
) -> list[dict]:
    """Run one delay-event extraction request over `documents`.

    `part` is (n, total) when the data room was split; it tells the model it is
    seeing one slice, so it doesn't reason about what the project as a whole is
    missing.
    """
    blocks = [header] if header else []
    if part:
        blocks.append(
            f"\nNOTE: these are part {part[0]} of {part[1]} of this project's data "
            "room. Identify the events evidenced by the documents below. The other "
            "parts are analysed separately, so do not conclude that a document or a "
            "piece of evidence is absent from the project as a whole."
        )
    for d in documents:
        name = d.get("name", "document")
        note = " (truncated)" if d.get("truncated") else ""
        body = d.get("text") or "(no machine-readable text — classify from the filename)"
        blocks.append(f"\n===== Document: {name} [{d.get('type', 'Other')}]{note} =====\n{body}")
    # The library goes last so the exact clause numbers are freshest when the
    # model writes each event's 'clause' field.
    if clauses:
        blocks.append(_clause_library_block(clauses))
    user_content = "\n".join(blocks)

    # Stream so a long generation doesn't hit the SDK's non-streaming timeout, and
    # use adaptive thinking at low effort for a good speed/quality balance.
    async with _client().messages.stream(
        model=EXTRACTION_MODEL,
        # A data room with a hundred documents yields a long register — each event
        # carries a narrative plus a chronology. At 8k the JSON was cut off mid-
        # string and the whole run failed to parse, so the budget matches the
        # chronology generator's.
        max_tokens=32000,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": _EVENTS_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _EVENTS_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = "".join(b.text for b in response.content if b.type == "text").strip()
    if not payload:
        raise ValueError(
            "The AI returned no delay events "
            f"(stop_reason={response.stop_reason}). Please run the identification again."
        )
    try:
        return json.loads(payload).get("events", [])
    except json.JSONDecodeError:
        # Keep whatever the model finished before it ran out of room rather than
        # losing the entire register to one half-written event.
        salvaged = _salvage_array_items(payload, "events")
        if salvaged:
            logger.warning(
                "Delay-event output truncated (stop_reason=%s); salvaged %d event(s)",
                response.stop_reason,
                len(salvaged),
            )
            return salvaged
        if response.stop_reason == "max_tokens":
            raise ValueError(
                "The delay-event register was cut off before completion — the data "
                "room may be very large. Please try again."
            ) from None
        raise ValueError(
            "The AI returned a malformed delay-event register. Please try again."
        ) from None


# ── Consolidating a batched register ────────────────────────────────────────
# Batches see different slices of the data room, so one delay can be drafted more
# than once — e.g. from the notice in one batch and the site records in another.
# The model only identifies which drafts describe the same event; the merge below
# is done in code, so consolidation can never lose an event or rewrite its text.

_EVENT_MERGE_SYSTEM_PROMPT = (
    "You are a forensic delay analyst consolidating a draft Extension of Time "
    "register. It was drafted in several passes over different parts of one "
    "project's data room, so the same underlying delay can appear more than once, "
    "worded differently and supported by different documents.\n\n"
    "You are given a numbered list of draft events. Return groups of index numbers "
    "that describe the SAME underlying delay event.\n\n"
    "Rules:\n"
    "- Group only genuine duplicates: the same disruption to the same part of the "
    "works over the same period, however differently the two drafts word it.\n"
    "- Events sharing a cause but affecting different work fronts, locations, "
    "trades or periods are DIFFERENT events — do not group them. Overlapping dates "
    "alone are not evidence of a duplicate.\n"
    "- Merging two distinct events is far more damaging than leaving a duplicate, "
    "so group only where you are confident.\n"
    "- Each index may appear in at most one group. Leave out events with no "
    "duplicate; return an empty list if nothing duplicates."
)

_EVENT_MERGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "duplicates": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "integer"}},
        }
    },
    "required": ["duplicates"],
    "additionalProperties": False,
}


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _events_for_merge(events: list[dict]) -> str:
    """Render the drafted register as a compact numbered list for the merge pass."""
    lines = []
    for i, e in enumerate(events):
        narrative = " ".join((e.get("narrative") or "").split())[:300]
        sources = ", ".join((e.get("sourceDocuments") or [])[:6])
        lines.append(
            f"[{i}] {e.get('title', '')} | {e.get('category', '')} "
            f"| cause={e.get('cause', '')} "
            f"| {e.get('startDate') or 'unknown'} to {e.get('endDate') or 'unknown'} "
            f"| clause={e.get('clause', '')}\n"
            f"    {narrative}\n"
            f"    sources: {sources}"
        )
    return "\n".join(lines)


def _combine_events(group: list[dict], primary: dict) -> dict:
    """Merge duplicate drafts into one event, keeping the union of their evidence."""
    merged = dict(primary)
    # Lead with the primary draft's evidence, so the source list reads in the
    # same order as the narrative that was kept.
    ordered = [primary] + [e for e in group if e is not primary]

    names: list[str] = []
    for e in ordered:
        for n in e.get("sourceDocuments") or []:
            if n not in names:
                names.append(n)
    merged["sourceDocuments"] = names

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for e in ordered:
        for c in e.get("chronology") or []:
            key = (
                (c.get("date") or "").strip(),
                " ".join((c.get("title") or "").split()).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(c)
    # Undated rows sort last rather than jumping to the top of the chronology.
    rows.sort(key=lambda c: (c.get("date") or "9999-12-31"))
    merged["chronology"] = rows

    starts = [s for s in ((e.get("startDate") or "").strip() for e in group) if s]
    ends = [s for s in ((e.get("endDate") or "").strip() for e in group) if s]
    if starts:
        merged["startDate"] = min(starts)
    if ends:
        merged["endDate"] = max(ends)
    merged["daysImpact"] = max(_as_int(e.get("daysImpact")) for e in group)
    merged["aiConfidence"] = max(_as_int(e.get("aiConfidence")) for e in group)
    merged["criticalPath"] = any(bool(e.get("criticalPath")) for e in group)
    return merged


def _fold_duplicate_events(events: list[dict], groups) -> list[dict]:
    """Apply the merge pass's duplicate groups, preserving the drafted order.

    Every index the model returns is validated against the register, so a
    malformed or out-of-range group is ignored rather than corrupting the result.
    """
    claimed: set[int] = set()
    folded: dict[int, dict] = {}
    dropped: set[int] = set()
    for group in groups or []:
        if not isinstance(group, list):
            continue
        idxs = [
            i
            for i in dict.fromkeys(group)
            if isinstance(i, int)
            and not isinstance(i, bool)
            and 0 <= i < len(events)
            and i not in claimed
        ]
        if len(idxs) < 2:
            continue
        claimed.update(idxs)
        # The best-supported draft leads; the others fold their evidence into it.
        primary = max(
            idxs,
            key=lambda i: (
                _as_int(events[i].get("aiConfidence")),
                len(events[i].get("narrative") or ""),
            ),
        )
        folded[primary] = _combine_events([events[i] for i in idxs], events[primary])
        dropped.update(i for i in idxs if i != primary)
    if not folded:
        return events
    return [folded.get(i, e) for i, e in enumerate(events) if i not in dropped]


async def _consolidate_delay_events(events: list[dict]) -> list[dict]:
    """Fold events that separate batches drafted from the same underlying delay.

    Only ever merges: no event is dropped, and a merged event keeps the union of
    both drafts' source documents and chronology. If the consolidation call fails
    the register is returned exactly as drafted — a visible duplicate is a much
    smaller problem than a lost event.
    """
    if len(events) < 2:
        return events
    try:
        async with _client().messages.stream(
            model=EXTRACTION_MODEL,
            # The output is just index groups; the budget is for the reasoning.
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _EVENT_MERGE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": _events_for_merge(events)}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": _EVENT_MERGE_OUTPUT_SCHEMA},
            },
        ) as stream:
            response = await stream.get_final_message()
        payload = "".join(b.text for b in response.content if b.type == "text").strip()
        groups = json.loads(payload).get("duplicates", []) if payload else []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Delay-event consolidation failed (%s) — keeping all %d drafted events",
            exc,
            len(events),
        )
        return events

    merged = _fold_duplicate_events(events, groups)
    if len(merged) != len(events):
        logger.info(
            "Consolidated %d drafted delay events into %d", len(events), len(merged)
        )
    return merged

# ── Per-event chronology generation ─────────────────────────────────────────
# Builds a detailed, dated chronology for EACH existing delay event, grounded in
# the project's data-room documents. The output replaces each event's
# `chronology` (see services/chronology_generation.py), so the Chronology tab
# tells the story of every delay from the correspondence and site records.

_CHRONOLOGY_SYSTEM_PROMPT = (
    "You are a forensic delay analyst drafting the DELAY EVENT NARRATIVE section of "
    "an Extension of Time (EOT) claim submission under standards such as FIDIC, NEC4 "
    "and CPWD. You are given (1) the register of delay events already identified for "
    "a project — each with a reference such as 'DE-01' — and (2) the extracted text "
    "of the project's data-room documents. For EACH delay event you must produce the "
    "full submission-quality write-up that a claims consultant would put in front of "
    "the Engineer: an introduction, a delay event timeline, a dated chronology of the "
    "correspondence, a cause & effect analysis and a statement of contractual "
    "entitlement.\n\n"
    "House style — this is a formal claim document, not a summary:\n"
    "- Write in the third person, past tense, naming the parties by their contractual "
    "role ('the Contractor', 'the Engineer', 'the Employer'), never 'we' or 'they'.\n"
    "- Spell dates out in full in prose, e.g. 'On 20 April 2025 the Engineer issued…'.\n"
    "- Quote letter references, RFC/EI/RFI numbers, sub-clause numbers, day counts and "
    "figures EXACTLY as they appear in the documents.\n"
    "- Do NOT use bullet points, markdown headings or markdown emphasis anywhere. Plain "
    "prose paragraphs only; separate paragraphs with a blank line.\n\n"
    "Rules:\n"
    "- Produce one write-up per delay event, keyed by the event's EXACT 'eventRef' as "
    "given (e.g. 'DE-01'). Do not invent event references.\n"
    "- Ground EVERY statement in the document text. Do NOT invent dates, letters, "
    "parties, clause numbers or figures the documents do not support. Where the record "
    "is silent, say so rather than filling the gap.\n\n"
    "Field by field:\n"
    "- 'introduction' — one to three paragraphs introducing what the event concerns, "
    "how it arose, the instruction or change that triggered it, and the Contractor's "
    "overall position on time and cost relief.\n"
    "- 'timeline' — one or two paragraphs narrating the event from commencement to "
    "closure: the date it commenced and what commenced it, the principal phases in "
    "between, and the date and basis on which it closed. If the event is still "
    "ongoing at the end of the record, state that it remains ongoing and that the cut "
    "off date is adopted as the event closure date for the purpose of the delay "
    "analysis.\n"
    "- 'chronology' — the ordered sequence of correspondence and site events, earliest "
    "first, one entry per letter, instruction, meeting, submission or response found "
    "in the documents. Be exhaustive: this is the evidential backbone of the claim, so "
    "include every step the documents record, not a selection.\n"
    "- 'causeEffect' — one or two paragraphs identifying the PRIMARY cause of the event "
    "and tracing it through to its effect on the Works: what was prevented or deferred, "
    "which activities and which parties were affected, and how the effect reached the "
    "Time for Completion.\n"
    "- 'entitlement' — one to three paragraphs setting out the contractual basis of the "
    "claim: the sub-clauses relied on for extension of time and for cost, how the "
    "Contractor complied with any notice or particulars requirement, and any "
    "reservation of rights. Cite only sub-clauses that appear in the documents or in "
    "the event's own recorded clause.\n\n"
    "Chronology entries:\n"
    "- Order by date, earliest first.\n"
    "- 'date' is ISO format (YYYY-MM-DD); use an empty string only when the document "
    "gives no date.\n"
    "- 'endDate' is ISO format (YYYY-MM-DD) and records when the step CONCLUDED — it "
    "drives the timeline bars, so set it on every entry that has a 'date'. For a step "
    "that happened on a single day (a letter issued, an instruction served, a meeting "
    "held, a submission made), set 'endDate' EQUAL to 'date'. For a step that ran over "
    "a period (a review or approval cycle, a series of workshops, a period of "
    "inactivity awaiting a response, an ongoing delay), set it to the date the "
    "documents record the step as concluding — and where the step is still running at "
    "the end of the record, use the cut-off date adopted for the analysis. Never guess "
    "a duration the documents do not support: if the step's end is genuinely unknown, "
    "use an empty string.\n"
    "- 'actor' is who took the step: 'Contractor', 'Engineer', 'Employer' or 'System'.\n"
    "- 'title' is a short one-line description of the step (e.g. 'RFC-058 issued — "
    "quotation requested under Sub-Clause 13.3A').\n"
    "- 'detail' is the substance of that step written as claim prose — typically two to "
    "five sentences opening with the date, e.g. 'On 20 April 2025, pursuant to "
    "Sub-Clause 13.3A, the Engineer requested the Contractor to submit a quotation "
    "for…'. Carry across the reference numbers, durations, positions taken and "
    "determinations recorded in the document.\n"
    "- 'sourceDocument' is the EXACT filename (from those provided) that evidences the "
    "step; use an empty string if no single document does.\n"
    "- If a delay event has no supporting record in the documents, return an empty "
    "chronology array and empty strings for the narrative fields rather than inventing "
    "content."
)

_CHRON_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        # Equal to `date` for a single-day step, later for one that ran over a
        # period. Drives the Gantt bars in the Chronology tab's timeline view.
        "endDate": {"type": "string"},
        "actor": {
            "type": "string",
            "enum": ["Contractor", "Engineer", "Employer", "System"],
        },
        "title": {"type": "string"},
        "detail": {"type": "string"},
        "sourceDocument": {"type": "string"},
    },
    "required": ["date", "endDate", "actor", "title", "detail", "sourceDocument"],
    "additionalProperties": False,
}

_EVENT_CHRON_SCHEMA = {
    "type": "object",
    "properties": {
        "eventRef": {"type": "string"},
        "introduction": {"type": "string"},
        "timeline": {"type": "string"},
        "chronology": {"type": "array", "items": _CHRON_ITEM_SCHEMA},
        "causeEffect": {"type": "string"},
        "entitlement": {"type": "string"},
    },
    "required": [
        "eventRef", "introduction", "timeline", "chronology", "causeEffect", "entitlement",
    ],
    "additionalProperties": False,
}

_CHRONOLOGY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"chronologies": {"type": "array", "items": _EVENT_CHRON_SCHEMA}},
    "required": ["chronologies"],
    "additionalProperties": False,
}


def _events_for_chronology(events: list[dict]) -> str:
    """Render the delay-event register compactly so the model can key chronologies
    back to each event by its exact reference."""
    lines = []
    for e in events:
        lines.append(
            f"\n### {e.get('ref', '')} — {e.get('title', '')}\n"
            f"Cause: {e.get('cause', '')} | Period: {e.get('startDate', '')} → "
            f"{e.get('endDate', '')} | Days impact: {e.get('daysImpact', 0)}\n"
            f"Narrative: {e.get('narrative', '')}"
        )
        srcs = e.get("sources") or []
        if srcs:
            lines.append("Linked documents: " + ", ".join(s.get("name", "") for s in srcs))
    return "\n".join(lines) or "(no delay events)"


# A submission-quality write-up runs to a few thousand tokens per event, so the
# register is processed in small batches rather than one call — otherwise the
# output budget truncates the JSON on projects with many delay events.
CHRONOLOGY_BATCH_SIZE = int(os.getenv("CHRONOLOGY_BATCH_SIZE", "3"))


async def generate_event_chronologies(
    *,
    events: list[dict],
    documents: list[dict],
    project_name: str | None = None,
    standard: str | None = None,
    on_progress=None,
) -> list[dict]:
    """Build the full narrative + dated chronology per delay event from the documents.

    `events` is the stored delay-event register (each with a 'ref'); `documents`
    is a list of {"name", "type", "text", "truncated"} dicts. Returns a list of
    {"eventRef", "introduction", "timeline", "causeEffect", "entitlement",
    "chronology": [{date, actor, title, detail, sourceDocument}]} — the caller maps
    each entry back onto its event by ref.

    Events are processed in batches of `CHRONOLOGY_BATCH_SIZE`. The data room is
    identical across batches and is sent as a cached content block, so only the
    first batch pays for it. `on_progress(done, total)` is called after each batch.
    """
    ctx_bits = []
    if project_name:
        ctx_bits.append(f"Project: {project_name}")
    if standard:
        ctx_bits.append(f"Contract standard: {standard}")
    header = " | ".join(ctx_bits)

    doc_blocks = [header] if header else []
    for d in documents:
        name = d.get("name", "document")
        note = " (truncated)" if d.get("truncated") else ""
        body = d.get("text") or "(no machine-readable text — use the filename only)"
        doc_blocks.append(f"\n===== Document: {name} [{d.get('type', 'Other')}]{note} =====\n{body}")
    docs_text = "\n".join(doc_blocks)

    batches = [
        events[i : i + CHRONOLOGY_BATCH_SIZE]
        for i in range(0, len(events), max(1, CHRONOLOGY_BATCH_SIZE))
    ]

    async def _run_batch(batch: list[dict]) -> list[dict]:
        async with _client().messages.stream(
            model=EXTRACTION_MODEL,
            # The narrative sections plus an exhaustive chronology are long; a
            # small budget silently truncates the JSON mid-event.
            max_tokens=32000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _CHRONOLOGY_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        # Cache breakpoint: the data room is the same for every
                        # batch, so batches after the first read it from cache.
                        {
                            "type": "text",
                            "text": docs_text,
                            "cache_control": {"type": "ephemeral"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "\n===== DELAY EVENTS REGISTER (write one full narrative "
                                "per event below, keyed by eventRef) =====\n"
                                + _events_for_chronology(batch)
                            ),
                        },
                    ],
                }
            ],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": _CHRONOLOGY_OUTPUT_SCHEMA},
            },
        ) as stream:
            response = await stream.get_final_message()

        payload = "".join(b.text for b in response.content if b.type == "text").strip()
        if not payload:
            logger.warning(
                "Empty chronology payload for %s (stop_reason=%s)",
                [e.get("ref") for e in batch],
                response.stop_reason,
            )
            return []
        return json.loads(payload).get("chronologies", [])

    results: list[dict] = []
    done = 0
    for i, batch in enumerate(batches):
        try:
            results.extend(await _run_batch(batch))
        except json.JSONDecodeError:
            # One over-long batch shouldn't lose the whole run — the events in it
            # keep their existing chronology and the rest still generate.
            logger.warning("Unparseable chronology output for batch %s", i, exc_info=True)
        done += len(batch)
        if on_progress:
            on_progress(done, len(events))
    return results


# ── Clause extraction (per project's own Clause Library) ────────────────────
# Reads a project's contract and drafts the clauses an EOT claim relies on, in
# the same shape as the project_clauses table. Output mirrors
# app.schemas.project_clause so the rows drop straight into the library.

_CLAUSES_SYSTEM_PROMPT = (
    "You are a construction-contract specialist. You are given the extracted text "
    "of a project's contract (conditions of contract — e.g. a FIDIC Red/Yellow/Silver "
    "Book or NEC4 form, plus any Particular Conditions). Extract the individual "
    "clauses and sub-clauses an Extension of Time (EOT) / delay claim would rely on — "
    "principally those covering time for completion, extension of time, delay damages, "
    "notices, claims procedure, variations, and payment.\n\n"
    "Rules:\n"
    "- Only output clauses that actually appear in the text. Do NOT invent clause "
    "numbers, titles or wording.\n"
    "- PRIORITISE the OPERATIVE clauses that grant rights and set procedures — these "
    "are what an EOT claim is built on. In FIDIC terms these are typically the "
    "Sub-Clauses under Clause 8 (Time/Extension of Time/Delay Damages), Clause 13 "
    "(Variations), Clause 14 (Payment), Clause 4 (Unforeseeable conditions) and "
    "Clause 20 (Claims/Notice procedure). For NEC4, the compensation-event and "
    "early-warning clauses.\n"
    "- Do NOT fill the list with entries from the Definitions section (e.g. FIDIC "
    "Sub-Clause 1.1 'Definition: …'). Include a definition ONLY if it is itself "
    "claim-critical (e.g. the definition of Delay Damages or Time for Completion), "
    "and never at the expense of an operative clause.\n"
    "- 'clause_number' is the exact number as written (e.g. '8.5', '20.2.1').\n"
    "- 'clause_title' is the heading as written (e.g. 'Extension of Time for Completion').\n"
    "- 'clause_description' is a concise one or two sentence plain-language summary of "
    "what the clause provides — grounded only in the text.\n"
    "- 'contract_standard' is the contract/book the clause is from (e.g. 'FIDIC Red 2017'); "
    "use the provided contract standard when given.\n"
    "- 'tags' are 2-4 short topical tags (e.g. 'EOT', 'notice', 'time-bar', 'variation').\n"
    "- Return at most ~40 clauses, ordered by how central they are to an EOT/delay claim.\n"
    "- If the text contains no usable clauses, return an empty list.\n\n"
    "Particular Conditions INCLUDED in the document:\n"
    "The contract may include its Particular Conditions — either as a separate PC "
    "section, or as amendment instructions printed alongside the General Conditions "
    "(e.g. 'insert the words … between …', 'add the following as a new paragraph at "
    "the end of Sub-Clause …', 'delete Sub-Clause …'). Treat each instruction as an "
    "amendment to the clause it references:\n"
    "- 'clause_description' must describe the clause AS AMENDED — the net effect once "
    "the Particular-Conditions changes are applied to the base wording.\n"
    "- Set 'modified' to true when the Particular Conditions amend, replace or delete "
    "the clause; otherwise false.\n"
    "- 'modification_note' is one concise sentence stating what the Particular "
    "Conditions change (empty string when 'modified' is false).\n"
    "- 'base_description' is a concise one or two sentence plain-language summary of "
    "the clause as it stands in the GENERAL Conditions — BEFORE the "
    "Particular-Conditions amendment. Fill it only when 'modified' is true (empty "
    "string otherwise); it must describe the unamended base wording, never the "
    "amendment itself.\n"
    "- 'interpretation' is one or two sentences on what the amendment MEANS in "
    "practice for a claim: which party it favours, the risk or obligation it shifts, "
    "and what the Contractor must now do differently. Explain the practical effect "
    "rather than restating the amendment. Fill it only when 'modified' is true "
    "(empty string otherwise)."
)

_CLAUSE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_standard": {"type": "string"},
        "clause_number": {"type": "string"},
        "clause_title": {"type": "string"},
        "clause_description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        # Set when the document's own Particular Conditions amend this clause.
        "modified": {"type": "boolean"},
        "modification_note": {"type": "string"},
        # The General-Conditions wording before that amendment, and what the
        # amendment means for a claim (both modified-only).
        "base_description": {"type": "string"},
        "interpretation": {"type": "string"},
    },
    "required": [
        "contract_standard", "clause_number", "clause_title",
        "clause_description", "tags", "modified", "modification_note",
        "base_description", "interpretation",
    ],
    "additionalProperties": False,
}

_CLAUSES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"clauses": {"type": "array", "items": _CLAUSE_ITEM_SCHEMA}},
    "required": ["clauses"],
    "additionalProperties": False,
}


async def extract_clauses(
    *,
    text: str,
    filename: str,
    standard: str | None = None,
    project_name: str | None = None,
) -> list[dict]:
    """Draft a project's clause library from its contract text.

    Returns a list of plain dicts, one per clause, in the project_clauses shape.
    """
    ctx_bits = []
    if project_name:
        ctx_bits.append(f"Project: {project_name}")
    if standard:
        ctx_bits.append(f"Contract standard: {standard}")
    header = " | ".join(ctx_bits)

    parts = [f"Filename: {filename}"]
    if header:
        parts.append(header)
    body = text or "(no machine-readable text could be extracted from the contract)"
    parts.append(f"\n--- Contract text ---\n{body}")
    user_content = "\n".join(parts)

    async with _client().messages.stream(
        model=EXTRACTION_MODEL,
        # Thinking disabled for the same reason as compare_pcc_to_book: on a
        # book-sized contract adaptive thinking can consume the whole token
        # budget before any JSON is emitted. 32k output covers ~40 clauses easily.
        max_tokens=32000,
        thinking={"type": "disabled"},
        system=[
            {
                "type": "text",
                "text": _CLAUSES_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _CLAUSES_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = "".join(b.text for b in response.content if b.type == "text").strip()
    if not payload:
        raise ValueError(
            "The AI returned no output for the clause extraction "
            f"(stop_reason={response.stop_reason}). Please try again."
        )
    try:
        return json.loads(payload).get("clauses", [])
    except json.JSONDecodeError:
        raise ValueError(
            "The AI returned a malformed extraction result. Please try uploading the contract again."
        ) from None


# ── Knowledge Center: standard contract book clause extraction ──────────────
# Reads a published standard form (FIDIC Red/Yellow/Silver, NEC4, …) and returns
# EVERY clause it finds, with the wording kept verbatim plus a plain-language
# summary. This differs from extract_clauses above in three ways that matter:
# it is exhaustive rather than EOT-focused, it keeps the full clause text, and it
# is called once per CHUNK of the book (a whole book's verbatim clauses do not
# fit in a single response). See services/book_clause_extraction.py.

_BOOK_SYSTEM_PROMPT = (
    "You are a contract librarian digitising a published standard form of "
    "construction contract (e.g. FIDIC Red/Yellow/Silver Book, NEC4, CPWD) into a "
    "structured reference library. You are given ONE EXCERPT of the book, in order. "
    "Extract every numbered clause and sub-clause that appears in this excerpt.\n\n"
    "Rules:\n"
    "- Be EXHAUSTIVE for this excerpt. Extract every numbered provision you see, "
    "including definitions, general conditions and procedural clauses. This is a "
    "reference library, not a claim — do not filter for relevance.\n"
    "- 'clause_number' is the number exactly as printed (e.g. '4.12', '8.5', '20.2.1').\n"
    "- 'clause_title' is the heading exactly as printed (e.g. 'Unforeseeable Physical "
    "Conditions'). If a provision has no heading, write a short descriptive one.\n"
    "- 'clause_text' is the clause's wording copied VERBATIM from the excerpt. Do not "
    "paraphrase, summarise, shorten or 'clean up' this field. Preserve sub-paragraph "
    "lettering and numbering. Omit running headers, page numbers and footers.\n"
    "- 'summary' is YOUR plain-language explanation of what the clause means and does, "
    "in two or three sentences, written for a reader who is not a lawyer. This is the "
    "only field where you paraphrase.\n"
    "- 'tags' are 2-4 short topical tags (e.g. 'EOT', 'notice', 'time-bar', 'payment').\n"
    "- The excerpt may begin or end mid-clause. If a clause's text is cut off at the "
    "START of the excerpt, SKIP it — the previous excerpt already covered it. If it is "
    "cut off at the END, still include it with the text you can see.\n"
    "- Never invent a clause number, heading or wording that is not in the excerpt. If "
    "the excerpt contains no numbered clauses (a title page, table of contents or "
    "index), return an empty list."
)

_BOOK_CLAUSE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "clause_number": {"type": "string"},
        "clause_title": {"type": "string"},
        "clause_text": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["clause_number", "clause_title", "clause_text", "summary", "tags"],
    "additionalProperties": False,
}

_BOOK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"clauses": {"type": "array", "items": _BOOK_CLAUSE_ITEM_SCHEMA}},
    "required": ["clauses"],
    "additionalProperties": False,
}


async def extract_book_clauses(
    *,
    text: str,
    book_name: str,
    edition: str | None = None,
    part: int = 1,
    of: int = 1,
) -> list[dict]:
    """Extract every clause from ONE excerpt of a standard contract book.

    `part`/`of` tell the model where the excerpt sits in the book so it can judge
    the truncated-clause rule at each edge. Returns a list of plain dicts in the
    book_clauses shape: {clause_number, clause_title, clause_text, summary, tags}.
    """
    header = [f"Book: {book_name}"]
    if edition:
        header.append(f"Edition: {edition}")
    header.append(f"Excerpt {part} of {of}")

    user_content = (
        "\n".join(header)
        + f"\n\n--- Book excerpt ({part}/{of}) ---\n"
        + (text or "(empty excerpt)")
    )

    async with _client().messages.stream(
        model=EXTRACTION_MODEL,
        # Verbatim clause text is far longer than a summary, so this needs a much
        # larger budget than the EOT clause extractor above.
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[
            {"type": "text", "text": _BOOK_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _BOOK_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(payload).get("clauses", [])


# ── Particular Conditions (PCC) comparison ──────────────────────────────────
# A project selects a base standard form (General Conditions) from the Knowledge
# Center, and its clauses are copied into the project's Clause Library. The
# analyst may then upload the project's Particular Conditions of Contract (PCC).
# The PCC may be a short amendments-only document, or a full marked-up copy of the
# contract. Either way Claude reads it against the base clauses and returns TWO
# lists: base clauses the PCC AMENDS (flagged "Modified") and brand-NEW clauses
# the PCC introduces that have no base equivalent (flagged "New clause").

_PCC_SYSTEM_PROMPT = (
    "You are a construction-contract specialist. A project uses a standard form "
    "of contract (the General Conditions — e.g. a FIDIC Red/Yellow/Silver Book or "
    "NEC4 form). You are given (1) the list of that form's base clauses already in "
    "the project's library, each with its number, title and a short description, "
    "and (2) the extracted text of the project's PARTICULAR CONDITIONS OF CONTRACT "
    "(PCC / Particular Conditions / Conditions of Particular Application / Contract "
    "Data). The PCC may be a short list of amendments, OR a full copy of the "
    "contract with the General Conditions marked up — amended wording, deletions, "
    "and entirely new sub-clauses inserted.\n\n"
    "Compare the PCC against the base clauses and return TWO lists.\n\n"
    "1) 'modifications' — base clauses from the provided list that the PCC AMENDS, "
    "replaces or deletes. One entry per changed base clause:\n"
    "- 'clause_number' MUST be the EXACT number of the matching BASE clause from the "
    "list provided (not a number invented from the PCC).\n"
    "- 'clause_title' is that base clause's title.\n"
    "- 'modification_note' is one concise sentence stating what the PCC changes (e.g. "
    "'PCC shortens the notice period from 28 to 21 days' or 'Sub-Clause deleted by "
    "the Particular Conditions').\n"
    "- 'new_description' is an updated one-or-two sentence plain-language description "
    "of the clause AS AMENDED — what it now provides once the PCC is read with the "
    "base clause.\n"
    "- 'interpretation' is one or two sentences on what the amendment MEANS in "
    "practice for a claim: which party it favours, the risk or obligation it shifts, "
    "and what the Contractor must now do differently (e.g. 'Tightens the time-bar — "
    "notice must be served within 21 days, so a late notice now defeats the claim "
    "outright; diarise the shorter deadline from the date of awareness.'). Explain "
    "the practical effect; do not merely restate the amendment.\n"
    "Only include base clauses the PCC actually changes. Ignore untouched clauses.\n\n"
    "2) 'additions' — brand-NEW clauses/sub-clauses the PCC introduces that do NOT "
    "correspond to any base clause in the provided list (e.g. a new Sub-Clause the "
    "Particular Conditions add). One entry per new clause:\n"
    "- 'clause_number' is the number as written in the PCC (e.g. '1.15', '4.28'). If "
    "the PCC gives no number, assign a sensible one based on where it sits.\n"
    "- 'clause_title' is the heading as written, or a short descriptive one.\n"
    "- 'clause_description' is a concise one-or-two sentence plain-language summary of "
    "what the new clause provides.\n"
    "- 'tags' are 2-4 short topical tags (e.g. 'EOT', 'notice', 'payment').\n\n"
    "IMPORTANT distinctions:\n"
    "- If a PCC provision changes an EXISTING base clause, it is a 'modification', "
    "NOT an 'addition'. Never list the same clause in both.\n"
    "- A clause counts as an 'addition' only if its number/subject is not already a "
    "base clause.\n"
    "- Base ONLY on the text provided. Do NOT invent numbers, wording or figures. If "
    "there are no modifications, or no additions, return an empty list for that key."
)

_PCC_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "clause_number": {"type": "string"},
        "clause_title": {"type": "string"},
        "modification_note": {"type": "string"},
        "new_description": {"type": "string"},
        "interpretation": {"type": "string"},
    },
    "required": [
        "clause_number", "clause_title", "modification_note", "new_description",
        "interpretation",
    ],
    "additionalProperties": False,
}

_PCC_ADDITION_SCHEMA = {
    "type": "object",
    "properties": {
        "clause_number": {"type": "string"},
        "clause_title": {"type": "string"},
        "clause_description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["clause_number", "clause_title", "clause_description", "tags"],
    "additionalProperties": False,
}

_PCC_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "modifications": {"type": "array", "items": _PCC_ITEM_SCHEMA},
        "additions": {"type": "array", "items": _PCC_ADDITION_SCHEMA},
    },
    "required": ["modifications", "additions"],
    "additionalProperties": False,
}


def _base_clauses_brief(base_clauses: list[dict]) -> str:
    """Render the project's base clauses compactly for the PCC-comparison prompt."""
    lines = []
    for c in base_clauses:
        num = c.get("clause_number") or ""
        title = c.get("clause_title") or ""
        # Number + title is what matching hinges on; a clipped description is
        # plenty of context and keeps the prompt small for big books.
        desc = (c.get("clause_description") or "").strip()[:200]
        lines.append(f"- [{num}] {title}" + (f" — {desc}" if desc else ""))
    return "\n".join(lines) or "(no base clauses)"


async def compare_pcc_to_book(
    *,
    base_clauses: list[dict],
    pcc_text: str,
    filename: str,
    standard: str | None = None,
) -> dict:
    """Compare Particular Conditions against the project's base clauses.

    `base_clauses` is the project's book-sourced clauses (each with clause_number,
    clause_title, clause_description). Returns a dict with two lists:
    - 'modifications': [{clause_number, clause_title, modification_note,
      new_description}] — base clauses the PCC amends.
    - 'additions': [{clause_number, clause_title, clause_description, tags}] —
      brand-new clauses the PCC introduces.
    """
    header = []
    if standard:
        header.append(f"Contract standard (General Conditions): {standard}")
    header.append(f"Particular Conditions file: {filename}")

    # The base-clauses brief is identical every time this project's PCC is
    # (re)compared, so it gets its own cached block; only the PCC text varies.
    base_block = (
        "\n".join(header)
        + "\n\n--- BASE CLAUSES ALREADY IN THE PROJECT LIBRARY ---\n"
        + _base_clauses_brief(base_clauses)
    )
    pcc_block = (
        "--- PARTICULAR CONDITIONS OF CONTRACT (PCC) TEXT ---\n"
        + (pcc_text or "(no machine-readable text could be extracted from the PCC)")
        + "\n\nReturn the base clauses the PCC modifies, and the new clauses it adds."
    )

    async with _client().messages.stream(
        model=EXTRACTION_MODEL,
        # Thinking is disabled here: it counts toward max_tokens, and on a
        # book-sized comparison adaptive thinking was observed consuming the
        # entire budget before any JSON was emitted (stop_reason=max_tokens with
        # no text). This is a structured matching task — constrained JSON output
        # without thinking is both reliable and much faster. 64k output leaves
        # room for a PCC that amends most of a 345-clause book.
        max_tokens=64000,
        thinking={"type": "disabled"},
        system=[
            {"type": "text", "text": _PCC_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": base_block, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": pcc_block},
                ],
            }
        ],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _PCC_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = "".join(b.text for b in response.content if b.type == "text").strip()
    if not payload:
        raise ValueError(
            "The AI returned no output for the comparison "
            f"(stop_reason={response.stop_reason}). Please run the comparison again."
        )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        if response.stop_reason == "max_tokens":
            raise ValueError(
                "The comparison result was cut off before completion — the PCC may be "
                "very large. Please try again or upload a shorter Particular Conditions file."
            ) from None
        raise ValueError(
            "The AI returned a malformed comparison result. Please run the comparison again."
        ) from None
    return {
        "modifications": data.get("modifications", []),
        "additions": data.get("additions", []),
    }


# ── Interpretation of PCC amendments ────────────────────────────────────────
# A PCC comparison now writes an 'interpretation' with each modification. This
# fills that field for clauses amended by an EARLIER comparison (before the field
# existed) without re-running the whole comparison: everything needed — the base
# wording, the amendment note and the amended wording — is already stored.

_INTERPRETATION_SYSTEM_PROMPT = (
    "You are a construction-claims specialist. For each clause you are given the "
    "base wording from the standard form (General Conditions), what the project's "
    "Particular Conditions (PCC) change, and the clause as amended.\n\n"
    "For every clause return an 'interpretation': one or two sentences on what the "
    "amendment MEANS in practice for an Extension of Time / delay claim — which "
    "party it favours, the risk or obligation it shifts, and what the Contractor "
    "must now do differently (e.g. 'Tightens the time-bar — notice must be served "
    "within 21 days, so a late notice now defeats the claim outright; diarise the "
    "shorter deadline from the date of awareness.').\n\n"
    "Rules:\n"
    "- Explain the practical effect; do NOT merely restate the amendment.\n"
    "- Return one entry per clause given, echoing 'clause_number' EXACTLY as "
    "provided, and cover every clause in the input.\n"
    "- Base it only on the wording provided — do not invent figures or obligations."
)

_INTERPRETATION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "interpretations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clause_number": {"type": "string"},
                    "interpretation": {"type": "string"},
                },
                "required": ["clause_number", "interpretation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["interpretations"],
    "additionalProperties": False,
}


async def interpret_modifications(
    *,
    clauses: list[dict],
    standard: str | None = None,
) -> dict[str, str]:
    """Write the practical interpretation of each amended clause.

    `clauses` each carry clause_number, clause_title, base_description,
    modification_note and clause_description (the amended wording). Returns
    {clause_number: interpretation} for the clauses the model covered.
    """
    if not clauses:
        return {}

    blocks = [f"Contract standard: {standard}"] if standard else []
    for c in clauses:
        blocks.append(
            f"\n--- Clause {c.get('clause_number') or ''} — {c.get('clause_title') or ''} ---\n"
            f"BASE (General Conditions): {(c.get('base_description') or '(not recorded)').strip()}\n"
            f"PCC AMENDMENT: {(c.get('modification_note') or '(not recorded)').strip()}\n"
            f"AS AMENDED: {(c.get('clause_description') or '').strip()}"
        )

    async with _client().messages.stream(
        model=EXTRACTION_MODEL,
        # ~1k tokens per clause covers a batch comfortably; thinking is disabled
        # for the same reason as the comparison it backfills.
        max_tokens=16000,
        thinking={"type": "disabled"},
        system=[
            {
                "type": "text",
                "text": _INTERPRETATION_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": "\n".join(blocks)}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _INTERPRETATION_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = "".join(b.text for b in response.content if b.type == "text").strip()
    if not payload:
        raise ValueError(
            "The AI returned no output for the clause interpretations "
            f"(stop_reason={response.stop_reason})."
        )
    try:
        items = json.loads(payload).get("interpretations", [])
    except json.JSONDecodeError:
        raise ValueError("The AI returned a malformed interpretation result.") from None

    out: dict[str, str] = {}
    for it in items:
        num = (it.get("clause_number") or "").strip()
        text_ = (it.get("interpretation") or "").strip()
        if num and text_:
            out[num] = text_
    return out


# ── EOT claim document generation ───────────────────────────────────────────
# Assembles a full Extension of Time claim document from the project's delay
# events and data-room documents, following the standard claim structure.

# The labels that keep documentary fact, each party's case, and the analyst's own
# inference visibly apart — the distinction a claim stands or falls on. Shared
# between the prompt and the output schema so the two can't drift.
_STATEMENT_LABELS = [
    "Fact",
    "Contractor's position",
    "Engineer / Employer's position",
    "Analysis",
    "Missing evidence",
    "Assessment",
]

# Standing instructions, identical on every pass. Kept byte-stable and cached as a
# prompt prefix: the per-pass task text rides in the user turn instead, so all the
# calls that build one claim share a cache hit.
_CLAIM_HOUSE_RULES = (
    "You are a senior forensic delay analyst at Al Qarar Management Solutions "
    "(AQMS) drafting a formal, submission-ready Extension of Time (EOT) claim for a "
    "construction project under a standard form such as FIDIC, NEC4 or CPWD. You are "
    "writing one part of that claim; the surrounding sections are drafted separately, "
    "so produce only what the task asks for.\n\n"
    "── NEVER INVENT ───────────────────────────────────────────────\n"
    "This document may be relied on in a contractual submission or in dispute "
    "proceedings. A fabricated date or clause is worse than a gap. Do NOT invent: "
    "dates, contract values, programme revisions, completion dates, EOT days, delay "
    "durations, clause numbers or amendments, notices, correspondence, evidence, "
    "critical-path activities, programme results, delay-analysis results, or "
    "attachments. Every clause you cite must appear in the supplied Clause Library or "
    "on a delay event. Every date, party, figure and document name must come from the "
    "supplied records.\n"
    "Where the records do not support a statement, say so in the report using one of: "
    "'Not available in the supplied records.' / 'To be confirmed.' / 'Evidence "
    "required.' / 'Unable to establish from the supplied documents.' A thin evidence "
    "set produces a short, heavily-qualified section — never an embellished one.\n\n"
    "── SEPARATE FACT FROM ANALYSIS ────────────────────────────────\n"
    "Use `statement` blocks, each carrying one of these labels, wherever the "
    "distinction matters:\n"
    "  Fact — what a supplied document records. Name the document.\n"
    "  Contractor's position — what the Contractor asserts, per the correspondence.\n"
    "  Engineer / Employer's position — what the Engineer or Employer asserts. Where "
    "the parties disagree, present the two positions as SEPARATE statements. Never "
    "merge them, and never let one side's assertion stand as fact.\n"
    "  Analysis — your inference or opinion. Must be labelled as such, never written "
    "as documentary fact.\n"
    "  Missing evidence — what would be needed to close a gap.\n"
    "  Assessment — a concluded position, with its reasoning.\n\n"
    "── EVIDENCE TRACEABILITY ──────────────────────────────────────\n"
    "Every material factual statement must be traceable to a source document. Name "
    "the document inline (e.g. 'the Engineer's letter of 14 March 2025') and list the "
    "documents relied on in an `evidence` block. Only ever name documents that appear "
    "in the supplied data-room schedule — never a document you expect to exist.\n\n"
    "── WRITING ────────────────────────────────────────────────────\n"
    "Formal, factual, third person, in the register of a contractual submission — "
    "'the Contractor', 'the Employer', 'the Engineer'. Prefer a table wherever the "
    "content is a register or a set of particulars; prose is for argument, not for "
    "lists of facts. Give every table a caption. Keep each table row exactly as long "
    "as its `columns`, and put 'Not available in the supplied records.' in a cell "
    "rather than leaving it blank or inventing a value. Let the volume of supplied "
    "evidence set the length: cover every event and document the records support, and "
    "do not pad where they are thin."
)

# ── Per-pass task instructions (user turn) ──────────────────────────────────

_CLAIM_TASK_FRONT = (
    "TASK — draft the front matter and the contractual case: sections 1 to 5.\n\n"
    "Return `title` (e.g. 'Extension of Time Claim'), `reference` (the project code "
    "and contract reference as supplied), and these sections:\n\n"
    "1. ABBREVIATIONS AND DEFINITIONS\n"
    "   1.1 Abbreviations — table (Abbreviation, Meaning) of abbreviations this claim "
    "actually uses.\n"
    "   1.2 Definitions — table (Term, Definition) of contractual and project terms "
    "drawn from the supplied contract and project records.\n\n"
    "2. EXECUTIVE SUMMARY\n"
    "   2.1 Introduction — the project, Contractor, Employer, Engineer, contract, the "
    "purpose of this claim, the claim cut-off date, and the general basis of "
    "entitlement.\n"
    "   2.2 Summary of Delay Events — table (Delay Event, Description, Cause, "
    "Responsible Party, Start Date, End Date, Claimed/Assessed Days, "
    "Critical/Non-Critical, Contractual Basis), one row per supplied event.\n"
    "   2.3 Summary of Relief Sought — EOT requested, delay period, original "
    "completion date, revised completion date, any evidenced cost entitlement, other "
    "contractual relief. Where entitlement cannot yet be established because programme "
    "data is missing, say so explicitly here.\n"
    "   2.4 Framework of the Submission — how this report is organised: Introduction, "
    "Project Background, Basis of Claim, Delay Events, Delay Analysis Methodology, "
    "Delay Analysis Results, Summary and Conclusion.\n"
    "   2.5 Purpose of the Submission\n"
    "   2.6 Reservation of Rights\n\n"
    "3. PROJECT DETAILS\n"
    "   3.1 Parties to the Contract\n"
    "   3.2 Contract Agreement\n"
    "   3.3 Salient Features of the Contract — table (Item, Particular) covering, only "
    "where supplied: Project, Employer, Contractor, Engineer, Contract Type, Contract "
    "Standard, Contract Value, Contract Date, Commencement Date, Time for Completion, "
    "Original Completion Date, Accepted Contract Amount, Delay Damages, Governing Law, "
    "Dispute Resolution, and any other important contractual provisions.\n"
    "   3.4 Programme — the baseline programme, revisions, accepted/approved "
    "programme, updates, as-built programme and data dates. Follow the PROGRAMME "
    "RECORDS status given below exactly; do not describe a programme that has not been "
    "supplied.\n\n"
    "4. BASIS OF THE CLAIM\n"
    "   4.1 Delay and Disruption to the Progress — the overall factual narrative: what "
    "happened, why, who was responsible, how the Works and the planned sequence were "
    "affected, how the critical path may have been affected, and what mitigation the "
    "Contractor undertook. Build this from the per-event chronologies in date order.\n"
    "   4.2 Contractual Entitlement to Extension of Time — for each applicable "
    "provision in the Clause Library: the sub-clause, its title, the contractual "
    "requirement, how the delay events satisfy it, the notice requirement, any "
    "time-bar, the evidence of compliance, the potential weaknesses, and the missing "
    "evidence. Include a table (Clause, Title, Requirement, Relevance, "
    "Compliance/Risk). Where a clause is marked amended by the Particular Conditions, "
    "address the amended wording and its interpretation. Do not present an uncertain "
    "contractual conclusion as settled — label it Analysis or Assessment.\n\n"
    "5. DELAY EVENTS — return this section with its 5.1 Introduction subsection ONLY: "
    "explain how the events were identified from the records and how each is assessed. "
    "The individual events are drafted separately, so leave the rest to us.\n"
)

_CLAIM_TASK_EVENT = (
    "TASK — draft ONE delay event subsection for section 5 of the claim.\n\n"
    "Return `heading` as '<ref> — <title>' for the event given below, and `parts` "
    "using exactly this template. Use every part; where the records do not support "
    "one, keep the part and say so in it.\n\n"
    "  A. Event Overview — event reference, title, cause, responsible party, affected "
    "works, status. A short table (Item, Detail) suits this.\n"
    "  B. Detailed Narrative — a chronological, professional cause-and-effect account "
    "built from the source documents. Do not merely restate the register entry.\n"
    "  C. Chronology — table (Date, Party/Actor, Reference, Event/Action, "
    "Consequence).\n"
    "  D. Cause of Delay — the actual cause the evidence supports.\n"
    "  E. Effect on Progress — affected activity, work area, dependency, downstream "
    "effect, and any potential critical-path effect.\n"
    "  F. Contractor's Actions / Mitigation — RFIs, notices, reminders, meetings, "
    "mitigation, alternative works, resequencing, acceleration, where evidenced.\n"
    "  G. Engineer / Employer Position — where the correspondence records an opposing "
    "position, give it as its own statement, separate from the Contractor's.\n"
    "  H. Contractual Basis — table (Clause, Title, Relevance, Evidence, "
    "Compliance/Risk).\n"
    "  I. Supporting Evidence — an `evidence` block naming the actual source documents "
    "relied on for this event.\n"
    "  J. Time Impact — claimed duration, assessed duration, critical/non-critical "
    "status, the evidence supporting the duration, and the programme evidence status.\n"
    "  K. Admissibility / Entitlement Assessment — classify as exactly one of: Strong, "
    "Likely, At Risk, Not Demonstrated, Insufficient Evidence — and explain why. "
    "Classify only as far as the evidence supports; prefer a weaker classification "
    "with reasons over an overstated one.\n"
)

_CLAIM_TASK_ANALYSIS = (
    "TASK — draft the analysis and conclusion: sections 6, 7 and 8.\n\n"
    "6. DELAY ANALYSIS\n"
    "   6.1 Introduction\n"
    "   6.2 Delay Analysis Methodology — compare As-Planned vs As-Built, Impacted "
    "As-Planned, Collapsed As-Built, and Time Impact Analysis / Window Analysis. For "
    "each: its suitability here, the programme data it requires, and whether that data "
    "is available. Then state the selected methodology and its limitations. You MUST "
    "distinguish 'methodology selected/recommended' from 'analysis actually performed' "
    "— never imply a Time Impact Analysis has been carried out when the programme "
    "records to perform it have not been supplied.\n"
    "   6.3 Window Periods — follow the PROGRAMME RECORDS status exactly. Where the "
    "programme data supports windows, give each window its period, starting programme, "
    "data date, delay events, impacted programme, updated impacted programme, longest "
    "path, completion date before and after impact, delay attributable, concurrent "
    "delay, Contractor contribution and a window conclusion. Where it does not, this "
    "subsection is a single 'Missing evidence' statement naming what is required. "
    "NEVER generate windows the source data does not support.\n"
    "   6.4 Delay Analysis Findings — where programme data exists: the impacted "
    "programme (insertion of the fragnet), the updated impacted programme (actual "
    "progress incorporated), the longest path (driving activity, "
    "predecessor/successor, event, resulting completion date), concurrent delay, and "
    "any Contractor delay the evidence supports. Where it does not exist, record the "
    "findings that CAN be drawn from the correspondence and event records, and label "
    "clearly what remains unquantified.\n"
    "   6.5 Summary — table (Window, Delay Event, Delay Days, Driving Activity, "
    "Critical Path, Concurrent Delay, Completion Impact). Where windows could not be "
    "constructed, present the per-event position instead and say so in the caption.\n\n"
    "7. SUMMARY OF ENTITLEMENT\n"
    "   Table (Delay Event, Event Description, Entitlement Days, Critical Path, "
    "Status), then: total EOT entitlement, original completion date, revised "
    "completion date, basis of entitlement, outstanding evidence, and any "
    "reservations or qualifications. Where entitlement cannot be calculated from the "
    "available evidence, state exactly: 'Final EOT entitlement cannot be reliably "
    "quantified until the required programme records are provided.'\n\n"
    "8. SCHEDULE OF ATTACHMENTS\n"
    "   Table (No., Document, Type, Relevance) built ONLY from the supplied data-room "
    "schedule. Do not list a document that was not supplied.\n"
)

# Three heading levels — section (6) → subsection (6.3) → part (A. Event Overview).
# Structured outputs reject recursive schemas, so each level is named explicitly
# rather than self-referenced.
#
# Every level is a `$ref` into `$defs`, which is load-bearing rather than tidiness:
# the API compiles the schema into a grammar and rejects the request outright once
# that grammar gets too large. Inlining the five-variant block union at all three
# levels blows past the limit ("The compiled grammar is too large"); defining it
# once and referencing it compiles fine and halves the schema.
#
# Each block variant is a fully-specified object rather than one loose shape with
# optional keys: structured outputs require `additionalProperties: false` on every
# object and only guarantee the keys named in `required`, so a discriminated
# `anyOf` union is what reliably round-trips.
def _block_variant(kind: str, **props) -> dict:
    return {
        "type": "object",
        "properties": {"type": {"const": kind}, **props},
        "required": ["type", *props],
        "additionalProperties": False,
    }


_STRING_LIST = {"type": "array", "items": {"type": "string"}}


def _ref(name: str) -> dict:
    return {"$ref": f"#/$defs/{name}"}


_CLAIM_BLOCK_SCHEMA = {
    "anyOf": [
        _block_variant("paragraph", text={"type": "string"}),
        _block_variant("bullets", items=_STRING_LIST),
        # The fact/position/analysis separation, carried in the data rather than
        # left to the reader to infer from prose.
        _block_variant(
            "statement",
            label={"enum": _STATEMENT_LABELS},
            text={"type": "string"},
        ),
        # Source documents relied on — rendered as a distinct, checkable list.
        _block_variant("evidence", items=_STRING_LIST),
        _block_variant(
            "table",
            caption={"type": "string"},
            columns=_STRING_LIST,
            rows={"type": "array", "items": _STRING_LIST},
        ),
    ]
}


def _heading_level(**extra) -> dict:
    return {
        "type": "object",
        "properties": {
            "number": {"type": "string"},
            "heading": {"type": "string"},
            "blocks": {"type": "array", "items": _ref("block")},
            **extra,
        },
        "required": ["number", "heading", "blocks", *extra],
        "additionalProperties": False,
    }


# Only the definitions a schema actually reaches are included — an unused `$defs`
# entry still counts toward the compiled grammar.
_DEFS_EVENT = {
    "block": _CLAIM_BLOCK_SCHEMA,
    # Level 3: "A. Event Overview" inside a delay-event subsection.
    "part": _heading_level(),
}
_DEFS_FULL = {
    **_DEFS_EVENT,
    "subsection": _heading_level(parts={"type": "array", "items": _ref("part")}),
    "section": _heading_level(
        subsections={"type": "array", "items": _ref("subsection")}
    ),
}

# Pass 1 — front matter and the contractual case (sections 1–5).
_CLAIM_FRONT_SCHEMA = {
    "type": "object",
    "$defs": _DEFS_FULL,
    "properties": {
        "title": {"type": "string"},
        "reference": {"type": "string"},
        "sections": {"type": "array", "items": _ref("section")},
    },
    "required": ["title", "reference", "sections"],
    "additionalProperties": False,
}

# Pass 2 — one delay event. The caller assigns its 5.n number.
_CLAIM_EVENT_SCHEMA = {
    "type": "object",
    "$defs": _DEFS_EVENT,
    "properties": {
        "heading": {"type": "string"},
        "blocks": {"type": "array", "items": _ref("block")},
        "parts": {"type": "array", "items": _ref("part")},
    },
    "required": ["heading", "blocks", "parts"],
    "additionalProperties": False,
}

# Pass 3 — analysis, entitlement and attachments (sections 6–8).
_CLAIM_ANALYSIS_SCHEMA = {
    "type": "object",
    "$defs": _DEFS_FULL,
    "properties": {"sections": {"type": "array", "items": _ref("section")}},
    "required": ["sections"],
    "additionalProperties": False,
}


def _events_brief(events: list[dict]) -> str:
    """Render the delay-event register into compact text for the claim prompt."""
    if not events:
        return "(No delay events have been identified for this project.)"
    lines = []
    for e in events:
        lines.append(
            f"\n### {e.get('ref', '')} — {e.get('title', '')}\n"
            f"Category: {e.get('category', '')} | Cause: {e.get('cause', '')} | "
            f"Clause: {e.get('clause', '')} | Admissibility: {e.get('admissibility', '')}\n"
            f"Period: {e.get('startDate', '')} → {e.get('endDate', '')} | "
            f"Days impact: {e.get('daysImpact', 0)} | "
            f"Critical path: {'yes' if e.get('criticalPath') else 'no'}\n"
            f"Narrative: {e.get('narrative', '')}"
        )
        chron = e.get("chronology") or []
        if chron:
            steps = "; ".join(
                f"{c.get('date', '')} {c.get('actor', '')}: {c.get('title', '')}" for c in chron
            )
            lines.append(f"Chronology: {steps}")
        srcs = e.get("sources") or []
        if srcs:
            lines.append("Sources: " + ", ".join(s.get("name", "") for s in srcs))
    return "\n".join(lines)


def _clauses_brief(clauses: list[dict]) -> str:
    """Render the project's Clause Library — the basis for the entitlement section."""
    if not clauses:
        return "(The project's Clause Library is empty — no clauses have been loaded.)"
    lines = []
    for c in clauses:
        head = f"\n### {c.get('clause_number', '')} — {c.get('clause_title', '')}"
        if c.get("modified"):
            head += "  [MODIFIED BY PARTICULAR CONDITIONS]"
        lines.append(head)
        if c.get("modified") and c.get("base_description"):
            lines.append(f"General Conditions wording: {c.get('base_description', '')}")
            lines.append(f"As amended: {c.get('clause_description', '')}")
            if c.get("modification_note"):
                lines.append(f"Amendment: {c.get('modification_note')}")
            if c.get("interpretation"):
                lines.append(f"Interpretation: {c.get('interpretation')}")
        else:
            lines.append(f"Wording: {c.get('clause_description', '')}")
        tags = c.get("tags") or []
        if tags:
            lines.append("Tags: " + ", ".join(str(t) for t in tags))
    return "\n".join(lines)


def _queries_brief(queries: list[dict]) -> str:
    """Render the queries / RFI register."""
    if not queries:
        return "(No queries or RFIs have been raised on this project.)"
    lines = []
    for q in queries:
        lines.append(
            f"- RFI {q.get('dateOfRfi', '')} [{q.get('status', '')}] "
            f"Subject: {q.get('eotDescription', '')} | "
            f"Query: {q.get('queryDescription', '')} | "
            f"Response: {q.get('responseFromGic', '') or '(none received)'} "
            f"({q.get('dateOfResponse', '') or 'no date'}) | "
            f"Remarks: {q.get('remarks', '')}"
        )
    return "\n".join(lines)


def _documents_brief(documents: list[dict], limit: int = 200) -> str:
    """Render the data-room schedule, including each document's AI classification."""
    if not documents:
        return "(No documents have been uploaded to the data room.)"
    lines = []
    for d in documents[:limit]:
        line = f"- {d.get('name', '')} [{d.get('type', '')}]"
        a = d.get("analysis") or {}
        if a.get("document_type"):
            line += f" — {a.get('document_type')}"
        if a.get("summary"):
            line += f": {a.get('summary')}"
        lines.append(line)
    if len(documents) > limit:
        lines.append(f"(+{len(documents) - limit} further documents not listed)")
    return "\n".join(lines)


def _json_brief(label: str, payload, limit: int = 12000) -> str:
    """Compactly serialise a stored assessment, truncated so one module can't
    crowd out the rest of the prompt."""
    if not payload:
        return f"(No {label} is available for this project.)"
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > limit:
        text = text[:limit] + f"… (truncated — {label} exceeds the prompt budget)"
    return text


_PROGRAMME_EXTS = (".xer", ".mpp", ".xml")


def _programme_status(project: dict, documents: list[dict]) -> str:
    """State plainly what programme evidence exists — the single fact that decides
    whether a windowed Time Impact Analysis can be performed at all.

    Programme files can be uploaded to the data room, but nothing parses them yet,
    so their activities, logic and critical path are unavailable even when a file
    is present. The prompt has to say which of those two situations applies, so the
    report neither invents windows nor claims a file is missing when it isn't.
    """
    named = (project or {}).get("baselineProgramme") or ""
    files = [
        d.get("name", "")
        for d in documents or []
        if d.get("type") in ("P6 XML", "MPP")
        or (d.get("name") or "").lower().endswith(_PROGRAMME_EXTS)
    ]

    lines = [
        "PROGRAMME RECORDS — this governs sections 3.4, 6.2, 6.3, 6.4 and 6.5.",
        f"Baseline programme named on the project record: {named or '(none)'}",
        f"Data date on the project record: {(project or {}).get('dataDate') or '(none)'}",
    ]
    if files:
        lines.append(
            "Programme files ARE present in the data room: "
            + ", ".join(files[:20])
            + ("" if len(files) <= 20 else f" (+{len(files) - 20} more)")
        )
        lines.append(
            "However, these files have NOT been parsed — no activity, logic, float or "
            "critical-path data has been extracted from them. You therefore know that "
            "the files exist and what they are called, and nothing about their "
            "contents. Do not describe activities, dates or a critical path from them."
        )
    else:
        lines.append(
            "No baseline, revised, updated or as-built programme has been supplied to "
            "the data room."
        )
    lines.append(
        "CONSEQUENCE: a windowed Time Impact Analysis CANNOT be performed and window "
        "periods CANNOT be constructed. In 6.3 emit a single 'Missing evidence' "
        "statement naming what is required (the baseline programme, the accepted "
        "revisions, the progress updates with their data dates, and the as-built "
        "programme, in P6 XER / P6 XML / MS Project form). In 6.2 you may still select "
        "and justify a methodology, but state explicitly that it has not yet been "
        "carried out. In 3.4 record the programme position as it actually stands. Any "
        "day figures elsewhere in the claim are the assessed event impacts, not the "
        "output of a critical-path analysis — say so wherever a total is given."
    )
    return "\n".join(lines)


def build_claim_context(
    *,
    project: dict,
    events: list[dict],
    documents: list[dict],
    clauses: list[dict] | None = None,
    queries: list[dict] | None = None,
    admissibility: dict | None = None,
    methodology: dict | None = None,
) -> str:
    """The evidence base, rendered once and reused byte-identically by every pass.

    Sits in the cached system prefix, so a claim with twenty delay events pays for
    this once rather than twenty-two times.
    """
    p = project or {}
    header = [
        f"Project: {p.get('name', '')}",
        f"Project code: {p.get('code', '')}",
        f"Location: {p.get('location', '')}",
        f"Contract standard: {p.get('standard', '')}",
        f"Employer: {p.get('employer', '')}",
        f"Engineer: {p.get('engineer', '')}",
        f"Contractor: {p.get('contractor', '')}",
        f"Contract value: {p.get('value', '')} {p.get('currency', '')}",
        f"LOA / LPO reference: {p.get('loaRef', '')}",
        f"Commencement date: {p.get('commencementDate', '')}",
        f"Original completion date: {p.get('completionDate', '')}",
        f"Time for completion (days): {p.get('timeForCompletionDays', '')}",
    ]
    return (
        "PROJECT AND CONTRACT PARTICULARS\n"
        + "\n".join(header)
        + "\n(An empty value above means the particular was not supplied — record it "
        "as 'Not available in the supplied records.', never as a guess.)"
        + "\n\n" + _programme_status(p, documents or [])
        + "\n\nDELAY EVENTS REGISTER\n" + _events_brief(events)
        + "\n\nPROJECT CLAUSE LIBRARY\n" + _clauses_brief(clauses or [])
        + "\n\nADMISSIBILITY ASSESSMENT\n"
        + _json_brief("admissibility assessment", admissibility)
        + "\n\nDELAY ANALYSIS METHODOLOGY ASSESSMENT\n"
        + _json_brief("methodology assessment", methodology)
        + "\n\nQUERIES / RFI REGISTER\n" + _queries_brief(queries or [])
        + "\n\nDATA ROOM — SCHEDULE OF DOCUMENTS\n" + _documents_brief(documents or [])
        + "\n(These are the only documents you may cite. Anything not listed here does "
        "not exist for the purposes of this claim.)"
    )


async def _claim_call(*, context: str, task: str, schema: dict, max_tokens: int) -> dict:
    """One structured pass over the shared claim context."""
    async with _client().messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=[
            {"type": "text", "text": _CLAIM_HOUSE_RULES},
            # Cache through the end of the evidence base: identical on every pass.
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": task}],
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": schema},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(payload)


async def generate_claim_front_matter(context: str) -> dict:
    """Sections 1–5.1. Returns {title, reference, sections}."""
    return await _claim_call(
        context=context,
        task=_CLAIM_TASK_FRONT,
        schema=_CLAIM_FRONT_SCHEMA,
        max_tokens=32000,
    )


async def generate_claim_delay_event(context: str, event: dict) -> dict:
    """One delay-event subsection. Returns {heading, blocks, parts}."""
    task = (
        _CLAIM_TASK_EVENT
        + "\n\nTHE EVENT TO DRAFT — use only this event's own record, plus the wider "
        "context above for the contract, clauses and documents:\n"
        + _events_brief([event])
    )
    return await _claim_call(
        context=context,
        task=task,
        schema=_CLAIM_EVENT_SCHEMA,
        max_tokens=16000,
    )


async def generate_claim_analysis(context: str, event_headings: list[str]) -> dict:
    """Sections 6–8. Returns {sections}."""
    drafted = "\n".join(f"- {h}" for h in event_headings) or "(none)"
    task = (
        _CLAIM_TASK_ANALYSIS
        + "\n\nThe delay events already drafted as section 5 of this claim, which your "
        "analysis and entitlement tables must cover and must not contradict:\n"
        + drafted
    )
    return await _claim_call(
        context=context,
        task=task,
        schema=_CLAIM_ANALYSIS_SCHEMA,
        max_tokens=32000,
    )


# ── Client proposal generation (costed services proposal) ───────────────────
# Produces a client-facing commercial PROPOSAL to engage the consultancy for the
# EOT / delay-claim work, with a costing breakdown derived from the identified
# delay events. This is NOT the EOT claim itself — it is the offer to the client.

# Al Qarar Management Solutions (AQMS) firm profile — the standing facts that
# every proposal is built on, drawn from AQMS's real "Claims Support Services"
# proposals so generated documents read in the firm's house style. The visual
# template is applied separately; this drives the CONTENT/structure only.
_AQMS_PROFILE = (
    "ABOUT THE FIRM — use these standing facts (do not contradict them):\n"
    "- The firm is Al Qarar Management Solutions (AQMS), a specialist provider of "
    "project-management, commercial and claims-advisory services: Contracts & "
    "Commercial Management, Project Planning, Monitoring & Controls, Forensic Delay "
    "Analysis, and Quantum Assessment, plus arbitration and expert-witness support.\n"
    "- Core values: Respect | Trust | Continual Improvement | Service.\n"
    "- Track record: over the past several years AQMS has supported 60+ clients and "
    "160+ Extension of Time (EOT) and quantum claims across the GCC, India and other "
    "regions, in buildings, infrastructure and mixed-use developments, for government "
    "entities, private developers and international EPC contractors.\n"
    "- Core services (reference list): Extension of Time Claims; Preparation of "
    "Commercial Claims; Independent Technical Evaluation; Tendering Support & "
    "Estimation; Contracts and Commercial Management; Forensic Planning and Delay "
    "Analysis; Quantum Claims (Cost/Damages); Arbitration Support and Expert Witness; "
    "Claims Documentation (Pleadings & Statements); Business Improvement and "
    "Transformation.\n"
    "- Standard team to present in 'Team Handling the Assignment': "
    "Kariyadan Nausher (PMP, ACIArb — Principal Consultant & Technical Expert; 3+ "
    "decades in Oman; Member, Society of Construction Law UK; registered Technical "
    "Expert with the Oman Commercial Arbitration Center); Hemanth Sarvabhotla "
    "(Director; 20+ years delivering large multi-disciplinary Design & Build "
    "contracts); Vamsi Krishna Valluri (MCIArb, RICS Expert Witness — Delay & Quantum, "
    "CPM/windows/time-impact analysis); Mohamed Ismail (PMP — Consultant, Planning & "
    "Controls); Ajmal Aboo (PMP — Consultant, Planning & Controls). Note the team is "
    "supported by other AQMS experts on a need basis.\n"
    "- The cover letter is signed off by 'Hemanth Sarvabhotla, Director'.\n"
    "- Governing law is the Sultanate of Oman; all fees are exclusive of VAT and any "
    "other applicable taxes."
)

_PROPOSAL_SYSTEM_PROMPT = (
    "You are the commercial lead at Al Qarar Management Solutions (AQMS) preparing a "
    "formal, submission-ready 'PROPOSAL FOR CLAIMS SUPPORT SERVICES' to a prospective "
    "CLIENT, offering to prepare and pursue their Extension of Time (EOT) and quantum / "
    "delay-and-disruption claim. You are given the project details, the register of "
    "delay events our AI has IDENTIFIED from the client's uploaded documents, and the "
    "list of those documents.\n\n"
    + _AQMS_PROFILE + "\n\n"
    "GROUND RULES:\n"
    "- The proposal must be built around the SPECIFIC delay events identified — name "
    "them, summarise them, and scope and price the work against them. This is what "
    "makes the proposal bespoke to the client.\n"
    "- Be factual and professional. Only rely on the firm profile above and the "
    "information provided; do NOT invent clause numbers, dates, parties or figures "
    "that are not given.\n"
    "- Address the client by their company name (the Employer/Client provided).\n\n"
    "Produce these narrative sections, in this order, in 'sections' (heading + body):\n"
    "1. Cover Letter — addressed to the client company, with a subject line 'Proposal "
    "for Claims Support Services', a short covering note (pleased to submit our "
    "proposal for the identified matter), and a sign-off 'Yours sincerely, Hemanth "
    "Sarvabhotla, Director'.\n"
    "2. Background & Introduction — the client's need for claims support on this "
    "project, a short AQMS introduction and track record, and the core-services list.\n"
    "3. Our Approach — the systematic claims approach (understanding the contractual "
    "framework; compilation & review of documents; in-depth study of the delay events; "
    "EOT claim preparation; quantification of damages; collaboration with experts/legal "
    "counsel; drafting the Statement of Claim; supporting documentation; final review "
    "and submission).\n"
    "4. Scope of Work & Methodology — our understanding of the scope tied to the "
    "identified delay events (contractual strategy; EOT and quantum claim; dispute-"
    "resolution support), and the methodology (initial data collection & review; delay "
    "analysis using Time Impact Analysis / As-Planned vs As-Built / windows analysis; "
    "quantum analysis; report preparation; collaboration & review; hearing "
    "preparation).\n"
    "5. Team Handling the Assignment — the standard AQMS team above, each with a short "
    "bio.\n"
    "6. Terms & Conditions — the standard headings: Service Assignment; Payment; "
    "Taxation (exclusive of VAT); Conflict of Interest; Liability (limited to fees "
    "paid); Governing Law (Sultanate of Oman); Confidentiality; Indemnification.\n\n"
    "COMMERCIAL PROPOSAL (returned as structured fields, not prose):\n"
    "- 'costing': the line items for the professional services, grouped sensibly by "
    "package/deliverable tied to the identified events (e.g. document review & claim "
    "strategy; EOT claim & quantum report per package). Each line has a short 'item', "
    "a one-line 'description', an 'timeline' (indicative, e.g. 'Week 1-3' — empty "
    "string if not applicable) and an 'amount' (a number in the project currency). "
    "Scale the effort and fees sensibly to the number and complexity of the identified "
    "delay events.\n"
    "- 'currency': the project currency. 'total': the sum of the line-item amounts.\n"
    "- 'paymentTerms': 3-5 short bullet strings for the payment schedule (e.g. advance "
    "on signing, interim on draft submission, balance on final submission), each "
    "payable within 30 days of invoice, exclusive of VAT.\n"
    "- 'reference': an AQMS proposal reference in the form 'AQMS/Proposal/<yy>/<nn>'.\n"
    "- 'date': the proposal date provided.\n\n"
    "ADMIN-PROVIDED FIELDS: the user may supply specific fields (client company, "
    "attention line, client address, subject, reference, date, signatory, a special "
    "discount, a fee basis, and free-form instructions). When provided, USE THEM "
    "EXACTLY — they take precedence over anything you would otherwise draft:\n"
    "- Put the client company, attention line and address at the top of the Cover "
    "Letter and use the given subject, reference and date.\n"
    "- Sign the Cover Letter off with the given signatory.\n"
    "- If a special discount is given, add it to 'costing' as a final line named 'Less "
    "special discount' with a NEGATIVE amount, and make 'total' the net (line items "
    "minus discount).\n"
    "- Follow any additional instructions/fee basis given.\n\n"
    "Each section 'body' is plain text; use blank lines between paragraphs and '- ' for "
    "bullets. If the identified-events register is empty, still produce a credible "
    "scoping proposal for an initial claims assessment and state that the scope and "
    "fees will be refined once the documents are reviewed."
)

_PROPOSAL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "reference": {"type": "string"},
        "date": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["heading", "body"],
                "additionalProperties": False,
            },
        },
        "costing": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "description": {"type": "string"},
                    "timeline": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["item", "description", "timeline", "amount"],
                "additionalProperties": False,
            },
        },
        "currency": {"type": "string"},
        "total": {"type": "number"},
        "paymentTerms": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title", "reference", "date", "sections", "costing", "currency",
        "total", "paymentTerms",
    ],
    "additionalProperties": False,
}


# Admin-entered fields (key → human label) woven into the proposal prompt.
_INPUT_LABELS = {
    "clientCompany": "Client company (addressee)",
    "attention": "Attention (contact & designation)",
    "clientAddress": "Client address",
    "subject": "Subject line",
    "reference": "Proposal reference",
    "date": "Proposal date",
    "signatory": "Signatory (name & title)",
    "currency": "Currency",
    "discount": "Special discount (amount to deduct)",
    "feeBasis": "Fee basis / commercial notes",
    "notes": "Additional instructions",
}


def _admin_block(inputs: dict) -> str:
    """Render the admin-provided fields for the prompt, skipping blanks."""
    lines = []
    for key, label in _INPUT_LABELS.items():
        val = (inputs or {}).get(key)
        if val is None or str(val).strip() == "":
            continue
        lines.append(f"- {label}: {val}")
    items = (inputs or {}).get("lineItems") or []
    priced = [i for i in items if str(i.get("item", "")).strip() and str(i.get("amount", "")).strip()]
    if priced:
        lines.append(
            "- Commercial line items (USE THESE EXACT items, timelines and amounts in "
            "the Commercial Proposal — do NOT invent your own fees):"
        )
        for it in priced:
            tl = str(it.get("timeline", "")).strip()
            desc = str(it.get("description", "")).strip()
            bits = [f"  • {it['item']} — amount {it['amount']}"]
            if tl:
                bits.append(f"timeline {tl}")
            if desc:
                bits.append(desc)
            lines.append(" — ".join(bits))
    return "\n".join(lines)


async def generate_client_proposal(
    *,
    project: dict,
    events: list[dict],
    document_names: list[str],
    inputs: dict | None = None,
) -> dict:
    """Draft the costed client proposal in AQMS house style. Returns
    {title, reference, date, sections:[{heading, body}],
     costing:[{item, description, timeline, amount}], currency, total,
     paymentTerms:[...]}. `inputs` holds admin-entered fields that override
     the AI's defaults (client address, attention, reference, date, discount, …)."""
    from app.services import proposal_templates

    p = project or {}
    inputs = inputs or {}
    ptype = str(p.get("proposalType") or "")
    currency = inputs.get("currency") or p.get("currency") or "OMR"
    today = str(inputs.get("date") or "").strip() or datetime.now(timezone.utc).strftime("%d %B %Y")
    header = [
        f"Proposal for: {p.get('name', '')}",
        f"Client / Employer: {p.get('employer', '')}",
        f"Reference code: {p.get('code', '')}",
        f"Contract standard: {p.get('standard', '')}",
        f"Engineer: {p.get('engineer', '')}",
        f"Contractor: {p.get('contractor', '')}",
        f"Location: {p.get('location', '')}",
        f"Currency: {currency}",
        f"Proposal date: {today}",
    ]
    docs = "\n".join(f"- {n}" for n in document_names) or "(none)"
    admin = _admin_block(inputs)
    admin_section = (
        "\n\nADMIN-PROVIDED FIELDS (use these exactly; they override your defaults)\n" + admin
        if admin
        else ""
    )
    # The system prompt + closing directive are built for the proposal's service
    # line (Claims Support, Quantum Expert, EOT, Delay/Arbitration Expert, Quantum
    # Claims) so the title, scope, approach, methodology and commercial framing match.
    system_prompt = proposal_templates.build_system_prompt(ptype, _AQMS_PROFILE)
    user_content = (
        "PROPOSAL DETAILS\n" + "\n".join(header)
        + admin_section
        + "\n\nDELAY EVENTS IDENTIFIED FROM THE PROJECT RECORDS (context for the proposal)\n"
        + _events_brief(events)
        + "\n\nSUPPORTING DOCUMENTS\n" + docs
        + proposal_templates.user_directive(ptype, currency, today)
    )

    async with _client().messages.stream(
        model=MODEL,
        # Headroom for the long-form service lines (the EOT template prescribes a
        # full six-section document) — thinking counts toward this budget too.
        max_tokens=32000,
        thinking={"type": "adaptive"},
        system=[
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": _PROPOSAL_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(payload)


# ── Admissibility scoring matrix ────────────────────────────────────────────
# Builds the weighted admissibility rubric for a project's EOT claim: the
# applicable contract clauses (typically the claims/notice clause, the EOT
# entitlement clause and the variations clause), each allocated marks out of 100,
# with a weighted checklist of compliance criteria. Grounded in the project's
# delay events and its Clause Library. The output is editable in the
# Admissibility tab (see services/admissibility_service.py).

_ADMISSIBILITY_SYSTEM_PROMPT = (
    "You are a forensic construction-claims analyst building an ADMISSIBILITY "
    "SCORING MATRIX for a project's Extension of Time (EOT) / delay claim under "
    "standards such as FIDIC, NEC4 and CPWD. You are given the project's delay events "
    "and its CLAUSE LIBRARY — the clauses that actually govern THIS contract, including "
    "any amended by the Particular Conditions. Build the matrix FRESH for this project, "
    "from its own clauses and events. Every project is different: derive the clauses, "
    "criteria and weights yourself; do not assume a fixed set.\n\n"
    "How the matrix works:\n"
    "- Select, FROM THE CLAUSE LIBRARY provided, the clauses that govern whether these "
    "delay events are ADMISSIBLE — typically the CLAIMS / NOTICE procedure clause, the "
    "EOT ENTITLEMENT clause and, where variations are involved, the VARIATIONS clause. "
    "Use as many as genuinely apply: it may be 1, 2 or 3 groups (occasionally more).\n"
    "- Each clause group gets a whole-number 'marks' allocation, and the marks across "
    "ALL groups MUST sum to 100. Weight by importance to admissibility for THESE events "
    "(the notice/claims clause is usually weighted most heavily).\n"
    "- Within each clause group, DERIVE the compliance CRITERIA from what THAT clause "
    "actually requires — each criterion is one requirement the claim must satisfy (a "
    "notice, a deadline, a content requirement, a record to keep). Give each a 'category' "
    "(the procedural stage, e.g. 'Delay Notice', 'Detailed EOT Claim', 'Time Bar'), the "
    "exact 'subClause' it comes from as numbered in this contract, a short 'description', "
    "and an 'overallWtg' percentage. The overallWtg values in a group MUST sum to 100; "
    "weight the pivotal requirements (the initial notice, the detailed claim, the time "
    "bar) far more heavily than minor content checks.\n"
    "- USE EACH CLAUSE AS IT APPLIES TO THIS PROJECT. When the Clause Library marks a "
    "clause [Modified by Particular Conditions], build its criteria from the AMENDED "
    "provisions (e.g. a changed notice period, an added requirement), set that group's "
    "'source' to 'modified', and state the change in 'note'. When the clause is NOT "
    "modified, use its original provisions and set 'source' to 'book'. A clause the "
    "Particular Conditions ADD is 'new'; a clause you add because the events clearly rely "
    "on it but it is missing from the library is 'manual'. Give 'note' one line where a "
    "modification or a new/manual clause needs explaining (empty string otherwise).\n\n"
    "Rules:\n"
    "- Cite ONLY clauses grounded in this project's Clause Library — take the clause "
    "numbers, titles and provisions from there, using the amended wording where the "
    "library flags a modification. Do NOT copy clauses, sub-clause references, criteria "
    "or weights from any other project or example.\n"
    "- Base the clause selection and weighting on the delay events (their causes and "
    "cited clauses). If the events are variation-driven, weight the variations clause "
    "higher; if they are pure Employer delays, the entitlement and notice clauses matter "
    "most.\n"
    "- A FORMAT EXAMPLE from a different project is provided in a separate block. Use it "
    "ONLY to match the STRUCTURE, granularity and weighting style — never its clauses, "
    "criteria or numbers.\n"
    "- 'summary' is one or two sentences explaining the clause selection and weighting."
)

# A FORMAT-ONLY example (from a different project) showing the structure, granularity
# and weighting style expected — NOT a template to reproduce. Kept as a separate cached
# system block. Rows: category | subClause | criteria | overallWtg.
_ADMISSIBILITY_REFERENCE = (
    "FORMAT EXAMPLE — ILLUSTRATIVE ONLY, from a DIFFERENT project. It shows the level of "
    "detail and the weighting pattern to aim for: clause groups → categories → sub-clause "
    "criteria, an overallWtg that sums to 100 per group, marks that sum to 100 across "
    "groups, and a few pivotal criteria weighted heavily with many minor checks weighted "
    "lightly. Do NOT copy its clauses, sub-clause numbers, criteria wording, marks or "
    "weights — build the real matrix from THIS project's Clause Library and delay events. "
    "This example just calibrates the format.\n\n"
    "=== Example: a Clause 20 group — marks 60 ===\n"
    "Delay Notice | 20.1A(1) | Notice within 14 days | 20\n"
    "Delay Notice | 20.1A(1) | Delay event described | 0.85\n"
    "Delay Notice | 20.1A(1) | Criticality explained | 0.85\n"
    "Delay Notice | 20.1A(1) | Programme impact identified | 0.85\n"
    "Delay Notice | 20.1A(1) | Recovery measures identified | 0.85\n"
    "Delay Notice | 20.1A(1) | Entitlement clause cited | 0.85\n"
    "Detailed EOT Claim | 20.1A(2) | Detailed claim submitted within 28 days | 20\n"
    "Detailed EOT Claim | 20.1A(2) | Relief & reasons identified | 0.85\n"
    "Detailed EOT Claim | 20.1A(2) | Delaying events described | 0.85\n"
    "Detailed EOT Claim | 20.1A(2) | Contractual entitlement identified | 0.85\n"
    "Detailed EOT Claim | 20.1A(2) | Contemporaneous records provided | 0.85\n"
    "Detailed EOT Claim | 20.1A(2) | Mitigation measures described | 0.85\n"
    "Interim EOT Claim | 20.1A(2) | Reason full claim unavailable | 0.85\n"
    "Interim EOT Claim | 20.1A(2) | Available details provided | 0.85\n"
    "Interim EOT Claim | 20.1A(2) | Updated every 28 days | 1.00\n"
    "Interim EOT Claim | 20.1A(2) | Final detailed claim submitted | 1.00\n"
    "Additional Payment | 20.1B(1) | Notice within 14 days for any additional payment or cost | 20\n"
    "Additional Payment | 20.1B(1) | Detailed monetary claim within 28 days | 1.00\n"
    "Additional Payment | 20.1B(2) | Interim claim submitted | 0.85\n"
    "Additional Payment | 20.1B(2) | Interim updates submitted | 0.85\n"
    "Additional Payment | 20.1B(2) | Final claim submitted | 0.85\n"
    "Additional Payment | 20.1B(2) | Quantum and evidence included | 0.85\n"
    "Time Bar | 20.1C(1) | Delay notice within 14 days | 0.85\n"
    "Claim Assessment | 20.1C(3) | Complete claim submitted pursuant to Clause 20.1 A or 20.1 B | 20\n"
    "Claim Assessment | 20.1C(3) | Additional info requested by Engineer & provided | 0.85\n"
    "Entitlement Determination | 20.1C(4) | Compliant EOT claim submitted pursuant to Clause 20.1 A - before determination | 0.85\n"
    "Entitlement Determination | 20.1C(4) | Compliant final cost claim submitted pursuant to Clause 20.1.B - before determination | 0.85\n"
    "(Example Clause 20 criteria weights sum to 100.)\n\n"
    "=== Example: a Clause 8.4 group — marks 30 ===\n"
    "EOT Entitlement | 8.4 | Is the Event Qualifying as defined in Clause 8.4 (Variation, any cause under these conditions, Employers delay) | 40\n"
    "EOT Entitlement | 8.4 | Criticality explained | 10\n"
    "EOT Entitlement | 8.4 | Impact on completion date demonstrated | 10\n"
    "EOT Entitlement | 8.4 | Claim submitted under Clause 20.1 | 40\n"
    "(Example Clause 8.4 criteria weights sum to 100.)\n\n"
    "=== Example: a Clause 13 group — marks 10 ===\n"
    "Variation Quotation | 13.3A | Quotation submitted within 14 days, after request from the Engineer | 40\n"
    "Variation Quotation | 13.3A | Delay implications included | 20\n"
    "Variation Without Quotation | 13.3B | If the Engineer does not request for Quotation, the Contractor within 14 days shall submit the cost of Delay/disruption it anticipates due to the variation | 40\n"
    "(Example Clause 13 criteria weights sum to 100.)"
)

_ADMISS_CRITERION_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "subClause": {"type": "string"},
        "description": {"type": "string"},
        "overallWtg": {"type": "number"},
    },
    "required": ["category", "subClause", "description", "overallWtg"],
    "additionalProperties": False,
}

_ADMISS_CLAUSE_SCHEMA = {
    "type": "object",
    "properties": {
        "clauseRef": {"type": "string"},
        "label": {"type": "string"},
        "marks": {"type": "number"},
        "source": {"type": "string", "enum": ["book", "modified", "new", "manual"]},
        "note": {"type": "string"},
        "criteria": {"type": "array", "items": _ADMISS_CRITERION_SCHEMA},
    },
    "required": ["clauseRef", "label", "marks", "source", "note", "criteria"],
    "additionalProperties": False,
}

_ADMISSIBILITY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "clauses": {"type": "array", "items": _ADMISS_CLAUSE_SCHEMA},
        "summary": {"type": "string"},
    },
    "required": ["clauses", "summary"],
    "additionalProperties": False,
}


def _clauses_for_admissibility(clauses: list[dict]) -> str:
    """Render the project's Clause Library for the admissibility prompt, surfacing
    each clause's origin and any Particular-Conditions modification so the model can
    set the group 'source' correctly."""
    if not clauses:
        return "(no clause library uploaded — infer the standard clauses from the events)"
    origin_of = {"book": "base standard form", "ai": "extracted from contract", "manual": "added manually"}
    lines = []
    for c in clauses:
        num = (c.get("clause_number") or "").strip()
        title = (c.get("clause_title") or "").strip()
        if not num and not title:
            continue
        desc = " ".join((c.get("clause_description") or "").split())
        if len(desc) > 200:
            desc = desc[:200].rstrip() + "…"
        origin = origin_of.get(c.get("source") or "manual", "added manually")
        line = f"- [{num}] {title} (origin: {origin}"
        if c.get("modified"):
            note = " ".join((c.get("modification_note") or "").split())
            line += f"; MODIFIED by Particular Conditions{': ' + note if note else ''}"
        line += ")"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines) or "(no usable clauses in the library)"


async def generate_admissibility_assessment(
    *,
    events: list[dict],
    clauses: list[dict],
    project_name: str | None = None,
    standard: str | None = None,
) -> dict:
    """Build the weighted admissibility matrix from the delay events + clause library.

    Returns {clauses: [{clauseRef, label, marks, source, note, criteria: [{category,
    subClause, description, overallWtg}]}], summary}. Clause `marks` sum to 100;
    each clause's criteria `overallWtg` sum to 100.
    """
    ctx_bits = []
    if project_name:
        ctx_bits.append(f"Project: {project_name}")
    if standard:
        ctx_bits.append(f"Contract standard: {standard}")
    header = " | ".join(ctx_bits)

    user_content = (
        (header + "\n\n" if header else "")
        + "DELAY EVENTS REGISTER (base the clause selection and weighting on these)\n"
        + _events_brief(events)
        + "\n\nPROJECT CLAUSE LIBRARY (cite these exact clause numbers; flag modified / new)\n"
        + _clauses_for_admissibility(clauses)
        + "\n\nBuild the admissibility scoring matrix now. Marks across clauses must sum "
        "to 100; each clause's criteria weights must sum to 100."
    )

    async with _client().messages.stream(
        model=EXTRACTION_MODEL,
        max_tokens=8192,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": _ADMISSIBILITY_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": _ADMISSIBILITY_REFERENCE,
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_content}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _ADMISSIBILITY_OUTPUT_SCHEMA},
        },
    ) as stream:
        response = await stream.get_final_message()

    payload = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(payload)


# ── Contractor admissibility scoring (Admissibility tab → Contractor) ───────
# Scores the admissibility matrix's criteria against EACH delay event: is the
# clause applicable to that event, did the Contractor comply, and what document
# evidences it. Events are scored in batches; the data room and the matrix are
# cached blocks so only the first batch pays for them.

_CONTRACTOR_ADMISS_SYSTEM_PROMPT = (
    "You are a forensic construction-claims analyst auditing a CONTRACTOR's "
    "compliance with the contract's claim procedure. You are given (a) the project's "
    "data room, (b) one or more DELAY EVENTS, and (c) an ADMISSIBILITY SCORING MATRIX "
    "— a numbered list of requirements drawn from this contract's clauses, each with a "
    "weightage. For EVERY delay event you are given, judge EVERY numbered requirement.\n\n"
    "For each requirement return:\n"
    "- 'applicable': 'Y' if that clause requirement genuinely bears on THIS delay event, "
    "'N' if it does not. Judge applicability from the nature of the event and the "
    "procedural route it took — e.g. requirements about a variation quotation are 'N' for "
    "an event that involves no variation; a requirement that only bites once the Engineer "
    "has requested something is 'N' when no such request was made; a requirement about an "
    "interim claim is 'N' where the Contractor went straight to a final detailed claim. "
    "Notice and detailed-claim requirements are normally 'Y' for every event.\n"
    "- 'complied': 'Y' when the record shows the Contractor did what the requirement asks "
    "— the right kind of document, from the right party, at the right time. Judge a "
    "document by its substance, not its caption: a letter that gives notice of a claim IS "
    "a notice, whether or not it quotes the clause number. Answer 'N' when the record "
    "holds nothing of the kind, when what it holds is plainly late against a dated "
    "deadline, or when it shows the step was not taken — NOT merely because you would "
    "like fuller wording or further corroboration. 'I cannot verify the contents' is not "
    "a finding of non-compliance. When 'applicable' is 'N', 'complied' MUST be 'N'.\n"
    "- 'evidence': the document reference and date that proves compliance, written as the "
    "documents themselves write it (e.g. 'OCC-SHSM-LTR-0191 dated 09 June 2024'). Where "
    "something was done but imperfectly (late, incomplete), still answer 'Y' on the "
    "substantive requirement it satisfies and say so here (e.g. 'Submitted, not within 14 "
    "days. Refer OCC-SHSM-LTR-0162 dated 02 May 2024'). Leave it as an empty string when "
    "the requirement is not applicable or when nothing was submitted. Keep it to one short "
    "line.\n\n"
    "Rules:\n"
    "- A DEADLINE requirement (e.g. 'Notice within 14 days', 'Detailed claim within 28 "
    "days') is 'complied': 'Y' ONLY if the document was actually submitted within that "
    "period, counted from the event's start / the date the Contractor became aware. If it "
    "was submitted late, answer 'N' and give the actual date in 'evidence'.\n"
    "- NEVER invent a document reference, letter number or date. Cite only references that "
    "appear in the record. Where the record genuinely holds nothing for a requirement, "
    "'complied' is 'N' and 'evidence' says so briefly, e.g. 'No notice traced in the "
    "record'.\n"
    "- Judge each event ON ITS OWN facts and its own correspondence. Two events rarely "
    "score identically.\n"
    "- Return exactly one entry per numbered requirement per event, echoing its 'slNo'. Do "
    "not renumber, skip or merge requirements.\n"
    "- 'remark' is one sentence per event summarising the compliance position (e.g. what "
    "was missed and why the claim is exposed)."
)

_CONTRACTOR_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "slNo": {"type": "integer"},
        "applicable": {"type": "string", "enum": ["Y", "N"]},
        "complied": {"type": "string", "enum": ["Y", "N"]},
        "evidence": {"type": "string"},
    },
    "required": ["slNo", "applicable", "complied", "evidence"],
    "additionalProperties": False,
}

_CONTRACTOR_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "eventRef": {"type": "string"},
        "remark": {"type": "string"},
        "rows": {"type": "array", "items": _CONTRACTOR_ROW_SCHEMA},
    },
    "required": ["eventRef", "remark", "rows"],
    "additionalProperties": False,
}

_CONTRACTOR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": _CONTRACTOR_EVENT_SCHEMA}},
    "required": ["events"],
    "additionalProperties": False,
}


def _criteria_for_contractor(criteria: list[dict]) -> str:
    """Render the flattened matrix criteria as a numbered list for the prompt.

    `criteria` are in display order and already carry a 1-based 'slNo'.
    """
    lines = []
    for c in criteria:
        clause = (c.get("clauseLabel") or "").strip()
        lines.append(
            f"{c.get('slNo')}. [{c.get('category', '')}] Sub-clause {c.get('subClause', '')} — "
            f"{c.get('description', '')} (weightage {c.get('weightage', 0)}"
            + (f"; from {clause}" if clause else "")
            + ")"
        )
    return "\n".join(lines)


# Delay events are scored a few at a time so the output budget can't truncate the
# JSON on projects with a long register and a large matrix.
CONTRACTOR_ADMISS_BATCH_SIZE = int(os.getenv("CONTRACTOR_ADMISS_BATCH_SIZE", "3"))
# Batches are independent once the register is cached, so they run concurrently;
# this bounds the burst so a long register can't trip the org's rate limit.
CONTRACTOR_ADMISS_CONCURRENCY = int(os.getenv("CONTRACTOR_ADMISS_CONCURRENCY", "6"))
# Judging a register entry is a reading task, not a reasoning one: at 'medium'
# the thinking tokens cost more than everything else in the run combined.
CONTRACTOR_ADMISS_EFFORT = os.getenv("CONTRACTOR_ADMISS_EFFORT", "low")


def provider_error_message(exc: anthropic.APIStatusError) -> str:
    """A user-facing line for a provider error that keeps the API's own explanation.

    A bare status code sends people hunting for a bug in the request: "400" reads
    identically whether the prompt overran the context window, the account is out
    of credit, or a parameter is malformed. The API says which — pass it through.
    """
    detail = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "").strip()
    if not detail:
        detail = str(getattr(exc, "message", "") or "").strip()
    if not detail:
        return f"AI provider error ({exc.status_code})."
    return f"AI provider error ({exc.status_code}): {detail}"


def _contractor_digest(documents: list[dict]) -> list[dict]:
    """Render each document as one register entry for the contractor scoring.

    Every batch of events has to re-read the whole data room, so what that room
    costs decides what the tab costs. Sending each document's full text put this
    project at 1.28M tokens — over the window, so the room had to be split into
    parts that were then re-read once per batch. The analysis already stored for
    each document at upload says what it is, who it is between, what it evidences
    and on what dates, which is what a procedural-compliance judgement actually
    turns on; at 179k tokens the whole room fits one cached prefix.

    The trade is fidelity: a summary can place and date a letter but not reproduce
    its body, so a deadline the register doesn't date cannot be tested from here.
    `documents` is a list of {"name", "type", "analysis"} dicts.
    """
    entries: list[dict] = []
    for d in documents:
        analysis = d.get("analysis") or {}
        name = d.get("name", "document")
        seg = [f"- {name} [{analysis.get('document_type') or d.get('type') or 'Other'}]"]
        title = (analysis.get("title") or "").strip()
        if title:
            seg.append(f" {title}")
        seg.append("\n")
        for label, key, cap in (("dates", "key_dates", 12), ("parties", "parties", 10)):
            values = analysis.get(key) or []
            if values:
                seg.append(f"  {label}: {', '.join(str(v) for v in values[:cap])}\n")
        for label, key in (("summary", "summary"), ("relevance", "relevance_to_claim")):
            value = (analysis.get(key) or "").strip()
            if value:
                seg.append(f"  {label}: {value}\n")
        points = analysis.get("key_points") or []
        if points:
            seg.append("  points: " + " | ".join(str(p) for p in points[:8]) + "\n")
        if not analysis:
            # Never analysed (or analysis failed) — say so rather than letting the
            # model read a bare filename as though the document had been reviewed.
            seg.append("  (not analysed — judge from the filename and type only)\n")
        entries.append({"name": name, "text": "".join(seg)})
    return entries


def _yes(value) -> bool:
    """True when an AI 'Y'/'N' field reads as yes."""
    return str(value or "").upper().startswith("Y")


def _merge_contractor_rows(into: dict, rows: list[dict]) -> None:
    """Fold one part's verdicts for an event into the running row map (keyed by slNo).

    A part sees only a slice of the data room, so its 'complied': 'N' means "not
    evidenced in THIS slice", not "never done". Compliance is therefore
    existential — a 'Y' from any part wins and brings its evidence line with it —
    and so is applicability, which a part can miss when the correspondence that
    makes the clause bite landed in another one.
    """
    for row in rows:
        try:
            sl = int(row.get("slNo"))
        except (TypeError, ValueError):
            continue
        prev = into.get(sl)
        if prev is None:
            into[sl] = dict(row)
            continue
        if _yes(row.get("applicable")):
            prev["applicable"] = "Y"
        if _yes(row.get("complied")) and not _yes(prev.get("complied")):
            prev["complied"] = "Y"
            prev["evidence"] = row.get("evidence") or ""
        elif not (prev.get("evidence") or "").strip():
            prev["evidence"] = row.get("evidence") or ""


def _merge_contractor_parts(parts: list[list[dict]]) -> list[dict]:
    """Consolidate the same events scored separately against each data-room part.

    Returns one entry per event, its rows merged per criterion and its remark
    taken from the part that could evidence the most compliance — the part that
    saw the correspondence, rather than one that saw none of it.
    """
    rows_by_ref: dict[str, dict] = {}
    remark_by_ref: dict[str, tuple[int, str]] = {}
    order: list[str] = []
    for part in parts:
        for res in part or []:
            ref = res.get("eventRef") or ""
            if ref not in rows_by_ref:
                rows_by_ref[ref] = {}
                remark_by_ref[ref] = (-1, "")
                order.append(ref)
            rows = res.get("rows") or []
            _merge_contractor_rows(rows_by_ref[ref], rows)
            remark = (res.get("remark") or "").strip()
            score = sum(1 for r in rows if _yes(r.get("complied")))
            if remark and score > remark_by_ref[ref][0]:
                remark_by_ref[ref] = (score, remark)
    return [
        {
            "eventRef": ref,
            "remark": remark_by_ref[ref][1],
            "rows": [rows_by_ref[ref][sl] for sl in sorted(rows_by_ref[ref])],
        }
        for ref in order
    ]


async def generate_contractor_admissibility(
    *,
    events: list[dict],
    criteria: list[dict],
    documents: list[dict],
    project_name: str | None = None,
    standard: str | None = None,
    on_progress=None,
) -> list[dict]:
    """Score every matrix criterion against every delay event.

    `criteria` is the flattened matrix (each with 'slNo', 'category', 'subClause',
    'description', 'weightage', 'clauseLabel'); `documents` is a list of
    {"name", "type", "text", "truncated"} dicts. Returns a list of
    {"eventRef", "remark", "rows": [{slNo, applicable, complied, evidence}]} —
    the caller maps each entry back onto its event and criterion.

    `on_progress(done, total)` is called after each batch of events.

    A data room that doesn't fit the model's context window is split into parts
    and every batch of events is scored against each part, then consolidated.
    Splitting never drops or truncates a document, so the only cost is that a
    part which didn't see the correspondence reports 'complied': 'N' where
    another part evidences it — which the consolidation resolves in favour of
    the part that found the evidence.
    """
    ctx_bits = []
    if project_name:
        ctx_bits.append(f"Project: {project_name}")
    if standard:
        ctx_bits.append(f"Contract standard: {standard}")
    header = " | ".join(ctx_bits)

    criteria_text = _criteria_for_contractor(criteria)

    batches = [
        events[i : i + CONTRACTOR_ADMISS_BATCH_SIZE]
        for i in range(0, len(events), max(1, CONTRACTOR_ADMISS_BATCH_SIZE))
    ]

    # Scoring reads the data-room REGISTER — one entry per document, built from the
    # analysis already run at upload — rather than every document's full text. On
    # this project that is 179k tokens against 1.28M, which is what makes the whole
    # room fit in a single cached prefix: each batch of events then costs one cache
    # read instead of seven full re-reads of the raw text.
    doc_parts = _batch_by_token_budget(
        _contractor_digest(documents), EXTRACTION_BATCH_TOKENS, _DIGEST_CHARS_PER_TOKEN
    )
    split = len(doc_parts) > 1
    if split:
        logger.info(
            "Document register exceeds one request — scoring %d event(s) against %d "
            "documents in %d parts",
            len(events),
            len(documents),
            len(doc_parts),
        )

    def _docs_text(part: list[dict], index: int) -> str:
        blocks = [header] if header else []
        if split:
            blocks.append(
                f"\nNOTE: this is part {index + 1} of {len(doc_parts)} of the register. "
                "The other parts are scored separately and merged afterwards, so a "
                "requirement you cannot evidence here may well be evidenced there — "
                "never conclude that the project as a whole is missing a document."
            )
        blocks.append(
            "\n===== DATA ROOM REGISTER — one entry per document held in the data room =====\n"
            "Each entry records a REAL document: its reference, type, date, parties and "
            "what it contains. Those facts are established — a document listed here is "
            "proven to exist and to have been issued as described. You cannot read the "
            "document bodies and you do not need to; judge each requirement on what the "
            "register shows.\n"
            "Answer 'complied': 'Y' when an entry, or several together, show the "
            "Contractor did what the requirement asks — the right kind of document, from "
            "the right party, at the right time. Judge documents by their substance, not "
            "their caption: a letter that gives notice of a claim IS a notice, whether or "
            "not it cites the clause number. Do NOT answer 'N' merely because you cannot "
            "see the full text, because the wording isn't quoted, or because you would "
            "like further corroboration — the register is the record, and 'cannot be "
            "verified' is not a finding of non-compliance.\n"
            "Answer 'N' when the register holds nothing of the kind, when what it holds "
            "is plainly late against a deadline the register itself dates, or when it "
            "shows the step was not taken. Cite only references that appear here, and "
            "where a deadline turns on a date the register does not give, say so in "
            "'evidence' rather than assuming one."
        )
        blocks.extend(d["text"] for d in part)
        return "\n".join(blocks)

    docs_texts = [_docs_text(p, i) for i, p in enumerate(doc_parts)]
    # The register is read back by every batch of events, so it is cached for an
    # hour when a long run would otherwise let a 5-minute entry lapse mid-flight.
    # One TTL for every breakpoint in the request: blocks are cached in order
    # (tools, system, messages) and a 1h block may not follow a 5m one.
    cache = {"type": "ephemeral", "ttl": "1h"} if len(batches) > 4 else {"type": "ephemeral"}

    async def _run_batch(batch: list[dict], docs_text: str) -> list[dict]:
        async with _client().messages.stream(
            model=EXTRACTION_MODEL,
            # One row per criterion per event, each with an evidence line — a small
            # budget silently truncates the JSON mid-event. Well above what a batch
            # needs: max_tokens is a ceiling, not a reservation, so the headroom is
            # free and keeps a dense batch off the truncation cliff.
            max_tokens=64000,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": _CONTRACTOR_ADMISS_SYSTEM_PROMPT,
                    "cache_control": cache,
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        # Cache breakpoints: the register and the matrix are the same
                        # for every batch of events, so every batch after the first
                        # reads them instead of re-sending them.
                        {
                            "type": "text",
                            "text": docs_text,
                            "cache_control": cache,
                        },
                        {
                            "type": "text",
                            "text": (
                                "\n===== ADMISSIBILITY MATRIX — score EVERY numbered "
                                "requirement below, for EVERY delay event =====\n"
                                + criteria_text
                            ),
                            "cache_control": cache,
                        },
                        {
                            "type": "text",
                            "text": (
                                "\n===== DELAY EVENTS TO SCORE (one 'events' entry per "
                                "event below, keyed by eventRef) =====\n"
                                + _events_brief(batch)
                            ),
                        },
                    ],
                }
            ],
            output_config={
                "effort": CONTRACTOR_ADMISS_EFFORT,
                "format": {"type": "json_schema", "schema": _CONTRACTOR_OUTPUT_SCHEMA},
            },
        ) as stream:
            response = await stream.get_final_message()

        payload = "".join(b.text for b in response.content if b.type == "text").strip()
        if not payload:
            logger.warning(
                "Empty contractor-admissibility payload for %s (stop_reason=%s)",
                [e.get("ref") for e in batch],
                response.stop_reason,
            )
            return []
        try:
            return json.loads(payload).get("events", [])
        except json.JSONDecodeError:
            # One unparseable part shouldn't lose the whole batch — the rest of the
            # register still scores and the merge keeps whatever it evidenced.
            logger.warning(
                "Unparseable contractor-admissibility output for %s",
                [e.get("ref") for e in batch],
                exc_info=True,
            )
            return []

    async def _score(batch: list[dict]) -> list[dict]:
        parts = await asyncio.gather(*(_run_batch(batch, t) for t in docs_texts))
        return _merge_contractor_parts(list(parts))

    done = 0

    def _tick(n: int) -> None:
        nonlocal done
        done += n
        if on_progress:
            on_progress(done, len(events))

    sem = asyncio.Semaphore(max(1, CONTRACTOR_ADMISS_CONCURRENCY))

    async def _guarded(batch: list[dict]) -> list[dict]:
        async with sem:
            out = await _score(batch)
        _tick(len(batch))
        return out

    if not batches:
        return []
    # The first batch alone writes the shared prefix to cache; the rest then run in
    # parallel off that one write. Firing everything at once would have every
    # request miss the cache and pay full price for the register — the batches are
    # independent, so this is the only ordering constraint in the run.
    results: list[dict] = await _score(batches[0])
    _tick(len(batches[0]))
    for scored in await asyncio.gather(*(_guarded(b) for b in batches[1:])):
        results.extend(scored)
    return results
