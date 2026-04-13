#!/usr/bin/env python3
"""
Parse Sorular.txt and update questions.json.
Matches by text similarity. Preserves existing NotebookLM data for unmatched.
"""
import json, re, unicodedata
from pathlib import Path

INPUT_TXT    = Path("Sorular.txt")
CURRENT_JSON = Path("questions.json")
OUTPUT_JSON  = Path("questions.json")
MIN_SCORE    = 0.70

def normalize(t):
    t = unicodedata.normalize("NFC", t).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def word_overlap(a, b):
    wa, wb = set(normalize(a).split()), set(normalize(b).split())
    return len(wa & wb) / len(wa) if wa else 0.0

def best_sim(a, b):
    return max(word_overlap(a, b), word_overlap(b, a))

def extract_section(block, *headers):
    hre = re.compile(r"\*\*(?:" + "|".join(re.escape(h) for h in headers) + r")[:\s]*\*\*", re.IGNORECASE)
    nre = re.compile(r"^\s*\*\*[^*\n]+\*\*\s*$", re.MULTILINE)
    m = hre.search(block)
    if not m: return ""
    rest = block[m.end():]
    nm = nre.search(rest)
    return rest[:nm.start() if nm else len(rest)].strip()

def ss(t): return re.sub(r"\*{1,2}", "", t).strip()

def parse_answer(block):
    m = re.search(r"[Dd]o.ru\s+cevap\s+\**([A-Ea-e])[).\s]", block)
    return m.group(1).lower() if m else None

def parse_opts(block):
    end = re.search(r"\*\*(Do.ru|Dogru)", block, re.IGNORECASE)
    pre = block[:end.start()] if end else block
    opts = {}
    for m in re.finditer(r"^\s*([a-e])\)\s*(.+)", pre, re.IGNORECASE|re.MULTILINE):
        opts[m.group(1).lower()] = m.group(2).strip()
    return opts

def parse_qtext(block):
    b = re.sub(r"^\s*\*\*Soru\s+\d+:\*\*\s*", "", block, flags=re.IGNORECASE)
    lines, out = b.split("\n"), []
    for ln in lines:
        s = ln.strip()
        if re.match(r"^[a-eA-E]\)\s|^\d+\)\s", s): break
        if re.match(r"\*\*(Do.ru|Dogru)", s, re.IGNORECASE): break
        out.append(s)
    t = " ".join(x for x in out if x).strip()
    t = re.sub(r"\s*\(M\d[^)]*\)\s*", " ", t).strip()
    t = re.sub(r"\s*X\d+\s*$", "", t).strip()
    return t

def parse_explanation(block):
    sec = extract_section(block, "Sorunun Aciklamasi", "Sorunun ve Oncullerin Aciklamasi",
                          "Aciklama", "Cevap Aciklamasi")
    return ss(sec) or None

def parse_opt_analysis(block):
    sec = extract_section(block, "Siklarin Aciklamasi", "Siklarin Analizi",
                          "Oncullerin Aciklamasi", "Sik Aciklamalari")
    if not sec: return {}
    out = {}
    for m in re.finditer(r"\*\*([a-e])\)[^*]*:\*\*\s*(.*?)(?=\n\s*\*\s*\*\*[a-e]\)|\Z)", sec, re.DOTALL|re.IGNORECASE):
        out[m.group(1).lower()] = ss(m.group(2))
    return out

def parse_terms(block):
    sec = extract_section(block, "Terimler", "Anahtar Terimler", "Onemli Terimler")
    if not sec: return []
    out = []
    for m in re.finditer(r"\*\*([^*:]+):\*\*\s*(.+?)(?=\n\s*\*\s*\*\*|\Z)", sec, re.DOTALL):
        n, d = m.group(1).strip(), ss(m.group(2))
        if n and d: out.append({"term": n, "definition": d})
    return out

def parse_subtopics(block):
    sec = extract_section(block, "Bilinmesi Gereken Alt Konular", "Alt Konular",
                          "Ilgili Konular", "Baglantili Konular")
    if not sec: return []
    out = []
    parts = re.split(r"\n\s*(?:\d+\.\s+\*\*|\*\s+\*\*)", sec)
    if len(parts) <= 1: parts = re.split(r"\n\s*\d+\.\s+", sec)
    for p in parts:
        p = p.strip()
        if not p: continue
        m2 = re.match(r"([^*:\n]+):\*\*\s*(.*)", p, re.DOTALL)
        if m2:
            title, content = m2.group(1).strip(), ss(m2.group(2))
        else:
            ls = p.split("\n", 1)
            title = ss(ls[0]).strip(": ")
            content = ss(ls[1]) if len(ls) > 1 else ""
        if title and len(title) > 3:
            out.append({"title": title, "content": content})
    return out

