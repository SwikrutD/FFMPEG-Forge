# Liquid Glass Theme

```css
:root {
  --glass-bg: rgba(7, 10, 22, .72);
  --glass-panel: rgba(23, 33, 59, .80);
  --glass-field: rgba(9, 16, 35, .86);
  --glass-edge: rgba(183, 207, 255, .58);
  --glass-accent: #78a8ff;
  --glass-accent-hi: #b8d4ff;
  --glass-text: #e8eaf2;
  --glass-muted: #aab5d1;
}
```

Panels use layered blue gradients, translucent borders, and soft black shadows. Controls use a darker translucent surface for readable text, a pale edge on hover, and a brighter blue pressed state. Accent text and progress fills use `--glass-accent-hi`; muted labels use `--glass-muted`. The PySide6 implementation lives in `ffmpeg_forge.py` and follows these tokens in its application stylesheet.
