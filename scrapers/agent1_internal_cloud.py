"""
agent1_internal_cloud.py
========================
Agent 1 — Internal Data Processor  (CLOUD VERSION)

Identical output schema to agent1_internal.py.
Identical file format support (.txt .md .json .csv .xlsx .xls .docx).
Identical public API — drop-in replacement.

What is REMOVED vs the local version:
  - No local model download
  - No USE_LOCAL_MODEL flag
  - No transformers / torch dependency

Classifier priority:
  1. HuggingFace Inference API  (if HF_TOKEN is set and valid)
       Fast-fail: one probe call on session start.
       If 401/403/timeout → skips ALL API calls for the rest of the run.
       No per-turn hanging, no cascading timeouts.
  2. Hybrid local  (sklearn TF-IDF + keyword rules, zero API)
       scikit-learn is the only non-stdlib dependency.
       Works fully offline, no GPU, no model files.
  3. Pure keyword rules  (if sklearn is missing)
       Zero dependencies. Always works.

Dependencies (all pip-installable, all lightweight):
  Required always:     scikit-learn
  For .xlsx files:     openpyxl
  For .xls  files:     xlrd
  For .docx files:     python-docx
  For HF API (opt):    nothing extra — uses stdlib urllib

Output JSON (one file per input, written to output_dir):
  {
    "metadata": {
      "source_file":    "...",
      "source_type":    "Internal",
      "entity":         "Vishal Agarwal",
      "meeting_type":   "Customer Interview",
      "file_date":      "2024-01-15",
      "processed_at":   "...",
      "classifier_used":"hf_api" | "hybrid" | "rule_based",
      "total_signals":  23
    },
    "signals": [
      {
        "signal_id":   "VA_001",
        "signal_type": "Complaint",
        "confidence":  0.87,
        "content":     "...",
        "time_range":  "00:27 - 00:58",
        "turn_index":  3
      }
    ]
  }

Usage — called from another script:
  from agent1_internal_cloud import agent1_internal
  result  = agent1_internal("raw/call.md")
  results = agent1_internal("raw/")
  results = agent1_internal(["file1.txt", "file2.csv"])

Usage — PyCharm Run button:
  Set DEMO_INPUT at the bottom, hit Run.

Usage — CLI:
  python agent1_internal_cloud.py path/to/file.txt
  python agent1_internal_cloud.py raw/
"""

from __future__ import annotations

import os
import re
import sys
import csv
import json
import dotenv
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Tuple, Union

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# HuggingFace token — paste here or set env var HF_TOKEN.
# Leave "" to skip HF API and go straight to hybrid classifier.
HF_TOKEN: str = os.environ.get("HF_TOKEN", "")

# Minimum character length for a turn to be kept as a signal.
MIN_CONTENT_LENGTH: int = 40

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".xlsx", ".xls", ".docx"}

# HF API endpoints — tried in order, first success wins.
# All confirmed alive (return 403 with wrong token, not 404).
_HF_CASCADE = [
    "https://router.huggingface.co/hf-inference/models/sileod/deberta-v3-base-tasksource-nli",
    "https://router.huggingface.co/hf-inference/models/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    "https://router.huggingface.co/hf-inference/models/typeform/distilbert-base-uncased-mnli",
    "https://router.huggingface.co/hf-inference/models/facebook/bart-large-mnli",
    "https://router.huggingface.co/hf-inference/models/MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
    "https://router.huggingface.co/hf-inference/models/joeddav/xlm-roberta-large-xnli",
    "https://router.huggingface.co/hf-inference/models/valhalla/distilbart-mnli-12-3",
]

_SIGNAL_LABELS  = ["Feature", "Complaint", "Trend", "Insight"]
_MEETING_LABELS = [
    "Customer Interview", "Sales Call", "Internal Meeting",
    "Product Discussion", "Investor Call", "Founder Note",
]

# CSV / Excel column name hints
_TEXT_COLS      = ["text", "content", "message", "transcript", "body", "notes",
                   "description", "comment", "remarks", "summary"]