def parse_exam_potential(block):
    sec = extract_section(block, "Sinavda Soru Olarak Sorulmasi Muhtemel Yerler",
                          "Sinavda Cikabilecek", "Sinav Potansiyeli")
    return ss(sec) or None

def parse_sorular_txt(path):
    text = path.read_text(encoding="utf-8")
    markers = list(re.finditer(r"\*\*Soru\s+(\d+):", text, re.IGNORECASE))
    results = []
    for i, m in enumerate(markers):
        qid = int(m.group(1))
        block = text[m.start(): markers[i+1].start() if i+1 < len(markers) else len(text)]
        qt = parse_qtext(block)
        if any(best_sim(qt, r["qt"]) > 0.90 for r in results):
            continue
        results.append({
            "sid": qid, "qt": qt,
            "options": parse_opts(block),
            "correct_answer": parse_answer(block),
            "explanation": parse_explanation(block),
            "option_analysis": parse_opt_analysis(block),
            "terms": parse_terms(block),
            "subtopics": parse_subtopics(block),
            "exam_potential": parse_exam_potential(block),
            "_raw_response": block.strip(),
        })
    return results

def main():
    print("Parsing Sorular.txt ...")
    sorular = parse_sorular_txt(INPUT_TXT)
    print(f"  Unique questions: {len(sorular)}")

    with open(CURRENT_JSON, encoding="utf-8") as f:
        json_qs = json.load(f)
    print(f"  Existing JSON   : {len(json_qs)}")

    by_id = {q["id"]: q for q in json_qs}
    matched_ids = set()
    new_qs = []
    log = []

    for sq in sorular:
        best_jq, best_sc = None, 0.0
        for jq in json_qs:
            if jq["id"] in matched_ids: continue
            sc = best_sim(sq["qt"], jq["question"])
            if sc > best_sc: best_sc, best_jq = sc, jq

        if best_jq and best_sc >= MIN_SCORE:
            matched_ids.add(best_jq["id"])
            rec = by_id[best_jq["id"]]
            if sq["correct_answer"]: rec["correct_answer"] = sq["correct_answer"]
            if sq["explanation"]:    rec["explanation"]    = sq["explanation"]
            if sq["option_analysis"]:rec["option_analysis"]= sq["option_analysis"]
            if sq["terms"]:          rec["terms"]          = sq["terms"]
            if sq["subtopics"]:      rec["subtopics"]      = sq["subtopics"]
            if sq["exam_potential"]: rec["exam_potential"] = sq["exam_potential"]
            if sq["_raw_response"]:  rec["_raw_response"]  = sq["_raw_response"]
            if not rec.get("options") and sq["options"]: rec["options"] = sq["options"]
            log.append((sq["sid"], best_jq["id"], best_sc, "matched"))
        else:
            log.append((sq["sid"], None, best_sc, "NEW"))
            new_qs.append(sq)

    next_id = max(by_id) + 1
    for sq in new_qs:
        by_id[next_id] = {
            "id": next_id, "question": sq["qt"],
            "options": sq["options"], "correct_answer": sq["correct_answer"],
            "explanation": sq["explanation"], "option_analysis": sq["option_analysis"],
            "terms": sq["terms"], "subtopics": sq["subtopics"],
            "exam_potential": sq["exam_potential"], "_raw_response": sq["_raw_response"],
        }
        next_id += 1

    print(f"\n{'Sor':>5} {'JSON':>6} {'Sc':>5}  Status")
    print("-"*35)
    for sid, jid, sc, st in log:
        print(f"{sid:>5} {str(jid) if jid else 'NEW':>6} {sc:>5.2f}  {st}")

    result = sorted(by_id.values(), key=lambda q: q["id"])
    wa = sum(1 for q in result if q.get("correct_answer"))
    we = sum(1 for q in result if q.get("explanation"))
    wt = sum(1 for q in result if q.get("terms"))
    na = [q["id"] for q in result if not q.get("correct_answer")]
    ne = [q["id"] for q in result if not q.get("explanation")]

    print(f"\nTotal  : {len(result)}")
    print(f"Matched: {len(matched_ids)} | New: {len(new_qs)} | Untouched: {len(json_qs)-len(matched_ids)}")
    print(f"Answers: {wa} | Explain: {we} | Terms: {wt}")
    if na: print(f"No answer: {na}")
    if ne: print(f"No explain: {ne}")

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saved -> {OUTPUT_JSON}")

if __name__ == "__main__":
    main()
