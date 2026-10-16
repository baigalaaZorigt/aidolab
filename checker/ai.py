"""Explain scan findings in Mongolian for beginner vibe-coders, using Claude."""
import json
import os

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM = """Чи AI ашиглан код бичиж сурч буй эхлэн суралцагчдад туслах Монгол багш.
Аюулгүй байдлын шалгалтын үр дүнг энгийн, ойлгомжтой монгол хэлээр тайлбарла.
Мэргэжлийн нэр томьёог (deploy, API key гэх мэт) англиар үлдээж болно, гэхдээ утгыг нь тайлбарла.
Засах алхмыг тодорхой, дарааллаар нь бич. Боломжтой бол богино кодын жишээ өг.
Хамгийн аюултай асуудлаас эхэл. Урмыг нь бүү хугал — алдаа гаргах нь суралцах хэсэг гэдгийг сануул."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 өгүүлбэрийн ерөнхий дүгнэлт"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string", "description": "Яагаад аюултай вэ"},
                    "fix": {"type": "string", "description": "Хэрхэн засах вэ, алхмаар"},
                },
                "required": ["title", "why", "fix"],
                "additionalProperties": False,
            },
        },
        "next_step": {"type": "string", "description": "Хамгийн түрүүнд хийх ганц зүйл"},
    },
    "required": ["summary", "items", "next_step"],
    "additionalProperties": False,
}


def explain(result: dict) -> dict | None:
    """Return {summary, items, next_step} or None if the API key is missing or the call fails."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    # Findings carry masked secrets only; raw values never leave the scanner.
    payload = {"score": result["score"], "findings": result["findings"][:40]}
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": "Хэрэглэгчийн repo-г шалгасан үр дүн (JSON):\n"
                           + json.dumps(payload, ensure_ascii=False, indent=2)
                           + "\n\nАсуудал бүрийг (ижил төрлийг нэгтгэж болно) тайлбарла.",
            }],
        )
    except anthropic.APIError:
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