_SPEAKER_COLS   = ["speaker", "name", "who", "author", "person", "from"]
_TIMESTAMP_COLS = ["time", "timestamp", "time_range", "start", "at", "datetime",
                   "date", "when"]

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("agent1_cloud")

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SignalRecord:
    signal_id:   str
    signal_type: str
    confidence:  float
    content:     str
    time_range:  Optional[str]
    turn_index:  int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InternalResult:
    source_file:     str
    signals_path:    str
    total_signals:   int
    classifier_used: str
    metadata:        Dict[str, Any]     = field(default_factory=dict)
    signals:         List[SignalRecord] = field(default_factory=list)
    error:           Optional[str]      = None


# ─────────────────────────────────────────────────────────────────────────────
# FILE READERS
# ─────────────────────────────────────────────────────────────────────────────
# All return List[Dict] with shape:
#   { "index": int, "text": str, "time_range": str|None, "speaker": str|None }

def _turns_from_cleaner_json(data: Dict) -> List[Dict]:
    return data.get("turns", [])


def _turns_from_raw_json(data: Any) -> List[Dict]:
    items = data if isinstance(data, list) else [data]
    turns = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            text, speaker, ts = str(item).strip(), None, None
        else:
            text = next(
                (str(item[c]).strip() for c in _TEXT_COLS if c in item), ""
            ) or " ".join(str(v) for v in item.values() if isinstance(v, str))
            speaker = next((str(item[c]).strip() for c in _SPEAKER_COLS if c in item), None)
            ts      = next((str(item[c]).strip() for c in _TIMESTAMP_COLS if c in item), None)
        if len(text) >= MIN_CONTENT_LENGTH:
            turns.append({"index": i, "text": text, "time_range": ts, "speaker": speaker})
    return turns


def _read_json(path: Path) -> List[Dict]:
    with path.open(encoding="utf-8", errors="ignore") as f:
        data = json.load(f)
    if isinstance(data, dict) and "turns" in data:
        log.info("  Format: transcript_cleaner JSON")
        return _turns_from_cleaner_json(data)
    log.info("  Format: raw JSON")
    return _turns_from_raw_json(data)


def _read_txt_md(path: Path) -> List[Dict]:
    raw   = path.read_text(encoding="utf-8", errors="ignore")
    turns = []
    idx   = 0

    ts_pat = re.compile(
        r"(?:^#{1,4}\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\s*[-]\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\n",
        re.MULTILINE,
    )
    parts = ts_pat.split(raw)

    if len(parts) > 3:
        log.info("  Format: timestamped paragraphs (txt/md)")
        i = 1
        while i + 2 < len(parts):
            t_start = parts[i].strip()
            t_end   = parts[i + 1].strip()
            body    = re.sub(r"\s{2,}", " ", parts[i + 2].strip().replace("\n", " "))
            i += 3
            if len(body) >= MIN_CONTENT_LENGTH:
                turns.append({"index": idx, "text": body,
                              "time_range": f"{t_start} - {t_end}", "speaker": None})
                idx += 1
        return turns

    log.info("  Format: plain paragraphs (txt/md)")
    for block in re.split(r"\n{2,}", raw):
        block = re.sub(r"^#{1,4}\s+", "", block.strip(), flags=re.M)
        block = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", block)
        block = re.sub(r"\s{2,}", " ", block.replace("\n", " ")).strip()
        if len(block) >= MIN_CONTENT_LENGTH:
            turns.append({"index": idx, "text": block, "time_range": None, "speaker": None})
            idx += 1
    return turns


def _pick_column(headers_lower: List[str], candidates: List[str]) -> Optional[int]:
    for c in candidates:
        for i, h in enumerate(headers_lower):
            if c in h:
                return i
    return None


