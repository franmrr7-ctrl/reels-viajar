# Reels de Viajar por Viajar

Genera Reels verticales (1440x2560) para Facebook a partir de una guia de viajarporviajar.com.
Estilo de marca: cobre del logo, crema de la web, tipografias Cormorant Garamond y Lora (SIL OFL).
Cada escena usa una foto distinta del propio articulo (API de WordPress, `post_id`).

- `generate.py guia.json salida.mp4` (ver `examples/granada.json`).
- `.github/workflows/reel.yml`: se lanza por `workflow_dispatch` desde Make y publica el mp4 como release `reel-<name>`.
- `music/`: pistas CC0 (ver `music/LICENSE.txt`).
