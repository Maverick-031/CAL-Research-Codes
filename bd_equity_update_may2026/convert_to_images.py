"""Convert PPTX to PNG slides via PowerPoint COM automation for visual QA."""
import os, sys, glob
import comtypes.client

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
pptx_path = os.path.join(SCRIPT_DIR, "output", "CAL_BD_Equity_Update_CBC_May2026.pptx")
out_dir = os.path.join(SCRIPT_DIR, "output", "qa_images")
os.makedirs(out_dir, exist_ok=True)

# clear stale
for f in glob.glob(os.path.join(out_dir, "*.png")) + glob.glob(os.path.join(out_dir, "*.jpg")):
    os.remove(f)

# 17 = ppSaveAsPNG; 32 = ppSaveAsJPG
ppt = comtypes.client.CreateObject("PowerPoint.Application")
try:
    pres = ppt.Presentations.Open(pptx_path, WithWindow=False)
    # Export each slide as PNG at 1600 x 900 (16:9)
    pres.Export(out_dir, "PNG", 1600, 900)
    pres.Close()
finally:
    ppt.Quit()

print(f"Exported to {out_dir}")
for f in sorted(os.listdir(out_dir)):
    print(" ", f)
