from pathlib import Path
import sys
import win32com.client

docx = Path(sys.argv[1]).resolve()
out_dir = Path(sys.argv[2]).resolve()
out_dir.mkdir(parents=True, exist_ok=True)
pdf = out_dir / (docx.stem + ".pdf")
word = win32com.client.DispatchEx("Word.Application")
word.Visible = False
word.DisplayAlerts = 0
doc = None
try:
    doc = word.Documents.Open(str(docx), ReadOnly=True, AddToRecentFiles=False)
    doc.ExportAsFixedFormat(str(pdf), 17, False, 0, 0, 1, 1, 0, True, True, 1, True, True, False)
finally:
    if doc is not None:
        doc.Close(False)
    word.Quit()
print(pdf)
