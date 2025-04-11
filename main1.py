import os
import cv2 as cv
import numpy as np
import json
import re
import sys
import pytesseract
from spellchecker import SpellChecker
from google.cloud import vision

# Set your Google Vision credential file path
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "google_vision_json/eastern-store-455819-u7-6d76619e02a8.json"

def resize_for_display(image, max_size=1000):
    """Resize the image to a max size for better processing."""
    (h, w) = image.shape[:2]
    scale = max_size / max(h, w)  # Scale based on the longer side
    resized = cv.resize(image, (int(w * scale), int(h * scale)), interpolation=cv.INTER_AREA)
    return resized

def spell_check_text(text):
    """Perform spell checking only on words that are alphabetic.
    
    Words containing digits or listed as exceptions (like units) are left as-is.
    """
    # Initialize the spell checker instance.
    spell = SpellChecker()
    
    # Define a set of words (or regex patterns) that we want to exclude from correction.
    # For example, "mg", "g" are typical measurement units.
    allowed_words = {"mg", "g", "kcal"}
    
    corrected_words = []
    for word in text.split():
        # Strip punctuation from word boundaries.
        clean_word = word.strip(".,:;?!()[]{}")
        
        # If the word contains digits or is in the allowed units, do not change it.
        if re.search(r'\d', clean_word) or clean_word.lower() in allowed_words:
            corrected_words.append(word)
        else:
            # If the spellchecker returns a correction that is different, use it.
            corrected = spell.correction(clean_word)
            corrected_words.append(corrected if corrected else word)
    
    return " ".join(corrected_words)

def extract_value(text, key, patterns, unit_mapping):
    """
    Return the first matched numerical value for a given key by trying 
    all regexes provided in the patterns list.
    """
    for pattern in patterns.get(key, []):
        match = re.search(pattern, text)
        if match:
            # Iterate over groups in case there are multiple capture groups.
            for group in match.groups():
                if group:
                    value = group.strip()
                    unit = unit_mapping.get(key, "")
                    # Append a unit if one exists.
                    return f"{value} {unit}".strip() if unit else value
    return None

def extract_nutrition(ocr_text):
    # Preprocess the text: collapse multiple spaces/newlines/tabs into one space
    cleaned_text = re.sub(r"\s+", " ", ocr_text)  

#     patterns = {
#         "servings": [
#             # Matches labels starting with an optional "No. of"
#             r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+container(?:\s*/\s*pack)?(?:\s*(?:\:|about)\s+)(\d+)",
#             r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+pack(?:\s*(?:\:|about)\s+)(\d+)",
#             r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+package(?:\s*(?:\:|about)\s+)(\d+)",
#             # Matches when the number is given first
#             r"(?i)about\s+(\d+)\s+servings?\s+per\s+container",
#             r"(?i)^(\d+)\s+servings?\s+per\s+container",
#             # A simple label with a colon separator
#             r"(?i)servings?\s*\:\s*(\d+)",
#             # Special case: "Servings per pack (value1g) value2" – only use the latter
#             r"(?i)servings?\s+per\s+pack\s*\(\s*\d+\s*g\s*\)\s+(\d+)"
#         ],
#         "sodium": [
#             # Covers optional parentheses or a comma before mg, and optional separators
#             r"(?i)sodium\s*(?:\(\s*mg\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*mg)?",
#             # Sometimes a comma is used, e.g., "Sodium, mg value"
#             r"(?i)sodium\s*,\s*mg\s*(\d+)"
#         ],
#         "protein": [
#             # Covers both "Total Protein" or just "Protein", with optional colon or separator and (g)
#             r"(?i)(?:total\s+protein|protein)\s*(?:\(\s*g\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*g)?"
#         ],
#         "carbohydrates": [
#             # This one covers:
#             # • "Total Carbohydrates" or "Total Carbohyrates" variants with optional (g)
#             # • "Total Carbohydrate" (singular) variants
#             # • "Carbohydrates" by itself or "Carb." abbreviations
#             # followed by an optional colon or hyphen separator
#             r"(?i)(?:total\s+carbo(?:hydra(?:te?s?)?|hyrates?)|carbo(?:hydra(?:te?s?)?|hydrate)?|carb\.?)\s*(?:\(\s*g\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*g)?"
#         ]
# }
    
    patterns = {
        "servings": [
            # Patterns that expect a label followed by a separator (colon or "about")
            r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+container(?:\s*/\s*pack)?(?:\s*(?:\:|about)\s+)(\d+)",
            r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+pack(?:\s*(?:\:|about)\s+)(\d+)",
            r"(?i)(?:No\.?\s+of\s+)?servings?\s+per\s+package(?:\s*(?:\:|about)\s+)(\d+)",
            # Pattern when the descriptive text comes first with "about" preceding the number
            r"(?i)about\s+(\d+)\s+servings?\s+per\s+container",
            # Pattern when the number is simply followed by "servings per container"
            r"(?i)(\d+)\s+servings?\s+per\s+container",
            # Pattern with a simple colon (e.g., "Servings: value")
            r"(?i)servings?\s*\:\s*(\d+)",
            # Special case: "Servings per pack (value1g) value2" – we only want the latter value
            r"(?i)servings?\s+per\s+pack\s*\(\s*\d+\s*g\s*\)\s+(\d+)",
            r"(?i)servings?\s+per\s+container\s+(\d+)"
        ],
        "sodium": [
            # Matches "Sodium (mg) value" variants including optional parentheses, colon or hyphen, trailing mg
            r"(?i)sodium\s*(?:\(\s*mg\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*mg)?",
            # Variant with a comma before mg (e.g., "Sodium, mg value")
            r"(?i)sodium\s*,\s*mg\s*(\d+)"
        ],
        "protein": [
            # Matches either "Total Protein" or "Protein", with optional (g), colon/hyphen and an optional trailing "g"
            r"(?i)(?:total\s+protein|protein)\s*(?:\(\s*g\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*g)?"
        ],
        "carbohydrates": [
            # This pattern attempts to capture the various spelling variants for carbohydrates:
            # It covers “Total Carbohydrates” (or mis-spelled "Carbohyrates"), “Total Carbohydrate” (singular),
            # as well as abbreviated forms “Carb.” or just “Carbohydrates”, with optional (g) and separator.
            r"(?i)(?:total\s+carbo(?:hydra(?:te?s?)?|hyrates?)|total\s+carbohydrate(?:s)?|carbo(?:hydra(?:te?s?)?|hydrate)?|carb\.?)\s*(?:\(\s*g\s*\))?\s*(?::|-)?\s*(\d+)(?:\s*g)?"
        ]
}


    # Define units (empty string for servings, since it’s unitless)
    unit_mapping = {
        "servings": "",
        "sodium": "mg",
        "protein": "g",
        "carbohydrates": "g"
    }
    
    extracted = {}
    # Use the preprocessed cleaned_text for extraction
    for key in patterns.keys():
        value = extract_value(cleaned_text, key, patterns, unit_mapping)
        if value:
            extracted[key] = value
    return extracted