def _rows_to_turns(rows: List[List[str]], headers: List[str]) -> List[Dict]:
    hl  = [h.lower().strip() for h in headers]
    ti  = _pick_column(hl, _TEXT_COLS)
    si  = _pick_column(hl, _SPEAKER_COLS)
    tsi = _pick_column(hl, _TIMESTAMP_COLS)

    if ti is None:
        sample_lengths = [0] * len(headers)
        for row in rows[:10]:
            for ci, val in enumerate(row):
                sample_lengths[ci] = max(sample_lengths[ci], len(str(val)))
        ti = sample_lengths.index(max(sample_lengths)) if sample_lengths else 0
        log.info(f"  No text column matched — using '{headers[ti]}' as text")

    turns = []
    for idx, row in enumerate(rows):
        def _get(col_idx: Optional[int]) -> Optional[str]:
            if col_idx is None or col_idx >= len(row): return None
            v = str(row[col_idx]).strip()
            return v if v and v.lower() not in ("none", "nan", "null", "") else None
        text = _get(ti) or ""
        if len(text) < MIN_CONTENT_LENGTH:
            continue
        turns.append({"index": idx, "text": text,
                      "time_range": _get(tsi), "speaker": _get(si)})
    return turns


def _read_csv(path: Path) -> List[Dict]:
    log.info("  Format: CSV")
    with path.open(encoding="utf-8-sig", errors="ignore", newline="") as f:
        rows = list(csv.reader(f))
    return [] if not rows else _rows_to_turns(rows[1:], rows[0])


def _read_excel(path: Path) -> List[Dict]:
    ext = path.suffix.lower()
    log.info(f"  Format: Excel ({ext})")
    if ext == ".xlsx":
        try: import openpyxl
        except ImportError:
            raise ImportError("pip install openpyxl")
        wb  = openpyxl.load_workbook(path, data_only=True)
        all_rows = [[str(c.value) if c.value is not None else ""
                     for c in row] for row in wb.active.iter_rows()]
    else:
        try: import xlrd
        except ImportError:
            raise ImportError("pip install xlrd")
        wb  = xlrd.open_workbook(str(path))
        ws  = wb.sheet_by_index(0)
        all_rows = [[str(ws.cell_value(r, c)) for c in range(ws.ncols)]
                    for r in range(ws.nrows)]
    return [] if not all_rows else _rows_to_turns(all_rows[1:], all_rows[0])


def _read_docx(path: Path) -> List[Dict]:
    log.info("  Format: Word document (.docx)")
    try: from docx import Document
    except ImportError:
        raise ImportError("pip install python-docx")
    doc, turns, idx, buf = Document(str(path)), [], 0, []

    def _flush(b: List[str]):
        nonlocal idx
        combined = " ".join(b)
        if len(combined) >= MIN_CONTENT_LENGTH:
            turns.append({"index": idx, "text": combined,
                          "time_range": None, "speaker": None})
            idx += 1

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            if buf: _flush(buf); buf = []
            continue
        m = re.match(r"^([A-Z][a-zA-Z ]{1,25}):\s*(.+)", text)
        if m:
            if buf: _flush(buf); buf = []
            body = m.group(2).strip()
            if len(body) >= MIN_CONTENT_LENGTH:
                turns.append({"index": idx, "text": body,
                              "time_range": None, "speaker": m.group(1).strip()})
                idx += 1
        else:
            buf.append(text)
            if len(" ".join(buf)) >= 200:
                _flush(buf); buf = []
    if buf: _flush(buf)
    return turns


def _read_any_format(path: Path) -> List[Dict]:
    ext = path.suffix.lower()
    if   ext == ".json":           return _read_json(path)
    elif ext in (".txt", ".md"):   return _read_txt_md(path)
    elif ext == ".csv":            return _read_csv(path)
    elif ext in (".xlsx", ".xls"): return _read_excel(path)
    elif ext == ".docx":           return _read_docx(path)
    raise ValueError(f"Unsupported: '{ext}'")


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY & DATE EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _extract_entity(stem: str) -> str:
    s = re.sub(r"_(clean|turns|raw|processed|signals)$", "", stem, flags=re.I)
    s = re.sub(r"^\d+_", "", s)
    s = re.sub(r"_\d{8,}$", "", s)
    return re.sub(r"[_\-]+", " ", s).strip().title()


def _entity_initials(entity: str) -> str:
    return "".join(w[0].upper() for w in entity.split() if w)[:4] or "XX"


