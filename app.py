"""
Notesmith — YouTube video se class notes ka PDF (free, apna clone).

Ek hi file mein poora kaam:
  1. YouTube link se transcript nikalna
  2. Transcript se structured notes banana (Gemini / Groq free API key se,
     ya key na ho to offline extractive notes)
  3. Notes ka sundar PDF banana (Devanagari sahi render hota hai)

Chalane ke tarike:
  Web app  : streamlit run app.py
  Command  : python app.py --cli "<youtube link>" --lang hinglish --out notes.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# 1. YouTube link / transcript
# ----------------------------------------------------------------------------

_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/))([A-Za-z0-9_-]{11})"
)


def video_id(url_or_id: str) -> str:
    """Link ya seedha ID — dono se 11-character video id nikalta hai."""
    s = (url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = _ID_RE.search(s)
    if m:
        return m.group(1)
    raise ValueError("Yeh YouTube link samajh nahi aaya. Poora link paste kijiye.")


@dataclass
class Segment:
    t: float          # start time, seconds
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    language: str = "unknown"
    auto_generated: bool = True
    title_hint: str = ""

    @property
    def plain(self) -> str:
        return " ".join(s.text for s in self.segments)

    def timestamped(self, limit_chars: int = 60000) -> str:
        """LLM ke liye [mm:ss] ke saath text; limit se zyada ho to kaat dete hain."""
        out, used = [], 0
        for s in self.segments:
            line = f"[{fmt_time(s.t)}] {s.text}"
            if used + len(line) > limit_chars:
                out.append("... (transcript ka baaki hissa chhota kiya gaya)")
                break
            out.append(line)
            used += len(line) + 1
        return "\n".join(out)


_NOISE = re.compile(r"^(\[.*?\]|\(.*?\)|♪.*♪|[\W_]+)$")


def clean_segments(raw: list[Segment]) -> list[Segment]:
    out = []
    for s in raw:
        txt = re.sub(r"\s+", " ", s.text).replace("\u200b", " ").strip()
        txt = re.sub(r"[♪]+", "", txt).strip()
        if not txt or _NOISE.match(txt):
            continue
        if out and txt == out[-1].text:
            continue
        out.append(Segment(s.t, txt))
    return out


def fetch_transcript(url_or_id: str, prefer=("hi", "en")) -> Transcript:
    """youtube-transcript-api se captions. Manual captions pehle, phir auto."""
    from youtube_transcript_api import YouTubeTranscriptApi

    vid = video_id(url_or_id)
    api = YouTubeTranscriptApi()
    listing = api.list(vid)

    tr = None
    for finder in (listing.find_transcript, listing.find_generated_transcript):
        try:
            tr = finder(list(prefer))
            break
        except Exception:
            tr = None
    if tr is None:
        available = list(listing)
        if not available:
            raise RuntimeError("Is video par koi caption/subtitle nahi hai.")
        tr = available[0]

    fetched = tr.fetch()
    segs = [Segment(float(s.start), s.text) for s in fetched.snippets]
    segs = clean_segments(segs)
    if not segs:
        raise RuntimeError("Transcript khali aaya. Doosra video try kijiye.")
    return Transcript(
        segments=segs,
        language=getattr(tr, "language_code", "unknown"),
        auto_generated=bool(getattr(tr, "is_generated", True)),
    )


def transcript_from_text(text: str) -> Transcript:
    """Jab user khud transcript paste kare (ya YouTube block kar de)."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    segs, t = [], 0.0
    for ln in lines:
        if not ln:
            continue
        m = re.match(r"^\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*(.*)$", ln)
        if m:
            a, b, c, rest = m.groups()
            secs = (int(a) * 3600 + int(b) * 60 + int(c)) if c else (int(a) * 60 + int(b))
            segs.append(Segment(float(secs), rest or ""))
        else:
            segs.append(Segment(t, ln))
            t += 6.0
    segs = clean_segments(segs)
    if not segs:
        raise RuntimeError("Paste kiya gaya text khali hai.")
    return Transcript(segments=segs, language="pasted", auto_generated=False)