def google_vision_ocr(image_path):
    """Use Google Cloud Vision API to perform OCR on the image."""
    client = vision.ImageAnnotatorClient()
    
    # Read the image file
    with open(image_path, "rb") as image_file:
        content = image_file.read()

    image = vision.Image(content=content)
    response = client.text_detection(image=image)
    
    if response.error.message:
        raise Exception(f'Google Vision API error: {response.error.message}')
    
    texts = response.text_annotations
    if texts:
        # The first annotation usually contains the full detected text
        return texts[0].description
    return ""

def process_image(image_path, use_google_vision=True):
    """Process the image and extract text and nutrition data."""
    image = cv.imread(image_path, cv.IMREAD_GRAYSCALE)
    if image is None:
        return {"error": "Invalid image file"}
    
    image = resize_for_display(image)

    # Pre-processing for both methods (apply bilateral filter and adaptive threshold)
    img = cv.bilateralFilter(image, 9, 75, 75)
    thresh = cv.adaptiveThreshold(img, 255, cv.ADAPTIVE_THRESH_GAUSSIAN_C, cv.THRESH_BINARY, 11, 2)

    raw_text = ""

    if use_google_vision:
        temp_image_path = "temp_google_vision.jpg"
        cv.imwrite(temp_image_path, thresh)
        try:
            raw_text = google_vision_ocr(temp_image_path)
        except Exception as e:
            print(f"[Warning] Google Vision OCR failed. Using Tesseract instead. Reason: {str(e)}")
        finally:
            os.remove(temp_image_path)

    # If Google Vision fails or returns no text, fallback to Tesseract
    if not raw_text.strip():
        raw_text = pytesseract.image_to_string(thresh)

    raw_extracted = extract_nutrition(raw_text)
    spellchecked_text = spell_check_text(raw_text)
    spellchecked_extracted = extract_nutrition(spellchecked_text)

    # Merge extractions: Prefer raw extracted data, supplement with spellchecked values if missing.
    final_extracted = raw_extracted.copy()
    for key, value in spellchecked_extracted.items():
        if key not in final_extracted:
            final_extracted[key] = value

    return raw_text, raw_extracted, spellchecked_text, spellchecked_extracted, final_extracted

def main():
    if len(sys.argv) < 2:
        print("Usage: python script.py <path_to_image>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"Error: File '{image_path}' does not exist.")
        sys.exit(1)
    
    # Set use_google_vision=True to use Google Cloud Vision for OCR
    raw_text, raw_extracted, spellchecked_text, spellchecked_extracted, final_extracted = process_image(image_path, use_google_vision=True)

    print("\n===== RAW OCR DETECTION =====")
    print(raw_text)

    print("\n===== RAW EXTRACTED VALUES BEFORE SPELL-CHECKING =====")
    print(json.dumps(raw_extracted, indent=4))

    print("\n===== SPELLCHECKED OCR DETECTION =====")
    print(spellchecked_text)

    print("\n===== SPELLCHECKED EXTRACTED VALUES =====")
    print(json.dumps(spellchecked_extracted, indent=4))

    print("\n===== FINAL MERGED NUTRITION DATA =====")
    print(json.dumps(final_extracted, indent=4))

if __name__ == "__main__":
    main()
