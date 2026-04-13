#!/usr/bin/env python3
"""
Process multiple choice questions through NotebookLM.

Workflow:
  Step 1: Fetch the entire answer key in one query (fast).
  Step 2: For each question, fetch a detailed explanation (one query each).
          This step supports --resume to skip already-explained questions.

Usage:
    # Full run (answer key + all explanations):
    python process_questions.py --input d3m5h3_questions.json

    # Fetch / refresh only the answer key, skip explanations:
    python process_questions.py --input d3m5h3_questions.json --answers-only

    # Explain only specific questions (answer key must exist already):
    python process_questions.py --input d3m5h3_questions.json --ids 1 5 12

    # Resume interrupted explanation run:
    python process_questions.py --input d3m5h3_questions.json --resume

Input JSON:
    [{"id": 1, "question": "...", "options": {"a": "...", "b": "..."}}, ...]

Output questions.json:
    [{"id": 1, "question": "...", "options": {...},
      "correct_answer": "c",
      "explanation": "...",
      "option_analysis": {"a": "...", "b": "..."},
      "terms": ["..."],
      "subtopics": ["..."],
      "exam_potential": "..."}, ...]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

SKILL_DIR = Path(r"C:\Users\mmnsa\.claude\skills\notebooklm")
DEFAULT_NOTEBOOK_ID = "dönem-3-modül-5---gis-&-renal-medical-study-guide"
DEFAULT_OUTPUT = Path("questions.json")
DELAY_BETWEEN_QUERIES = 4   # seconds

# ── NotebookLM call ───────────────────────────────────────────────────────────

def ask_notebooklm(prompt: str, notebook_id: str) -> str | None:
    """Call the notebooklm skill. Returns the answer text or None."""
    try:
        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "run.py"),
             "ask_question.py", "--question", prompt, "--notebook-id", notebook_id],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(SKILL_DIR), timeout=360,
        )
    except subprocess.TimeoutExpired:
        print("  [!] Timeout")
        return None
    except Exception as e:
        print(f"  [!] Subprocess error: {e}")
        return None

    if result.returncode != 0:
        return None

    sep = "=" * 60
    parts = result.stdout.split(sep)
    return parts[2].strip() if len(parts) >= 3 else result.stdout.strip() or None


# ── Step 1: Answer key ────────────────────────────────────────────────────────

def fetch_answer_key(notebook_id: str, source_label: str) -> dict[int, str]:
    """
    One-shot query to get all correct answers.
    Returns {question_id: answer_letter} e.g. {1: 'e', 2: 'b', ...}
    """
    prompt = (
        f"{source_label} çıkmış sorularının cevap anahtarını tam olarak listele. "
        "Her satırda sadece soru numarası ve doğru şıkkın harfini yaz, "
        "örnek: '1-C', '2-B' gibi. Tüm soruları eksiksiz listele."
    )
    print("Fetching answer key...")
    raw = ask_notebooklm(prompt, notebook_id)
    if not raw:
        print("  [!] Could not fetch answer key")
        return {}

    # Parse "1-E 2-B 3-D ..." or "1-E\n2-B\n..." or "1. E ..."
    pattern = re.compile(r"(\d+)\s*[-–\.]\s*([A-Ea-e](?:,\d+(?:,\d+)*)?)", re.IGNORECASE)
    key = {}
    for m in pattern.finditer(raw):
        qid = int(m.group(1))
        answer = m.group(2).lower()
        key[qid] = answer

    print(f"  Parsed {len(key)} answers from answer key.")
    return key


# ── Step 2: Explanation per question ─────────────────────────────────────────

def build_explanation_prompt(q: dict, correct_answer: str | None) -> str:
    options_text = "\n".join(
        f"{k.upper()}) {v}" for k, v in q.get("options", {}).items()
    )
    answer_hint = (
        f"(Not: Cevap anahtarına göre doğru şık {correct_answer.upper() if correct_answer else '?'}'dir.)\n\n"
        if correct_answer else ""
    )
    return (
        f"Soru: {q['question']}\n{options_text}\n\n"
        f"{answer_hint}"
        "Bu soruyu şu başlıklar altında açıkla:\n"
        "1. Doğru cevap neden doğrudur?\n"
        "2. Yanlış şıkların her biri neden yanlıştır?\n"
        "3. Bu konudaki önemli tıbbi terimler (virgülle ayrılmış liste).\n"
        "4. Bu konuyla bağlantılı, sınavda çıkabilecek alt konular.\n"
        "5. Bu konudan sınavda başka nasıl sorular sorulabilir?"
    )


_FOLLOWUP_RE = re.compile(r"\n*EXTREMELY IMPORTANT:.*$", re.DOTALL | re.IGNORECASE)
_OPTION_RE = re.compile(r"^\s*([A-Ea-e])[):\.][ \t]+(.+)", re.MULTILINE)

_KW = {
    "terms":          re.compile(r"terim|anahtar kelime|önemli kavram", re.IGNORECASE),
    "subtopics":      re.compile(r"alt konu|ilgili konu|bağlantılı konu", re.IGNORECASE),
    "exam_potential": re.compile(r"sınav|çıkabilecek|başka nasıl|farklı soru", re.IGNORECASE),
}


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _list_from_para(text: str, kw_re) -> list[str]:
    for para in _paragraphs(text):
        if kw_re.search(para):
            lines = para.split("\n")
            body = " ".join(lines[1:]) if len(lines) > 1 else para
            items = [i.strip(" -•*1234567890.") for i in re.split(r"[,،;]", body) if i.strip()]
            return [i for i in items if len(i) > 2]
    return []


def parse_explanation(raw: str, options: dict) -> dict:
    text = _FOLLOWUP_RE.sub("", raw).strip()

    option_analysis = {
        m.group(1).lower(): m.group(2).strip()
        for m in _OPTION_RE.finditer(text)
        if m.group(1).lower() in options
    }

    first_nl = text.find("\n")
    explanation = text[first_nl:].strip() if first_nl != -1 else text

    terms     = _list_from_para(text, _KW["terms"])
    subtopics = _list_from_para(text, _KW["subtopics"])

    exam_paras = [p for p in _paragraphs(text) if _KW["exam_potential"].search(p)]
    exam_potential = "\n\n".join(exam_paras) or None

    return {
        "explanation":    explanation,
        "option_analysis": option_analysis,
        "terms":          terms,
        "subtopics":      subtopics,
        "exam_potential": exam_potential,
        "_raw_response":  text,
    }


# ── Persistence ───────────────────────────────────────────────────────────────

def load_output(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_output(data: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query NotebookLM answer key + explanations for MCQs."
    )
    parser.add_argument("--input",       "-i", required=True)
    parser.add_argument("--output",      "-o", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--notebook-id", "-n", default=DEFAULT_NOTEBOOK_ID)
    parser.add_argument("--source-label",      default="D3 M5 H3",
                        help="Label used in the answer-key query, e.g. 'D3 M5 H4'")
    parser.add_argument("--answers-only", action="store_true",
                        help="Only fetch/update the answer key, skip explanations")
    parser.add_argument("--resume",      action="store_true",
                        help="Skip questions that already have explanations in output")
    parser.add_argument("--ids", nargs="+", type=int,
                        help="Only explain these question IDs")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    notebook_id = args.notebook_id

    if not input_path.exists():
        sys.exit(f"Error: {input_path} not found")

    with open(input_path, encoding="utf-8") as f:
        questions: list[dict] = json.load(f)

    q_by_id = {q["id"]: q for q in questions}

    print(f"Input      : {input_path}  ({len(questions)} questions)")
    print(f"Output     : {output_path}")
    print(f"Notebook   : {notebook_id}")
    print(f"Resume     : {args.resume}")
    print(f"Source     : {args.source_label}\n")

    # ── Load existing output ──────────────────────────────────────────────────
    existing_list = load_output(output_path)
    existing = {item["id"]: item for item in existing_list}

    # ── Step 1: Answer key ────────────────────────────────────────────────────
    answer_key = fetch_answer_key(notebook_id, args.source_label)
    time.sleep(DELAY_BETWEEN_QUERIES)

    # Merge answer key into existing records (or create new ones)
    for q in questions:
        qid = q["id"]
        correct = answer_key.get(qid)
        if qid in existing:
            existing[qid]["correct_answer"] = correct
        else:
            existing[qid] = {
                "id": qid,
                "question": q["question"],
                "options": q.get("options", {}),
                "correct_answer": correct,
                "explanation": None,
                "option_analysis": {},
                "terms": [],
                "subtopics": [],
                "exam_potential": None,
            }

    # Save with just the answer key filled in
    results = [existing[q["id"]] for q in questions if q["id"] in existing]
    save_output(results, output_path)
    print(f"Answer key saved ({len(answer_key)} answers).\n")

    if args.answers_only:
        print("--answers-only: done.")
        return

    # ── Step 2: Explanations ──────────────────────────────────────────────────
    target_ids = set(args.ids) if args.ids else {q["id"] for q in questions}

    todo = []
    for q in questions:
        qid = q["id"]
        if qid not in target_ids:
            continue
        if args.resume and existing.get(qid, {}).get("_raw_response"):
            print(f"[{qid:>3}] Skipping (already explained)")
            continue
        todo.append(q)

    total = len(todo)
    print(f"Explaining {total} question(s)...\n")

    for idx, q in enumerate(todo, 1):
        qid = q["id"]
        correct = existing.get(qid, {}).get("correct_answer")
        short = q["question"][:65]
        print(f"[{qid:>3}] ({idx}/{total}) {short}...")

        prompt = build_explanation_prompt(q, correct)
        raw = ask_notebooklm(prompt, notebook_id)

        if raw is None:
            print("  [!] No response — skipping explanation")
        else:
            parsed = parse_explanation(raw, q.get("options", {}))
            existing[qid].update(parsed)
            n_opts = len(parsed["option_analysis"])
            n_terms = len(parsed["terms"])
            print(f"  [✓] options={n_opts} | terms={n_terms} | exam_potential={'yes' if parsed['exam_potential'] else 'no'}")

        # Save after every question
        results = [existing[q["id"]] for q in questions if q["id"] in existing]
        save_output(results, output_path)
        time.sleep(DELAY_BETWEEN_QUERIES)

    # Summary
    no_answer = [r["id"] for r in results if not r.get("correct_answer")]
    no_explain = [r["id"] for r in results if not r.get("explanation") and r["id"] in target_ids]
    print(f"\nDone. {len(results)} questions saved to {output_path}")
    if no_answer:
        print(f"  Missing answers : {no_answer}")
    if no_explain:
        print(f"  Missing explanations: {no_explain}")


if __name__ == "__main__":
    main()