# ----------------------------------------------------------------------------
# 2. Notes generation
# ----------------------------------------------------------------------------

def fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


LANG_LINE = {
    "hinglish": "Write everything in Hinglish: Hindi sentences in Roman script, technical words in English (e.g. 'Prakash ka paravartan yaani reflection of light do niyamon par chalta hai').",
    "hindi": "Write everything in simple, clear Hindi using Devanagari script. Technical terms ke saath bracket mein English word bhi dijiye.",
    "english": "Write everything in simple, clear English suitable for an Indian school/college student.",
}

PROMPT = """You are an expert teacher who turns lecture transcripts into revision-ready class notes.

VIDEO TRANSCRIPT (each line starts with [mm:ss] timestamp):
---
{transcript}
---

{languageline}

Rules:
- Use ONLY what is said in the transcript. Do not add facts from outside. If something is unclear, leave it out.
- Notes must be useful for exam revision: short points, definitions, formulae, examples that the teacher actually gave.
- 3 to 8 sections depending on the length and content. Each section needs 3-7 points.
- "anchor" must be an exact 5-10 word quote copied verbatim from the transcript where that section starts (used to find the timestamp). No paraphrase.
- Key terms: only terms that are actually defined or emphasised in the video (0 to 8 terms).
- Formulas: only if the video actually states them (may be empty). A rule or law said in words
  (e.g. "angle of incidence equals angle of reflection") should also be written here as an equation.
- Exam tips: only what the teacher says or clearly implies about exams/important points (may be empty).
- Revision questions: 5 to 8 short questions answerable from the notes.

Return ONLY valid JSON, no markdown fences, in exactly this shape:
{{
  "title": "short descriptive title of the topic",
  "subject": "subject name, e.g. Physics / Biology / Economics",
  "summary": "2-3 sentence summary of what the video teaches",
  "sections": [
    {{"heading": "section heading", "anchor": "exact quote from transcript",
      "points": ["point", "point"],
      "terms": [{{"term": "term", "meaning": "one line meaning"}}]}}
  ],
  "formulas": ["formula — what it means"],
  "exam_tips": ["tip"],
  "questions": ["question"]
}}"""


@dataclass
class Notes:
    title: str
    subject: str = ""
    summary: str = ""
    sections: list = field(default_factory=list)   # [{heading, points, terms, time}]
    formulas: list = field(default_factory=list)
    exam_tips: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    source: str = "ai"        # "ai" ya "offline"
    video_url: str = ""
    language: str = "english"


def _json_from_text(raw: str) -> dict:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        chunk = raw[start : end + 1]
        try:
            return json.loads(chunk)
        except Exception:
            chunk = re.sub(r",\s*([}\]])", r"\1", chunk)   # trailing commas hatao
            return json.loads(chunk)
    raise ValueError("AI ka jawab JSON mein nahi tha.")


def _post_json(url: str, body: dict, headers: dict, timeout: int = 180) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            err = json.loads(e.read().decode()).get("error", {})
            detail = f"{err.get('status','')} {err.get('message','')}".strip()
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}") from None


# Model naam Google aksar badalta hai, isliye naam hardcode nahi karte —
# key se available models ki list maang kar usme se flash model chunte hain.
GEMINI_PREFER = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.6-flash",
                 "gemini-3.5-flash", "gemini-2.5-flash"]
_NOT_TEXT = ("image", "tts", "lyria", "computer", "robotics", "embedding",
             "deep-research", "antigravity", "omni", "transcribe", "gemma",
             "nano-banana")