def _extract_date(stem: str, file_path: Path) -> Optional[str]:
    for pat, fmt in [
        (r"(\d{4})[_\-](\d{2})[_\-](\d{2})", "%Y%m%d"),
        (r"(\d{8})",                           "%Y%m%d"),
        (r"(\d{2})[_\-](\d{2})[_\-](\d{4})", "%d%m%Y"),
    ]:
        m = re.search(pat, stem)
        if m:
            joined = "".join(m.groups()) if len(m.groups()) > 1 else m.group(1)
            try: return datetime.strptime(joined, fmt).date().isoformat()
            except ValueError: continue
    try: return datetime.fromtimestamp(file_path.stat().st_mtime).date().isoformat()
    except OSError: return None


# ─────────────────────────────────────────────────────────────────────────────
# HYBRID CLASSIFIER  (Tier 2 — sklearn TF-IDF + keyword rules)
# ─────────────────────────────────────────────────────────────────────────────

_LABEL_DESCRIPTIONS: Dict[str, str] = {
    "Complaint": (
        "problem issue pain struggle challenge difficulty frustrated missing gap broken "
        "doesn't work not able can't find hard to use concern worry bad experience poor "
        "quality no way i am not getting not sure unclear confused"
    ),
    "Feature": (
        "platform tool feature product build launch create integrate automate dashboard "
        "portal website app system module upload report workflow crm subscriber "
        "functionality capability we can do they can do automation process"
    ),
    "Trend": (
        "growing industry market people are everyone moving shifting trend emerging "
        "increasing adoption future sector competitors regulation expanding landscape "
        "sebi rbi compliance change direction more and more leaving reducing"
    ),
    "Insight": (
        "interesting finding observation opportunity idea strategy approach understand "
        "realize potential valuable important learning discovery pattern behaviour "
        "people think know notice believe feel experience"
    ),
}

_RULE_KEYWORDS: Dict[str, List[str]] = {
    "Complaint": [
        "problem", "can't", "cannot", "don't know", "struggle", "challenge", "issue",
        "pain", "not able", "difficult", "frustrated", "missing", "lack", "gap", "fail",
        "wrong", "bad", "poor", "concern", "worry", "i am not getting",
        "no way", "doesn't work", "not getting",
    ],
    "Feature": [
        "platform", "tool", "feature", "build", "launch", "create", "integrate",
        "automate", "dashboard", "portal", "website", "app", "system", "module",
        "upload", "report", "workflow", "crm", "subscriber", "functionality", "capability",
    ],
    "Trend": [
        "growing", "industry", "market", "people are", "everyone", "reducing", "moving",
        "shifting", "trend", "emerging", "increasing", "decreasing", "adoption", "future",
        "more and more", "sector", "competitors", "regulation", "sebi", "rbi",
        "compliance", "leaving", "expanding",
    ],
}

_tfidf_vec = None
_tfidf_mat = None


def _get_tfidf():
    global _tfidf_vec, _tfidf_mat
    if _tfidf_vec is None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            _tfidf_vec = TfidfVectorizer(ngram_range=(1, 2))
            _tfidf_mat = _tfidf_vec.fit_transform(list(_LABEL_DESCRIPTIONS.values()))
        except ImportError:
            _tfidf_vec = None
    return _tfidf_vec, _tfidf_mat


def _classify_hybrid(text: str) -> Tuple[str, float]:
    """
    Hybrid TF-IDF + keyword classifier.
    Falls back to pure keyword rules if sklearn is not installed.
    """
    labels = list(_LABEL_DESCRIPTIONS.keys())
    lower  = text.lower()

    # Keyword rule scores (0–1)
    rule_scores: Dict[str, float] = {
        label: min(sum(1 for kw in kws if kw in lower) / 2.0, 1.0)
        for label, kws in _RULE_KEYWORDS.items()
    }
    rule_scores["Insight"] = 0.0

    # No keyword hit → Insight (catch-all for observations)
    if max(rule_scores.values()) < 0.3:
        return "Insight", 0.60

    # TF-IDF component
    vec, mat = _get_tfidf()
    if vec is not None:
        try:
            from sklearn.metrics.pairwise import cosine_similarity
            sims     = cosine_similarity(vec.transform([text]), mat)[0]
            tfidf    = dict(zip(labels, sims.tolist()))
            combined = {
                label: 0.65 * rule_scores.get(label, 0.0) + 0.35 * tfidf.get(label, 0.0)
                for label in labels
            }
            best = max(combined, key=combined.get)
            return best, round(min(combined[best] + 0.35, 0.95), 2)
        except Exception:
            pass

    # Pure keyword rules (sklearn absent or failed)
    for label, kws in _RULE_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            return label, 0.80
    return "Insight", 0.60


