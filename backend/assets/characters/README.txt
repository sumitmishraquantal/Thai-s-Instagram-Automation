CHARACTER PHOTOS — drop files here
===================================

Folder (full path on your machine):
  Thai-s-Instagram-Automation\backend\assets\characters\

Required files (rename to match exactly):
  host.png   — photo of the podcast HOST (Ron)
  guest.png  — photo of the podcast GUEST (Jason)

Optional:
  both.png   — both people in ONE photo (for opening two-shot + thumbnail fallback)

Accepted formats: .png, .jpg, .jpeg, .webp, .heic (iPhone)
You can name them host.jpg etc.; the pipeline normalizes to host.png automatically.

What to ask your teammate for
-------------------------------
Send this checklist:

  1. HOST headshot
     - Clear face, shoulders-up or chest-up
     - Front-facing or slight 3/4 angle
     - Good lighting, not blurry
     - Plain or simple background is fine
     - Save as: host.png (or host.jpg)

  2. GUEST headshot
     - Same guidelines as host
     - Save as: guest.png (or guest.jpg)

  3. (Optional) Both hosts together
     - Both faces clearly visible, seated or standing side by side
     - Save as: both.png

Where to paste on your machine
------------------------------
Copy the files into:

  backend\assets\characters\host.png
  backend\assets\characters\guest.png
  backend\assets\characters\both.png   (optional)

Quick verify (from backend folder, venv active):
  python prepare_inputs.py

Then run the pipeline as usual. Identity images and thumbnails both read from
this folder (and from assets\identity_cache\ after the first successful run).