def gemini_models(api_key: str) -> list[str]:
    url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
    req = urllib.request.Request(url, headers={"x-goog-api-key": api_key})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode())
    return [m["name"].split("/")[-1] for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def pick_gemini_models(api_key: str) -> list[str]:
    try:
        have = gemini_models(api_key)
    except Exception:
        return GEMINI_PREFER[:3]
    ordered = [m for m in GEMINI_PREFER if m in have]
    extra = [m for m in have if "flash" in m
             and not any(b in m for b in _NOT_TEXT) and m not in ordered]
    return (ordered + extra)[:6] or GEMINI_PREFER[:3]


def call_gemini(prompt: str, api_key: str) -> str:
    last = None
    for model in pick_gemini_models(api_key):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json",
                                 "maxOutputTokens": 8192},
        }
        try:
            res = _post_json(url, body, {"Content-Type": "application/json",
                                         "x-goog-api-key": api_key})
            cands = res.get("candidates") or []
            if not cands:
                last = RuntimeError(f"{model}: koi jawab nahi aaya")
                continue
            parts = cands[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if text.strip():
                return text
            last = RuntimeError(f"{model}: khali jawab")
        except Exception as e:      # model band / quota / key galat
            last = e
            continue
    raise RuntimeError(f"Gemini call fail hui: {last}")


def call_groq(prompt: str, api_key: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
    }
    res = _post_json(url, body, {"Content-Type": "application/json",
                                 "Authorization": f"Bearer {api_key}"})
    return res["choices"][0]["message"]["content"]


_STOP = set("""a an the and or but if then than that this these those is are was were be been being
of in on at to for from with by as it its it's we you your i he she they them his her their
so not no do does did have has had will would can could should may might must about into over
under again very just also more most some any all each other such only own same too s t don now
kya hai hain ka ki ke ko se me mein ek yeh vah aur par bhi to nahi kuch karna karte hota hoti""".split())


def offline_notes(tr: Transcript, language: str) -> Notes:
    """Bina API key: transcript se hi extractive notes. Quality kam, par kaam karta hai."""
    words_all = tr.plain.split()
    n_words = max(1, len(words_all))
    n_sections = max(3, min(8, n_words // 400 or 3))

    # sentences with their approximate time
    sents: list[tuple[float, str]] = []
    for seg in tr.segments:
        for piece in re.split(r"(?<=[.!?।])\s+", seg.text):
            piece = piece.strip()
            if len(piece.split()) >= 4:
                sents.append((seg.t, piece))
    if not sents:
        sents = [(s.t, s.text) for s in tr.segments]

    per = max(1, len(sents) // n_sections)
    freq: dict[str, int] = {}
    for _, s in sents:
        for w in re.findall(r"[\w\u0900-\u097F]+", s.lower()):
            if len(w) > 3 and w not in _STOP:
                freq[w] = freq.get(w, 0) + 1

    sections = []
    for i in range(n_sections):
        chunk = sents[i * per : (i + 1) * per] if i < n_sections - 1 else sents[i * per :]
        if not chunk:
            continue
        scored = sorted(
            chunk,
            key=lambda ts: sum(freq.get(w, 0) for w in re.findall(r"[\w\u0900-\u097F]+", ts[1].lower()))
            / max(4, len(ts[1].split())),
            reverse=True,
        )
        points = []
        for _, s in scored:
            s = s.strip()
            if s and s not in points:
                points.append(s)
            if len(points) == 5:
                break
        head_words = [w for w in re.findall(r"[\w\u0900-\u097F]+", " ".join(points).lower())
                      if len(w) > 4 and w not in _STOP][:3]
        heading = ("Part " + str(i + 1)) + ((" — " + ", ".join(head_words)) if head_words else "")
        sections.append({"heading": heading, "points": points, "terms": [], "time": chunk[0][0]})

    summary = " ".join(s for _, s in sents[:2])[:400]
    return Notes(
        title="Video notes",
        subject="",
        summary=summary,
        sections=sections,
        source="offline",
        language=language,
    )


def _attach_times(notes: Notes, tr: Transcript) -> None:
    """LLM ke diye anchor quote se transcript mein timestamp dhoondhte hain."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\u0900-\u097F ]+", " ", s.lower())).strip()

    normed = [(s.t, norm(s.text)) for s in tr.segments]
    for sec in notes.sections:
        if sec.get("time") is not None:
            continue
        anchor = norm(sec.pop("anchor", "") or "")
        sec.pop("anchor", None)
        if not anchor:
            sec["time"] = None
            continue
        sec["time"] = None
        for n_words in (7, 5, 3):
            needle = " ".join(anchor.split()[:n_words])
            if len(needle) < 8:
                continue
            for t, text in normed:
                if needle in text:
                    sec["time"] = t
                    break
            if sec["time"] is not None:
                break


def make_notes(tr: Transcript, language: str = "hinglish", api_key: str = "",
               provider: str = "gemini", video_url: str = "") -> Notes:
    lang = language if language in LANG_LINE else "hinglish"
    if not api_key:
        n = offline_notes(tr, lang)
        n.video_url = video_url
        return n

    prompt = PROMPT.format(transcript=tr.timestamped(), languageline=LANG_LINE[lang])
    raw = call_groq(prompt, api_key) if provider == "groq" else call_gemini(prompt, api_key)
    data = _json_from_text(raw)

    sections = []
    for sec in (data.get("sections") or []):
        if not isinstance(sec, dict):
            continue
        sections.append({
            "heading": str(sec.get("heading") or "").strip() or "Section",
            "anchor": sec.get("anchor") or "",
            "points": [str(p).strip() for p in (sec.get("points") or []) if str(p).strip()],
            "terms": [t for t in (sec.get("terms") or []) if isinstance(t, dict) and t.get("term")],
            "time": None,
        })
    if not sections:
        raise ValueError("AI ne sections nahi diye. Dobara try kijiye.")

    n = Notes(
        title=str(data.get("title") or "Video notes").strip(),
        subject=str(data.get("subject") or "").strip(),
        summary=str(data.get("summary") or "").strip(),
        sections=sections,
        formulas=[str(x).strip() for x in (data.get("formulas") or []) if str(x).strip()],
        exam_tips=[str(x).strip() for x in (data.get("exam_tips") or []) if str(x).strip()],
        questions=[str(x).strip() for x in (data.get("questions") or []) if str(x).strip()],
        source="ai",
        video_url=video_url,
        language=lang,
    )
    _attach_times(n, tr)
    return n


# ----------------------------------------------------------------------------
# 3. PDF
# ----------------------------------------------------------------------------

FONT_URL = ("https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/"
            "NotoSansDevanagari/NotoSansDevanagari-Regular.ttf")
FONT_NAME = "NotoSansDevanagari-Regular.ttf"


def ensure_font() -> str | None:
    """Devanagari font cache mein laata hai (ek baar download, phir local)."""
    cache = Path.home() / ".cache" / "notesmith"
    path = cache / FONT_NAME
    if path.exists() and path.stat().st_size > 50000:
        return str(path)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(FONT_URL, timeout=60) as r, open(tmp, "wb") as f:
            f.write(r.read())
        tmp.replace(path)
        return str(path)
    except Exception:
        return None


LABELS = {
    "hindi": {"notes": "नोट्स", "formulas": "सूत्र", "tips": "परीक्षा के लिए ज़रूरी",
              "questions": "दोहराने के लिए प्रश्न", "term": "शब्द", "meaning": "अर्थ",
              "brand": "क्लास नोट्स"},
}
LABELS_DEFAULT = {"notes": "Notes", "formulas": "Formulae", "tips": "Exam tips",
                  "questions": "Revision questions", "term": "Term", "meaning": "Meaning",
                  "brand": "class notes"}

CSS = """
@page {
  size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-center { content: counter(page) " / " counter(pages);
                   font-size: 8.5pt; color: #8a8f98; }
  @bottom-right { content: "Notesmith"; font-size: 8pt; color: #b8bcc4; }
}
* { box-sizing: border-box; }
body { font-family: "ND", "DejaVu Sans", sans-serif; font-size: 10.4pt;
       line-height: 1.5; color: #1c2430; }
.brand { font-size: 8.5pt; letter-spacing: 1.6px; text-transform: uppercase;
         color: #5b6bff; font-weight: 700; }
h1 { font-size: 19pt; line-height: 1.25; margin: 4px 0 6px; color: #10151d; }
.chips { margin: 0 0 10px; }
.chip { display: inline-block; font-size: 8.4pt; padding: 2px 8px; margin-right: 6px;
        border-radius: 10px; background: #eef0ff; color: #3b47c9; }
.chip.grey { background: #f1f3f6; color: #5c6672; }
.summary { background: #f6f8ff; border-left: 3px solid #5b6bff; padding: 9px 12px;
           border-radius: 4px; font-size: 10pt; margin: 8px 0 14px; }
.meta { font-size: 8.4pt; color: #7a828d; margin-bottom: 14px; }
h2 { font-size: 12.4pt; margin: 16px 0 6px; padding-bottom: 3px;
     border-bottom: 1px solid #e4e8ef; color: #1b2b6b; page-break-after: avoid; }
.sec { page-break-inside: avoid; margin-bottom: 10px; }
.sec h3 { font-size: 11pt; margin: 12px 0 4px; color: #16233c; page-break-after: avoid; }
.sec h3 .ts { font-size: 8.4pt; font-weight: 400; color: #5b6bff; margin-left: 6px; }
ul { margin: 4px 0 6px; padding-left: 17px; }
li { margin-bottom: 3px; }
table { width: 100%; border-collapse: collapse; font-size: 9.6pt; margin: 4px 0 8px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #e8ecf2;
         vertical-align: top; }
th { background: #f6f8ff; color: #1b2b6b; font-size: 9pt; }
.formula { background: #fbfbf5; border: 1px dashed #d8d2b8; padding: 7px 10px;
           border-radius: 4px; margin-bottom: 5px; font-size: 10pt; }
ol { padding-left: 18px; }
.tip { color: #146b4b; }
.foot { margin-top: 18px; padding-top: 8px; border-top: 1px solid #e4e8ef;
        font-size: 8.2pt; color: #98a0ab; }
"""


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def notes_to_pdf(notes: Notes, out_path: str, video_url: str = "",
                 transcript: Transcript | None = None, include_transcript: bool = False) -> str:
    from weasyprint import HTML

    font = ensure_font()
    face = ""
    if font:
        face = ('@font-face { font-family: "ND"; src: url("file://%s"); }' % font)

    L = LABELS.get(notes.language, LABELS_DEFAULT)
    url = video_url or notes.video_url
    chips = []
    if notes.subject:
        chips.append(f'<span class="chip">{_esc(notes.subject)}</span>')
    chips.append('<span class="chip grey">AI notes</span>' if notes.source == "ai"
                 else '<span class="chip grey">Auto notes (no AI key)</span>')
    if transcript is not None:
        chips.append(f'<span class="chip grey">{_esc(transcript.language)}'
                     f'{"" if not transcript.auto_generated else " (auto)"}</span>')

    parts = [f'<div class="brand">Notesmith &middot; {_esc(L["brand"])}</div>',
             f'<h1>{_esc(notes.title)}</h1>',
             f'<div class="chips">{"".join(chips)}</div>']
    if notes.summary:
        parts.append(f'<div class="summary">{_esc(notes.summary)}</div>')
    meta_bits = [datetime.now().strftime("%d/%m/%Y")]
    if url:
        meta_bits.append(f'<a href="{_esc(url)}">{_esc(url)}</a>')
    parts.append(f'<div class="meta">{" &nbsp;·&nbsp; ".join(meta_bits)}</div>')

    parts.append(f"<h2>{_esc(L['notes'])}</h2>")
    for sec in notes.sections:
        head = _esc(sec.get("heading") or "Section")
        ts = sec.get("time")
        ts_html = ""
        if ts is not None:
            label = fmt_time(ts)
            if url:
                sep = "&" if "?" in url else "?"
                ts_html = (f'<a class="ts" href="{_esc(url)}{sep}t={int(ts)}s">'
                           f'&#9654; {label}</a>')
            else:
                ts_html = f'<span class="ts">&#9654; {label}</span>'
        block = [f'<div class="sec"><h3>{head}{ts_html}</h3>']
        pts = sec.get("points") or []
        if pts:
            block.append("<ul>" + "".join(f"<li>{_esc(p)}</li>" for p in pts) + "</ul>")
        terms = sec.get("terms") or []
        if terms:
            rows = "".join(
                f"<tr><td><b>{_esc(t.get('term',''))}</b></td>"
                f"<td>{_esc(t.get('meaning',''))}</td></tr>" for t in terms)
            block.append(f'<table><tr><th style="width:32%">{_esc(L["term"])}</th>'
                         f'<th>{_esc(L["meaning"])}</th></tr>{rows}</table>')
        block.append("</div>")
        parts.append("".join(block))

    if notes.formulas:
        parts.append(f"<h2>{_esc(L['formulas'])}</h2>")
        parts.extend(f'<div class="formula">{_esc(f)}</div>' for f in notes.formulas)

    if notes.exam_tips:
        parts.append(f"<h2>{_esc(L['tips'])}</h2>")
        parts.append("<ul>" + "".join(f'<li class="tip">{_esc(t)}</li>'
                                     for t in notes.exam_tips) + "</ul>")

    if notes.questions:
        parts.append(f"<h2>{_esc(L['questions'])}</h2>")
        parts.append("<ol>" + "".join(f"<li>{_esc(q)}</li>" for q in notes.questions) + "</ol>")

    if include_transcript and transcript is not None:
        parts.append("<h2>Transcript</h2>")
        parts.append("".join(f'<p style="font-size:9pt;color:#4b5563">'
                             f'<b>{fmt_time(s.t)}</b> {_esc(s.text)}</p>'
                             for s in transcript.segments))

    parts.append('<div class="foot">Notesmith se banaya gaya — AI galti kar sakta hai, '
                 'exam se pehle apni textbook se check kijiye.</div>')

    html = (f"<html><head><meta charset='utf-8'><style>{face}{CSS}</style></head>"
            f"<body>{''.join(parts)}</body></html>")
    HTML(string=html, base_url=tempfile.gettempdir()).write_pdf(out_path)
    return out_path


# ----------------------------------------------------------------------------
# 4. Ek hi function jo sab jodta hai
# ----------------------------------------------------------------------------

def build_pdf(url_or_text: str, out_path: str, language: str = "hinglish",
              api_key: str = "", provider: str = "gemini",
              from_text: bool = False, include_transcript: bool = False) -> tuple[str, Notes, Transcript]:
    if from_text:
        tr = transcript_from_text(url_or_text)
        link = ""
    else:
        vid = video_id(url_or_text)
        tr = fetch_transcript(vid)
        link = f"https://www.youtube.com/watch?v={vid}"
    notes = make_notes(tr, language=language, api_key=api_key, provider=provider, video_url=link)
    notes_to_pdf(notes, out_path, video_url=link, transcript=tr,
                 include_transcript=include_transcript)
    return out_path, notes, tr


# ----------------------------------------------------------------------------
# 5. Streamlit UI (mobile-first)
# ----------------------------------------------------------------------------

def run_app() -> None:
    import streamlit as st

    st.set_page_config(page_title="Notesmith — video se notes", page_icon="📝",
                       layout="centered")

    st.markdown("### 📝 Notesmith")
    st.caption("Kisi bhi YouTube video ka link daaliye → ready class notes ka PDF.")

    def secret(name: str) -> str:
        if os.environ.get(name):
            return os.environ[name]
        try:
            return st.secrets.get(name, "")
        except Exception:
            return ""

    with st.sidebar:
        st.markdown("**Settings**")
        language = st.selectbox("Notes ki bhasha", ["hinglish", "hindi", "english"],
                                format_func=lambda x: {"hinglish": "Hinglish",
                                                       "hindi": "हिंदी",
                                                       "english": "English"}[x])
        provider = st.radio("AI provider", ["gemini", "groq"], horizontal=True,
                            help="Gemini free key aistudio.google.com se milti hai, "
                                 "Groq ki console.groq.com se.")
        key_in = st.text_input(f"{provider.upper()} API key", type="password",
                               value=secret(f"{provider.upper()}_API_KEY"),
                               help="Khali chhodiye to offline (basic) notes banenge.")
        include_tr = st.checkbox("PDF mein poora transcript bhi jodein", value=False)
        st.caption("Key sirf aapke session mein rehti hai, kahin save nahi hoti.")

    tab_link, tab_paste = st.tabs(["🔗 Link se", "📋 Transcript paste"])

    with tab_link:
        url = st.text_input("YouTube link", placeholder="https://youtu.be/...",
                            label_visibility="collapsed")
        go_link = st.button("Notes banao", type="primary", use_container_width=True)

    with tab_paste:
        pasted = st.text_area("Transcript paste kijiye", height=180,
                              placeholder="Video ke captions/subtitles yahan paste karein…")
        go_paste = st.button("Paste se notes banao", type="primary", use_container_width=True)

    if not (go_link or go_paste):
        st.info("Tip: YouTube par video ke neeche **…more → Show transcript** se "
                "transcript copy kar sakte hain.")
        return

    source_text = pasted if go_paste else url
    if not (source_text or "").strip():
        st.warning("Pehle link ya transcript daaliye.")
        return

    with st.spinner("Transcript nikaal raha hoon… phir notes banaunga (30-60 second)"):
        try:
            out = Path(tempfile.gettempdir()) / f"notes_{datetime.now():%H%M%S}.pdf"
            path, notes, tr = build_pdf(
                source_text.strip(), str(out), language=language,
                api_key=key_in.strip(), provider=provider,
                from_text=bool(go_paste), include_transcript=include_tr,
            )
            data = Path(path).read_bytes()
        except Exception as e:
            msg = str(e)
            st.error("Kaam nahi bana: " + msg)
            if "block" in msg.lower() or "ip" in msg.lower() or "403" in msg:
                st.info("YouTube ne is server se request rok di. **Transcript paste** "
                        "tab use kijiye — wahi notes ban jayenge.")
            elif "caption" in msg.lower() or "transcript" in msg.lower():
                st.info("Is video par captions nahi mile. Doosra video ya paste tab "
                        "try kijiye.")
            return

    st.success(f"Ban gaya — {notes.title}")
    st.caption(f"{len(notes.sections)} sections · "
               f"{'AI notes' if notes.source == 'ai' else 'offline notes'}")
    st.download_button("⬇️ PDF download kijiye", data=data,
                       file_name=re.sub(r"[^\w\-]+", "_", notes.title)[:60] + ".pdf",
                       mime="application/pdf", type="primary",
                       use_container_width=True)
    with st.expander("PDF mein kya hai (preview)"):
        st.write(notes.summary or "—")
        for sec in notes.sections:
            ts = f" ({fmt_time(sec['time'])})" if sec.get("time") is not None else ""
            st.markdown(f"**{sec['heading']}**{ts}")
            for p in sec.get("points", []):
                st.markdown(f"- {p}")
    with st.expander("Transcript dekhein"):
        st.write(tr.timestamped(20000))


def main() -> None:
    if len(sys.argv) > 1 and "--cli" in sys.argv:
        ap = argparse.ArgumentParser(description="Video se notes PDF")
        ap.add_argument("--cli", metavar="URL")
        ap.add_argument("--out", default="notes.pdf")
        ap.add_argument("--lang", default="hinglish", choices=list(LANG_LINE))
        ap.add_argument("--key", default=os.environ.get("GEMINI_API_KEY", ""))
        ap.add_argument("--provider", default="gemini", choices=["gemini", "groq"])
        ap.add_argument("--transcript-file", default="")
        ap.add_argument("--with-transcript", action="store_true")
        a = ap.parse_args()
        if a.transcript_file:
            src, from_text = Path(a.transcript_file).read_text(encoding="utf-8"), True
        else:
            src, from_text = a.cli, False
        path, notes, tr = build_pdf(src, a.out, language=a.lang, api_key=a.key,
                                    provider=a.provider, from_text=from_text,
                                    include_transcript=a.with_transcript)
        print(f"PDF: {path}")
        print(f"Title: {notes.title}")
        print(f"Sections: {len(notes.sections)} · source: {notes.source}")
        return
    run_app()


if __name__ == "__main__":
    main()
