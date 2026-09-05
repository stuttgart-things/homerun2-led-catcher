# Visual Aid

Images and GIFs shown by the `image` and `gif` display modes. Reference them
by filename in the profile:

```yaml
displayRules:
  github-error:
    kind: gif
    image: sunset.gif
```

Anything Pillow can open works. Frames are scaled to 64x64 with LANCZOS at
display time, so source images do not need to be pre-sized — but square
sources avoid distortion, and small files keep GIF playback smooth on a Pi.

## Lookup order

`led_catcher.display.modes` resolves an image name against, in order:

1. `$VISUAL_AID_DIR` (if set)
2. this directory, relative to the repo/install root
3. `/app/visual_aid` (container layout)
4. the name as an absolute path

No images are bundled — this directory is intentionally empty so deployments
can mount their own (a ConfigMap/hostPath in Kubernetes, a plain directory on
the Pi).