# ─────────────────────────────────────────────────────────────────────────────
# HF API CLASSIFIER  (Tier 1 — fast-fail, cloud-safe)
# ─────────────────────────────────────────────────────────────────────────────
# Key behaviours:
#   • One probe call at session start. If it fails → _hf_ok = False.
#   • All remaining turns skip the API instantly (no timeout, no network call).
#   • Per-call timeout: 5 seconds only.
#   • One warning log, not one per turn.

_hf_ok: Optional[bool] = None   # None = untested, True = working, False = dead


def _hf_call(url: str, text: str, labels: List[str], token: str) -> Optional[Tuple[str, float]]:
    """Single HF endpoint call. Returns result or None. Never raises."""
    import urllib.request, urllib.error
    body = json.dumps({"inputs": text, "parameters": {"candidate_labels": labels}}).encode()
    req  = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        if "labels" in data and "scores" in data:
            return data["labels"][0], round(data["scores"][0], 4)
    except urllib.error.HTTPError as e:
        log.debug(f"  HF {e.code} — {url}")
    except Exception as e:
        log.debug(f"  HF error — {e}")
    return None


def _hf_classify(text: str, labels: List[str], token: str) -> Optional[Tuple[str, float]]:
    """
    Try HF cascade with fast-fail.
    After first session failure → returns None instantly for all subsequent calls.
    """
    global _hf_ok

    if not token or _hf_ok is False:
        return None

    for url in _HF_CASCADE:
        result = _hf_call(url, text, labels, token)
        if result is not None:
            if _hf_ok is None:
                log.info(f"  HF API: working  ({url.split('/')[-1]})")
            _hf_ok = True
            return result

    # All models failed on this call
    if _hf_ok is None:
        _hf_ok = False
        log.warning(
            "  HF API unavailable (403/timeout on all models). "
            "Using hybrid classifier for all turns. "
            "Fix: go to huggingface.co → Settings → Access Tokens → "
            "New token → Fine-grained → enable 'Make calls to Inference Providers'."
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MEETING TYPE  (one classifier call per file)
# ─────────────────────────────────────────────────────────────────────────────

def _classify_meeting_type(turns: List[Dict], token: str) -> Tuple[str, str]:
    sample = " ".join(
        t.get("text", "") for t in turns[:6] if len(t.get("text", "")) > 20
    )[:600]
    if not sample:
        return "Internal Meeting", "rule_based"

    if token:
        result = _hf_classify(sample, _MEETING_LABELS, token)
        if result:
            return result[0], "hf_api"

    lower = sample.lower()
    if any(w in lower for w in ["subscriber", "client", "advisor", "investor", "customer"]):
        return "Customer Interview", "rule_based"
    if any(w in lower for w in ["revenue", "funding", "valuation", "pitch"]):
        return "Investor Call", "rule_based"
    if any(w in lower for w in ["feature", "sprint", "build", "product", "design"]):
        return "Product Discussion", "rule_based"
    if any(w in lower for w in ["buy", "sell", "pricing", "proposal", "deal"]):
        return "Sales Call", "rule_based"
    return "Internal Meeting", "rule_based"


# ─────────────────────────────────────────────────────────────────────────────
# CORE PROCESSOR
# ─────────────────────────────────────────────────────────────────────────────

def _make_signal_id(initials: str, pos: int) -> str:
    return f"{initials}_{pos:03d}"


class _Processor:
    def __init__(self, output_dir: str, hf_token: str):
        self.out_dir = Path(output_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.token   = hf_token.strip()

    def process(self, file_path: Path) -> InternalResult:
        log.info(f"Processing: {file_path.name}")

        try:
            raw_turns = _read_any_format(file_path)
        except ImportError as e:
            log.error(f"  Skipped — missing library: {e}")
            return InternalResult(file_path.name, "", 0, "none", error=str(e))
        except Exception as e:
            log.error(f"  Skipped — read error: {e}")
            return InternalResult(file_path.name, "", 0, "none", error=str(e))

        if not raw_turns:
            log.warning(f"  No usable content in {file_path.name}")
            return InternalResult(file_path.name, "", 0, "none",
                                  error="No usable content found")

        stem         = file_path.stem
        entity       = _extract_entity(stem)
        initials     = _entity_initials(entity)
        file_date    = _extract_date(stem, file_path)
        meeting_type, mt_src = _classify_meeting_type(raw_turns, self.token)

        log.info(f"  Entity       : {entity}")
        log.info(f"  Meeting type : {meeting_type}  [{mt_src}]")
        log.info(f"  File date    : {file_date or 'null'}")
        log.info(f"  Turns loaded : {len(raw_turns)}")

        signals: List[SignalRecord] = []
        clf_used: set = set()
        position = 0

        for turn in raw_turns:
            text = turn.get("text", "").strip()
            if len(text) < MIN_CONTENT_LENGTH:
                continue

        for turn in raw_turns:
            r_text = turn.get("text", "")

            # 1. STRICT SANITIZATION:
            text = re.sub(r'[^a-zA-Z0-9\s.,\'";:?!()\-]', '', r_text)
            # 2. COLLAPSE WHITESPACE:
            text = re.sub(r'\s+', ' ', text).strip()

            if len(text) < MIN_CONTENT_LENGTH:
                continue

            # Tier 1: HF API
            result = _hf_classify(text, _SIGNAL_LABELS, self.token)
            if result:
                signal_type, confidence = result
                clf_used.add("hf_api")
            else:
                # Tier 2/3: hybrid (sklearn + rules) or pure rules
                signal_type, confidence = _classify_hybrid(text)
                clf_used.add("hybrid")

            position += 1
            signals.append(SignalRecord(
                signal_id   = _make_signal_id(initials, position),
                signal_type = signal_type,
                confidence  = confidence,
                content     = text,
                time_range  = turn.get("time_range"),
                turn_index  = turn.get("index", position),
            ))

        # Determine overall classifier label
        if "hf_api" in clf_used and "hybrid" in clf_used:
            classifier_used = "hf_api+hybrid"
        elif "hf_api" in clf_used:
            classifier_used = "hf_api"
        else:
            classifier_used = "hybrid"

        log.info(f"  Signals      : {len(signals)}")
        log.info(f"  Classifier   : {classifier_used}")

        metadata = {
            "source_file"    : file_path.name,
            "source_type"    : "Internal",
            "entity"         : entity,
            "meeting_type"   : meeting_type,
            "file_date"      : file_date,
            "processed_at"   : datetime.now(timezone.utc).isoformat(),
            "classifier_used": classifier_used,
            "total_signals"  : len(signals),
        }

        out_stem = re.sub(r"_(turns|clean|raw|processed)$", "", stem, flags=re.I)
        out_stem = re.sub(r"[^a-zA-Z0-9]+", "_", out_stem).strip("_").lower()
        out_path = self.out_dir / f"{out_stem}_signals.json"

        out_path.write_text(
            json.dumps({"metadata": metadata, "signals": [s.to_dict() for s in signals]},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"  -> {out_path}\n")

        return InternalResult(
            source_file     = file_path.name,
            signals_path    = str(out_path),
            total_signals   = len(signals),
            classifier_used = classifier_used,
            metadata        = metadata,
            signals         = signals,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API  (drop-in replacement for agent1_internal.py)
# ─────────────────────────────────────────────────────────────────────────────

def agent1_internal(
    input_path : Union[str, Path, List[Union[str, Path]]],
    output_dir : str = "./signals",
    hf_token   : str = HF_TOKEN,
) -> Union[InternalResult, List[InternalResult]]:
    """
    Process one file, a list of files, or a folder.
    Drop-in replacement for agent1_internal.agent1_internal().
    """
    processor = _Processor(output_dir=output_dir, hf_token=hf_token)

    if isinstance(input_path, list):
        results = []
        for p in input_path:
            fp = Path(p)
            if not fp.is_file():
                log.warning(f"Not a file, skipping: {p}"); continue
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                log.warning(f"Unsupported format, skipping: {fp.name}"); continue
            results.append(processor.process(fp))
        return results

    p = Path(input_path)
    if p.is_dir():
        return agent1_internal_batch(str(p), output_dir, hf_token)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {input_path}")
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported format: '{p.suffix}'")
    return processor.process(p)


def agent1_internal_batch(
    input_dir  : str,
    output_dir : str = "./signals",
    hf_token   : str = HF_TOKEN,
) -> List[InternalResult]:
    folder = Path(input_dir)
    proc   = _Processor(output_dir=output_dir, hf_token=hf_token)
    files  = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        log.warning(f"No supported files in '{input_dir}'.")
        return []

    log.info(f"Batch: {len(files)} file(s) in '{input_dir}'")
    results = []
    for f in files:
        try:
            results.append(proc.process(f))
        except Exception as e:
            log.error(f"  x {f.name}: {e}")
            results.append(InternalResult(f.name, "", 0, "none", error=str(e)))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# PRINT HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _print_result(r: InternalResult):
    status = "OK" if not r.error else f"SKIP [{r.error}]"
    print(f"\n[{status}] {r.source_file}")
    if not r.error:
        print(f"  Entity       : {r.metadata.get('entity')}")
        print(f"  Meeting type : {r.metadata.get('meeting_type')}")
        print(f"  File date    : {r.metadata.get('file_date') or 'null'}")
        print(f"  Signals      : {r.total_signals}")
        print(f"  Classifier   : {r.classifier_used}")
        print(f"  -> {r.signals_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        prog="agent1_internal_cloud",
        description="Agent 1 Internal — Cloud version (HF API + hybrid, no local model)",
    )
    parser.add_argument("input", help="File or folder path")
    parser.add_argument("--output-dir", default="signals")
    parser.add_argument("--hf-token", default=HF_TOKEN)
    args   = parser.parse_args()
    result = agent1_internal(args.input, args.output_dir, args.hf_token or HF_TOKEN)
    if isinstance(result, list):
        for r in result: _print_result(r)
    else:
        _print_result(result)


# ─────────────────────────────────────────────────────────────────────────────
# DEMO — PyCharm Run button
# ─────────────────────────────────────────────────────────────────────────────

DEMO_INPUT      = input("Enter file path: ").strip()
DEMO_OUTPUT_DIR = "output"
DEMO_HF_TOKEN   = HF_TOKEN   # set HF_TOKEN env var, or paste here


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
    else:
        print(f"\n{'='*60}")
        print("  Agent 1 Internal — Cloud")
        print(f"{'='*60}\n")

        result  = agent1_internal(DEMO_INPUT, DEMO_OUTPUT_DIR, DEMO_HF_TOKEN)
        results = result if isinstance(result, list) else [result]

        print(f"\n{'='*60}")
        print(f"  Files processed : {len(results)}")
        print(f"  Succeeded       : {len([r for r in results if not r.error])}")
        print(f"  Skipped/failed  : {len([r for r in results if r.error])}")
        print(f"{'='*60}")

        for r in results:
            _print_result(r)

        first_ok = next((r for r in results if not r.error and r.signals), None)
        if first_ok:
            print(f"\nSample signals from '{first_ok.source_file}' (first 5):")
            for s in first_ok.signals[:5]:
                ts = f"  [{s.time_range}]" if s.time_range else ""
                print(f"\n  {s.signal_id}{ts}")
                print(f"  Type    : {s.signal_type}  (confidence: {s.confidence})")
                print(f"  Content : {s.content[:120]}")

        print(f"\n{'='*60}\n")