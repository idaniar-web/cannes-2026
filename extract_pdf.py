import sys
from pypdf import PdfReader

path = sys.argv[1]
reader = PdfReader(path)
for i, page in enumerate(reader.pages):
    print(f"--- PAGE {i+1} ---")
    print(page.extract_text())
