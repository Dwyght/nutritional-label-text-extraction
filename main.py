# app.py

import os
import shutil
import re
import cv2 as cv
import pytesseract
from spellchecker import SpellChecker
from google.cloud import vision

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from typing import List, Optional, Dict, Any
from fastapi.middleware.cors import CORSMiddleware

# ——————— Configuration ——————— #
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "google_vision_json/eastern-store-455819-u7-6d76619e02a8.json"
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ——————— OCR + Extraction Pipeline ——————— #

def resize_for_display(image, max_size=1000):
    (h, w) = image.shape[:2]
    scale = max_size / max(h, w)
    return cv.resize(image, (int(w * scale), int(h * scale)), interpolation=cv.INTER_AREA)

def spell_check_text(text: str) -> str:
    spell = SpellChecker()
    allowed = {"mg", "g", "kcal"}
    out = []
    for word in text.split():
        clean = word.strip(".,:;?!()[]{}")
        if re.search(r'\d', clean) or clean.lower() in allowed:
            out.append(word)
        else:
            corr = spell.correction(clean) or word
            out.append(corr)
    return " ".join(out)

def extract_value(text: str, key: str, patterns: Dict[str, List[str]], unit_mapping: Dict[str, str]) -> Optional[str]:
    for pat in patterns[key]:
        m = re.search(pat, text)
        if m:
            for g in m.groups():
                if g:
                    unit = unit_mapping[key]
                    return f"{g.strip()} {unit}".strip() if unit else g.strip()
    return None

def extract_nutrition(ocr_text: str) -> Dict[str, Optional[str]]:
    cleaned = re.sub(r"\s+", " ", ocr_text)

    patterns = {
        "servings": [
            r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+container(?:\s*/\s*pack)?(?:\s*(?:\:|about)\s+)(\d+)",
            r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+pack(?:\s*(?:\:|about)\s+)(\d+)",
            r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+package(?:\s*(?:\:|about)\s+)(\d+)",
            r"(?i)about\s+(\d+)\s+servings?\s+per\s+container",
            r"(?i)(\d+)\s+servings?\s+per\s+container",
            r"(?i)servings?\s*\:\s*(\d+)",
            r"(?i)servings?\s+per\s+pack\s*\(\s*\d+\s*g\s*\)\s+(\d+)",
            r"(?i)servings?\s+per\s+container\s+(\d+)"
        ],
        "sodium": [
            r"(?i)sodium\s*(?:\(\s*mg\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*mg)?",
            r"(?i)sodium\s*,\s*mg\s*(\d+)"
        ],
        "protein": [
            r"(?i)(?:total\s+protein|protein)\s*(?:\(\s*g\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*g)?"
        ],
        "carbohydrates": [
            r"(?i)(?:total\s+carbo(?:hydra(?:te?s?)?|hyrates?)|total\s+carbohydrate(?:s)?|carbo(?:hydra(?:te?s?)?|hydrate)?|carb\.?)\s*(?:\(\s*g\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*g)?"
        ]
    }

    unit_mapping = {
        "servings": "",
        "sodium": "mg",
        "protein": "g",
        "carbohydrates": "g"
    }

    result = {}
    for key in patterns:
        result[key] = extract_value(cleaned, key, patterns, unit_mapping)
    return result

def google_vision_ocr(image_path: str) -> str:
    client = vision.ImageAnnotatorClient()
    with open(image_path, "rb") as f:
        img = vision.Image(content=f.read())
    resp = client.text_detection(image=img)
    if resp.error.message:
        raise Exception(f"Google Vision error: {resp.error.message}")
    return resp.text_annotations[0].description if resp.text_annotations else ""

def parse_number(val: Optional[str]) -> Optional[int]:
    if not val:
        return None
    m = re.match(r"(\d+)", val)
    return int(m.group(1)) if m else None

def process_image(image_path: str, use_google_vision: bool=True) -> Dict[str, Any]:
    img = cv.imread(image_path, cv.IMREAD_GRAYSCALE)
    empty = {k: None for k in ("servings","sodium","protein","carbohydrates")}
    if img is None:
        return {
            "raw_text": "",
            "raw_extracted": empty.copy(),
            "spellchecked_text": "",
            "spellchecked_extracted": empty.copy(),
            "final_extracted": empty.copy(),
            "protein_per_serving": None,
            "sodium_per_serving": None,
            "carbs_per_serving": None,
            "protein_total": None,
            "sodium_total": None,
            "carbs_total": None
        }

    img = resize_for_display(img)
    filt = cv.bilateralFilter(img, 9, 75, 75)
    thresh = cv.adaptiveThreshold(filt, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C, cv.THRESH_BINARY, 11, 2)

    raw_text = ""
    if use_google_vision:
        tmp = "tmp.jpg"
        cv.imwrite(tmp, thresh)
        try:
            raw_text = google_vision_ocr(tmp)
        except Exception:
            pass
        finally:
            os.remove(tmp)

    if not raw_text.strip():
        # raw_text = pytesseract.image_to_string(thresh)
        pass

    raw_ex = extract_nutrition(raw_text)
    spell_text = spell_check_text(raw_text)
    spell_ex = extract_nutrition(spell_text)

    final = {}
    for key in raw_ex:
        final[key] = raw_ex[key] or spell_ex[key] or None

    # per‑serving values
    protein_per = final["protein"]
    sodium_per  = final["sodium"]
    carbs_per   = final["carbohydrates"]

    # numeric values
    servings_n = parse_number(final["servings"])
    protein_n  = parse_number(protein_per)
    sodium_n   = parse_number(sodium_per)
    carbs_n    = parse_number(carbs_per)

    # totals = per‑serving 
    protein_tot = protein_n * servings_n if protein_n is not None and servings_n is not None else None
    sodium_tot  = sodium_n * servings_n  if sodium_n  is not None and servings_n is not None else None
    carbs_tot   = carbs_n * servings_n   if carbs_n   is not None and servings_n is not None else None

    return {
        "raw_text": raw_text,
        "raw_extracted": raw_ex,
        "spellchecked_text": spell_text,
        "spellchecked_extracted": spell_ex,
        "final_extracted": final,
        "protein_per_serving": protein_per,
        "sodium_per_serving": sodium_per,
        "carbs_per_serving": carbs_per,
        "protein_total": f"{protein_tot} g" if protein_tot is not None else None,
        "sodium_total":  f"{sodium_tot} mg" if sodium_tot  is not None else None,
        "carbs_total":   f"{carbs_tot} g" if carbs_tot   is not None else None
    }

# ——————— FastAPI App ——————— #

app = FastAPI(title="Nutrition Label OCR API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict this to your app domain later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/test-vision")
async def test_vision(file: UploadFile = File(...)):
    content = await file.read()
    image = vision.Image(content=content)

    client = vision.ImageAnnotatorClient()
    response = client.text_detection(image=image)

    if response.error.message:
        return {"error": response.error.message}

    texts = response.text_annotations
    if texts:
        return {"detected_text": texts[0].description}
    else:
        return {"detected_text": ""}

@app.post("/extract/")
async def extract_multiple(
    files: List[UploadFile] = File(...),
    use_google_vision: bool = True
):
    if not files:
        raise HTTPException(400, "No files uploaded")

    items = []
    combined_totals = {
        "protein_total": 0,
        "sodium_total":  0,
        "carbs_total":   0
    }
    counts = {k: 0 for k in combined_totals}

    for file in files:
        if not file.content_type.startswith("image/"):
            raise HTTPException(415, f"Unsupported file type: {file.filename}")

        path = os.path.join(UPLOAD_DIR, file.filename)
        with open(path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        try:
            res = process_image(path, use_google_vision)
        finally:
            os.remove(path)

        items.append({"filename": file.filename, **res})

        # aggregate totals
        for key in combined_totals:
            val = res[key]
            if val:
                n = int(re.match(r"(\d+)", val).group(1))
                combined_totals[key] += n
                counts[key] += 1

    # format combined, or None if no contributions
    for key in combined_totals:
        if counts[key] == 0:
            combined_totals[key] = None
        else:
            unit = "g" if "protein" in key or "carbs" in key else "mg"
            combined_totals[key] = f"{combined_totals[key]} {unit}"

    return JSONResponse({"items": items, "combined": combined_totals})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
